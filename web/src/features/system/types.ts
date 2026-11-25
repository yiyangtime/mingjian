/** 运行治理页的本地契约：服务状态与审计日志，仅本功能域使用。 */

export interface SystemInfo {
  version: string
  server: string
  python: string
  platform: string
  uptime_sec: number
  now: string
  paths: { root: string; db: string; output: string; logs: string }
  db_backend: 'sqlite' | 'postgres' | 'mysql'
  db_bytes: number
  output: { reports: number; tables: number; files: number }
  counts: Record<string, number>
  llm: { active: boolean; model: string; base: string }
  scheduler: { running: boolean }
  limits: Record<string, number>
}

export interface AuditRow {
  id?: number
  ts?: string
  actor?: string
  action?: string
  target?: string
  entity_key?: string
  run_id?: string
  old_value?: string
  new_value?: string
  reason?: string
}