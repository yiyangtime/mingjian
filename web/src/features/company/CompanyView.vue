<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '../../shared/components/AppHeader.vue'
import SlideDrawer from '../../shared/components/SlideDrawer.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import { confirmAsk } from '../../shared/composables/confirm'
import { actorName } from '../../shared/composables/identity'
import { useRunPolling } from '../../shared/composables/runPolling'
import { copyText, durationText, relTime, stamp, statusCls, statusLabel } from '../../shared/lib/format'
import { renderMd } from '../../shared/lib/markdown'
import { readJson, writeJson } from '../../shared/lib/storage'
import type { Fact, Health, RunEvent, RunPoll, RunResult, RunSummary, Schedule, StepState } from '../../shared/types'

const STEPS = ['实体解析', '多源采集', '正文解析', '事实抽取', '变化比对', '报告成稿', '留痕入库']
type Tab = 'facts' | 'report' | 'quality'

const route = useRoute()
const router = useRouter()

const engineText = ref('检测引擎…')
const stateText = ref('待命')
const company = ref('')
const days = ref(30)
const fetchN = ref(12)

const history = ref<RunSummary[]>([])
const histLoaded = ref(false)
const HIST_PAGE = 10
const histQ = ref('')
const histStatus = ref('')
const histOffset = ref(0)
const histTotal = ref(0)
const histBusy = ref(false)
let histTimer: number | undefined

const lastRun = ref<{ id: string; company: string; ts: number } | null>(loadLastRun())
const lastRunAgo = computed(() => (lastRun.value ? relTime(new Date(lastRun.value.ts).toISOString()) : ''))

const schedules = ref<Schedule[]>([])
const schedLoaded = ref(false)
const schedForm = reactive({ company: '', freq: 'daily', weekday: '1', time: '09:00', days: 30 })

const progressVisible = ref(false)
const running = ref(false)
const steps = ref<StepState[]>([])
const logs = ref<{ ts: string; msg: string; level: string }[]>([])
const logsEl = ref<HTMLElement | null>(null)
const lastId = ref('')
const cancelable = ref(false)
const resumable = ref(false)
const cancelBusy = ref(false)
const failedState = ref(false)
const failMsg = ref('')
const runCompany = ref('')
const elapsed = ref(0)
const logFollow = ref(true)
const harvestBusy = ref(false)
let elapsedTimer: number | undefined

const polling = useRunPolling(700)

const result = ref<RunResult | null>(null)
const reportOnly = ref(false)
const resultEl = ref<HTMLElement | null>(null)
const tab = ref<Tab>('facts')
const q = ref('')
const typeFilter = ref('')
const drawerFact = ref<Fact | null>(null)

/* ---------- 上次运行 ---------- */
function loadLastRun(): { id: string; company: string; ts: number } | null {
  const d = readJson<{ id?: string; company?: string; ts?: number } | null>('mj_last_run', null)
  return d && d.id ? { id: d.id, company: d.company || '', ts: Number(d.ts) || Date.now() } : null
}

function saveLastRun(res: RunResult): void {
  const item = { id: res.run_id, company: res.company || runCompany.value || company.value, ts: Date.now() }
  lastRun.value = item
  writeJson('mj_last_run', item)
}

/* ---------- 引擎与历史 ---------- */
async function loadHealth(): Promise<void> {
  try {
    const d = await api.get<Health>('/api/health')
    engineText.value = d.llm ? `模型已接入 · ${d.model || ''}` : '规则引擎（未配模型密钥）'
  } catch {
    engineText.value = '服务未连接'
  }
}

async function loadHistory(reset = true): Promise<void> {
  if (reset) histOffset.value = 0
  histBusy.value = true
  try {
    const d = await api.get<{ runs: RunSummary[]; total?: number }>('/api/runs', {
      limit: HIST_PAGE,
      offset: histOffset.value,
      q: histQ.value.trim() || undefined,
      status: histStatus.value || undefined,
    })
    const rows = d.runs || []
    history.value = reset ? rows : [...history.value, ...rows]
    histTotal.value = typeof d.total === 'number' ? d.total : history.value.length
  } catch {
    if (reset) history.value = []
    histTotal.value = 0
  } finally {
    histLoaded.value = true
    histBusy.value = false
  }
}

function queueHist(): void {
  if (histTimer) window.clearTimeout(histTimer)
  histTimer = window.setTimeout(() => { loadHistory() }, 300)
}

function loadMore(): void {
  if (histBusy.value) return
  histOffset.value = history.value.length
  loadHistory(false)
}

/* ---------- 运行 ---------- */
function addLog(msg: string, level = 'i', ts?: string): void {
  logs.value.push({ ts: ts || stamp(), msg, level })
}

watch(
  () => logs.value.length,
  () => nextTick(() => {
    // 用户往上翻看时不要强行拽回底部
    if (logsEl.value && logFollow.value) logsEl.value.scrollTop = logsEl.value.scrollHeight
  }),
)

function onLogScroll(): void {
  const el = logsEl.value
  if (!el) return
  logFollow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 36
}

function jumpLatest(): void {
  logFollow.value = true
  nextTick(() => {
    if (logsEl.value) logsEl.value.scrollTop = logsEl.value.scrollHeight
  })
}

function startElapsed(): void {
  stopElapsed()
  elapsed.value = 0
  const t0 = Date.now()
  elapsedTimer = window.setInterval(() => {
    elapsed.value = Math.floor((Date.now() - t0) / 1000)
  }, 500)
}

function stopElapsed(): void {
  if (elapsedTimer) {
    window.clearInterval(elapsedTimer)
    elapsedTimer = undefined
  }
}

const elapsedText = computed(() => durationText(elapsed.value))

function stopPoll(): void {
  polling.stop()
}

