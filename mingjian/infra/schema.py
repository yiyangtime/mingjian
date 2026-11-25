"""跨数据库表结构的单一来源。

运行期默认连 SQLite（零配置、单文件备份、单进程写入），也可通过 `db_url` 切到
PostgreSQL / MySQL。本模块把同一份表定义渲染成三种方言，供 `sql/` 下的初始化
脚本、以及 `tools/dbinit.py`、`tools/dbcheck.py` 使用。

新增表或字段时只改 `TABLES`，然后：

    python tools/dbinit.py --all       # 重新生成 sql/*.sql
    python tools/dbinit.py --check     # 与运行期 store.SCHEMA 做语义比对

`--check` 会建两个临时库、逐表比对列与索引，避免这里的定义和
`mingjian/infra/store.py` 里的 SCHEMA 走偏。

类型只有六种，各库的差异被收敛在这里，业务代码不必感知：

    pk_auto  自增主键
    str      短文本（MySQL 落 VARCHAR，可建索引/主键）
    text     中长文本（标题、URL、JSON 片段，不建索引）
    long     超长文本（正文、事件载荷、快照）
    int      整数
    real     浮点
"""
from __future__ import annotations

from dataclasses import dataclass

DIALECTS = ("sqlite", "postgres", "mysql")


@dataclass(frozen=True)
class Col:
    name: str
    kind: str = "str"
    length: int = 255          # 仅 MySQL 的 str 使用；其它方言忽略
    notnull: bool = False
    default: str | None = None  # SQL 字面量，如 "1"、"'[]'"
    unique: bool = False        # 单列唯一
    pk: bool = False            # 单列主键（文本主键，非自增）


@dataclass(frozen=True)
class Table:
    name: str
    cols: tuple[Col, ...]
    pk: tuple[str, ...] = ()                                    # 复合主键
    uniques: tuple[tuple[str, tuple[str, ...]], ...] = ()        # (约束名, 列)
    indexes: tuple[tuple[str, tuple[str, ...]], ...] = ()        # (索引名, 列)


