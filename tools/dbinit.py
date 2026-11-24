"""生成/校验多数据库初始化脚本。

    python tools/dbinit.py --all            # 重新生成 sql/sqlite.sql 等三份
    python tools/dbinit.py --check          # 与运行期 store.SCHEMA 语义比对
    python tools/dbinit.py --dialect mysql --stdout

`--check` 分别用 store.SCHEMA 与 schema 渲染的 SQLite 语句各建一个内存库，
逐表比对列类型、非空、默认值、主键与索引；不一致时打印差异并返回非零。
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mingjian.infra import schema, store  # noqa: E402

_TYPE_ALIAS = {"varchar": "text", "bigint": "integer", "int": "integer", "double": "real",
               "double precision": "real", "longtext": "text"}


def _norm_type(raw: str) -> str:
    t = (raw or "").split("(")[0].strip().lower()
    return _TYPE_ALIAS.get(t, t)


def _snapshot(con: sqlite3.Connection) -> dict:
    out = {}
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for n in names:
        cols = [(_norm_type(t), notnull, dflt, pk)
                for _, _name, t, notnull, dflt, pk in con.execute(f"PRAGMA table_info({n})")]
        col_names = [r[1] for r in con.execute(f"PRAGMA table_info({n})")]
        idx = []
        for row in con.execute(f"PRAGMA index_list({n})"):
            iname, unique = row[1], row[2]
            icols = tuple(r[2] for r in con.execute(f"PRAGMA index_info({iname})"))
            explicit = not iname.startswith("sqlite_autoindex")
            idx.append((iname if explicit else "", bool(unique), icols))
        out[n] = {"cols": tuple(zip(col_names, cols)), "indexes": tuple(sorted(idx))}
    return out


def _build(statements: list[str]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    for s in statements:
        con.execute(s)
    return con


def check() -> int:
    try:
        a = _snapshot(_build([s for s in store.SCHEMA.split(";") if s.strip()]))
    except sqlite3.Error:
        # store.SCHEMA 的语句里带注释与换行，按语句切分不可靠时退回 executescript
        con = sqlite3.connect(":memory:")
        con.executescript(store.SCHEMA)
        a = _snapshot(con)
    b = _snapshot(_build(schema.ddl("sqlite")))

    diffs: list[str] = []
    for name in sorted(set(a) - set(b)):
        diffs.append(f"仅运行期有表：{name}")
    for name in sorted(set(b) - set(a)):
        diffs.append(f"仅 schema.py 有表：{name}")
    for name in sorted(set(a) & set(b)):
        if a[name]["cols"] != b[name]["cols"]:
            for got, want in zip(a[name]["cols"], b[name]["cols"]):
                if got != want:
                    diffs.append(f"{name}.{got[0]}：运行期 {got[1]} ≠ schema.py {want[1]}")
            if len(a[name]["cols"]) != len(b[name]["cols"]):
                diffs.append(f"{name}：列数 {len(a[name]['cols'])} ≠ {len(b[name]['cols'])}")
        if a[name]["indexes"] != b[name]["indexes"]:
            diffs.append(f"{name} 索引：运行期 {a[name]['indexes']} ≠ schema.py {b[name]['indexes']}")

    if diffs:
        print(f"结构不一致，共 {len(diffs)} 处：")
        for d in diffs:
            print("  - " + d)
        print("\n请同步 mingjian/infra/store.py 的 SCHEMA 与 mingjian/infra/schema.py 的 TABLES。")
        return 1
    total_idx = sum(len(v["indexes"]) for v in b.values())
    print(f"结构一致：{len(b)} 张表 / {total_idx} 个索引")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="生成多数据库初始化脚本")
    ap.add_argument("--dialect", choices=[*schema.DIALECTS, "all"], default="all")
    ap.add_argument("--out", default="sql", help="输出目录，默认 sql/")
    ap.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    ap.add_argument("--all", action="store_true", help="等价于 --dialect all")
    ap.add_argument("--check", action="store_true", help="校验与运行期 SQLite 结构一致")
    args = ap.parse_args()

    if args.check:
        return check()

    dialects = schema.DIALECTS if (args.all or args.dialect == "all") else (args.dialect,)
    if args.stdout:
        for d in dialects:
            print(schema.script(d))
        return 0

    out = pathlib.Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    for d in dialects:
        path = out / f"{d}.sql"
        path.write_text(schema.script(d), encoding="utf-8", newline="\n")
        print(f"已写入 {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
