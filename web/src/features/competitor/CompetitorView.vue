<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from '../../shared/components/AppHeader.vue'
import AppModal from '../../shared/components/AppModal.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import { useRunPolling } from '../../shared/composables/runPolling'
import { download, stamp, copyText } from '../../shared/lib/format'
import { renderMd } from '../../shared/lib/markdown'
import { readJson, readRaw, writeJson, writeRaw } from '../../shared/lib/storage'
import type { RunEvent, RunPoll, RunResult } from '../../shared/types'
import type {
  Coverage, DiscoverResp, LogLine, Monitor, ProfileResp,
  SearchItem, Stage, StepRow, SubjectResp, Tab,
} from './types'
import { FALLBACK_MAX_MONITORS, monitorType, typeFromCode } from './monitor'
import MonitorTableCard from './components/MonitorTableCard.vue'
import CompareMatrix from './components/CompareMatrix.vue'
import RunTimeline from './components/RunTimeline.vue'

// 我方基线的 11 个字段与 /api/subject 一一对应，备注独占两列
const SELF_FIELDS = [
  { k: 'name', label: '企业名称', ph: '如 苏州某某精密传动有限公司' },
  { k: 'region', label: '所在地', ph: '苏州' },
  { k: 'industry', label: '行业', ph: '工业机器人零部件' },
  { k: 'products', label: '主营产品', ph: '谐波减速器,精密行星减速机' },
  { k: 'process', label: '关键工艺', ph: '柔轮齿形加工' },
  { k: 'capacity', label: '产能与产线', ph: '年产 12 万台' },
  { k: 'price_band', label: '价格带', ph: '800—2600 元/台' },
  { k: 'customers', label: '主要客户行业', ph: '机器人本体厂' },
  { k: 'channels', label: '销售渠道', ph: '直销+区域代理' },
  { k: 'scale', label: '营收规模档', ph: '3—5 亿元' },
] as const

const route = useRoute()

// 首次进入不预置示例名单：名单要么来自本地存档，要么由用户显式添加
const monitors = ref<Monitor[]>([])
const maxMonitors = ref(FALLBACK_MAX_MONITORS)
// 兜底值；没存过偏好时以服务端 /api/system 的 limits.days 为准
const days = ref(30)
const stage = ref<Stage>(1)
const polling = useRunPolling(900)
const { runId } = polling
const runSub = ref('')
const running = ref(false)
const cancelable = ref(false)
const resumable = ref(false)
const cancelBusy = ref(false)

const stepRows = ref<StepRow[]>([])
const logs = ref<LogLine[]>([])

const result = ref<RunResult | null>(null)
const tab = ref<Tab>('report')

/* ---------- 线索发现 ---------- */
const dscOpen = ref(false)
const dscTerms = ref('')
const dscRegion = ref('')
const dscBusy = ref(false)
const dsc = ref<DiscoverResp | null>(null)

/* ---------- 我方基线 ---------- */
const selfOpen = ref(false)
const selfLoaded = ref(false)
const selfBusy = ref(false)
const selfKey = ref('')
const selfNote = ref('')
const selfForm = reactive<Record<string, string>>(
  Object.fromEntries(SELF_FIELDS.map((f) => [f.k, ''])) as Record<string, string>,
)
const selfNoteInput = ref('')

/* ---------- 通用 ---------- */
function addLog(msg: string, level = 'i', ts?: string): void {
  logs.value.push({ ts: ts || stamp(), msg, level })
}

const companies = computed(() => monitors.value.map((m) => m.name))

/* ---------- 监测对象：列表 + 弹窗搜索 ---------- */
function loadMonitors(): void {
  const arr = readJson<unknown>('mj_monitors_v2', null)
  if (Array.isArray(arr)) {
    const list = arr
      .filter((x) => x && typeof x.name === 'string' && x.name.trim())
      .slice(0, maxMonitors.value)
      .map((x) => ({
        name: String(x.name).trim(),
        code: x.code ? String(x.code) : undefined,
        market: x.market ? String(x.market) : undefined,
        type_label: x.type_label ? String(x.type_label) : undefined,
        industry: x.industry ? String(x.industry) : undefined,
        business: x.business ? String(x.business) : undefined,
        full_name: x.full_name ? String(x.full_name) : undefined,
        enriched: !!x.enriched,
      }))
    if (list.length) {
      monitors.value = list
      return
    }
  }
  // 兼容旧版逗号分隔名单
  const legacy = readRaw('mj_compare_list')
  if (legacy) {
    const list = legacy.split(/[，,、\s]+/).map((s) => s.trim()).filter(Boolean)
    if (list.length) monitors.value = list.slice(0, maxMonitors.value).map((name) => ({ name }))
  }
}

function saveMonitors(): void {
  writeJson('mj_monitors_v2', monitors.value)
  writeRaw('mj_compare_list', companies.value.join(','))
}

function addMonitor(m: Monitor): boolean {
  const name = (m.name || '').trim()
  if (!name) return false
  if (monitors.value.some((x) => x.name === name)) {
    toast(`${name} 已在监测对象里`)
    return false
  }
  if (monitors.value.length >= maxMonitors.value) {
    toast(`最多 ${maxMonitors.value} 家，先移除一家`)
    return false
  }
  monitors.value.push({
    name, code: m.code, market: m.market, type_label: m.type_label,
    industry: m.industry, business: m.business, full_name: m.full_name,
    enriched: m.enriched,
  })
  saveMonitors()
  return true
}

function removeMonitor(name: string): void {
  monitors.value = monitors.value.filter((m) => m.name !== name)
  saveMonitors()
}

function addPeer(name: string, code?: string, market?: string): void {
  if (addMonitor({ name, code, market })) {
    toast(`已加入：${name}`)
    void enrichMonitors()
  }
}

/* ---------- 公司档案：列表需要公司类型与所处产业 ---------- */
const enriching = ref(false)

