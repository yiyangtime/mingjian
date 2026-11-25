/** 与后端接口一一对应的最小类型集。字段可缺省的地方就留可选，避免前端为后端的容错逻辑买单。 */

export interface RunStats {
  docs?: number
  full_text?: number
  facts?: number
  high_conf?: number
  elapsed_sec?: number
  companies?: number
  reused?: number
  conflicts?: number
  judgments_rule?: number
  judgments_llm?: number
  judgments_rejected?: number
  [k: string]: number | undefined
}

export interface Fact {
  id: string
  doc_id?: string
  type: string
  type_label: string
  statement: string
  evidence_quote?: string
  amount?: string
  event_date?: string
  confidence: number
  corroboration?: number
  conflict?: number
  conflict_note?: string
  source?: string
  url?: string
  published_at?: string
  evidence_level?: string
  // 竞品追踪报告里的事实带公司前缀编号与轻量字段别名
  ref?: string
  kind?: string
  text?: string
  date?: string
  src?: string
}

export interface Doc {
  id?: string
  kind?: string
  source?: string
  url?: string
  published_at?: string
  evidence_level?: string
  title?: string
}

export interface Changes {
  has_baseline: boolean
  baseline_count: number
  baseline_run?: string
  new: unknown[]
  escalated: unknown[]
  faded: unknown[]
}

export interface Entity {
  code?: string
  short_name?: string
  full_name?: string
  industry?: string
  market?: string
  [k: string]: unknown
}

export interface ComparePack {
  company: string
  run_id?: string
  reused?: boolean
  error?: string
  facts: Fact[]
}

export interface MatrixRow {
  label: string
  cells: Record<string, { n: number }>
}

export interface Judgment {
  id: string
  text: string
  basis: string
  conf: string
}

export interface JudgmentItem {
  text: string
  citations: string[]
  conf?: number
  source?: string
  support?: number
  index?: number
}

export interface JudgmentReject {
  text: string
  reason: string
}

/** 判断改写留痕：规则版、采用版与被拒原因，用于事后对比模型到底改了什么。 */
export interface JudgmentTrail {
  run_id: string
  model: string
  requested?: number
  accepted?: number
  rule: JudgmentItem[]
  final: JudgmentItem[]
  rejected: JudgmentReject[]
  raw?: string
}

export interface RunResult {
  run_id: string
  company: string
  entity?: Entity
  entity_key?: string
  docs: Doc[]
  facts: Fact[]
  changes: Changes
  report: string
  stats: RunStats
  errors?: string[]
  restored?: boolean
  // 竞品追踪报告专有
  companies?: ComparePack[]
  matrix?: MatrixRow[]
  judgments?: Judgment[]
  self?: { company: string } | null
}

export type RunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface StepState {
  idx: number
  name: string
  status: string
  detail: string
}

export interface RunEvent {
  seq?: number
  type: string
  msg?: string
  level?: string
  ts?: string
  idx?: number
  name?: string
  status?: string
  detail?: string
  stats?: RunStats
}

export interface RunPoll {
  run_id: string
  status: RunStatus
  company: string
  kind: 'run' | 'compare'
  steps: StepState[]
  events: RunEvent[]
  next: number
  result: RunResult | null
  error: string | null
  cancelable: boolean
  resumable: boolean
}

export interface RunSummary {
  id: string
  company: string
  entity_key?: string
  status: RunStatus
  started_at?: string
  finished_at?: string
  stats: RunStats
  kind?: string
}

export interface ScheduleSpec {
  freq: 'daily' | 'weekly'
  time: string
  weekday?: number
}

export interface Schedule {
  id: string
  name: string
  kind: string
  params: Record<string, unknown>
  spec: ScheduleSpec
  enabled: boolean
  next_run_at?: string
  last_run_id?: string
  last_run_at?: string
  describe?: string
}

export interface Health {
  ok: boolean
  llm: boolean
  model: string
}