function applyEvent(e: RunEvent): void {
  if (e.type === 'log' && e.msg) addLog(e.msg, e.level || 'i', e.ts)
}

function startPoll(id: string, since = 0): void {
  lastId.value = id
  cancelable.value = true
  resumable.value = false
  polling.start(id, {
    onEvent: applyEvent,
    onTick: (d) => {
      if (d.steps && d.steps.length) steps.value = d.steps
    },
    onDone: (d) => {
      if (d.result) {
        finish(d.result)
        return
      }
      // 重启后竞品周报没有内存矩阵：退到报告文件，不无限轮询
      toast('结果明细不在内存中，已打开报告文件')
      openHist(id)
    },
    onFailed: (d) => {
      cancelable.value = false
      resumable.value = d.resumable
      fail(d.error || '运行失败')
    },
    onCancelled: (d) => {
      cancelable.value = false
      resumable.value = d.resumable
      cancelled(d.error || '已取消')
    },
    onError: (err) => {
      cancelable.value = false
      fail(err.message)
    },
  }, since)
}

async function start(): Promise<void> {
  const c = company.value.trim()
  if (!c) {
    toast('请输入公司名称')
    return
  }
  stopPoll()
  result.value = null
  reportOnly.value = false
  lastId.value = ''
  runCompany.value = c
  running.value = true
  failedState.value = false
  failMsg.value = ''
  stateText.value = '运行中'
  progressVisible.value = true
  resumable.value = false
  steps.value = []
  logs.value = []
  logFollow.value = true
  startElapsed()
  addLog('提交任务：' + c)
  try {
    const d = await api.post<{ run_id: string }>('/api/run', {
      company: c,
      days: Number(days.value) || 30,
      max_fetch: Number(fetchN.value) || 12,
    })
    lastId.value = d.run_id
    syncUrl(d.run_id)
    startPoll(d.run_id)
  } catch (e) {
    fail((e as Error).message)
  }
}

function syncUrl(id: string): void {
  const query: Record<string, string> = {}
  for (const [k, v] of Object.entries(route.query)) {
    if (k !== 'tab' && k !== 'fact' && typeof v === 'string') query[k] = v
  }
  query.run = id
  router.replace({ path: route.path, query })
}

function fail(msg: string): void {
  stopElapsed()
  failedState.value = true
  failMsg.value = msg
  running.value = false
  stateText.value = '失败'
  addLog('失败：' + msg, 'error')
  toast('运行失败：' + msg)
}

function cancelled(msg: string): void {
  stopElapsed()
  failedState.value = true
  failMsg.value = msg || '已取消'
  running.value = false
  stateText.value = '已取消'
  toast((msg || '已取消') + '，可从断点续跑')
}