async function fetchProfile(m: Monitor): Promise<Monitor> {
  const d = await api.get<ProfileResp>('/api/entity/profile', {
    name: m.name,
    code: m.code || undefined,
    market: m.market || undefined,
  })
  return {
    ...m,
    code: d.code || m.code,
    market: d.market || m.market,
    type_label: d.type_label || m.type_label,
    industry: d.industry || m.industry,
    business: d.main_business || m.business,
    full_name: d.full_name || m.full_name,
    enriched: true,
  }
}

// 网络失败也记一次「已尝试」，列表显示未标注；用户可在编辑弹窗里手动重试
async function enrichMonitor(m: Monitor): Promise<Monitor> {
  try {
    return await fetchProfile(m)
  } catch {
    return { ...m, enriched: true }
  }
}

async function enrichMonitors(): Promise<void> {
  const targets = monitors.value
    .map((m, i) => ({ i, name: m.name, m }))
    .filter((x) => !x.m.enriched)
  if (!targets.length) return
  enriching.value = true
  try {
    const done = await Promise.all(targets.map(async (t) => ({ ...t, next: await enrichMonitor(t.m) })))
    for (const d of done) {
      const cur = monitors.value[d.i]
      if (!cur || cur.name !== d.name || cur.enriched) continue
      monitors.value[d.i] = { ...d.next, name: cur.name }
    }
    saveMonitors()
  } finally {
    enriching.value = false
  }
}

/* ---------- 监测对象：编辑档案 ---------- */
const monEditOpen = ref(false)
const monEditBusy = ref(false)
const monEditKey = ref('')
const monForm = reactive({
  name: '', type_label: '非上市企业', code: '', market: '',
  industry: '', business: '', full_name: '',
})

watch(() => monForm.code, (v) => {
  const c = v.trim()
  if (c && (!monForm.type_label || monForm.type_label === '非上市企业')) {
    monForm.type_label = typeFromCode(c)
  }
})

function openMonitorEdit(m: Monitor): void {
  monEditKey.value = m.name
  monForm.name = m.name
  monForm.type_label = monitorType(m)
  monForm.code = m.code || ''
  monForm.market = m.market || ''
  monForm.industry = m.industry || ''
  monForm.business = m.business || ''
  monForm.full_name = m.full_name || ''
  monEditOpen.value = true
}

async function refillMonitor(): Promise<void> {
  monEditBusy.value = true
  try {
    const d = await fetchProfile({
      name: monForm.name,
      code: monForm.code.trim() || undefined,
      market: monForm.market.trim() || undefined,
    })
    monForm.code = monForm.code.trim() || d.code || ''
    monForm.market = monForm.market.trim() || d.market || ''
    if (d.code && d.type_label && monForm.type_label === '非上市企业') monForm.type_label = d.type_label
    monForm.industry = monForm.industry.trim() || d.industry || ''
    monForm.business = monForm.business.trim() || d.business || ''
    monForm.full_name = monForm.full_name.trim() || d.full_name || ''
    toast(monForm.industry ? `已补全所处产业：${monForm.industry}` : '公开档案没有返回产业，请手动填写')
  } catch (e) {
    toast('补全失败：' + (e as Error).message)
  } finally {
    monEditBusy.value = false
  }
}

function saveMonitorEdit(): void {
  const i = monitors.value.findIndex((m) => m.name === monEditKey.value)
  if (i < 0) {
    monEditOpen.value = false
    return
  }
  const code = monForm.code.trim()
  monitors.value[i] = {
    ...monitors.value[i],
    code: code || undefined,
    market: monForm.market.trim() || undefined,
    type_label: monForm.type_label || typeFromCode(code),
    industry: monForm.industry.trim() || undefined,
    business: monForm.business.trim() || undefined,
    full_name: monForm.full_name.trim() || undefined,
    enriched: true,
  }
  saveMonitors()
  monEditOpen.value = false
  toast(`已更新：${monForm.name}`)
}

const pkOpen = ref(false)
const pkQ = ref('')
const pkBusy = ref(false)
const pkErr = ref('')
const pkItems = ref<SearchItem[]>([])
const pkPicked = ref<Monitor[]>([])
const pkSuggest = ref<string[]>([])
const pkInput = ref<HTMLInputElement | null>(null)
const pkSaving = ref(false)
let pkTimer: number | undefined
let pkSeq = 0

// 输入词已经在候选里时不再重复给「按输入建档」入口
const pkExact = computed(() => {
  const kw = pkQ.value.trim()
  return !!kw && pkItems.value.some((it) => it.name === kw)
})

// 已监测 + 本次勾选（去重）后的总数，用于提示 6 家上限
const pkTotal = computed(() => monitors.value.length + pkPicked.value
  .filter((p) => !monitors.value.some((m) => m.name === p.name)).length)

function hasMonitor(name: string): boolean {
  return monitors.value.some((m) => m.name === name)
}

function isPicked(name: string): boolean {
  return pkPicked.value.some((m) => m.name === name)
}

function openPicker(): void {
  if (pkTimer) window.clearTimeout(pkTimer)
  pkSeq += 1
  pkOpen.value = true
  pkQ.value = ''
  pkItems.value = []
  pkPicked.value = []
  pkErr.value = ''
  pkBusy.value = false
  loadSuggest()
  nextTick(() => pkInput.value?.focus())
}

async function loadSuggest(): Promise<void> {
  try {
    const d = await api.get<{ runs: { company?: string; kind?: string }[] }>('/api/runs', { limit: 30 })
    const seen = new Set<string>()
    const out: string[] = []
    for (const r of d.runs || []) {
      if (r.kind !== 'compare' || !r.company) continue
      for (const part of r.company.split('·')) {
        const name = part.trim()
        if (!name || seen.has(name) || hasMonitor(name)) continue
        seen.add(name)
        out.push(name)
      }
    }
    pkSuggest.value = out.slice(0, 8)
  } catch {
    pkSuggest.value = []
  }
}

