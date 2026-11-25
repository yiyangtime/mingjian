"""
持久层。默认连本地 SQLite，可通过 db_url 切到 PostgreSQL / MySQL：
十三张表对应需求文档里"可追溯"的最小集合：

entities    公司实体（一次解析，长期复用）
runs        每次运行的元信息（谁、何时、参数、统计）
documents   采集到的每一份材料（原始 URL、发布时间、正文、证据等级）
facts       从材料中抽出的每一条事实（含证据句、置信度、来源文档）
snapshots   每次运行的事实快照（用于下次做变化比对）
audit       人工操作留痕（确认/修正/驳回）
run_events  执行过程事件（步骤/日志），服务重启后仍能回放
checkpoints 步骤级检查点（断点续跑只重跑未完成的步骤）
schedules   定时任务定义（每天/每周，由 serve 进程执行）
subjects    非上市主体自述档案（我方基线，标注为企业自述）
memory      跨运行记忆（事实摘要、判断口径、偏好），带引用与命中计数
settings    运行期配置（模型接入等），值为 JSON 原文

关键约束：facts 必须挂 doc_id，documents 必须留 url。没有来源的事实不入库。
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from contextlib import contextmanager

from ..config import DB_PATH, DB_URL
from . import db as dbmod
from . import schema as schemadef

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE,               -- 归一化后的查询名
  name TEXT,                     -- 用户输入
  short_name TEXT,
  full_name TEXT,
  code TEXT,
  org_id TEXT,
  market TEXT,
  resolved_by TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  entity_key TEXT,
  company TEXT,
  status TEXT,                   -- running / done / failed
  started_at TEXT,
  finished_at TEXT,
  params TEXT,
  stats TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  entity_key TEXT,
  kind TEXT,                     -- announcement / news / web
  title TEXT,
  url TEXT,
  source TEXT,
  published_at TEXT,
  body TEXT,
  evidence_level TEXT,           -- full / snippet / title
  fetched_at TEXT,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_run ON documents(run_id);
CREATE INDEX IF NOT EXISTS idx_doc_entity ON documents(entity_key);

CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  entity_key TEXT,
  doc_id TEXT,
  type TEXT,
  type_label TEXT,
  statement TEXT,
  evidence_quote TEXT,
  amount TEXT,
  event_date TEXT,
  confidence REAL,
  dedupe_key TEXT,
  corroboration INTEGER DEFAULT 1,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fact_run ON facts(run_id);
CREATE INDEX IF NOT EXISTS idx_fact_entity ON facts(entity_key);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_key TEXT,
  run_id TEXT,
  run_at TEXT,
  facts TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_entity ON snapshots(entity_key);

CREATE TABLE IF NOT EXISTS subjects (
  key TEXT PRIMARY KEY,          -- 归一化企业名
  name TEXT,                     -- 展示名
  data TEXT,                     -- JSON：自述档案全字段
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_key TEXT,
  run_id TEXT,
  ts TEXT,
  actor TEXT,
  action TEXT,                   -- confirm / correct / reject / note
  target TEXT,                   -- fact id
  old_value TEXT,
  new_value TEXT,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,          -- 同一次运行内单调递增，前端按 seq 增量拉取
  type TEXT,                     -- step / log / done
  payload TEXT,                  -- 事件原文 JSON
  created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_run_seq ON run_events(run_id, seq);

CREATE TABLE IF NOT EXISTS checkpoints (
  run_id TEXT NOT NULL,
  step INTEGER NOT NULL,         -- 已完成到第几步（1～4）
  payload TEXT,                  -- 该步结束时的实体/档案/统计快照
  created_at TEXT,
  PRIMARY KEY (run_id, step)
);

CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY,
  name TEXT,
  kind TEXT,                     -- run（企业洞察）/ compare（竞品追踪）
  params TEXT,                   -- JSON：公司、回溯天数、抓取条数
  spec TEXT,                     -- JSON：{freq: daily|weekly, weekday: 1-7, time: HH:MM}
  enabled INTEGER DEFAULT 1,
  last_run_at TEXT,
  last_run_id TEXT,
  next_run_at TEXT,              -- 本地时间，格式 YYYY-MM-DD HH:MM:SS
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sched_next ON schedules(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'global',   -- 实体 key 或 global
  key TEXT NOT NULL DEFAULT '',           -- 记忆主题（公司名/口径名）
  kind TEXT NOT NULL DEFAULT 'note',      -- fact/judgment/caliber/preference/note
  content TEXT NOT NULL,
  refs TEXT NOT NULL DEFAULT '[]',        -- JSON：run_id / doc_id / url
  source TEXT NOT NULL DEFAULT '',
  confidence REAL,
  hits INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind);

CREATE TABLE IF NOT EXISTS companies (
  key TEXT PRIMARY KEY,          -- 归一化企业名
  name TEXT,
  credit_code TEXT,              -- 统一社会信用代码
  legal_person TEXT,
  reg_capital TEXT,
  reg_status TEXT,               -- 存续 / 在业 / 注销 / 吊销
  establish_date TEXT,
  location TEXT,                 -- 注册地址
  website TEXT,                  -- 公司官网
  industry TEXT,
  business TEXT,                 -- 经营范围
  source TEXT,                   -- 工商接口标识
  raw TEXT,                      -- 上游原文，便于字段映射排错
  fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_company_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_company_code ON companies(credit_code);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,          -- 配置键，如 llm（模型接入配置）
  value TEXT NOT NULL,           -- JSON 原文
  updated_at TEXT NOT NULL
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def dialect() -> str:
    """当前库的方言：sqlite / postgres / mysql。"""
    return dbmod.dialect_of(DB_URL)


def target() -> str:
    """当前库的可展示标识；服务端连接串中的密码会被隐去。"""
    return dbmod.target_of(DB_URL, DB_PATH)


def db_size() -> int:
    """SQLite 库文件大小；服务端数据库没有单一文件，返回 0。"""
    if dialect() != dbmod.SQLITE:
        return 0
    path = dbmod.sqlite_path(DB_URL) if DB_URL else str(DB_PATH)
    try:
        return pathlib.Path(path).stat().st_size
    except OSError:
        return 0


def ddl_statements() -> list[str]:
    """建表语句：SQLite 用带中文注释的 SCHEMA，另两种方言由 schema.py 渲染。"""
    if dialect() == dbmod.SQLITE:
        return [s for s in SCHEMA.split(";") if s.strip()]
    return schemadef.ddl(dialect())


# 旧库补列：CREATE TABLE IF NOT EXISTS 不会改动已有表，升级后在这里按需加列
_PATCH_COLUMNS = {"companies": (("website", "TEXT"),)}


def _patch_columns(c: dbmod.Conn) -> None:
    for table, cols in _PATCH_COLUMNS.items():
        if c.dialect == dbmod.SQLITE:
            rows = c.execute(f"PRAGMA table_info({table})").fetchall()
            have = {str(r["name"]) for r in rows}
        else:
            rows = c.execute(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name=?", (table,)).fetchall()
            have = {str(r["name"]) for r in rows}
        for name, typ in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def conn() -> dbmod.Conn:
    c = getattr(_local, "conn", None)
    if c is None:
        c = dbmod.connect(DB_PATH, DB_URL)
        _local.conn = c
    return c


@contextmanager
def tx():
    c = conn()
    c.begin()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


_ready: tuple[str, str] | None = None


def init(force: bool = False) -> None:
    """建表（幂等）。同一目标只执行一次 DDL，避免每次读写都往返一轮。

    `force=True` 供命令行显式重建；换库或换 test 临时路径时缓存键不匹配，
    会自动重新建表。旧库缺失的新列在这里补齐。
    """
    global _ready
    key = (DB_URL, str(DB_PATH))
    if _ready == key and not force:
        return
    with tx() as c:
        for stmt in ddl_statements():
            c.execute(stmt)
        _patch_columns(c)
    _ready = key


# ---------------- entities ----------------

def upsert_entity(key: str, **fields) -> None:
    ts = now()
    with tx() as c:
        row = c.execute("SELECT id FROM entities WHERE key=?", (key,)).fetchone()
        if row:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE entities SET {sets}, updated_at=? WHERE key=?",
                      (*fields.values(), ts, key))
        else:
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            c.execute(f"INSERT INTO entities (key,{cols},created_at,updated_at) VALUES (?,{marks},?,?)",
                      (key, *fields.values(), ts, ts))


def get_entity(key: str):
    row = conn().execute("SELECT * FROM entities WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


# ---------------- runs ----------------

def create_run(run_id: str, entity_key: str, company: str, params: dict) -> None:
    """创建一次运行；同编号重跑时复用原记录（保留首跑时间，供断点续跑）。"""
    payload = json.dumps(params, ensure_ascii=False)
    with tx() as c:
        row = c.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
        if row:
            c.execute("UPDATE runs SET entity_key=?, company=?, status='running', params=?, "
                      "finished_at=NULL, error=NULL WHERE id=?",
                      (entity_key, company, payload, run_id))
            return
        c.execute("INSERT INTO runs (id,entity_key,company,status,started_at,params) VALUES (?,?,?,?,?,?)",
                  (run_id, entity_key, company, "running", now(), payload))


def mark_running(run_id: str) -> None:
    """把已有运行重新置为 running（续跑时调用，不重建记录）。"""
    with tx() as c:
        c.execute("UPDATE runs SET status='running', finished_at=NULL, error=NULL WHERE id=?", (run_id,))


def reset_run(run_id: str, keep_events: bool = False) -> None:
    """清空一次运行的过程产物。

    keep_events=True 用于「没有检查点、只能从头跑」的续跑：失败时的日志仍然保留，
    新事件接着原 seq 往后追加，验收时能看出中间失败过。
    """
    tables = ["documents", "facts", "snapshots", "checkpoints"]
    if not keep_events:
        tables.append("run_events")
    with tx() as c:
        for t in tables:
            c.execute(f"DELETE FROM {t} WHERE run_id=?", (run_id,))


def finish_run(run_id: str, status: str, stats: dict | None = None, error: str | None = None) -> None:
    with tx() as c:
        c.execute("UPDATE runs SET status=?, finished_at=?, stats=?, error=? WHERE id=?",
                  (status, now(), json.dumps(stats or {}, ensure_ascii=False), error, run_id))


def get_run(run_id: str):
    row = conn().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def _run_where(q: str = "", status: str = "") -> tuple[str, list]:
    clauses, args = [], []
    if q:
        like = f"%{q}%"
        op = dbmod.like_op(dialect())        # PostgreSQL 用 ILIKE，搜索不分大小写
        clauses.append(f"(company {op} ? OR id {op} ? OR entity_key {op} ?)")
        args += [like, like, like]
    if status:
        clauses.append("status = ?")
        args.append(status)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def list_runs(limit: int = 30, offset: int = 0, q: str = "", status: str = ""):
    where, args = _run_where(q, status)
    rows = conn().execute(
        f"SELECT * FROM runs{where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
        (*args, max(1, int(limit)), max(0, int(offset))),
    ).fetchall()
    return [dict(r) for r in rows]


def count_runs(q: str = "", status: str = "") -> int:
    where, args = _run_where(q, status)
    row = conn().execute(f"SELECT COUNT(*) AS n FROM runs{where}", args).fetchone()
    return int(row["n"] if row else 0)


def reconcile_interrupted_runs() -> int:
    """服务启动时把上一个进程留下的 queued/running 标成失败。

    作业队列只在内存里，进程重启后线程已经不在；如果不落一条失败状态，
    前端会对着一个永远不会前进的 running 一直轮询。
    """
    with tx() as c:
        cur = c.execute(
            "UPDATE runs SET status='failed', finished_at=?, error=? "
            "WHERE status IN ('queued','running')",
            (now(), "服务重启，运行被中断；可从断点续跑"),
        )
        return cur.rowcount or 0


def table_counts() -> dict:
    """各表行数，供运行治理页展示；单表损坏不影响其它表。"""
    out = {}
    for name in ("entities", "runs", "documents", "facts", "snapshots", "audit",
                 "run_events", "checkpoints", "schedules", "subjects", "memory",
                 "companies", "settings"):
        try:
            row = conn().execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
            out[name] = int(row["n"] if row else 0)
        except Exception:  # noqa: BLE001
            out[name] = 0
    return out


# ---------------- companies ----------------

_COMPANY_COLS = ("key", "name", "credit_code", "legal_person", "reg_capital",
                 "reg_status", "establish_date", "location", "website", "industry",
                 "business", "source", "raw", "fetched_at")


def upsert_company(c: dict) -> None:
    """按归一化名称落一条。工商接口按次计费，查过一次就不必再查。"""
    with tx() as cx:
        cx.execute(
            dbmod.upsert_sql("companies", _COMPANY_COLS, ("key",), cx.dialect),
            tuple(str(c.get(k) or "") for k in _COMPANY_COLS[:-1]) + (now(),))


def get_company(key: str):
    row = conn().execute("SELECT * FROM companies WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


def search_companies(q: str, limit: int = 8):
    """本地工商缓存：名称或信用代码包含即命中，完全同名排最前、短名优先。"""
    like = f"%{q}%"
    op = dbmod.like_op(dialect())
    rows = conn().execute(
        f"SELECT * FROM companies WHERE name {op} ? OR credit_code {op} ? "
        "ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, length(name) LIMIT ?",
        (like, like, q, max(1, int(limit)))).fetchall()
    return [dict(r) for r in rows]


def company_stats() -> dict:
    """缓存规模与最近抓取时间，供接入中心展示。"""
    row = conn().execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(fetched_at),'') AS last FROM companies").fetchone()
    return {"total": int(row["n"] if row else 0), "last_at": str(row["last"] if row else "")}


# ---------------- settings ----------------


def get_setting(key: str):
    """读一条配置；不存在或 JSON 坏掉时返回 None。"""
    row = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"] or "null")
    except Exception:
        return None


def put_setting(key: str, value) -> None:
    """整条覆盖写。配置量很小，单行 JSON 比逐字段建表更好演进。"""
    with tx() as c:
        c.execute(dbmod.upsert_sql("settings", ("key", "value", "updated_at"), ("key",), c.dialect),
                  (key, json.dumps(value, ensure_ascii=False), now()))


# ---------------- documents ----------------

_DOC_COLS = ("id", "run_id", "entity_key", "kind", "title", "url", "source",
             "published_at", "body", "evidence_level", "fetched_at", "extra")


def add_document(doc: dict) -> None:
    with tx() as c:
        c.execute(
            dbmod.upsert_sql("documents", _DOC_COLS, ("id",), c.dialect),
            (doc["id"], doc["run_id"], doc["entity_key"], doc["kind"], doc.get("title", ""),
             doc.get("url", ""), doc.get("source", ""), doc.get("published_at", ""),
             doc.get("body", ""), doc.get("evidence_level", "title"), doc.get("fetched_at", now()),
             json.dumps(doc.get("extra") or {}, ensure_ascii=False)))


def docs_of_run(run_id: str):
    rows = conn().execute("SELECT * FROM documents WHERE run_id=? ORDER BY published_at DESC", (run_id,)).fetchall()
    return [dict(r) for r in rows]


def clear_documents(run_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM documents WHERE run_id=?", (run_id,))


# ---------------- facts ----------------

def facts_of_run(run_id: str):
    rows = conn().execute("SELECT * FROM facts WHERE run_id=? ORDER BY confidence DESC", (run_id,)).fetchall()
    return [dict(r) for r in rows]


_FACT_COLS = ("id", "run_id", "entity_key", "doc_id", "type", "type_label", "statement",
              "evidence_quote", "amount", "event_date", "confidence", "dedupe_key",
              "corroboration", "created_at")


def save_facts(run_id: str, entity_key: str, facts: list[dict]) -> None:
    """整批替换某次运行的事实。

    断点续跑会重跑第 4 步，整批替换比逐条累加更安全，不会出现重复事实。
    """
    with tx() as c:
        c.execute("DELETE FROM facts WHERE run_id=?", (run_id,))
        for f in facts:
            fid = str(f.get("id") or "")
            if not fid.startswith(run_id + "-"):
                fid = f"{run_id}-{fid}"
            c.execute(
                dbmod.upsert_sql("facts", _FACT_COLS, ("id",), c.dialect),
                (fid, run_id, entity_key, f.get("doc_id", ""), f.get("type", ""),
                 f.get("type_label", ""), f.get("statement", ""), f.get("evidence_quote", ""),
                 f.get("amount", ""), f.get("event_date", ""), f.get("confidence", 0),
                 f.get("dedupe_key", ""), f.get("corroboration", 1), now()))


def facts_with_doc(limit: int = 200, entity_key: str = ""):
    """事实连同来源文档标题与链接，供人工复核和离线评测集沉淀。

    只做联表读取，不判断事实对错；标准答案必须由人读原文后标注。
    """
    sql = ("SELECT f.id AS fact_id, f.run_id, f.entity_key, f.type, f.type_label, "
           "f.statement, f.evidence_quote, f.confidence, f.created_at, "
           "d.id AS doc_id, d.title AS doc_title, d.url AS url, d.kind AS doc_kind, "
           "d.evidence_level AS evidence_level, d.body AS doc_body "
           "FROM facts f JOIN documents d ON d.id = f.doc_id")
    args: list = []
    if entity_key:
        sql += " WHERE f.entity_key=?"
        args.append(entity_key)
    sql += " ORDER BY f.created_at DESC, f.id LIMIT ?"
    args.append(max(1, int(limit)))
    rows = conn().execute(sql, tuple(args)).fetchall()
    return [dict(r) for r in rows]


def docs_with_fact_state(limit: int = 200, entity_key: str = "",
                         only_blind: bool = True):
    """文档连同事实数与检索词，供召回评测标注。

    only_blind=True 只返回没有产出事实的文档——这是抽取与检索的盲区；
    False 时连已产出事实的文档一起返回，用于检查误报。
    """
    sql = ("SELECT d.id AS doc_id, d.run_id, d.entity_key, d.kind, d.title, d.url, "
           "d.source, d.published_at, d.body, d.evidence_level, d.extra, "
           "(SELECT COUNT(*) FROM facts f WHERE f.doc_id = d.id) AS fact_count, "
           "(SELECT f.type FROM facts f WHERE f.doc_id = d.id ORDER BY f.id LIMIT 1) AS fact_type "
           "FROM documents d")
    where: list[str] = []
    args: list = []
    if entity_key:
        where.append("d.entity_key=?")
        args.append(entity_key)
    if only_blind:
        where.append("NOT EXISTS (SELECT 1 FROM facts f WHERE f.doc_id = d.id)")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    rows = conn().execute(sql, tuple(args)).fetchall()
    return [dict(r) for r in rows]


def clear_facts(run_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM facts WHERE run_id=?", (run_id,))


# ---------------- snapshots ----------------

def last_snapshot(entity_key: str, exclude_run: str | None = None):
    sql = "SELECT * FROM snapshots WHERE entity_key=?"
    args = [entity_key]
    if exclude_run:
        sql += " AND run_id<>?"
        args.append(exclude_run)
    row = conn().execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
    if not row:
        return None
    d = dict(row)
    d["facts"] = json.loads(d["facts"] or "[]")
    return d


def save_snapshot(entity_key: str, run_id: str, facts: list[dict]) -> None:
    slim = [{k: f.get(k) for k in ("id", "type", "statement", "dedupe_key", "confidence", "event_date")}
            for f in facts]
    with tx() as c:
        # 续跑会重跑第 7 步；同一运行只保留一份快照，避免基线重复。
        c.execute("DELETE FROM snapshots WHERE run_id=?", (run_id,))
        c.execute("INSERT INTO snapshots (entity_key,run_id,run_at,facts) VALUES (?,?,?,?)",
                  (entity_key, run_id, now(), json.dumps(slim, ensure_ascii=False)))


# ---------------- subjects ----------------

def save_subject(key: str, name: str, data: dict) -> None:
    ts = now()
    blob = json.dumps(data, ensure_ascii=False)
    with tx() as c:
        row = c.execute("SELECT key FROM subjects WHERE key=?", (key,)).fetchone()
        if row:
            c.execute("UPDATE subjects SET name=?, data=?, updated_at=? WHERE key=?",
                      (name, blob, ts, key))
        else:
            c.execute("INSERT INTO subjects (key,name,data,created_at,updated_at) VALUES (?,?,?,?,?)",
                      (key, name, blob, ts, ts))


def get_subject(key: str):
    row = conn().execute("SELECT * FROM subjects WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["data"] = json.loads(d.get("data") or "{}")
    return d


def list_subjects(limit: int = 50):
    rows = conn().execute("SELECT * FROM subjects ORDER BY updated_at DESC LIMIT ?",
                          (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.get("data") or "{}")
        out.append(d)
    return out


# ---------------- audit ----------------

def add_audit(entity_key: str, run_id: str, action: str, target: str,
              old_value: str = "", new_value: str = "", reason: str = "", actor: str = "业务用户") -> None:
    with tx() as c:
        c.execute("INSERT INTO audit (entity_key,run_id,ts,actor,action,target,old_value,new_value,reason) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (entity_key, run_id, now(), actor, action, target, old_value, new_value, reason))


def list_audit(entity_key: str | None = None, limit: int = 100):
    if entity_key:
        rows = conn().execute("SELECT * FROM audit WHERE entity_key=? ORDER BY id DESC LIMIT ?",
                              (entity_key, limit)).fetchall()
    else:
        rows = conn().execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------- run events ----------------


_EVENT_COLS = ("run_id", "seq", "type", "payload", "created_at")


def append_event(run_id: str, seq: int, event: dict) -> None:
    """执行事件落库。seq 由服务端分配，保证同一运行内可增量拉取。"""
    with tx() as c:
        c.execute(dbmod.upsert_sql("run_events", _EVENT_COLS, ("run_id", "seq"), c.dialect),
                  (run_id, int(seq), event.get("type", ""),
                   json.dumps(event, ensure_ascii=False), now()))


def events_of_run(run_id: str, since: int = 0, limit: int = 2000):
    rows = conn().execute(
        "SELECT seq,payload FROM run_events WHERE run_id=? AND seq>=? ORDER BY seq LIMIT ?",
        (run_id, int(since), int(limit))).fetchall()
    out = []
    for r in rows:
        try:
            e = json.loads(r["payload"] or "{}")
        except Exception:
            e = {}
        e["seq"] = r["seq"]
        out.append(e)
    return out


def max_event_seq(run_id: str) -> int:
    row = conn().execute("SELECT COALESCE(MAX(seq),0) AS n FROM run_events WHERE run_id=?",
                         (run_id,)).fetchone()
    return int(row["n"] or 0)


# ---------------- checkpoints ----------------


_CKPT_COLS = ("run_id", "step", "payload", "created_at")


def save_checkpoint(run_id: str, step: int, payload: dict) -> None:
    with tx() as c:
        c.execute(dbmod.upsert_sql("checkpoints", _CKPT_COLS, ("run_id", "step"), c.dialect),
                  (run_id, int(step), json.dumps(payload, ensure_ascii=False), now()))


def last_checkpoint(run_id: str):
    row = conn().execute("SELECT step,payload FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT 1",
                         (run_id,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return {"step": int(row["step"]), "payload": payload}


# ---------------- schedules ----------------


def _schedule_row(row) -> dict:
    d = dict(row)
    for k in ("params", "spec"):
        try:
            d[k] = json.loads(d.get(k) or "{}")
        except Exception:
            d[k] = {}
    d["enabled"] = bool(d.get("enabled"))
    return d


def list_schedules():
    rows = conn().execute("SELECT * FROM schedules ORDER BY created_at DESC").fetchall()
    return [_schedule_row(r) for r in rows]


def get_schedule(schedule_id: str):
    row = conn().execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    return _schedule_row(row) if row else None


def upsert_schedule(s: dict):
    ts = now()
    params = json.dumps(s.get("params") or {}, ensure_ascii=False)
    spec = json.dumps(s.get("spec") or {}, ensure_ascii=False)
    with tx() as c:
        row = c.execute("SELECT id FROM schedules WHERE id=?", (s["id"],)).fetchone()
        if row:
            c.execute("UPDATE schedules SET name=?, kind=?, params=?, spec=?, enabled=?, "
                      "next_run_at=?, updated_at=? WHERE id=?",
                      (s.get("name", ""), s.get("kind", "run"), params, spec,
                       1 if s.get("enabled", True) else 0, s.get("next_run_at", ""), ts, s["id"]))
        else:
            c.execute("INSERT INTO schedules (id,name,kind,params,spec,enabled,next_run_at,created_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (s["id"], s.get("name", ""), s.get("kind", "run"), params, spec,
                       1 if s.get("enabled", True) else 0, s.get("next_run_at", ""), ts, ts))
    return get_schedule(s["id"])


def delete_schedule(schedule_id: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def set_schedule_enabled(schedule_id: str, enabled: bool, next_run_at: str = "") -> None:
    with tx() as c:
        c.execute("UPDATE schedules SET enabled=?, next_run_at=?, updated_at=? WHERE id=?",
                  (1 if enabled else 0, next_run_at, now(), schedule_id))


def set_schedule_next(schedule_id: str, next_run_at: str) -> None:
    """先推进下一次时间再执行，避免某次执行异常导致同一时刻反复触发。"""
    with tx() as c:
        c.execute("UPDATE schedules SET next_run_at=?, updated_at=? WHERE id=?",
                  (next_run_at, now(), schedule_id))


def mark_schedule_run(schedule_id: str, run_id: str) -> None:
    ts = now()
    with tx() as c:
        c.execute("UPDATE schedules SET last_run_at=?, last_run_id=?, updated_at=? WHERE id=?",
                  (ts, run_id, ts, schedule_id))


def due_schedules(now_text: str, limit: int = 10):
    rows = conn().execute(
        "SELECT * FROM schedules WHERE enabled=1 AND next_run_at<>'' AND next_run_at<=? "
        "ORDER BY next_run_at LIMIT ?", (now_text, int(limit))).fetchall()
    return [_schedule_row(r) for r in rows]