function finish(res: RunResult, scroll = true): void {
  stopElapsed()
  failedState.value = false
  failMsg.value = ''
  result.value = res
  reportOnly.value = false
  running.value = false
  stateText.value = '已完成'
  cancelable.value = false
  resumable.value = false
  if (res.run_id) {
    lastId.value = res.run_id
    runCompany.value = res.company || runCompany.value
    syncUrl(res.run_id)
    saveLastRun(res)
  }
  addLog('结果已生成：' + (res.facts?.length || 0) + ' 条事实', 'ok')
  loadHistory()
  if (scroll) {
    nextTick(() => resultEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }
}

function runAgain(): void {
  if (!company.value.trim() && runCompany.value) company.value = runCompany.value
  start()
}

async function cancel(): Promise<void> {
  if (!lastId.value) return
  cancelBusy.value = true
  try {
    await api.post(`/api/run/${lastId.value}/cancel`)
    stateText.value = '取消中'
    toast('已发出取消信号，当前步骤结束后停止')
  } catch (e) {
    toast('取消失败：' + (e as Error).message)
  } finally {
    cancelBusy.value = false
  }
}

async function resume(): Promise<void> {
  if (!lastId.value) return
  resumable.value = false
  failedState.value = false
  failMsg.value = ''
  stateText.value = '续跑中'
  progressVisible.value = true
  running.value = true
  startElapsed()
  try {
    const d = await api.post<{ from_step?: number }>(`/api/run/${lastId.value}/resume`)
    toast(d.from_step ? `从第 ${d.from_step} 步续跑` : '没有检查点，从头重跑')
    startPoll(lastId.value)
  } catch (e) {
    toast('续跑失败：' + (e as Error).message)
    failedState.value = true
    resumable.value = true
    running.value = false
    stopElapsed()
  }
}

const stepRows = computed(() => STEPS.map((nm, i) => {
  const s = steps.value.find((x) => x.idx === i + 1)
  const st = !s ? 'idle' : s.status === 'start' ? 'run' : 'done'
  const dt = s?.detail || (st === 'run' ? '处理中…' : '')
  return { nm, st, dt, mark: st === 'done' ? '✓' : String(i + 1) }
}))

const stepProgress = computed(() => {
  const done = stepRows.value.filter((s) => s.st === 'done').length
  return { done, total: STEPS.length, pct: Math.round((done / STEPS.length) * 100) }
})

/* ---------- 结果 ---------- */
const facts = computed(() => result.value?.facts || [])
const reportHtml = computed(() => renderMd(result.value?.report || ''))

const statCards = computed(() => {
  const s = result.value?.stats || {}
  return [
    { k: '材料', v: s.docs || 0, u: '份', cls: '' },
    { k: '取得全文', v: s.full_text || 0, u: '份', cls: 'g' },
    { k: '事实', v: s.facts || 0, u: '条', cls: '' },
    { k: '高置信', v: s.high_conf || 0, u: '条', cls: 'g' },
    { k: '用时', v: s.elapsed_sec || 0, u: '秒', cls: '' },
  ]
})

const factTypes = computed(() => {
  const m = new Map<string, string>()
  for (const f of facts.value) {
    if (f.type && !m.has(f.type)) m.set(f.type, f.type_label || f.type)
  }
  return [...m.entries()].map(([k, label]) => ({ k, label }))
})

const filteredFacts = computed(() => {
  const kw = q.value.trim()
  const tf = typeFilter.value
  return facts.value.filter((f) => {
    if (tf && f.type !== tf) return false
    if (!kw) return true
    return `${f.statement}${f.evidence_quote || ''}${f.source || ''}${f.type_label || ''}`.includes(kw)
  })
})

function confCls(v: number): string {
  const c = Number(v) || 0
  return c >= 0.85 ? 'hi' : c >= 0.65 ? 'mid' : 'lo'
}

function confText(v: number): string {
  const c = Number(v) || 0
  return c >= 0.85 ? '高' : c >= 0.65 ? '中高' : c >= 0.55 ? '中' : '中低'
}

function levelText(v?: string): string {
  return v === 'full' ? '全文' : v === 'snippet' ? '摘要' : '标题'
}

const quality = computed(() => {
  const res = result.value
  const docs = res?.docs || []
  const fs = res?.facts || []
  if (!res) return { rows: [], gaps: [] as string[] }
  const full = docs.filter((d) => d.evidence_level === 'full').length
  const ann = docs.filter((d) => d.kind === 'announcement').length
  const news = docs.filter((d) => d.kind === 'news').length
  const ch = res.changes || { has_baseline: false, baseline_count: 0 }
  const rows = [
    { k: '材料总数', v: `${docs.length} 份`, note: `公告 ${ann} 份，资讯 ${news} 份` },
    { k: '取得全文', v: `${full} 份`, note: full ? '已解析正文，证据句可回链原文' : '本次未取得正文，全部为摘要/标题级' },
    { k: '事实总数', v: `${fs.length} 条`, note: '全部绑定来源文档' },
    { k: '可回溯', v: `${fs.filter((f) => f.doc_id).length}/${fs.length}`, note: '无来源的事实不入库' },
    { k: '多源互证', v: `${fs.filter((f) => (f.corroboration || 1) > 1).length} 条`, note: '同一事件被多个来源报道' },
    {
      k: '历史基线',
      v: ch.has_baseline ? `${ch.baseline_count} 条` : '无',
      note: ch.has_baseline ? '已做新增/升温比对' : '首次运行，不做变化判断',
    },
  ]
  const gaps: string[] = []
  if (!ch.has_baseline) gaps.push('无历史基线：本期不判断"新增/升温"，避免把首次采集说成变化。')
  if (!fs.some((f) => f.type === 'performance')) gaps.push('缺少经营业绩类事实：无法判断增长质量，需接入财务数据。')
  if (!fs.some((f) => f.type === 'policy')) gaps.push('未检出适用政策：可能确无相关政策，也可能检索词未覆盖。')
  if (docs.length && full / docs.length < 0.4) {
    gaps.push(`正文取得率偏低：${full}/${docs.length}，多数事实仅有摘要级证据。`)
  }
  const img = docs.filter((d) => d.evidence_level === 'title' && (d.url || '').toLowerCase().endsWith('.pdf')).length
  if (img) gaps.push(`${img} 份公告 PDF 为图片型（无文本层），程序无法读取，已保留链接。`)
  return { rows, gaps }
})

/* ---------- 事实抽屉 ---------- */
const drawerKv = computed(() => {
  const f = drawerFact.value
  if (!f) return []
  return [
    {
      k: '置信度',
      v: `${(Number(f.confidence) || 0).toFixed(2)}（${confText(f.confidence)}）`,
      cls: `conf ${confCls(f.confidence)}`,
    },
    { k: '证据等级', v: f.evidence_level === 'full' ? '全文（已读原文）' : f.evidence_level === 'snippet' ? '摘要（检索结果）' : '仅标题', cls: '' },
    { k: '来源', v: f.source || '—', cls: '' },
    { k: '发布时间', v: f.published_at || '—', cls: '' },
    { k: '事件日期', v: f.event_date || '—', cls: '' },
    { k: '金额/规模', v: f.amount || '—', cls: '' },
    { k: '交叉印证', v: (f.corroboration || 1) > 1 ? `${f.corroboration} 个来源` : '单源', cls: '' },
  ]
})

function openFact(id: string): void {
  const f = facts.value.find((x) => x.id === id)
  if (f) drawerFact.value = f
}

async function audit(action: 'confirm' | 'reject'): Promise<void> {
  const cur = drawerFact.value
  const res = result.value
  if (!cur || !res) return
  try {
    await api.post('/api/audit', {
      entity_key: res.entity_key || res.entity?.code || res.company,
      run_id: res.run_id,
      action,
      target: cur.id,
      old_value: cur.statement,
      new_value: cur.statement,
      reason: action === 'reject' ? '业务复核存疑，待补充原文' : '业务复核确认',
      actor: actorName(),
    })
    toast(action === 'confirm' ? '已确认，记录已留痕' : '已标记存疑，记录已留痕')
    drawerFact.value = null
  } catch (e) {
    toast('留痕失败：' + (e as Error).message)
  }
}

/* ---------- 历史与深链接 ---------- */
async function openHist(id: string): Promise<void> {
  // 切走前先停掉当前轮询与计时，避免两条运行互相覆盖日志
  stopPoll()
  stopElapsed()
  progressVisible.value = false
  failedState.value = false
  failMsg.value = ''
  // finish() 里的 syncUrl 会清掉 tab/fact，深链接参数必须在加载前读走
  const tb = route.query.tab
  const fc = route.query.fact
  try {
    const d = await api.get<RunPoll>(`/api/run/${id}`)
    lastId.value = id
    runCompany.value = d.company || ''
    if (d.result) {
      finish(d.result, true)
      if (tb === 'facts' || tb === 'report' || tb === 'quality') tab.value = tb
      if (fc) nextTick(() => openFact(String(fc)))
      return
    }
    if (d.status === 'done') {
      toast('结果明细不在内存中，已打开报告文件')
      stateText.value = '已完成'
      running.value = false
      cancelable.value = false
      resumable.value = false
      syncUrl(id)
      try {
        const text = await api.text(`/api/report/${id}.md`)
        const res: RunResult = {
          run_id: id, company: d.company || '', docs: [], facts: [],
          changes: { has_baseline: false, baseline_count: 0, new: [], escalated: [], faded: [] },
          report: text, stats: {},
        }
        result.value = res
        reportOnly.value = true
        saveLastRun(res)
        // 报告文件路径没有事实与质量明细，固定落到报告
        tab.value = 'report'
      } catch {
        result.value = null
        toast('报告已不在本地')
      }
      return
    }
    logs.value = []
    steps.value = []
    if (d.events) d.events.forEach(applyEvent)
    if (d.steps && d.steps.length) steps.value = d.steps
    progressVisible.value = true
    if (d.status === 'queued' || d.status === 'running') {
      stateText.value = d.status === 'queued' ? '排队中' : '运行中'
      running.value = true
      failedState.value = false
      startElapsed()
      startPoll(id, d.next || 0)
      return
    }
    // failed / cancelled：恢复执行过程，给出续跑入口
    cancelable.value = false
    resumable.value = d.resumable
    running.value = false
    failedState.value = true
    failMsg.value = d.error || (d.status === 'cancelled' ? '该运行已取消' : '该运行失败')
    stateText.value = d.status === 'cancelled' ? '已取消' : '失败'
    toast(d.status === 'cancelled' ? '该运行已取消，可从断点续跑' : '该运行失败，可从断点续跑')
  } catch (e) {
    toast('加载失败：' + (e as Error).message)
  }
}

/* ---------- 定时任务 ---------- */
async function loadSchedules(): Promise<void> {
  try {
    const d = await api.get<{ schedules: Schedule[] }>('/api/schedules')
    schedules.value = d.schedules || []
  } catch {
    schedules.value = []
  } finally {
    schedLoaded.value = true
  }
}

async function addSchedule(): Promise<void> {
  const c = schedForm.company.trim()
  if (!c) {
    toast('请输入公司名')
    return
  }
  const spec: Record<string, unknown> = { freq: schedForm.freq, time: schedForm.time || '09:00' }
  if (schedForm.freq === 'weekly') spec.weekday = Number(schedForm.weekday) || 1
  try {
    const d = await api.post<{ schedule: Schedule }>('/api/schedules', {
      kind: 'run',
      name: c + ' 情报',
      params: { company: c, days: Number(schedForm.days) || 30 },
      spec,
      enabled: true,
    })
    toast(`已保存：${d.schedule.describe || ''}，下次 ${d.schedule.next_run_at || '—'}`)
    schedForm.company = ''
    loadSchedules()
  } catch (e) {
    toast('保存失败：' + (e as Error).message)
  }
}

async function schedAct(action: 'run' | 'toggle' | 'del', id: string): Promise<void> {
  const url = { run: '/api/schedules/run', toggle: '/api/schedules/toggle', del: '/api/schedules/delete' }[action]
  const body: Record<string, unknown> = { id }
  if (action === 'toggle') {
    const s = schedules.value.find((x) => x.id === id)
    body.enabled = !(s && s.enabled)
  }
  if (action === 'del') {
    const s = schedules.value.find((x) => x.id === id)
    const ok = await confirmAsk({
      title: '删除这个定时任务？',
      message: `${s?.name || id} 删除后不再自动触发；已生成的历史运行不受影响。`,
      confirmText: '删除任务',
      danger: true,
    })
    if (!ok) return
  }
  try {
    const d = await api.post<{ run_id?: string }>(url, body)
    if (action === 'run') {
      result.value = null
      logs.value = []
      steps.value = []
      progressVisible.value = true
      running.value = true
      stateText.value = '运行中'
      addLog('定时任务已触发：' + (d.run_id || ''))
      if (d.run_id) startPoll(d.run_id)
      loadSchedules()
      return
    }
    toast(action === 'del' ? '已删除' : '已更新')
    loadSchedules()
    loadHistory()
  } catch (e) {
    toast('操作失败：' + (e as Error).message)
  }
}

async function download(kind: 'md' | 'csv'): Promise<void> {
  const rid = result.value?.run_id || lastId.value
  if (!rid) return
  const name = result.value?.company || runCompany.value || rid
  try {
    const got = await api.download(
      kind === 'md' ? `/api/report/${rid}.md` : `/api/facts/${rid}.csv`,
      kind === 'md' ? `${name}_情报报告_${rid}.md` : `${name}_事实表_${rid}.csv`,
    )
    toast(`已下载 ${got}`)
  } catch (e) {
    toast('下载失败：' + (e as Error).message)
  }
}

async function copyLink(): Promise<void> {
  const rid = result.value?.run_id || lastId.value
  if (!rid) return
  const url = `${location.origin}${location.pathname}?run=${encodeURIComponent(rid)}&tab=${tab.value}`
  const ok = await copyText(url)
  toast(ok ? '链接已复制，可直接发给同事' : '复制失败，请手动复制地址栏')
}

async function harvestMemory(): Promise<void> {
  const rid = result.value?.run_id || lastId.value
  if (!rid || harvestBusy.value) return
  harvestBusy.value = true
  try {
    const d = await api.post<{ written?: number }>('/api/memory/harvest', { run_id: rid, limit: 12 })
    toast(`已沉淀 ${d.written || 0} 条记忆，下次分析同类公司会自动带上`)
  } catch (e) {
    toast('沉淀失败：' + (e as Error).message)
  } finally {
    harvestBusy.value = false
  }
}

onMounted(() => {
  loadHealth()
  loadHistory()
  loadSchedules()
  const c = route.query.c
  if (c) company.value = String(c)
  const rid = route.query.run
  if (rid) setTimeout(() => openHist(String(rid)), 300)
})
</script>

<template>
  <AppHeader
    :badge="engineText"
    :state="stateText"
  />

  <div class="wrap">
    <section class="card hero">
      <h1>要分析哪家公司？</h1>
      <div class="searchbar">
        <input
          v-model="company" type="text" class="company-input"
          placeholder="输入公司简称或全称" autocomplete="off"
          aria-label="公司名称"
          @keydown.enter="start"
        >
        <button class="btn btn-primary btn-lg" :disabled="running" @click="start">
          <span v-if="running" class="spin"></span>{{ running ? '分析中…' : result ? '重新分析' : '开始分析' }}
        </button>
      </div>
      <div class="opt">
        <label>回溯天数 <input v-model.number="days" type="number" min="3" max="365"></label>
        <label>抓取原文 <input v-model.number="fetchN" type="number" min="0" max="40"> 份</label>
      </div>
    </section>

    <!-- 上次运行 -->
    <div v-if="lastRun" class="lastrun section-gap">
      <span class="lr-lab">上次分析</span>
      <b>{{ lastRun.company || lastRun.id }}</b>
      <span class="lr-time">{{ lastRunAgo }}</span>
      <span class="lr-id">{{ lastRun.id }}</span>
      <button class="btn btn-ghost btn-sm" @click="openHist(lastRun.id)">打开结果</button>
    </div>

    <!-- 最近运行 -->
    <section class="card section-gap">
      <div class="sec-h">
        <h3>最近运行</h3>
        <div class="hist-tools">
          <input
            v-model="histQ" type="text" class="hist-q" placeholder="搜公司 / 运行编号"
            aria-label="搜索运行记录" @input="queueHist"
          >
          <select v-model="histStatus" aria-label="按状态筛选" @change="loadHistory()">
            <option value="">全部状态</option>
            <option value="done">已完成</option>
            <option value="running">运行中</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
          </select>
        </div>
      </div>
      <div class="hist">
        <div v-if="!histLoaded" class="empty">加载中…</div>
        <div v-else-if="!history.length" class="empty">
          {{ histQ || histStatus ? '没有符合条件的运行记录' : '还没有运行记录' }}
        </div>
        <div v-for="r in history" :key="r.id" class="hrow" role="button" tabindex="0"
             @click="openHist(r.id)" @keydown.enter="openHist(r.id)">
          <span class="run-st" :class="statusCls(r.status)">{{ statusLabel(r.status) }}</span>
          <div class="h-main">
            <div class="cn">{{ r.company || '—' }}</div>
            <div class="mt">
              {{ relTime(r.started_at) }} · {{ r.stats?.facts || 0 }} 条事实 · {{ r.id }}
            </div>
          </div>
          <span class="h-go">打开</span>
        </div>
      </div>
      <div class="hist-foot">
        <span>共 {{ histTotal }} 条{{ histQ || histStatus ? '（已筛选）' : '' }}</span>
        <button
          v-if="history.length < histTotal" class="btn btn-ghost btn-sm"
          :disabled="histBusy" @click="loadMore"
        >{{ histBusy ? '加载中…' : '加载更多' }}</button>
      </div>
    </section>

    <!-- 定时任务 -->
    <section class="card section-gap">
      <div class="sec-h">
        <h3 title="停机期间错过的时点不补跑">定时任务</h3>
      </div>
      <div class="pad">
        <div class="row">
          <input v-model="schedForm.company" type="text" placeholder="公司名" class="sched-company">
          <select v-model="schedForm.freq">
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
          </select>
          <select v-if="schedForm.freq === 'weekly'" v-model="schedForm.weekday">
            <option value="1">周一</option><option value="2">周二</option><option value="3">周三</option>
            <option value="4">周四</option><option value="5">周五</option><option value="6">周六</option>
            <option value="7">周日</option>
          </select>
          <input v-model="schedForm.time" type="time">
          <label class="inline-lab">回溯 <input v-model.number="schedForm.days" type="number" min="3" max="365" class="days-input"> 天</label>
          <button class="btn btn-primary" @click="addSchedule">新建任务</button>
        </div>
        <div class="sched-list">
          <div v-if="!schedLoaded" class="empty" style="padding:14px">加载中…</div>
          <div v-else-if="!schedules.length" class="empty" style="padding:14px">还没有定时任务</div>
          <div v-for="s in schedules" :key="s.id" class="srow">
            <div>
              <div class="nm">{{ s.name }}</div>
              <div class="mt">
                {{ s.describe || '' }} · 下次 {{ s.enabled ? (s.next_run_at || '—') : '已停用' }}
                <template v-if="s.last_run_id"> · 最近 {{ s.last_run_id }}</template>
              </div>
            </div>
            <div class="acts">
              <button class="btn btn-ghost btn-sm" @click="schedAct('run', s.id)">立即运行</button>
              <button class="btn btn-ghost btn-sm" @click="schedAct('toggle', s.id)">{{ s.enabled ? '停用' : '启用' }}</button>
              <button class="btn btn-ghost btn-sm" @click="schedAct('del', s.id)">删除</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 进度 -->
    <section v-if="progressVisible" class="card section-gap">
      <div class="sec-h">
        <h3>执行过程</h3>
        <span
          class="run-st"
          :class="running ? 'run' : failedState ? (stateText === '已取消' ? 'warn' : 'err') : 'ok'"
        >{{ stateText }}</span>
        <span class="note">{{ lastId }}</span>
        <button v-if="cancelable" class="btn btn-ghost btn-sm" :disabled="cancelBusy" @click="cancel">取消</button>
        <button v-if="resumable" class="btn btn-ghost btn-sm" @click="resume">从断点续跑</button>
      </div>
      <div class="prog">
        <div class="prog-bar"><i :style="{ width: stepProgress.pct + '%' }"></i></div>
        <span class="prog-t">已完成 {{ stepProgress.done }}/{{ stepProgress.total }} 步</span>
        <span v-if="running" class="prog-t">已等待 {{ elapsedText }}</span>
      </div>
      <div v-if="failedState" class="fail">
        <div class="fail-t">{{ stateText === '已取消' ? '本次运行在结束前被取消' : '本次运行未能完成' }}</div>
        <div class="fail-m">{{ failMsg }}</div>
        <div class="fail-a">
          <button class="btn btn-primary btn-sm" :disabled="running" @click="runAgain">用原参数重新运行</button>
          <button v-if="resumable" class="btn btn-sm" @click="resume">从断点续跑</button>
        </div>
      </div>
      <div class="steps">
        <div v-for="s in stepRows" :key="s.nm" class="st" :class="s.st">
          <div class="dot">{{ s.mark }}</div>
          <div>
            <div class="nm">{{ s.nm }}</div>
            <div v-if="s.dt" class="dt">{{ s.dt }}</div>
          </div>
        </div>
      </div>
      <div class="logs-h">
        <span class="follow" :class="{ off: !logFollow }">{{ logFollow ? '日志实时跟随' : '已暂停跟随（向上滚动查看历史）' }}</span>
        <button v-if="!logFollow" class="btn btn-ghost btn-sm" @click="jumpLatest">回到最新</button>
      </div>
      <div ref="logsEl" class="logs logs-box" @scroll="onLogScroll">
        <div v-for="(l, i) in logs" :key="i">
          <span class="t">{{ l.ts }}</span>
          <span :class="{ w: l.level === 'warn', e: l.level === 'error', o: l.level === 'ok' }">{{ l.msg }}</span>
        </div>
      </div>
    </section>

    <!-- 结果 -->
    <div v-if="result" ref="resultEl">
      <div class="res-head">
        <div>
          <h2>{{ result.company || runCompany }}</h2>
          <div class="res-meta">运行 {{ result.run_id }}</div>
        </div>
        <button class="btn btn-ghost btn-sm" @click="runAgain" :disabled="running">重新分析</button>
      </div>
      <div v-if="reportOnly" class="ro-note">历史报告 · 仅保留正文</div>
      <div v-if="!reportOnly" class="stats">
        <div v-for="c in statCards" :key="c.k" class="sbox" :class="c.cls">
          <div class="k">{{ c.k }}</div>
          <div class="v">{{ c.v }}<small>{{ c.u }}</small></div>
        </div>
      </div>

      <section class="card section-gap">
        <div class="tabs">
          <div class="seg">
            <div v-if="!reportOnly" class="seg-i" :class="{ on: tab === 'facts' }" @click="tab = 'facts'">事实清单<span class="seg-n">{{ facts.length }}</span></div>
            <div class="seg-i" :class="{ on: tab === 'report' }" @click="tab = 'report'">情报报告</div>
            <div v-if="!reportOnly" class="seg-i" :class="{ on: tab === 'quality' }" @click="tab = 'quality'">质量与缺口</div>
          </div>
          <div class="spacer"></div>
          <div class="tab-acts">
            <button class="btn btn-ghost btn-sm" @click="copyLink">复制链接</button>
            <button class="btn btn-ghost btn-sm" :disabled="harvestBusy" @click="harvestMemory">
              {{ harvestBusy ? '沉淀中…' : '存为记忆' }}
            </button>
            <button class="btn btn-ghost btn-sm" @click="download('md')">下载报告</button>
            <button class="btn btn-ghost btn-sm" @click="download('csv')">下载事实表</button>
          </div>
        </div>

        <div v-show="tab === 'facts'">
          <div class="facts-bar">
            <input v-model="q" type="text" placeholder="搜索事实 / 来源…" class="q-input">
            <select v-model="typeFilter">
              <option value="">全部类型</option>
              <option v-for="t in factTypes" :key="t.k" :value="t.k">{{ t.label }}</option>
            </select>
          </div>
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th style="width:56px">编号</th><th style="width:96px">类型</th>
                  <th>事实陈述</th><th style="width:120px">金额/规模</th>
                  <th style="width:96px">事件日期</th><th style="width:88px">置信度</th>
                  <th style="width:150px">来源</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="f in filteredFacts" :key="f.id" @click="openFact(f.id)">
                  <td><span class="fid">{{ f.id }}</span></td>
                  <td><span class="pill" :class="f.type">{{ f.type_label }}</span></td>
                  <td>
                    {{ f.statement }}
                    <span v-if="(f.corroboration || 1) > 1" class="pill">佐证 {{ f.corroboration }} 源</span>
                  </td>
                  <td class="sub-cell">{{ f.amount || '—' }}</td>
                  <td class="mute-cell">{{ f.event_date || '—' }}</td>
                  <td><span class="conf" :class="confCls(f.confidence)">{{ (Number(f.confidence) || 0).toFixed(2) }}</span></td>
                  <td>
                    <div class="src" :title="f.source">{{ f.source || '—' }}</div>
                    <div class="src level">{{ levelText(f.evidence_level) }}</div>
                  </td>
                </tr>
                <tr v-if="!filteredFacts.length">
                  <td colspan="7" class="empty-cell">没有匹配的事实</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-show="tab === 'report'" class="report-wrap">
          <div class="md" v-html="reportHtml"></div>
        </div>

        <div v-show="tab === 'quality'" class="report-wrap">
          <div class="quality">
            <h2>证据结构</h2>
            <table>
              <thead><tr><th>指标</th><th>数值</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="r in quality.rows" :key="r.k">
                  <td>{{ r.k }}</td><td><b>{{ r.v }}</b></td><td class="sub-cell">{{ r.note }}</td>
                </tr>
              </tbody>
            </table>
            <h2>数据缺口与局限</h2>
            <ul>
              <li v-for="g in quality.gaps" :key="g">{{ g }}</li>
              <li v-if="!quality.gaps.length">未发现明显缺口。</li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  </div>

  <!-- 事实证据抽屉 -->
  <SlideDrawer
    :open="!!drawerFact"
    :title="drawerFact ? `[${drawerFact.id}] ${drawerFact.type_label}` : '事实详情'"
    :subtitle="drawerFact?.statement || ''"
    @close="drawerFact = null"
  >
    <template v-if="drawerFact">
      <div class="kv">
        <template v-for="row in drawerKv" :key="row.k">
          <div class="k">{{ row.k }}</div>
          <div class="v"><span :class="row.cls">{{ row.v }}</span></div>
        </template>
      </div>
      <div class="quote">
        <div class="lb">原文证据</div>
        <div>{{ drawerFact.evidence_quote || '（该事实仅有标题，无正文证据句）' }}</div>
      </div>
    </template>
    <template #footer>
      <a
        class="btn btn-ghost btn-sm" :class="{ disabled: !drawerFact?.url }"
        :href="drawerFact?.url || '#'" target="_blank" rel="noopener"
      >打开原文</a>
      <button class="btn btn-primary btn-sm" @click="audit('confirm')">确认无误</button>
      <button class="btn btn-ghost btn-sm" @click="audit('reject')">存疑</button>
    </template>
  </SlideDrawer>
</template>

<style scoped>
.wrap { width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 72px; }
.section-gap { margin-top: 18px; }
.pad { padding: 18px 22px 20px; }

.hero { padding: 26px 28px 22px; }
.hero h1 { margin: 0 0 14px; font-size: 25px; line-height: 1.35; letter-spacing: -.3px; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

/* 搜索条：输入框与主按钮合成一个控件，聚焦时整条高亮 */
.searchbar {
  display: flex; align-items: center; gap: 10px; padding: 7px;
  background: #fff; border: 1px solid var(--line-strong); border-radius: 13px;
  box-shadow: 0 1px 2px rgba(20, 29, 45, .04);
  transition: border-color .15s, box-shadow .15s;
}
.searchbar:focus-within { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(31, 102, 208, .10); }
.searchbar .company-input {
  flex: 1; min-width: 200px; font-size: 15.5px; padding: 8px 12px;
  border: none; background: transparent; border-radius: 8px;
}
.searchbar .company-input:hover { border-color: transparent; }
.searchbar .company-input:focus { box-shadow: none; }
.opt {
  display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
  margin-top: 18px; padding-top: 16px; border-top: 1px dashed var(--line);
  font-size: 12.5px; color: var(--sub);
}
.opt input[type=number] { width: 76px; padding: 6px 9px; font-size: 13px; }
@media (max-width: 640px) {
  .hero { padding: 20px 18px; }
  .hero h1 { font-size: 21px; }
  .searchbar { flex-direction: column; align-items: stretch; padding: 10px; }
  .searchbar .btn { width: 100%; }
}

/* 上次运行 */
.lastrun {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 12px 16px; border: 1px dashed var(--line-strong); border-radius: 11px; background: #FBFCFE;
}
.lr-lab { font-size: 11px; color: var(--mute); }
.lastrun b { font-size: 13px; }
.lr-time { font-size: 11.5px; color: var(--sub); }
.lr-id { font-size: 11px; color: var(--mute); font-family: ui-monospace, Consolas, monospace; }
.lastrun .btn { margin-left: auto; }

.sec-h { display: flex; align-items: center; gap: 10px; padding: 16px 20px; border-bottom: 1px solid var(--line); }
.sec-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.sec-h .note { font-size: 12px; color: var(--mute); margin-left: auto; }
.hist-tools { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.hist-q { width: 190px; padding: 6px 10px !important; font-size: 12.5px !important; }
.hist-tools select { padding: 6px 8px; font-size: 12.5px; }

.hist { max-height: 420px; overflow: auto; padding: 10px 16px 14px; display: flex; flex-direction: column; gap: 8px; }
.hrow {
  padding: 11px 14px; border: 1px solid var(--line); border-radius: 10px; cursor: pointer;
  display: flex; gap: 10px; align-items: center; transition: .12s;
}
.hrow:hover { border-color: var(--accent-line); background: var(--accent-soft); }
.hrow:focus-visible { border-color: var(--accent); }
.h-main { flex: 1; min-width: 0; }
.hrow .cn { font-size: 13.5px; font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hrow .mt { font-size: 11.5px; color: var(--mute); margin-top: 2px; }
.h-go { font-size: 11.5px; color: var(--accent); opacity: 0; transition: .12s; }
.hrow:hover .h-go, .hrow:focus-visible .h-go { opacity: 1; }
.hist-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 0 20px 14px; font-size: 11.5px; color: var(--mute);
}

.sched-company { flex: 1; min-width: 200px; }
.inline-lab { font-size: 12.5px; color: var(--sub); }
.days-input { width: 72px; }
.sched-list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.srow {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  border: 1px solid var(--line); border-radius: 9px; padding: 9px 12px;
}
.srow .nm { font-size: 13px; font-weight: 550; }
.srow .mt { font-size: 11.5px; color: var(--mute); margin-top: 1px; }
.srow .acts { margin-left: auto; display: flex; gap: 6px; }

.steps { padding: 10px 20px 18px; }
.prog { display: flex; align-items: center; gap: 12px; padding: 14px 20px 0; }
.prog-bar { flex: 1; height: 6px; border-radius: 3px; background: #EEF1F5; overflow: hidden; }
.prog-bar i { display: block; height: 100%; background: var(--accent); transition: width .3s; }
.prog-t { font-size: 11.5px; color: var(--mute); white-space: nowrap; }
.fail {
  margin: 12px 18px 0; border: 1px solid #F6C9C4; background: var(--err-soft);
  border-radius: 9px; padding: 12px 14px;
}
.fail-t { font-size: 13px; font-weight: 600; color: var(--err); }
.fail-m { margin-top: 4px; font-size: 12px; color: #7A271A; line-height: 1.7; word-break: break-word; }
.fail-a { margin-top: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.st { display: flex; gap: 12px; padding: 9px 0; align-items: flex-start; }
.st .dot {
  width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid var(--line); flex: 0 0 22px;
  display: grid; place-items: center; font-size: 11px; color: var(--mute); margin-top: 1px; background: #fff; transition: .2s;
}
.st.run .dot {
  border-color: var(--accent); color: var(--accent); background: var(--accent-soft);
  animation: pulse 1.4s infinite;
}
.st.done .dot { border-color: var(--ok); background: var(--ok); color: #fff; }
@keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(26, 109, 213, .30); } 50% { box-shadow: 0 0 0 5px rgba(26, 109, 213, 0); } }
.st .nm { font-size: 14px; font-weight: 550; }
.st .dt { font-size: 12px; color: var(--mute); margin-top: 1px; }
.st.run .nm { color: var(--accent); }
.logs-box { height: 250px; margin: 0 20px 18px; }
.logs-h { display: flex; align-items: center; gap: 10px; padding: 0 20px 8px; }
.logs-h .follow { font-size: 11px; color: var(--mute); }
.logs-h .follow.off { color: var(--warn); }
.logs-h .btn { margin-left: auto; }

.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-top: 18px; }
.res-head { margin-top: 24px; display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
.ro-note {
  margin-top: 12px; padding: 10px 14px; border-radius: 9px;
  background: var(--warn-soft); color: var(--warn); font-size: 12px; line-height: 1.75;
}
.res-head h2 { margin: 0; font-size: 22px; letter-spacing: -.2px; }
.res-meta { margin-top: 4px; font-size: 12px; color: var(--mute); }
@media (max-width: 860px) { .stats { grid-template-columns: repeat(2, 1fr); } }
.sbox { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; box-shadow: var(--shadow-card); }
.sbox .k { font-size: 12.5px; color: var(--sub); }
.sbox .v { font-size: 26px; font-weight: 680; margin-top: 4px; letter-spacing: -.5px; }
.sbox .v small { font-size: 12px; font-weight: 500; color: var(--mute); margin-left: 3px; }
.sbox.g .v { color: var(--ok); }

.tabs {
  display: flex; gap: 10px; padding: 10px 20px; border-bottom: 1px solid var(--line);
  align-items: center; flex-wrap: wrap;
}
.tab-acts { display: flex; gap: 8px; flex-wrap: wrap; }

.facts-bar {
  padding: 14px 20px; border-bottom: 1px solid var(--line);
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
}
.q-input { flex: 1; max-width: 320px; padding: 8px 11px !important; font-size: 13px !important; }
.facts-bar select { padding: 8px 10px; font-size: 13px; }
.table-scroll { overflow: auto; max-height: 640px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 11px 14px; text-align: left; font-size: 13px; vertical-align: top; }
th {
  font-size: 12px; color: var(--mute); font-weight: 600; background: #F8FAFC;
  border-bottom: 1px solid var(--line); position: sticky; top: 0; white-space: nowrap; z-index: 1;
}
tbody tr { border-bottom: 1px solid #F1F3F6; cursor: pointer; transition: .1s; }
tbody tr:hover { background: #F8FAFF; }
.fid { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: var(--accent); font-weight: 600; }
.pill.capacity { background: #E8F1FE; color: #1D4ED8; }
.pill.order { background: #E8F7F1; color: #0E7C5A; }
.pill.price { background: #FEF4E6; color: #B45309; }
.pill.product { background: #F0EDFE; color: #5B3FC4; }
.pill.finance { background: #EAF3FB; color: #1F6FA8; }
.pill.performance { background: #E9F7F1; color: #0B6B4F; }
.pill.policy { background: #FDF0F3; color: #A8324F; }
.pill.risk { background: #FDEDEC; color: #B42318; }
.pill.personnel { background: #F3F1EA; color: #7A6A3A; }
.sub-cell { color: var(--sub); }
.mute-cell { color: var(--mute); font-size: 12px; }
.src.level { font-size: 11px; }
.empty-cell { padding: 26px; text-align: center; color: var(--mute); }

.report-wrap { padding: 26px 30px; }
.quality h2 { font-size: 16px; margin: 24px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
.quality h2:first-child { margin-top: 0; }
.quality table { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin-bottom: 8px; }
.quality td { border-top: 1px solid #F1F3F6; }
.quality ul { margin: 8px 0; padding-left: 22px; }
.quality li { margin: 6px 0; }

/* 抽屉内容 */
.kv { display: grid; grid-template-columns: 78px 1fr; gap: 9px 14px; font-size: 13px; margin-bottom: 18px; align-items: baseline; }
.kv .k { color: var(--mute); font-size: 12px; white-space: nowrap; }
.kv .v { color: var(--ink); line-height: 1.55; }
.quote {
  background: #FFFBEA; border: 1px solid #F2E4B8; border-radius: 9px;
  padding: 13px 15px; font-size: 13px; line-height: 1.8; color: #4A3C10;
}
.quote .lb { font-size: 11px; color: #9A7B21; font-weight: 600; margin-bottom: 6px; letter-spacing: .4px; }
.btn.disabled { opacity: .4; pointer-events: none; }
</style>