async function searchEntity(kw: string): Promise<void> {
  const seq = ++pkSeq
  pkBusy.value = true
  pkErr.value = ''
  try {
    const d = await api.get<{ items: SearchItem[] }>('/api/entity/search', { q: kw, limit: 8 })
    if (seq !== pkSeq) return
    pkItems.value = d.items || []
  } catch (e) {
    if (seq !== pkSeq) return
    pkItems.value = []
    pkErr.value = (e as Error).message
  } finally {
    if (seq === pkSeq) pkBusy.value = false
  }
}

watch(pkQ, (v) => {
  if (pkTimer) window.clearTimeout(pkTimer)
  const kw = v.trim()
  if (!kw) {
    pkSeq += 1
    pkItems.value = []
    pkErr.value = ''
    pkBusy.value = false
    return
  }
  pkTimer = window.setTimeout(() => searchEntity(kw), 280)
})

function togglePick(it: Monitor | SearchItem): void {
  const name = (it.name || '').trim()
  if (!name) return
  if (isPicked(name)) {
    unpick(name)
    return
  }
  if (hasMonitor(name)) {
    toast(`${name} 已在监测对象里`)
    return
  }
  if (pkTotal.value >= maxMonitors.value) {
    toast(`最多 ${maxMonitors.value} 家，先移除一家`)
    return
  }
  pkPicked.value.push({ name, code: it.code || undefined, market: it.market || undefined })
}

function unpick(name: string): void {
  pkPicked.value = pkPicked.value.filter((m) => m.name !== name)
}

function pickManual(): void {
  const kw = pkQ.value.trim()
  if (!kw) return
  togglePick({ name: kw })
  pkQ.value = ''
}

async function confirmPicks(): Promise<void> {
  if (!pkPicked.value.length || pkSaving.value) return
  pkSaving.value = true
  try {
    // 加入前补全档案：列表要能直接看到公司类型与所处产业
    const list = await Promise.all(pkPicked.value.map(async (p) => (
      p.enriched ? p : await enrichMonitor(p)
    )))
    let added = 0
    for (const p of list) {
      if (hasMonitor(p.name)) continue
      if (monitors.value.length >= maxMonitors.value) break
      monitors.value.push(p)
      added += 1
    }
    saveMonitors()
    pkOpen.value = false
    if (added) toast(`已加入 ${added} 家，确认名单后点「生成周报」`)
  } finally {
    pkSaving.value = false
  }
}

/* ---------- 线索发现 ---------- */
async function runDiscover(): Promise<void> {
  const terms = dscTerms.value.trim()
  if (!terms) {
    toast('先填品类词')
    return
  }
  dscBusy.value = true
  dsc.value = null
  try {
    dsc.value = await api.post<DiscoverResp>('/api/discover', {
      products: terms,
      region: dscRegion.value.trim(),
      exclude: [selfForm.name.trim(), readRaw('mj_self_name')],
    })
    if (!(dsc.value.candidates || []).length) toast('没有抽出候选企业')
  } catch (e) {
    toast('检索失败：' + (e as Error).message)
  } finally {
    dscBusy.value = false
  }
}

/* ---------- 我方基线 ---------- */
function showSelfCov(cov?: Coverage): void {
  if (!cov) return
  const blank = (cov.blank || []).map((x) => x[1]).slice(0, 3).join('、')
  selfNote.value = `已存为自述基线：覆盖 ${cov.filled}/${cov.total} 个对比维度`
    + (blank ? `；未填 ${blank}` : '')
    + '。未填的维度在矩阵里显示「—」，不猜。'
}

async function loadSelf(): Promise<void> {
  const name = readRaw('mj_self_name')
  if (!name) return
  try {
    const d = await api.get<SubjectResp>('/api/subject', { key: name })
    if (d.found && d.subject) {
      for (const f of SELF_FIELDS) {
        if (d.subject[f.k]) selfForm[f.k] = d.subject[f.k]
      }
      selfNoteInput.value = d.subject.note || ''
      selfKey.value = d.subject.key || name
      showSelfCov(d.coverage)
    }
  } catch {
    /* 读不到就当作还没建档，不弹错 */
  }
}

watch(selfOpen, (open) => {
  if (open && !selfLoaded.value) {
    selfLoaded.value = true
    if (!selfForm.name) loadSelf()
  }
})

async function saveSelf(): Promise<void> {
  const body: Record<string, string> = {}
  for (const f of SELF_FIELDS) body[f.k] = (selfForm[f.k] || '').trim()
  body.note = selfNoteInput.value.trim()
  if (!body.name) {
    toast('先填企业名称')
    return
  }
  selfBusy.value = true
  try {
    const d = await api.post<SubjectResp>('/api/subject', body)
    selfKey.value = d.subject?.key || body.name
    writeRaw('mj_self_name', body.name)
    showSelfCov(d.coverage)
    toast('已保存：这次生成周报会带上「我方」列')
  } catch (e) {
    toast('保存失败：' + (e as Error).message)
  } finally {
    selfBusy.value = false
  }
}

/* ---------- 运行 ---------- */
function applyEvent(e: RunEvent): void {
  if (e.type === 'step') {
    // 同名步骤可能出现多次（如逐家「采集 X」），用 idx+name 做行键避免串行覆盖
    const key = `${e.idx ?? 0}:${e.name || ''}`
    const cur = stepRows.value.find((r) => r.key === key)
    if (cur) {
      cur.status = e.status || ''
      cur.detail = e.detail || ''
    } else {
      stepRows.value.push({ key, name: e.name || '步骤', status: e.status || '', detail: e.detail || '' })
    }
  } else if (e.type === 'log' && e.msg) {
    addLog(e.msg, e.level || 'i', e.ts)
  }
}