TABLES: tuple[Table, ...] = (
    Table(
        "entities",
        cols=(
            Col("id", "pk_auto"),
            Col("key", length=255, unique=True),   # 归一化后的查询名
            Col("name"),
            Col("short_name"),
            Col("full_name"),
            Col("code"),
            Col("org_id"),
            Col("market"),
            Col("resolved_by"),
            Col("created_at"),
            Col("updated_at"),
        ),
    ),
    Table(
        "runs",
        cols=(
            Col("id", pk=True),
            Col("entity_key"),
            Col("company"),
            Col("status"),
            Col("started_at"),
            Col("finished_at"),
            Col("params", "text"),
            Col("stats", "text"),
            Col("judgments", "text"),
            Col("error", "text"),
        ),
    ),
    Table(
        "documents",
        cols=(
            Col("id", pk=True),
            Col("run_id"),
            Col("entity_key"),
            Col("kind"),
            Col("title", "text"),
            Col("url", "text"),
            Col("source"),
            Col("published_at"),
            Col("body", "long"),
            Col("evidence_level"),
            Col("fetched_at"),
            Col("extra", "text"),
        ),
        indexes=(("idx_doc_run", ("run_id",)), ("idx_doc_entity", ("entity_key",))),
    ),
    Table(
        "facts",
        cols=(
            Col("id", pk=True),
            Col("run_id"),
            Col("entity_key"),
            Col("doc_id"),
            Col("type"),
            Col("type_label"),
            Col("statement", "text"),
            Col("evidence_quote", "text"),
            Col("amount"),
            Col("event_date"),
            Col("confidence", "real"),
            Col("dedupe_key"),
            Col("corroboration", "int", default="1"),
            Col("conflict", "int", default="0"),
            Col("conflict_note", "text"),
            Col("created_at"),
        ),
        indexes=(("idx_fact_run", ("run_id",)), ("idx_fact_entity", ("entity_key",))),
    ),
    Table(
        "snapshots",
        cols=(
            Col("id", "pk_auto"),
            Col("entity_key"),
            Col("run_id"),
            Col("run_at"),
            Col("facts", "long"),
        ),
        indexes=(("idx_snap_entity", ("entity_key",)),),
    ),
    Table(
        "subjects",
        cols=(
            Col("key", pk=True),          # 归一化企业名
            Col("name"),
            Col("data", "long"),
            Col("created_at"),
            Col("updated_at"),
        ),
    ),
    Table(
        "audit",
        cols=(
            Col("id", "pk_auto"),
            Col("entity_key"),
            Col("run_id"),
            Col("ts"),
            Col("actor"),
            Col("action"),
            Col("target"),
            Col("old_value", "text"),
            Col("new_value", "text"),
            Col("reason", "text"),
        ),
    ),
    Table(
        "run_events",
        cols=(
            Col("id", "pk_auto"),
            Col("run_id", notnull=True),
            Col("seq", "int", notnull=True),
            Col("type"),
            Col("payload", "long"),
            Col("created_at"),
        ),
        uniques=(("idx_event_run_seq", ("run_id", "seq")),),
    ),
    Table(
        "checkpoints",
        cols=(
            Col("run_id", notnull=True),
            Col("step", "int", notnull=True),
            Col("payload", "long"),
            Col("created_at"),
        ),
        pk=("run_id", "step"),
    ),
    Table(
        "schedules",
        cols=(
            Col("id", pk=True),
            Col("name"),
            Col("kind"),
            Col("params", "text"),
            Col("spec", "text"),
            Col("enabled", "int", default="1"),
            Col("last_run_at"),
            Col("last_run_id"),
            Col("next_run_at"),
            Col("created_at"),
            Col("updated_at"),
        ),
        indexes=(("idx_sched_next", ("enabled", "next_run_at")),),
    ),
    Table(
        "memory",
        cols=(
            Col("id", pk=True),
            Col("scope", notnull=True, default="'global'"),
            Col("key", notnull=True, default="''"),
            Col("kind", notnull=True, default="'note'"),
            Col("content", "long", notnull=True),
            Col("refs", length=2048, notnull=True, default="'[]'"),
            Col("source", notnull=True, default="''"),
            Col("confidence", "real"),
            Col("hits", "int", notnull=True, default="0"),
            Col("created_at", notnull=True),
            Col("updated_at", notnull=True),
            Col("used_at"),
        ),
        indexes=(("idx_memory_scope", ("scope",)), ("idx_memory_kind", ("kind",))),
    ),
    Table(
        "companies",
        cols=(
            Col("key", pk=True),                    # 归一化企业名
            Col("name"),
            Col("credit_code", length=64),          # 统一社会信用代码
            Col("legal_person"),
            Col("reg_capital"),
            Col("reg_status"),
            Col("establish_date"),
            Col("location", "text"),
            Col("website"),                           # 公司官网
            Col("industry"),
            Col("business", "long"),
            Col("source"),                          # 工商接口标识
            Col("raw", "long"),                     # 上游原文，便于字段映射排错
            Col("fetched_at"),
        ),
        indexes=(("idx_company_name", ("name",)), ("idx_company_code", ("credit_code",))),
    ),
    Table(
        "settings",
        cols=(
            Col("key", pk=True),                    # 配置键，如 llm
            Col("value", "long", notnull=True),      # JSON 原文
            Col("updated_at", notnull=True),
        ),
    ),
)

# 各库的列类型映射。pk_auto 单独处理，因为三种库的自增语法位置不同。
_TYPES = {
    "sqlite": {"str": "TEXT", "text": "TEXT", "long": "TEXT", "int": "INTEGER", "real": "REAL"},
    "postgres": {"str": "TEXT", "text": "TEXT", "long": "TEXT", "int": "INTEGER", "real": "DOUBLE PRECISION"},
    "mysql": {"str": "VARCHAR({length})", "text": "TEXT", "long": "LONGTEXT", "int": "INT", "real": "DOUBLE"},
}

