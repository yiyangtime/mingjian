"""数据库连接层：SQLite / PostgreSQL / MySQL。

默认连本地 SQLite（零配置、单文件备份）。在 `config.json` 写 `db_url`，或设置
环境变量 `MINGJIAN_DB_URL`，即可切到服务端数据库：

    postgresql://user:pass@127.0.0.1:5432/mingjian
    mysql://user:pass@127.0.0.1:3306/mingjian

业务代码只调 `store.conn()` / `store.tx()`，不感知方言。差异全部收敛在这里：

    占位符   SQLite 用 ?，psycopg / PyMySQL 用 %s，execute 时统一转译
    百分号   SQL 里的字面量 % 转成 %%，否则驱动会按参数格式解析而报错
    保留字   MySQL 的 key 必须加反引号，按完整标识符替换，不误伤 dedupe_key
    行工厂   SQLite 用 sqlite3.Row，PostgreSQL 用 dict_row，MySQL 用 DictCursor
    建表     SQLite 用 store.SCHEMA，另两种用 schema.ddl() 渲染
    自增     AUTOINCREMENT / BIGSERIAL / AUTO_INCREMENT
    事务     服务端库连接开自动提交，tx() 显式 BEGIN；嵌套只在最外层提交

连接按线程缓存（`store._local`）：API 线程池、作业线程、调度线程各持一条，
不会跨线程共享。服务端数据库的连接数因此等于常驻线程数。
"""
from __future__ import annotations

import sqlite3
import urllib.parse

from ..config import mask_db_url

SQLITE = "sqlite"
POSTGRES = "postgres"
MYSQL = "mysql"

_SCHEMES = {
    "sqlite": SQLITE, "sqlite3": SQLITE,
    "postgres": POSTGRES, "postgresql": POSTGRES,
    "mysql": MYSQL, "mariadb": MYSQL,
}

_VERSION_SQL = {
    SQLITE: "SELECT sqlite_version() AS v",
    POSTGRES: "SELECT version() AS v",
    MYSQL: "SELECT version() AS v",
}


def dialect_of(url: str) -> str:
    """空串表示本地 SQLite。"""
    if not url:
        return SQLITE
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in _SCHEMES:
        raise ValueError(f"不支持的数据库连接串：{url.split('://', 1)[0]}://"
                         "（可选 sqlite / postgresql / mysql）")
    return _SCHEMES[scheme]


def sqlite_path(url: str) -> str:
    """从 sqlite:// 连接串取文件路径，兼容 sqlite:///C:/x.db 与相对路径。"""
    rest = url.split("://", 1)[1] if "://" in url else url
    if rest in ("", ":memory:"):
        return ":memory:"
    if rest.startswith("/") and len(rest) > 2 and rest[2] == ":":
        return rest[1:]                      # /C:/data/x.db
    if rest.startswith("/"):
        return rest[1:]
    return rest


def target_of(url: str, sqlite_path_hint) -> str:
    """可展示的库标识：SQLite 给文件路径，服务端库给隐去密码的连接串。"""
    if not url:
        return str(sqlite_path_hint)
    return mask_db_url(url)


_MYSQL_RESERVED = frozenset({"key"})     # 只用到的保留字，按分词替换


def like_op(dialect: str) -> str:
    """PostgreSQL 的 LIKE 区分大小写，搜索统一用 ILIKE 保持行为一致。"""
    return "ILIKE" if dialect == POSTGRES else "LIKE"


def translate_sql(sql: str, dialect: str) -> str:
    """把 SQL 调整成目标驱动可直接执行的形态。

    SQLite 原样返回；另两种方言做三件事，都在字符串字面量之外进行：

        ?  → %s        占位符换成驱动认的 pyformat
        %  → %%        字面量百分号转义，避免被当成参数格式符
        key → `key`    MySQL 保留字；按完整标识符匹配，不动 dedupe_key / entity_key
    """
    if dialect == SQLITE:
        return sql
    out: list[str] = []
    quote = ""
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:          # '' / "" / `` 转义
                    out.append(sql[i + 1])
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "?":
            out.append("%s")
            i += 1
            continue
        if ch == "%":
            out.append("%%")
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            if dialect == MYSQL and word.lower() in _MYSQL_RESERVED:
                out.append(f"`{word}`")
            else:
                out.append(word)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class DriverMissing(RuntimeError):
    """缺少对应数据库的 Python 驱动。"""


class ConnectFailed(RuntimeError):
    """连接串不可用或数据库不可达。"""


