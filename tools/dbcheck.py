"""数据库连通性与结构自检（SQLite / PostgreSQL / MySQL）。

    python tools/dbcheck.py --url sqlite://:memory: --init
    python tools/dbcheck.py --url sqlite://data/mingjian.db
    python tools/dbcheck.py --url postgresql://user:pass@127.0.0.1:5432/mingjian --init
    python tools/dbcheck.py --url mysql://user:pass@127.0.0.1:3306/mingjian --init

走的是服务运行期同一套连接层（`mingjian.infra.db`）：方言识别、占位符转译、
保留字与百分号处理、行工厂都与线上一致；`--init` 再用 schema.py 渲染的对应
方言建表，最后用中文串做一轮写入—读取—删除探针。

退出码：0 通过；2 缺少驱动；3 连接失败；4 建表失败；5 探针失败。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mingjian.config import DB_PATH  # noqa: E402
from mingjian.infra import db as dbmod  # noqa: E402
from mingjian.infra import schema  # noqa: E402

PROBE_KEY = "_dbcheck_"


def _die(code: int, msg: str) -> None:
    print(f"失败：{msg}")
    raise SystemExit(code)


def probe(con) -> None:
    """写入—读取—删除，顺带验证中文、占位符与 key 保留字处理。"""
    name = "明鉴自检 · 中文与符号 ✓"
    ts = "2026-01-01 00:00:00"
    try:
        con.execute("DELETE FROM entities WHERE key=?", (PROBE_KEY,))
        con.execute("INSERT INTO entities (key,name,created_at,updated_at) VALUES (?,?,?,?)",
                    (PROBE_KEY, name, ts, ts))
        con.commit()
        row = con.execute("SELECT name FROM entities WHERE key=?", (PROBE_KEY,)).fetchone()
        if not row or row["name"] != name:
            _die(5, f"读回内容不一致：{dict(row) if row else None}")
        con.execute("DELETE FROM entities WHERE key=?", (PROBE_KEY,))
        con.commit()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        _die(5, f"{type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="数据库连通性与结构自检")
    ap.add_argument("--url", required=True,
                    help="sqlite://:memory: | postgresql://user:pass@host:5432/db | "
                         "mysql://user:pass@host:3306/db")
    ap.add_argument("--init", action="store_true", help="先按对应方言建表（幂等）")
    args = ap.parse_args()

    try:
        dialect = dbmod.dialect_of(args.url)
    except ValueError as e:
        _die(3, str(e))

    try:
        con = dbmod.connect(DB_PATH, args.url)
    except dbmod.DriverMissing as e:
        _die(2, str(e))
    except Exception as e:  # noqa: BLE001
        _die(3, f"{type(e).__name__}: {e}")

    print(f"方言：{dialect}")
    print(f"目标：{dbmod.target_of(args.url, DB_PATH)}")
    try:
        print(f"版本：{con.version()}")
    except Exception as e:  # noqa: BLE001
        _die(3, f"查询版本失败：{type(e).__name__}: {e}")

    if args.init:
        stmts = schema.ddl(dialect)
        try:
            for stmt in stmts:
                con.execute(stmt)
            con.commit()
        except Exception as e:  # noqa: BLE001
            _die(4, f"建表失败：{type(e).__name__}: {e}")
        print(f"建表：执行 {len(stmts)} 条语句（{len(schema.TABLES)} 张表）")

    probe(con)
    print("探针：写入 / 读取 / 删除 均通过（含中文）")
    con.close()
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