function startPoll(id: string, since = 0): void {
  cancelable.value = true
  resumable.value = false
  polling.start(id, {
    onEvent: applyEvent,
    onDone: (d) => {
      cancelable.value = false
      running.value = false
      if (d.result) {
        result.value = d.result
        stage.value = 3
        toast('周报已生成')
        return
      }
      // 重启后竞品周报的矩阵不在内存里：退到报告文件，不无限轮询
      toast('结果明细不在内存中，已打开报告文件')
      openLink(id)
    },
    onFailed: (d) => {
      cancelable.value = false
      running.value = false
      resumable.value = d.resumable
      addLog('失败：' + (d.error || ''), 'error')
      toast('运行失败：' + (d.error || ''))
    },
    onCancelled: (d) => {
      cancelable.value = false
      running.value = false
      resumable.value = d.resumable
      toast(d.resumable ? '已取消，可从断点续跑' : '已取消；周报由多家子运行组成，请重新发起')
    },
    onError: (err) => {
      cancelable.value = false
      running.value = false
      addLog('轮询失败：' + err.message, 'error')
    },
  }, since)
}

async function start(): Promise<void> {
  const names = companies.value
  if (names.length < 2) {
    toast('至少两家公司：点「＋ 新增」搜索添加')
    return
  }
  if (names.length > maxMonitors.value) {
    toast(`一次最多 ${maxMonitors.value} 家`)
    return
  }
  saveMonitors()
  writeRaw('mj_compare_days', String(days.value))
  result.value = null
  stepRows.value = []
  logs.value = []
  runId.value = ''
  running.value = true
  cancelable.value = false
  resumable.value = false
  stage.value = 2
  runSub.value = names.join(' · ')
  addLog(`任务已提交：${names.length} 家，回溯 ${days.value} 天`)
  try {
    const r = await api.post<{ run_id: string }>('/api/compare', {
      companies: names,
      days: Number(days.value) || undefined,
      self_key: selfKey.value || readRaw('mj_self_name'),
    })
    startPoll(r.run_id)
  } catch (e) {
    running.value = false
    addLog('提交失败：' + (e as Error).message, 'error')
    toast('提交失败：' + (e as Error).message)
  }
}

async function cancel(): Promise<void> {
  if (!runId.value) return
  cancelBusy.value = true
  try {
    await api.post(`/api/run/${runId.value}/cancel`)
    toast('已发出取消信号，当前步骤结束后停止')
  } catch (e) {
    toast('取消失败：' + (e as Error).message)
  } finally {
    cancelBusy.value = false
  }
}

async function resume(): Promise<void> {
  if (!runId.value) return
  resumable.value = false
  running.value = true
  try {
    const d = await api.post<{ from_step?: number }>(`/api/run/${runId.value}/resume`)
    toast(d.from_step ? `从第 ${d.from_step} 步续跑` : '没有检查点，从头重跑')
    startPoll(runId.value)
  } catch (e) {
    toast('续跑失败：' + (e as Error).message)
    resumable.value = true
    running.value = false
  }
}

/* ---------- 结果与深链接 ---------- */
const packs = computed(() => result.value?.companies || [])
const matrixRows = computed(() => result.value?.matrix || [])
const judgments = computed(() => result.value?.judgments || [])
const selfCompany = computed(() => result.value?.self?.company || '')
const reportHtml = computed(() => renderMd(result.value?.report || ''))

const statCards = computed(() => {
  const s = result.value?.stats || {}
  const cached = !!s.companies && s.reused === s.companies
  return [
    { k: '监测公司', v: s.companies || 0, u: '家' },
    { k: '采集材料', v: s.docs || 0, u: '份' },
    { k: '有效事实', v: s.facts || 0, u: '条' },
    { k: '高置信', v: s.high_conf || 0, u: '条' },
    { k: '用时', v: cached ? '缓存' : (s.elapsed_sec || 0), u: cached ? '' : '秒' },
  ]
})

async function openLink(id: string): Promise<void> {
  try {
    const d = await api.get<RunPoll>(`/api/run/${id}`)
    runId.value = id
    if (d.result) {
      result.value = d.result
      stage.value = 3
      return
    }
    if (d.status === 'done') {
      try {
        const text = await api.text(`/api/report/${id}.md`)
        result.value = {
          run_id: id, company: '', docs: [], facts: [],
          changes: { has_baseline: false, baseline_count: 0, new: [], escalated: [], faded: [] },
          report: text, stats: {},
        }
        tab.value = 'report'
        stage.value = 3
      } catch {
        toast('报告已不在本地')
      }
      return
    }
    // 还在跑或中断过：回放事件，恢复执行现场
    stepRows.value = []
    logs.value = []
    if (d.events) d.events.forEach(applyEvent)
    stage.value = 2
    runSub.value = d.company || ''
    if (d.status === 'queued' || d.status === 'running') {
      running.value = true
      startPoll(id, d.next || 0)
      return
    }
    running.value = false
    resumable.value = d.resumable
    toast((d.status === 'cancelled' ? '该运行已取消' : '该运行失败')
      + (d.resumable ? '，可从断点续跑' : '；周报请重新发起'))
  } catch (e) {
    toast('加载失败：' + (e as Error).message)
  }
}

/* ---------- 下载 ---------- */
function downloadMd(): void {
  if (!result.value) return
  download(result.value.report || '', `竞品周报_${runId.value || 'run'}.md`, 'text/markdown;charset=utf-8')
}

