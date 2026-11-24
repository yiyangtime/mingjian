/** 竞品周报页的本地契约：后端响应与页面行结构；跨域共享类型放 shared/types.ts。 */

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
  type_label?: string
  industry?: string
  business?: string
  full_name?: string
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
  source: string
}

export interface SearchItem {
  name: string
  code?: string
  market?: string
  column?: string
  delisted?: boolean
  /** listed=证券主体；local=本地档案；news=资讯线索（多为非上市） */
  source?: 'listed' | 'local' | 'news'
  hint?: string
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