class Cursor:
    """统一游标：fetchone/fetchall 返回字典（SQLite 返回 sqlite3.Row）。"""

    def __init__(self, cur, dialect: str):
        self._cur = cur
        self._dialect = dialect

    def _row(self, row):
        if row is None or self._dialect == SQLITE:
            return row
        if isinstance(row, dict):
            return row
        cols = [d[0] for d in (self._cur.description or [])]
        return dict(zip(cols, row))

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def close(self) -> None:
        self._cur.close()


class Conn:
    """统一连接：begin / execute / executescript / commit / rollback / close。

    SQLite 用隐式事务（执行写语句即开始）；服务端库连接开自动提交，由 `begin()`
    显式发 BEGIN。这样只读查询不会留下悬挂事务，`store.tx()` 仍是原子的。
    事务按连接计数，嵌套 tx() 只在最外层真正提交。
    """

    def __init__(self, raw, dialect: str, url: str = ""):
        self.raw = raw
        self.dialect = dialect
        self.url = url
        self._depth = 0

    def begin(self) -> None:
        if self._depth == 0 and self.dialect != SQLITE:
            cur = self.raw.cursor()
            try:
                cur.execute("BEGIN" if self.dialect == POSTGRES else "START TRANSACTION")
            finally:
                cur.close()
        self._depth += 1

    def execute(self, sql: str, params=()) -> Cursor:
        cur = self.raw.cursor()
        cur.execute(translate_sql(sql, self.dialect), tuple(params))
        return Cursor(cur, self.dialect)

    def executescript(self, sql: str) -> None:
        if self.dialect == SQLITE:
            self.raw.executescript(sql)
            return
        for stmt in sql.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def commit(self) -> None:
        if self._depth > 1:
            self._depth -= 1
            return
        self._depth = 0
        self.raw.commit()

    def rollback(self) -> None:
        self._depth = 0
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def version(self) -> str:
        row = self.execute(_VERSION_SQL[self.dialect]).fetchone()
        return str(row["v"]) if row else ""


def connect(sqlite_path_hint, url: str = "") -> Conn:
    """按连接串建连接；url 为空时连本地 SQLite。

    服务端库开自动提交，只读查询不占用长事务；写操作由 `store.tx()` 显式开事务。
    连接失败统一抛 `ConnectFailed`，消息里隐去密码。
    """
    dialect = dialect_of(url)

    if dialect == SQLITE:
        path = sqlite_path(url) if url else str(sqlite_path_hint)
        raw = sqlite3.connect(path, timeout=20, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        if path != ":memory:":
            # WAL 与会话内同步策略只对文件库有意义；内存库设了反而报错
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA synchronous=NORMAL")
        return Conn(raw, SQLITE, url)

    if dialect == POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:                       # 回退 psycopg2
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
            except ImportError:
                raise DriverMissing("缺少 PostgreSQL 驱动：pip install \"psycopg[binary]\"") from None
            try:
                raw = psycopg2.connect(url, cursor_factory=RealDictCursor)
                raw.autocommit = True
            except Exception as e:  # noqa: BLE001
                raise ConnectFailed(f"连接 {mask_db_url(url)} 失败：{e}") from e
            return Conn(raw, POSTGRES, url)
        try:
            raw = psycopg.connect(url, row_factory=dict_row, autocommit=True)
        except Exception as e:  # noqa: BLE001
            raise ConnectFailed(f"连接 {mask_db_url(url)} 失败：{e}") from e
        return Conn(raw, POSTGRES, url)

    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError:
        raise DriverMissing("缺少 MySQL 驱动：pip install PyMySQL") from None

    p = urllib.parse.urlparse(url)
    try:
        raw = pymysql.connect(
            host=p.hostname or "127.0.0.1", port=p.port or 3306,
            user=urllib.parse.unquote(p.username or ""),
            password=urllib.parse.unquote(p.password or ""),
            database=(p.path or "/").lstrip("/") or None,
            charset="utf8mb4", cursorclass=DictCursor, autocommit=True,
        )
    except Exception as e:  # noqa: BLE001
        raise ConnectFailed(f"连接 {mask_db_url(url)} 失败：{e}") from e
    return Conn(raw, MYSQL, url)


def upsert_sql(table: str, cols: tuple[str, ...], conflict: tuple[str, ...], dialect: str) -> str:
    """三种方言的「存在即覆盖」写法，占位符统一用 ?，由 execute 转译。"""
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    if dialect == SQLITE:
        return f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})"
    if dialect == MYSQL:
        # REPLACE INTO 命中主键或唯一键时先删后插，语义与 SQLite 的 OR REPLACE 一致
        return f"REPLACE INTO {table} ({names}) VALUES ({marks})"
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in conflict)
    tgt = ", ".join(conflict)
    if not sets:
        return f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT ({tgt}) DO NOTHING"
    return f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT ({tgt}) DO UPDATE SET {sets}"
