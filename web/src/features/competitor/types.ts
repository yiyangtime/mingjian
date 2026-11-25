/** 竞品追踪页的本地契约：后端响应与页面行结构；跨域共享类型放 shared/types.ts。 */

export type Stage = 1 | 2 | 3
export type Tab = 'report' | 'diff' | 'src'

/** 步骤条的一行：同名步骤可能重复出现，key 由 idx+name 合成 */
export interface StepRow {
  key: string
  name: string
  status: string
  detail: string
}

export interface LogLine {
  ts: string
  msg: string
  level: string
}

export interface Monitor {
  name: string
  code?: string
  market?: string
  credit_code?: string
  type_label?: string
  industry?: string
  business?: string
  full_name?: string
  website?: string
  /** 旧存档没有官网时补拉过一次，避免每次进页都重复请求 */
  site_tried?: boolean
  enriched?: boolean
}

export interface ProfileResp {
  name: string
  full_name: string
  code: string
  market: string
  type_label: string
  listed: boolean
  industry: string
  main_business: string
  region: string
  credit_code?: string
  reg_status?: string
  reg_capital?: string
  establish_date?: string
  website?: string
  source: string
}

export interface SearchItem {
  name: string
  code?: string
  market?: string
  column?: string
  delisted?: boolean
  /** listed=证券主体；registry=工商登记；local=本地档案；news=资讯线索；manual=按输入名称建档 */
  source?: 'listed' | 'registry' | 'local' | 'news' | 'manual'
  hint?: string
  credit_code?: string
  reg_status?: string
  reg_capital?: string
  establish_date?: string
  website?: string
}

export interface Candidate {
  name: string
  listed?: boolean
  code?: string
  count?: number
  evidence?: { quote?: string; url?: string; date?: string; source?: string }
}

export interface DiscoverResp {
  candidates?: Candidate[]
  queries?: string[]
  scanned?: number
  fetched?: number
  notes?: string[]
}

export interface Coverage {
  filled: number
  total: number
  blank?: [string, string][]
}

export interface SubjectResp {
  found?: boolean
  subject?: Record<string, string>
  coverage?: Coverage
  error?: string
}