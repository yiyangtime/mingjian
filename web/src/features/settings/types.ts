/** 模型配置的本地契约：仅本功能域使用，跨域共享的放 shared/types.ts。 */

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

/** 报告推送：渠道地址与密钥都按凭证处理，后端只回是否已设置与尾号。 */
export interface PushType {
  id: string
  name: string
  group: string
  fields: string[]
  note?: string
}

export interface PushChannel {
  id: string
  type: string
  name: string
  url_set: boolean
  url_hint: string
  secret_set: boolean
  secret_hint: string
  chat_id: string
  enabled: boolean
}

export interface PushConfig {
  types: PushType[]
  channels: PushChannel[]
  auto: boolean
  base_url: string
  enabled: boolean
  recorded: boolean
}

export interface PushTestResp {
  ok: boolean
  ms: number
  error: string
  reply?: string
}