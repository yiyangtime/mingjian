/** 模型配置与系统状态页的本地契约：仅本功能域使用，跨域共享的放 shared/types.ts。 */

export interface Provider {
  id: string
  name: string
  group: string
  base: string
  models: string[]
  note?: string
  keyless?: boolean
}

export interface Profile {
  id: string
  name: string
  base: string
  model: string
  timeout: number
  key_set: boolean
  key_hint: string
  active: boolean
}

export interface LlmConfig {
  providers: Provider[]
  profiles: Profile[]
  active_id: string
  base: string
  model: string
  timeout: number
  key_set: boolean
  key_hint: string
  active: boolean
}

export interface TestResp {
  ok: boolean
  ms: number
  reply?: string
  error?: string
}

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

/** 工商数据源：预设只负责填好请求形态，全部字段均可覆盖。 */
export interface RegistryPreset {
  id: string
  name: string
  vendor: string
  base: string
  path: string
  auth: string
  auth_name: string
  auth_prefix: string
  fields: Record<string, string>
  items_path: string
  code_path: string
  msg_path: string
  ok_codes: string[]
  empty_codes: string[]
  map: Record<string, string>
  note?: string
}

export interface RegistryProfile {
  id: string
  name: string
  preset: string
  base: string
  path: string
  auth: string
  auth_name: string
  auth_prefix: string
  fields: Record<string, string>
  items_path: string
  code_path: string
  msg_path: string
  map: Record<string, string>
  ok_codes: string[]
  empty_codes: string[]
  timeout: number
  key_set: boolean
  key_hint: string
  secret_set: boolean
  active: boolean
}

export interface RegistryConfig {
  presets: RegistryPreset[]
  profiles: RegistryProfile[]
  active_id: string
  enabled: boolean
  fields: Record<string, string>
  cache: { total: number; last_at: string }
}

export interface RegistryTestResp {
  ok: boolean
  status: string
  ms: number
  count: number
  fields_hit: string[]
  sample: Record<string, string>
  error: string
}