/* 导出 Word：用 Word 能打开的 HTML 封装，避免引入 docx 依赖 */
function downloadWord(): void {
  if (!result.value) return
  const body = renderMd(result.value.report || '')
  const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office"
    xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
    <head><meta charset="utf-8"><title>竞品动态周报</title>
    <style>
      body { font-family: "Microsoft YaHei", "SimSun", serif; font-size: 11pt; line-height: 1.8; }
      h1 { font-size: 18pt; } h2 { font-size: 14pt; margin-top: 18pt; }
      h3 { font-size: 12pt; } table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #999; padding: 5px 8px; font-size: 9.5pt; }
      th { background: #F2F2F2; } blockquote { color: #555; border-left: 3px solid #ccc; padding-left: 10px; }
    </style></head><body>${body}</body></html>`
  download('\ufeff' + html, `竞品周报_${runId.value || 'run'}.doc`, 'application/msword;charset=utf-8')
  toast('已导出 Word，可直接在 Word 中打开继续编辑')
}

/* 导出事实表：逐条事实 + 来源链接，供业务方核对 */
function downloadFacts(): void {
  if (!result.value) return
  const rows: string[][] = [['公司', '事实编号', '类型', '事实表述', '日期', '来源', '链接']]
  packs.value.forEach((p) => {
    (p.facts || []).forEach((f) => {
      rows.push([
        p.company, f.ref || f.id, f.kind || f.type || '', f.statement || f.text || '',
        f.date || f.published_at || '', f.src || f.source || '', f.url || '',
      ])
    })
  })
  if (rows.length === 1) {
    toast('本期没有可导出的事实')
    return
  }
  const csv = rows.map((r) => r.map((v) => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`).join(',')).join('\r\n')
  download('\ufeff' + csv, `竞品事实表_${runId.value || 'run'}.csv`, 'text/csv;charset=utf-8')
  toast(`已导出 ${rows.length - 1} 条事实`)
}

function resetAll(): void {
  result.value = null
  stage.value = 1
}

async function copyLink(): Promise<void> {
  if (!runId.value) return
  const url = `${location.origin}${location.pathname}?run=${encodeURIComponent(runId.value)}`
  const ok = await copyText(url)
  toast(ok ? '链接已复制，同事打开即可复看本次周报' : '复制失败，请手动复制地址栏')
}

/** 没存过偏好时跟随服务端配置，避免前后端各写一套默认值。 */
async function syncServerDefaults(): Promise<void> {
  try {
    const d = await api.get<{ limits?: { days?: number; max_companies?: number } }>('/api/system')
    const n = Number(d.limits?.days)
    if (!readRaw('mj_compare_days') && n > 0) days.value = n
    const m = Number(d.limits?.max_companies)
    if (m > 0) maxMonitors.value = m
  } catch {
    /* 读不到就用本地兜底值，不打断配置 */
  }
}

onMounted(async () => {
  await syncServerDefaults()
  loadMonitors()
  void enrichMonitors()
  const saved = Number(readRaw('mj_compare_days'))
  if (saved) days.value = saved
  const rid = route.query.run
  if (rid) setTimeout(() => openLink(String(rid)), 200)
})

onBeforeUnmount(() => {
  if (pkTimer) window.clearTimeout(pkTimer)
})
</script>