_HEADERS = {
    "sqlite": (
        "-- SQLite 初始化脚本（由 mingjian/infra/schema.py 生成，请勿手工修改）\n"
        "-- 用法：sqlite3 data/mingjian.db < sql/sqlite.sql\n"
        "-- 运行期会自动建表，本脚本供手工预建库、表结构审阅与离线比对。"
    ),
    "postgres": (
        "-- PostgreSQL 12+ 初始化脚本（由 mingjian/infra/schema.py 生成，请勿手工修改）\n"
        "-- 前置：先创建数据库，本脚本只建表与索引。\n"
        "-- 用法：psql \"$DATABASE_URL\" -f sql/postgres.sql"
    ),
    "mysql": (
        "-- MySQL 8.0+ 初始化脚本（由 mingjian/infra/schema.py 生成，请勿手工修改）\n"
        "-- 前置：先创建 utf8mb4 数据库，本脚本只建表与索引。\n"
        "-- 用法：mysql -u USER -p DB < sql/mysql.sql\n"
        "-- 字符集固定 utf8mb4，避免中文与 emoji 被截断。\n"
        "SET NAMES utf8mb4;"
    ),
}


def _ident(name: str, dialect: str) -> str:
    # MySQL 里 key 是保留字，统一加反引号最省心；另两种库保持可读的裸标识符。
    return f"`{name}`" if dialect == "mysql" else name


def _type_sql(c: Col, dialect: str) -> str:
    if c.kind == "pk_auto":
        if dialect == "sqlite":
            return "INTEGER PRIMARY KEY AUTOINCREMENT"   # SQLite 要求三个词连写
        if dialect == "postgres":
            return "BIGSERIAL PRIMARY KEY"
        return "BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY"
    tpl = _TYPES[dialect][c.kind]
    return tpl.format(length=c.length) if "{length}" in tpl else tpl


def _col_sql(c: Col, dialect: str) -> str:
    parts = [_ident(c.name, dialect), _type_sql(c, dialect)]
    if c.notnull and c.kind != "pk_auto":
        parts.append("NOT NULL")
    if c.default is not None:
        parts.append("DEFAULT " + c.default)
    if c.unique:
        parts.append("UNIQUE")
    if c.pk:
        parts.append("PRIMARY KEY")
    return " ".join(parts)


def _cols_sql(cols: tuple[str, ...], dialect: str) -> str:
    return ", ".join(_ident(c, dialect) for c in cols)


def table_sql(t: Table, dialect: str) -> str:
    lines = ["  " + _col_sql(c, dialect) for c in t.cols]
    if t.pk:
        lines.append(f"  PRIMARY KEY ({_cols_sql(t.pk, dialect)})")
    if dialect == "mysql":
        for name, cols in t.uniques:
            lines.append(f"  UNIQUE KEY {_ident(name, dialect)} ({_cols_sql(cols, dialect)})")
        # MySQL 不支持 CREATE INDEX IF NOT EXISTS，索引写在建表语句里才可重复执行
        for name, cols in t.indexes:
            lines.append(f"  KEY {_ident(name, dialect)} ({_cols_sql(cols, dialect)})")
    body = ",\n".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {_ident(t.name, dialect)} (\n{body}\n);"


def ddl(dialect: str) -> list[str]:
    """返回可直接逐条执行的建表/建索引语句。"""
    if dialect not in DIALECTS:
        raise ValueError(f"不支持的方言：{dialect}，可选 {', '.join(DIALECTS)}")
    out: list[str] = []
    for t in TABLES:
        out.append(table_sql(t, dialect))
        if dialect != "mysql":
            # 唯一约束在 SQLite/PostgreSQL 上落成具名唯一索引，与运行期 SCHEMA 的写法一致
            for name, cols in t.uniques:
                out.append(f"CREATE UNIQUE INDEX IF NOT EXISTS {_ident(name, dialect)} "
                           f"ON {_ident(t.name, dialect)} ({_cols_sql(cols, dialect)});")
            for name, cols in t.indexes:
                out.append(f"CREATE INDEX IF NOT EXISTS {_ident(name, dialect)} "
                           f"ON {_ident(t.name, dialect)} ({_cols_sql(cols, dialect)});")
    return out


def script(dialect: str) -> str:
    """返回带文件头的完整初始化脚本。"""
    head = _HEADERS[dialect]
    return head + "\n\n" + "\n\n".join(ddl(dialect)) + "\n"
