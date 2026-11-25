-- PostgreSQL 12+ 初始化脚本（由 mingjian/infra/schema.py 生成，请勿手工修改）
-- 前置：先创建数据库，本脚本只建表与索引。
-- 用法：psql "$DATABASE_URL" -f sql/postgres.sql

CREATE TABLE IF NOT EXISTS entities (
  id BIGSERIAL PRIMARY KEY,
  key TEXT UNIQUE,
  name TEXT,
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
  status TEXT,
  started_at TEXT,
  finished_at TEXT,
  params TEXT,
  stats TEXT,
  judgments TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  entity_key TEXT,
  kind TEXT,
  title TEXT,
  url TEXT,
  source TEXT,
  published_at TEXT,
  body TEXT,
  evidence_level TEXT,
  fetched_at TEXT,
  extra TEXT
);

CREATE INDEX IF NOT EXISTS idx_doc_run ON documents (run_id);

CREATE INDEX IF NOT EXISTS idx_doc_entity ON documents (entity_key);

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
  confidence DOUBLE PRECISION,
  dedupe_key TEXT,
  corroboration INTEGER DEFAULT 1,
  conflict INTEGER DEFAULT 0,
  conflict_note TEXT,
  created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_fact_run ON facts (run_id);

CREATE INDEX IF NOT EXISTS idx_fact_entity ON facts (entity_key);

CREATE TABLE IF NOT EXISTS snapshots (
  id BIGSERIAL PRIMARY KEY,
  entity_key TEXT,
  run_id TEXT,
  run_at TEXT,
  facts TEXT
);

CREATE INDEX IF NOT EXISTS idx_snap_entity ON snapshots (entity_key);

CREATE TABLE IF NOT EXISTS subjects (
  key TEXT PRIMARY KEY,
  name TEXT,
  data TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  id BIGSERIAL PRIMARY KEY,
  entity_key TEXT,
  run_id TEXT,
  ts TEXT,
  actor TEXT,
  action TEXT,
  target TEXT,
  old_value TEXT,
  new_value TEXT,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT,
  payload TEXT,
  created_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_event_run_seq ON run_events (run_id, seq);

CREATE TABLE IF NOT EXISTS checkpoints (
  run_id TEXT NOT NULL,
  step INTEGER NOT NULL,
  payload TEXT,
  created_at TEXT,
  PRIMARY KEY (run_id, step)
);

CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY,
  name TEXT,
  kind TEXT,
  params TEXT,
  spec TEXT,
  enabled INTEGER DEFAULT 1,
  last_run_at TEXT,
  last_run_id TEXT,
  next_run_at TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sched_next ON schedules (enabled, next_run_at);

CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'global',
  key TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'note',
  content TEXT NOT NULL,
  refs TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT '',
  confidence DOUBLE PRECISION,
  hits INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory (scope);

CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory (kind);

CREATE TABLE IF NOT EXISTS companies (
  key TEXT PRIMARY KEY,
  name TEXT,
  credit_code TEXT,
  legal_person TEXT,
  reg_capital TEXT,
  reg_status TEXT,
  establish_date TEXT,
  location TEXT,
  website TEXT,
  industry TEXT,
  business TEXT,
  source TEXT,
  raw TEXT,
  fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_company_name ON companies (name);

CREATE INDEX IF NOT EXISTS idx_company_code ON companies (credit_code);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