<template>
  <AppHeader :task="runId || undefined" />

  <div class="wrap">
    <!-- 1 配置 -->
    <div v-if="stage === 1">
      <section class="card">
        <div class="sec-h">
          <h3>竞品周报 · 任务配置</h3>
        </div>
        <div class="pad">
          <div class="mon-top">
            <div class="fl mon-label">监测对象 {{ monitors.length }}/{{ maxMonitors }}</div>
            <div class="mon-tools">
              <div>
                <div class="fl">回溯天数</div>
                <input v-model.number="days" type="number" min="3" max="365" class="fi-days">
              </div>
              <button class="btn btn-primary" :disabled="running" @click="start">生成周报</button>
            </div>
          </div>
          <MonitorTableCard
            :monitors="monitors" :enriching="enriching" :max="maxMonitors"
            @edit="openMonitorEdit" @remove="removeMonitor" @add="openPicker"
          />
        </div>
      </section>

      <!-- 竞品线索发现 -->
      <section class="card section-gap">
        <div class="sec-h">
          <h3>竞品线索发现</h3>
          <button class="btn btn-ghost btn-sm" style="margin-left: auto" @click="dscOpen = !dscOpen">{{ dscOpen ? '收起' : '展开' }}</button>
        </div>
        <div v-show="dscOpen" class="pad">
          <div class="row">
            <div class="grow-sm">
              <div class="fl">品类词</div>
              <input v-model="dscTerms" type="text" class="fi" placeholder="如 谐波减速器、工业机器人伺服">
            </div>
            <div>
              <div class="fl">所在地</div>
              <input v-model="dscRegion" type="text" class="fi region" placeholder="苏州">
            </div>
            <button class="btn btn-primary btn-sm" :disabled="dscBusy" @click="runDiscover">
              <span v-if="dscBusy" class="spin"></span>找线索
            </button>
          </div>
          <div v-if="dsc" class="dsc-out">
            <div class="meta">
              检索词 {{ (dsc.queries || []).join(' / ') }}　|　扫 {{ dsc.scanned || 0 }} 篇　|　读正文 {{ dsc.fetched || 0 }} 篇
            </div>
            <div v-for="c in dsc.candidates || []" :key="c.name" class="cand">
              <div class="cand-h">
                <b>{{ c.name }}</b>
                <span v-if="c.listed" class="listed">上市 {{ c.code }}</span>
                <span v-else class="unlisted">未上市</span>
                <span class="mute">曝光 {{ c.count }} 篇</span>
                <button class="btn btn-ghost btn-xs" @click="addPeer(c.name, c.listed ? c.code : undefined)">加入监测</button>
              </div>
              <div v-if="c.evidence?.quote" class="quote-line">「{{ c.evidence.quote }}」</div>
              <a v-if="c.evidence?.url" :href="c.evidence.url" target="_blank" rel="noopener" class="ev-link">
                {{ c.evidence.date }} {{ c.evidence.source }} · 原文
              </a>
            </div>
            <div v-for="(n, i) in dsc.notes || []" :key="i" class="note-line">· {{ n }}</div>
            <div v-if="!(dsc.candidates || []).length && !(dsc.notes || []).length" class="note-line">没有抽出候选企业。</div>
          </div>
        </div>
      </section>

      <!-- 我方基线 -->
      <section class="card section-gap">
        <div class="sec-h">
          <h3>我方基线</h3>
          <button class="btn btn-ghost btn-sm" style="margin-left: auto" @click="selfOpen = !selfOpen">{{ selfOpen ? '收起' : '展开' }}</button>
        </div>
        <div v-show="selfOpen" class="pad">
          <div class="selfgrid">
            <div v-for="f in SELF_FIELDS" :key="f.k">
              <div class="fl">{{ f.label }}</div>
              <input v-model="selfForm[f.k]" type="text" class="fi" :placeholder="f.ph">
            </div>
            <div class="span2">
              <div class="fl">补充说明</div>
              <input v-model="selfNoteInput" type="text" class="fi" placeholder="可选">
            </div>
          </div>
          <div class="row" style="margin-top: 14px">
            <button class="btn btn-primary btn-sm" :disabled="selfBusy" @click="saveSelf">保存基线</button>
            <span class="hintline selfnote">{{ selfNote }}</span>
          </div>
        </div>
      </section>

      <AppModal
        :open="pkOpen" title="新增监测对象"
        width="620px" @close="pkOpen = false"
      >
        <div class="pk-sea">
          <input
            ref="pkInput" v-model="pkQ" class="fi" type="search"
            placeholder="公司简称、全称或股票代码" autocomplete="off"
          >
          <span v-if="pkBusy" class="pk-busy">检索中…</span>
        </div>
        <div v-if="pkErr" class="pk-err">搜索失败：{{ pkErr }}。可直接手动添加。</div>

        <div v-if="pkPicked.length" class="pk-sel">
          <span class="pk-sellabel">待加入</span>
          <span v-for="p in pkPicked" :key="p.name" class="pk-chip">
            {{ p.name }}<small v-if="p.code">{{ p.code }}</small>
            <button type="button" :aria-label="'移出 ' + p.name" @click="unpick(p.name)">×</button>
          </span>
        </div>

        <div class="pk-list">
          <div
            v-for="it in pkItems" :key="it.code || it.name" class="pk-row"
            :class="{ on: isPicked(it.name), added: hasMonitor(it.name) }"
            @click="togglePick(it)"
          >
            <div class="pk-main">
              <b>{{ it.name }}</b>
              <small v-if="it.code">{{ it.code }}</small>
              <span v-if="it.market" class="pk-mk">{{ it.market }}</span>
              <span v-if="it.delisted" class="pk-off">已退市</span>
              <span v-else-if="it.source === 'news'" class="pk-mk" :title="it.hint || ''">资讯线索</span>
              <span v-else-if="it.source === 'local'" class="pk-mk">本地档案</span>
            </div>
            <span class="pk-act">{{ isPicked(it.name) ? '已选' : hasMonitor(it.name) ? '已加入' : '加入' }}</span>
          </div>
          <div
            v-if="!pkBusy && pkQ.trim() && !pkExact && !pkErr
              && !isPicked(pkQ.trim()) && !hasMonitor(pkQ.trim())"
            class="pk-tip"
          >
            <span v-if="!pkItems.length">未匹配到公开档案</span>
            <button type="button" class="btn btn-ghost btn-xs" @click="pickManual">添加「{{ pkQ.trim() }}」</button>
          </div>
        </div>

        <div v-if="pkSuggest.length" class="pk-sug">
          <div class="pk-sh">最近周报跑过的公司</div>
          <button
            v-for="s in pkSuggest" :key="s" type="button" class="pk-sugbtn"
            @click="togglePick({ name: s })"
          >＋ {{ s }}</button>
        </div>

        <template #footer>
          <button class="btn btn-primary" :disabled="!pkPicked.length || pkSaving" @click="confirmPicks">
            {{ pkSaving ? '正在补全公司档案…' : `加入所选（${pkPicked.length}）` }}
          </button>
          <button class="btn btn-ghost" :disabled="pkSaving" @click="pkOpen = false">取消</button>
          <span class="pk-foot">加入后 {{ pkTotal }}/{{ maxMonitors }} 家</span>
        </template>
      </AppModal>

      <AppModal
        :open="monEditOpen" title="编辑监测对象"
        width="580px" @close="monEditOpen = false"
      >
        <div class="mform2">
          <div class="mf">
            <label>公司名</label>
            <input class="fi" type="text" :value="monForm.name" readonly>
          </div>
          <div class="mf">
            <label>公司类型</label>
            <select v-model="monForm.type_label">
              <option value="非上市企业">非上市企业</option>
              <option value="上市公司 · A股">上市公司 · A股</option>
              <option value="上市公司 · 港股">上市公司 · 港股</option>
              <option value="上市公司 · 北交所">上市公司 · 北交所</option>
            </select>
          </div>
          <div class="mf">
            <label>股票代码</label>
            <input v-model.trim="monForm.code" class="fi" type="text" placeholder="6 位代码，非上市可留空">
          </div>
          <div class="mf">
            <label>所处产业</label>
            <input v-model.trim="monForm.industry" class="fi" type="text" placeholder="如 电力设备-电池">
          </div>
          <div class="mf wide">
            <label>主营 / 备注</label>
            <input v-model.trim="monForm.business" class="fi" type="text" placeholder="可选">
          </div>
          <div class="mf wide">
            <label>工商全称</label>
            <input v-model.trim="monForm.full_name" class="fi" type="text" placeholder="可选">
          </div>
        </div>
        <template #footer>
          <button class="btn btn-primary" @click="saveMonitorEdit">保存</button>
          <button class="btn" :disabled="monEditBusy" @click="refillMonitor">
            {{ monEditBusy ? '查询中…' : '从公开档案补全' }}
          </button>
          <button class="btn btn-ghost" @click="monEditOpen = false">取消</button>
        </template>
      </AppModal>
    </div>

    <!-- 2 执行 -->
    <div v-else-if="stage === 2">
      <section class="card">
        <div class="sec-h">
          <h3>执行过程</h3>
          <span class="note">{{ runSub }}</span>
          <button v-if="cancelable" class="btn btn-ghost btn-sm" :disabled="cancelBusy" @click="cancel">取消</button>
          <button v-if="resumable" class="btn btn-ghost btn-sm" @click="resume">从断点续跑</button>
        </div>
        <RunTimeline :steps="stepRows" :logs="logs" />
      </section>
    </div>

    <!-- 3 结果 -->
    <div v-else>
      <div class="stats">
        <div v-for="c in statCards" :key="c.k" class="sbox">
          <div class="k">{{ c.k }}</div>
          <div class="v">{{ c.v }}<small>{{ c.u }}</small></div>
        </div>
      </div>

      <section class="card section-gap">
        <div class="tabs">
          <div class="tab" :class="{ on: tab === 'report' }" @click="tab = 'report'">周报正文</div>
          <div class="tab" :class="{ on: tab === 'diff' }" @click="tab = 'diff'">横向对比</div>
          <div class="tab" :class="{ on: tab === 'src' }" @click="tab = 'src'">来源与证据</div>
        </div>

        <div v-show="tab === 'report'" class="report-wrap">
          <div class="md" v-html="reportHtml"></div>
        </div>

        <div v-show="tab === 'diff'" class="report-wrap">
          <CompareMatrix
            :packs="packs" :rows="matrixRows" :judgments="judgments" :self-company="selfCompany"
          />
        </div>

        <div v-show="tab === 'src'" class="report-wrap">
          <div v-for="p in packs" :key="p.company" class="pack">
            <div class="pack-h">
              {{ p.company }}
              <span class="pack-meta">
                {{ p.run_id ? '运行 ' + p.run_id : '' }}{{ p.reused ? ' · 复用 24 小时内结果' : '' }}{{ p.error ? ' · 采集失败：' + p.error : '' }}
              </span>
            </div>
            <table v-if="(p.facts || []).length" class="src-table">
              <thead>
                <tr>
                  <th style="width: 86px">编号</th>
                  <th>事实</th>
                  <th style="width: 170px">来源</th>
                  <th style="width: 190px">证据句</th>
                  <th style="width: 70px; text-align: right">置信度</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="f in p.facts" :key="f.id">
                  <td class="fid">{{ f.ref || f.id }}</td>
                  <td>{{ f.statement }}</td>
                  <td class="s">
                    <a v-if="f.url" :href="f.url" target="_blank" rel="noopener">{{ f.source || '原文' }}</a>
                    <span v-else>{{ f.source || '—' }}</span>
                    <div class="mute">{{ (f.published_at || '').slice(0, 10) }}</div>
                  </td>
                  <td class="q">{{ (f.evidence_quote || '').slice(0, 60) }}</td>
                  <td class="cf">{{ (Number(f.confidence) || 0).toFixed(2) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty" style="padding: 14px">本期无有效事实。</div>
          </div>
          <div v-if="!packs.length" class="empty">本页只保留了报告正文；明细需重新运行。</div>
        </div>
      </section>

      <div class="acts">
        <button class="btn btn-ghost" @click="downloadMd">下载 Markdown</button>
        <button class="btn btn-ghost" @click="downloadWord">导出 Word</button>
        <button class="btn btn-ghost" @click="downloadFacts">导出事实表</button>
        <button class="btn btn-ghost" @click="copyLink">复制结果链接</button>
        <button class="btn btn-ghost" @click="resetAll">重新配置</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wrap { width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 72px; }
.section-gap { margin-top: 18px; }
.pad { padding: 18px 22px 20px; }
.row { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.grow-sm { flex: 1; min-width: 260px; }
.fl { font-size: 12px; color: var(--mute); margin-bottom: 6px; letter-spacing: .02em; }
.fi {
  width: 100%; border: 1px solid #E4E7EC; border-radius: 9px; padding: 10px 12px;
  font-size: 13px; font-family: inherit; outline: none; color: var(--ink); background: #fff;
}
.fi:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(26, 109, 213, .10); }
.fi-days { width: 110px; font-size: 14px; }

/* 监测对象：列表（公司名 / 类型 / 代码 / 产业） */
.mon-top {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 14px; flex-wrap: wrap; margin-bottom: 12px;
}
.mon-label { margin-bottom: 0; align-self: center; }
.mon-tools { display: flex; align-items: flex-end; gap: 10px; }

/* 监测对象编辑弹窗 */
.mform2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 16px; }
.mf { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.mf.wide { grid-column: span 2; }
.mf label { font-size: 12px; color: var(--mute); }
.mf select { width: 100%; }
@media (max-width: 560px) {
  .mform2 { grid-template-columns: 1fr; }
  .mf.wide { grid-column: span 1; }
}

/* 新增监测对象弹窗 */
.pk-sea { display: flex; align-items: center; gap: 10px; }
.pk-sea .fi { flex: 1; }
.pk-busy { font-size: 11.5px; color: var(--mute); white-space: nowrap; }
.pk-err {
  margin-top: 10px; font-size: 11.5px; color: var(--err);
  background: var(--err-soft); border-radius: 7px; padding: 7px 10px; line-height: 1.7;
}
.pk-sel {
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  margin-top: 10px; padding: 8px 10px; border-radius: 8px; background: #F7F9FC;
}
.pk-sellabel { font-size: 11px; color: var(--mute); }
.pk-chip {
  display: inline-flex; align-items: center; gap: 5px; font-size: 12px;
  background: var(--accent-soft); color: var(--accent);
  border-radius: 6px; padding: 3px 4px 3px 8px;
}
.pk-chip small { font-size: 10.5px; color: #5B7FB4; }
.pk-chip button {
  border: none; background: none; color: #5B7FB4; cursor: pointer;
  font-size: 13px; line-height: 1; padding: 0 3px;
}
.pk-chip button:hover { color: var(--err); }
.pk-list { margin-top: 8px; max-height: 300px; overflow: auto; }
.pk-row {
  display: flex; align-items: center; gap: 10px; padding: 9px 10px;
  border-radius: 8px; cursor: pointer; border: 1px solid transparent;
}
.pk-row:hover { background: #F7F9FC; }
.pk-row.on { background: var(--accent-soft); border-color: #C9DBF6; }
.pk-row.added { opacity: .55; cursor: default; }
.pk-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.pk-main b { font-size: 13px; font-weight: 550; }
.pk-main small { font-size: 11px; color: var(--mute); font-variant-numeric: tabular-nums; }
.pk-mk { font-size: 10.5px; color: #1D4ED8; background: #EFF3FF; border-radius: 4px; padding: 1px 6px; }
.pk-off { font-size: 10.5px; color: var(--err); background: var(--err-soft); border-radius: 4px; padding: 1px 6px; }
.pk-act { flex: none; font-size: 11.5px; color: var(--accent); }
.pk-row.added .pk-act { color: var(--mute); }
.pk-tip { font-size: 11.5px; color: var(--mute); line-height: 1.8; padding: 10px 10px 2px; }
.pk-tip .btn { margin-left: 4px; }
.pk-sug { margin-top: 14px; border-top: 1px dashed var(--line); padding-top: 10px; }
.pk-sh { font-size: 11px; color: var(--mute); margin-bottom: 7px; }
.pk-sugbtn {
  border: 1px dashed #D5DBE3; background: #fff; border-radius: 7px;
  padding: 4px 10px; font-size: 11.5px; color: var(--sub); cursor: pointer; margin: 0 6px 6px 0;
}
.pk-sugbtn:hover { border-color: var(--accent); color: var(--accent); }
.pk-foot { margin-left: auto; font-size: 11.5px; color: var(--mute); }
.region { width: 150px; }
.hintline { font-size: 12px; color: var(--mute); }
.selfnote { margin-left: auto; max-width: 560px; text-align: right; }

.sec-h {
  display: flex; align-items: center; gap: 10px; padding: 16px 20px;
  border-bottom: 1px solid var(--line);
}
.sec-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.sec-h .note { font-size: 12px; color: var(--mute); margin-left: auto; }
.sec-h .btn { flex: 0 0 auto; }

.dsc-out { margin-top: 14px; }
.dsc-out .meta { font-size: 11.5px; color: var(--mute); margin-bottom: 8px; }
.cand { padding: 10px 0; border-bottom: 1px solid #F1F3F6; }
.cand-h { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.cand-h b { font-size: 13px; }
.listed { padding: 1px 6px; border-radius: 4px; background: #EFF3FF; color: #1D4ED8; font-size: 10.5px; }
.unlisted { padding: 1px 6px; border-radius: 4px; background: #F3F4F6; color: #6B7280; font-size: 10.5px; }
.cand-h .mute { font-size: 11.5px; color: var(--mute); }
.cand-h .btn { margin-left: auto; }
.quote-line { font-size: 12px; color: #4B5563; margin-top: 6px; line-height: 1.7; }
.ev-link { font-size: 11.5px; color: var(--accent); text-decoration: none; }
.ev-link:hover { text-decoration: underline; }
.note-line { font-size: 11.5px; color: var(--mute); margin-top: 8px; line-height: 1.7; }

.selfgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.selfgrid .span2 { grid-column: span 2; }
@media (max-width: 900px) {
  .selfgrid { grid-template-columns: 1fr; }
  .selfgrid .span2 { grid-column: span 1; }
}

.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-top: 20px; }
@media (max-width: 860px) { .stats { grid-template-columns: repeat(2, 1fr); } }
.sbox { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; box-shadow: var(--shadow-card); }
.sbox .k { font-size: 12px; color: var(--mute); }
.sbox .v { font-size: 26px; font-weight: 680; margin-top: 4px; letter-spacing: -.5px; }
.sbox .v small { font-size: 11.5px; font-weight: 500; color: var(--mute); margin-left: 3px; }

.tabs { display: flex; gap: 4px; padding: 0 20px; border-bottom: 1px solid var(--line); }
.tab {
  padding: 13px 15px; font-size: 13.5px; color: var(--sub); cursor: pointer;
  border-bottom: 2px solid transparent; margin-bottom: -1px; font-weight: 500; user-select: none;
}
.tab.on { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.report-wrap { padding: 26px 30px; }

.src-table { width: 100%; border-collapse: collapse; }

.pack { margin-bottom: 20px; }
.pack-h { font-size: 13.5px; font-weight: 650; margin-bottom: 8px; }
.pack-meta { font-weight: 400; color: var(--mute); font-size: 11.5px; margin-left: 8px; }
.src-table th, .src-table td { padding: 8px 10px; text-align: left; font-size: 12.5px; vertical-align: top; }
.src-table th {
  background: #FAFBFC; border-bottom: 1px solid var(--line); color: var(--mute);
  font-size: 11.5px; font-weight: 500; white-space: nowrap;
}
.src-table td { border-bottom: 1px solid #F1F3F6; }
.src-table .fid { color: var(--mute); font-size: 11.5px; white-space: nowrap; }
.src-table .s { font-size: 11.5px; color: #5F6672; }
.src-table .s a { color: #1D4ED8; text-decoration: none; }
.src-table .s a:hover { text-decoration: underline; }
.src-table .s .mute { color: #9AA1AA; }
.src-table .q { font-size: 11.5px; color: var(--mute); line-height: 1.7; }
.src-table .cf { text-align: right; font-variant-numeric: tabular-nums; }

.acts { display: flex; gap: 10px; align-items: center; margin-top: 18px; flex-wrap: wrap; }

@media (max-width: 640px) {
  .wrap { padding: 20px 16px 48px; }
  .pad { padding: 16px 18px; }
  .mon-tools { width: 100%; justify-content: space-between; }
}
</style>