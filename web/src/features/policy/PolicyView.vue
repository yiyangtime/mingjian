<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import AppHeader from '../../shared/components/AppHeader.vue'
import SlideDrawer from '../../shared/components/SlideDrawer.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import { actorName } from '../../shared/composables/identity'
import { b64EncodeBytes, b64EncodeUtf8, download, esc, nowText, sleep } from '../../shared/lib/format'

const FIELDS = [
  { key: 'issuer', label: '发文机关' },
  { key: 'docNo', label: '文号' },
  { key: 'date', label: '发布日期' },
  { key: 'region', label: '适用地区' },
  { key: 'industry', label: '适用行业' },
  { key: 'support', label: '支持标准' },
  { key: 'deadline', label: '申报截止' },
] as const
type FieldKey = (typeof FIELDS)[number]['key']
const TABLE_FIELDS: FieldKey[] = ['issuer', 'region', 'support', 'deadline']

const PLAN = ['文件识别', '文本解析', '字段抽取', '校验归集']

interface PolicyDoc {
  id: string
  name: string
  type?: string
  pages?: number
  status: 'ok' | 'partial' | 'failed'
  conf: number
  f: Record<string, string | null>
  hl: Record<string, string>
  lowFields?: string[]
  issue?: string
  real?: boolean
  level?: string
  url?: string
  page?: string
  pageLabel?: string
}

interface AuditItem {
  time: string
  user: string
  doc: string
  fname: string
  field: FieldKey
  label: string
  old: string
  new: string
  action: '确认' | '修正'
  reason: string
}

// 本次批任务号：按日期 + 随机后缀生成，与审计日志里的记录对齐
const TASK_ID = 'P' + new Date().toISOString().slice(0, 10).replace(/-/g, '') + '-'
  + String(Math.floor(Math.random() * 9000) + 1000)
const actorNow = actorName()

const stage = ref(1)
const docs = ref<PolicyDoc[]>([])
const overrides = reactive<Record<string, string>>({})
const audit = ref<AuditItem[]>([])
const bundleMeta = ref<{ dir?: string } | null>(null)
const scanInfo = ref<{ skipped?: string[] } | null>(null)
const curTab = ref<'ok' | 'issue'>('ok')
const running = ref(false)
const runSub = ref('')
const runLogs = ref<{ t: string; text: string; cls: string }[]>([])
const runErr = ref('')
const runLogsEl = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)
const pasteOpen = ref(false)
const pasteName = ref('')
const pasteText = ref('')
const gov = reactive({
  company: '',
  province: '',
  note: '站点：工业和信息化部 · 各省工业和信息化厅',
  busy: false,
  logs: [] as { t: string; msg: string; kind: string }[],
})
const govLogsEl = ref<HTMLElement | null>(null)

// 抽屉
const drawerOpen = ref(false)
const drawerMode = ref<'field' | 'raw' | 'audit'>('field')
const curDoc = ref<PolicyDoc | null>(null)
const curField = ref<FieldKey | null>(null)
const editing = ref(false)
const edVal = ref('')
const edReason = ref('')
const edInput = ref<HTMLInputElement | null>(null)

let runTimer: number | undefined

/* ---------- 取值与复核状态 ---------- */
function aval(d: PolicyDoc | null, key: FieldKey): string {
  if (!d) return ''
  const k = d.id + ':' + key
  if (k in overrides) return overrides[k]
  return d.f?.[key] || ''
}

function stateOf(id: string, key: FieldKey): '确认' | '修正' | null {
  for (let i = audit.value.length - 1; i >= 0; i--) {
    const a = audit.value[i]
    if (a.doc === id && a.field === key) return a.action
  }
  return null
}

function confOf(d: PolicyDoc): number {
  return typeof d.conf === 'number' && isFinite(d.conf) ? d.conf : 0
}

function confColor(c: number): string {
  return c >= 0.9 ? '#22A06B' : c >= 0.75 ? '#E0A030' : '#DC2626'
}

function addRunLog(text: string, cls = ''): void {
  runLogs.value.push({ t: new Date().toTimeString().slice(0, 8), text, cls })
  nextTick(() => {
    if (runLogsEl.value) runLogsEl.value.scrollTop = runLogsEl.value.scrollHeight
  })
}

function setStage(n: number): void {
  stage.value = n
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

/* ---------- 批任务执行（解析在接入时已完成，这里汇总真实结果） ---------- */
const stepSummary = computed(() => {
  const list = docs.value
  const N = list.length
  const ok = list.filter((d) => d.status === 'ok').length
  const partial = list.filter((d) => d.status === 'partial').length
  const failed = list.filter((d) => d.status === 'failed').length
  let fields = 0
  let locatable = 0
  list.forEach((d) => FIELDS.forEach((f) => {
    if (aval(d, f.key)) {
      fields++
      if (d.hl?.[f.key]) locatable++
    }
  }))
  return [
    { name: '文件识别', done: N, total: N },
    { name: '文本解析', done: N - failed, total: N },
    { name: '字段抽取', done: locatable, total: Math.max(fields, 1) },
    { name: '校验归集', done: ok + partial, total: N },
  ]
})

async function startRun(): Promise<void> {
  if (running.value) return
  if (!docs.value.length) {
    toast('当前没有可执行的材料，请先接入')
    return
  }
  running.value = true
  runErr.value = ''
  setStage(2)
  runSub.value = `政策与补贴公告批量解析 · ${docs.value.length} 份材料`
  // 看门狗：超过 15 秒说明卡住，必须给一条出路，否则按钮永远不可用
  runTimer = window.setTimeout(() => {
    if (!running.value) return
    running.value = false
    addRunLog('任务超过 15 秒未完成，已中止', 'err')
    runErr.value = '任务超时未完成，已中止。可重新执行，或返回批次清单。'
  }, 15000)
  try {
    runLogs.value = []
    addRunLog(`任务 ${TASK_ID} 汇总开始，模板 v1.2（已冻结）`)
    addRunLog(`数据源：${bundleMeta.value?.dir ? 'materials/ · 服务端解析' : '本次上传'}`)
    await sleep(200)
    docs.value.forEach((d) => {
      if (d.status === 'failed') addRunLog(`${d.id} ${d.name.slice(0, 20)} → 解析失败：${d.issue}`, 'err')
      else if (d.status === 'partial') addRunLog(`${d.id} ${d.name.slice(0, 20)} → 部分成功，置信度 ${confOf(d).toFixed(2)}，转复核`, 'warn')
      else addRunLog(`${d.id} ${d.name.slice(0, 20)} → 7 字段命中，置信度 ${confOf(d).toFixed(2)}`, 'ok')
    })
    let fields = 0
    let locatable = 0
    docs.value.forEach((d) => FIELDS.forEach((f) => {
      if (aval(d, f.key)) {
        fields++
        if (d.hl?.[f.key]) locatable++
      }
    }))
    addRunLog(`汇总完成：${fields} 个字段抽取，${locatable} 个可点回原文`, 'ok')
    await sleep(320)
  } catch (e) {
    const msg = (e as Error)?.message || String(e)
    addRunLog(`执行中断：${msg}`, 'err')
    runErr.value = `执行中断：${msg}。可重新执行，或返回批次清单。`
    toast('任务执行中断')
  } finally {
    running.value = false
    if (runTimer) {
      window.clearTimeout(runTimer)
      runTimer = undefined
    }
    if (!runErr.value) setStage(3)
  }
}

function resetAll(): void {
  drawerOpen.value = false
  if (runTimer) {
    window.clearTimeout(runTimer)
    runTimer = undefined
  }
  running.value = false
  runErr.value = ''
  curTab.value = 'ok'
  setStage(1)
}

/* ---------- 统计与表格 ---------- */
const stats = computed(() => {
  const ok = docs.value.filter((d) => d.status === 'ok').length
  let fields = 0
  let locatable = 0
  docs.value.forEach((d) => FIELDS.forEach((f) => {
    if (aval(d, f.key)) {
      fields++
      if (d.hl?.[f.key]) locatable++
    }
  }))
  const pending = docs.value.filter((d) => (d.status !== 'ok' || (d.lowFields && d.lowFields.length)) &&
    !FIELDS.some((f) => stateOf(d.id, f.key))).length
  return { total: docs.value.length, ok, fields, locatable, pending }
})

const issues = computed(() => docs.value.filter((d) => d.status !== 'ok' || (d.lowFields && d.lowFields.length)))

function issueResolved(d: PolicyDoc): boolean {
  return FIELDS.some((f) => stateOf(d.id, f.key))
}

function issueType(d: PolicyDoc): string {
  if (d.status === 'failed') return '解析失败'
  if (!aval(d, 'deadline')) return '字段缺失'
  if (d.lowFields) return '置信度不足'
  return '需复核'
}

function openIssue(d: PolicyDoc): void {
  if (d.status === 'failed') {
    openRaw(d)
  } else if (!aval(d, 'deadline')) {
    openDrawer(d.id, 'deadline')
  } else if (d.lowFields) {
    // 后端给的是字段键，落回模板字段类型
    openDrawer(d.id, (d.lowFields[0] || 'support') as FieldKey)
  } else {
    openDrawer(d.id, 'support')
  }
}

/* ---------- 抽屉 ---------- */
const drawerTitle = computed(() => {
  if (drawerMode.value === 'audit') return '审计记录'
  if (drawerMode.value === 'raw') return '异常材料详情'
  return '原文定位'
})

const drawerSub = computed(() => {
  if (drawerMode.value === 'audit') return `任务 ${TASK_ID}　·　共 ${audit.value.length} 条操作`
  const d = curDoc.value
  return d ? `${d.name}　·　共 ${d.pages || 1} 页` : ''
})

const fieldLabel = computed(() => FIELDS.find((f) => f.key === curField.value)?.label || '字段')
const curValue = computed(() => aval(curDoc.value, curField.value as FieldKey))
const curState = computed(() => (curDoc.value && curField.value ? stateOf(curDoc.value.id, curField.value) : null))

function openDrawer(id: string, key: FieldKey): void {
  const d = docs.value.find((x) => x.id === id)
  if (!d) return
  curDoc.value = d
  curField.value = key
  drawerMode.value = 'field'
  editing.value = false
  drawerOpen.value = true
}

function openRaw(d: PolicyDoc): void {
  curDoc.value = d
  curField.value = null
  drawerMode.value = 'raw'
  editing.value = false
  drawerOpen.value = true
}

function openAudit(): void {
  curDoc.value = null
  curField.value = null
  drawerMode.value = 'audit'
  drawerOpen.value = true
}

const paperHtml = computed(() => {
  const d = curDoc.value
  if (!d) return ''
  if (!curField.value) return `<span class="failed-text">${esc(d.page)}</span>`
  let paper = esc(d.page)
  const hl = d.hl?.[curField.value]
  if (hl) {
    const eh = esc(hl)
    if (paper.includes(eh)) {
      paper = paper.replace(eh, `<mark>${eh}</mark>`)
    } else {
      // 证据句跨换行或含不可见字符时，退化为按首句定位
      const head = esc(String(hl).split(/[。；\n]/)[0].slice(0, 24))
      if (head.length > 6 && paper.includes(head)) {
        paper = paper.replace(head, `<mark>${head}</mark>`)
      }
    }
  }
  return paper
})

const basisHtml = computed(() => {
  const d = curDoc.value
  if (!d) return ''
  if (drawerMode.value === 'raw') {
    return `<b>异常说明</b><br>${esc(d.issue)}`
      + '<br><b>处理策略</b>：单份材料解析失败不阻断批次，其余材料正常输出；'
      + '该文件已归入异常清单并给出补充材料建议，可重新上传后单文件重跑。'
  }
  const v = curValue.value
  let basis = ''
  if (v) {
    const loc = d.real ? `${d.pageLabel || '第 1 页'}，句式匹配命中原文片段` : '第 1 页第 2—4 段'
    basis = `<b>抽取依据</b><br>「${esc(fieldLabel.value)}」抽取器从${loc}识别，取值「${esc(v)}」，`
      + `格式校验通过，置信度 <b>${confOf(d).toFixed(2)}</b>。`
      + `<br><b>可追溯性</b>：字段值绑定来源记录 ${d.id}，可回链原文复核。`
  } else {
    basis = `<b>未提取到取值</b><br>${esc(d.issue || '全文中未找到与该字段匹配的表述')}。`
      + '<br>该字段已标记为缺失，不以推测值填充，需人工补录后留痕。'
  }
  if (curState.value) {
    const rec = [...audit.value].reverse().find((a) => a.doc === d.id && a.field === curField.value)
    if (rec) {
      basis += `<br><b>人工操作</b>：${rec.time} 由 ${esc(rec.user)} 执行「${rec.action}」`
        + (rec.action === '修正' ? `，原因：${esc(rec.reason)}` : '') + '。'
    }
  }
  return basis
})

function startEdit(): void {
  if (!curDoc.value || !curField.value) {
    toast('该文件无字段可修正')
    return
  }
  edVal.value = curValue.value
  edReason.value = ''
  editing.value = true
  nextTick(() => edInput.value?.focus())
}

function saveEdit(): void {
  const d = curDoc.value
  const key = curField.value
  if (!d || !key) return
  const nv = edVal.value.trim()
  const rs = edReason.value.trim()
  if (!nv) {
    toast('请填写修正后的字段值')
    return
  }
  if (!rs) {
    toast('修正原因必填，需写入审计日志')
    return
  }
  const ov = aval(d, key)
  overrides[d.id + ':' + key] = nv
  audit.value.push({
    time: nowText(), user: actorName(), doc: d.id, fname: d.name,
    field: key, label: FIELDS.find((f) => f.key === key)?.label || key,
    old: ov || '（空）', new: nv, action: '修正', reason: rs,
  })
  pushAudit('修正', key, ov, nv, rs)
  editing.value = false
  toast('已修正，原值与新值一并留痕')
}

function confirmField(): void {
  const d = curDoc.value
  const key = curField.value
  if (!d || !key) {
    toast('该文件无字段可确认')
    return
  }
  const v = aval(d, key)
  if (!v) {
    toast('该字段为空，请使用「修正」补录')
    return
  }
  audit.value.push({
    time: nowText(), user: actorName(), doc: d.id, fname: d.name,
    field: key, label: FIELDS.find((f) => f.key === key)?.label || key,
    old: v, new: v, action: '确认', reason: '',
  })
  pushAudit('确认', key, v, v, '')
  toast('已确认，操作写入审计日志')
}

/* 本地先落 UI，服务端留痕异步写；写失败不影响复核流程，但要能看到提示 */
function pushAudit(action: '确认' | '修正', field: FieldKey, oldValue: string, newValue: string, reason: string): void {
  api.post('/api/policy-radar/confirm', {
    doc: curDoc.value?.id || '-',
    field,
    action: action === '确认' ? 'confirm' : 'correct',
    old_value: oldValue || '',
    new_value: newValue || '',
    reason: reason || '',
    actor: actorName(),
  }).catch((e) => toast('留痕失败：' + (e as Error).message))
}

/* ---------- 接入：上传 / 粘贴 / materials ---------- */
function normDoc(d: Partial<PolicyDoc> | null): PolicyDoc | null {
  if (!d || typeof d !== 'object') return null
  const f = { ...(d.f || {}) }
  FIELDS.forEach((x) => {
    if (f[x.key] === undefined) f[x.key] = null
  })
  return {
    id: d.id || 'D00',
    name: d.name || d.id || '未命名',
    type: d.type || '文本',
    pages: d.pages || 1,
    status: d.status || 'partial',
    conf: typeof d.conf === 'number' && isFinite(d.conf) ? d.conf : 0,
    f,
    hl: { ...(d.hl || {}) },
    lowFields: d.lowFields,
    issue: d.issue,
    real: d.real,
    level: d.level,
    url: d.url,
    page: d.page || '',
    pageLabel: d.pageLabel,
  }
}

function addDocs(list: Partial<PolicyDoc>[]): number {
  if (!list || !list.length) return 0
  const clean = list.map(normDoc).filter(Boolean) as PolicyDoc[]
  clean.forEach((d) => { d.real = true })
  docs.value.unshift(...clean)
  return clean.length
}

function onPick(e: Event): void {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (files.length) readFiles(files)
}

function readFiles(files: File[]): void {
  if (!files.length) return
  toast(`正在解析 ${files.length} 份材料…`)
  Promise.all(files.map((file) => new Promise<{ name: string; b64: string } | null>((resolve) => {
    const fr = new FileReader()
    fr.onload = () => resolve({ name: file.name, b64: b64EncodeBytes(fr.result as ArrayBuffer) })
    fr.onerror = () => resolve(null)
    fr.readAsArrayBuffer(file)
  }))).then(async (list) => {
    const payload = list.filter(Boolean) as { name: string; b64: string }[]
    if (!payload.length) {
      toast('文件读取失败')
      return
    }
    try {
      const d = await api.post<{ docs: Partial<PolicyDoc>[]; stats?: { locatable?: number }; skipped?: string[] }>(
        '/api/policy-radar/parse', { files: payload },
      )
      const added = addDocs(d.docs || [])
      const skipped = d.skipped || []
      if (added) toast(`已解析 ${added} 份，字段 ${d.stats?.locatable || 0} 个可回溯原文`)
      if (skipped.length) setTimeout(() => toast(`${skipped.length} 份未解析：${skipped[0].slice(0, 30)}`), 1500)
    } catch (e) {
      toast('解析失败：' + (e as Error).message)
    }
  })
}

function openPaste(): void {
  pasteOpen.value = true
  setTimeout(() => document.getElementById('pasteName')?.focus(), 60)
}

async function doPaste(): Promise<void> {
  const name = pasteName.value.trim() || `粘贴材料_${new Date().toISOString().slice(0, 10)}.txt`
  const text = pasteText.value
  if (text.replace(/\s/g, '').length < 60) {
    toast('正文太短，至少需要 60 字才能解析')
    return
  }
  try {
    const d = await api.post<{ docs: Partial<PolicyDoc>[] }>('/api/policy-radar/parse', {
      files: [{ name, b64: b64EncodeUtf8(text) }],
    })
    if (!(d.docs || []).length) {
      toast('未解析出字段')
      return
    }
    const doc = d.docs[0]
    addDocs([doc])
    pasteOpen.value = false
    pasteName.value = ''
    pasteText.value = ''
    toast(`已解析「${name.slice(0, 18)}${name.length > 18 ? '…' : ''}」，置信度 ${Number(doc.conf || 0).toFixed(2)}`)
  } catch (e) {
    toast('解析失败：' + (e as Error).message)
  }
}

async function loadFromServer(manual: boolean): Promise<void> {
  try {
    const d = await api.get<{ docs: Partial<PolicyDoc>[]; dir?: string; skipped?: string[] }>('/api/policy-radar/scan')
    const list = d.docs || []
    scanInfo.value = { skipped: d.skipped }
    if (!list.length) {
      if (!docs.value.length) bundleMeta.value = { dir: d.dir }
      if (manual) toast('materials/ 为空，请拖入文件或放入后刷新')
      return
    }
    const clean = list.map(normDoc).filter(Boolean) as PolicyDoc[]
    clean.forEach((x) => { x.real = true })
    docs.value = clean
    bundleMeta.value = { dir: d.dir }
    if (manual) toast(`已从 materials/ 载入 ${clean.length} 份材料`)
  } catch (e) {
    if (manual) toast('读取失败：' + (e as Error).message)
  }
}

function clearReal(): void {
  docs.value = docs.value.filter((d) => !d.real)
  bundleMeta.value = null
  toast('已清空结果，可重新拖入材料')
}

/* 拖拽上传 */
function onDragEnter(e: DragEvent): void {
  e.preventDefault()
  e.stopPropagation()
  dragOver.value = true
}
function onDragLeave(e: DragEvent): void {
  e.preventDefault()
  e.stopPropagation()
  dragOver.value = false
}
function onDrop(e: DragEvent): void {
  e.preventDefault()
  e.stopPropagation()
  dragOver.value = false
  const fs = e.dataTransfer?.files
  if (fs && fs.length) readFiles(Array.from(fs))
}

/* ---------- 按产业自动获取（工信部 / 省厅） ---------- */
function govLog(msg: string, kind = ''): void {
  gov.logs.push({ t: new Date().toTimeString().slice(0, 8), msg, kind })
  nextTick(() => {
    if (govLogsEl.value) govLogsEl.value.scrollTop = govLogsEl.value.scrollHeight
  })
}

async function loadGovHealth(): Promise<void> {
  try {
    const d = await api.get<{ unavailable?: string[] }>('/api/policy-radar/gov/health')
    const bad = d.unavailable || []
    if (!bad.length) return
    gov.note = `站点：工业和信息化部 · 各省工业和信息化厅　已知取不到：${bad.join('、')}（请改用下方材料上传）`
  } catch {
    /* 健康状态拿不到不影响主流程 */
  }
}

async function fetchGov(): Promise<void> {
  const company = gov.company.trim()
  if (!company) {
    toast('请先输入公司名')
    return
  }
  gov.busy = true
  gov.logs = []
  govLog(`开始：${company}，去工业和信息化部与省级工信厅官网检索`)
  const q: Record<string, string | number> = { company, central: 4, local: 4 }
  if (gov.province.trim()) q.province = gov.province.trim()
  try {
    const d = await api.get<{
      industry?: string; industry_source?: string; province?: string; keywords?: string[]
      logs?: { msg: string; kind?: string }[]; docs?: Partial<PolicyDoc>[]
      sources?: { level: string; keyword?: string; ok: boolean; got?: number; name: string }[]
      warnings?: string[]; cached?: boolean; stats?: { ok?: number; partial?: number; failed?: number; fields?: number }
    }>('/api/policy-radar/gov', q)
    const ind = d.industry
      ? `行业：${d.industry}${d.industry_source === '企业自述档案' ? '（取自自述档案，非公开档案）' : ''}`
      : '行业：未识别'
    govLog(`${ind}｜${d.province ? `地方：${d.province}` : '地方：未识别到省份'}`)
    if ((d.keywords || []).length) govLog('检索词：' + d.keywords!.slice(0, 6).join('、'))
    ;(d.logs || []).forEach((l) => govLog(l.msg, l.kind))
    const list = d.docs || []
    const srcs = d.sources || []
    if (srcs.length) {
      const bad = srcs.filter((s) => !s.ok)
      govLog('通道：' + srcs.map((s) => {
        const tag = s.level + (s.keyword ? `「${s.keyword}」` : '')
        return `${tag} ${s.ok ? (s.got ? `取到 ${s.got} 条` : '正常·无匹配') : '失败'}`
      }).join('；'), bad.length ? 'warn' : '')
    }
    ;(d.warnings || []).forEach((w) => govLog(w, 'warn'))
    if (!list.length) {
      const bad = srcs.filter((s) => !s.ok)
      if (bad.length) {
        govLog(`没取到文件，但不是「没有政策」：${bad.map((s) => s.name).join('、')} 本次无法访问。`
          + '可换省份重试，或把文件放进 materials/ 后重新扫描', 'warn')
        gov.note = '取数失败：' + bad.map((s) => s.name + ' 不可达').join('；')
      } else {
        govLog('通道正常，但没有匹配到本产业的政策文件。可换关键词，或在 materials/ 放入文件后重新扫描', 'warn')
        gov.note = `已检索：${company}｜未匹配到相关政策`
      }
      return
    }
    const added = addDocs(list)
    const st = d.stats || {}
    govLog(`并入 ${added} 份（完全成功 ${st.ok || 0} / 部分成功 ${st.partial || 0} / 失败 ${st.failed || 0}），`
      + `字段 ${st.fields || 0} 个可回溯原文`, 'ok')
    if (d.cached) govLog('本次结果来自 24 小时内缓存，点按钮可强制刷新')
    const warnN = (d.warnings || []).length
    gov.note = `已完成：${company}｜${list.length} 份政策公告` + (warnN ? `｜${warnN} 项通道告警见日志` : '')
    toast(`已获取 ${added} 份政策公告`)
  } catch (e) {
    govLog('取数失败：' + (e as Error).message, 'warn')
    toast('取数失败：' + (e as Error).message)
  } finally {
    gov.busy = false
  }
}

/* ---------- 导出 ---------- */
function exportCSV(): void {
  const head = ['编号', '文件名称', '来源', '格式', '页数', '发文机关', '文号', '发布日期', '适用地区', '适用行业', '支持标准', '申报截止', '置信度', '状态', '人工复核']
  const rows = docs.value.map((d) => [
    d.id, d.name, d.real ? '真实材料' : '样例', d.type || '', d.pages || 1,
    aval(d, 'issuer'), aval(d, 'docNo'), aval(d, 'date'), aval(d, 'region'),
    aval(d, 'industry'), aval(d, 'support'), aval(d, 'deadline'),
    d.status === 'failed' ? '' : confOf(d).toFixed(2),
    d.status === 'ok' ? '成功' : d.status === 'partial' ? '部分成功' : '解析失败',
    FIELDS.filter((f) => stateOf(d.id, f.key)).map((f) => `${f.label}:${stateOf(d.id, f.key)}`).join(' / '),
  ])
  const csv = '\uFEFF' + [head, ...rows]
    .map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\r\n')
  download(csv, '政策公告抽取结果.csv', 'text/csv;charset=utf-8')
  toast(`已导出 ${docs.value.length} 条记录`)
}

function exportJSON(): void {
  const out = docs.value.map((d) => ({
    id: d.id, file: d.name, source: d.real ? 'real' : 'demo', type: d.type, pages: d.pages,
    confidence: d.status === 'failed' ? null : d.conf,
    status: d.status,
    fields: FIELDS.reduce<Record<string, string>>((a, f) => { a[f.key] = aval(d, f.key); return a }, {}),
    review: FIELDS.filter((f) => stateOf(d.id, f.key))
      .reduce<Record<string, string>>((a, f) => { a[f.key] = stateOf(d.id, f.key) || ''; return a }, {}),
    evidence: Object.keys(d.hl || {}).reduce<Record<string, { page: number; text: string }>>((a, k) => {
      a[k] = { page: 1, text: d.hl[k] }
      return a
    }, {}),
  }))
  download(JSON.stringify(out, null, 2), '政策公告抽取结果.json', 'application/json')
  toast('已导出 JSON（含证据定位与复核标记）')
}

/* ---------- 生命周期 ---------- */
onMounted(() => {
  loadFromServer(false)
  loadGovHealth()
  document.addEventListener('dragenter', onDragEnter)
  document.addEventListener('dragover', onDragEnter)
  document.addEventListener('dragleave', onDragLeave)
  document.addEventListener('drop', onDrop)
  // 深链接：#result 直接看结果页，#run 直接开始执行，便于汇报时跳过等待
  if (location.hash === '#result') setTimeout(() => setStage(3), 80)
  if (location.hash === '#run') setTimeout(() => startRun(), 120)
})

onBeforeUnmount(() => {
  document.removeEventListener('dragenter', onDragEnter)
  document.removeEventListener('dragover', onDragEnter)
  document.removeEventListener('dragleave', onDragLeave)
  document.removeEventListener('drop', onDrop)
  if (runTimer) window.clearTimeout(runTimer)
})
</script>

<template>
  <AppHeader :task="'任务 ' + TASK_ID" />

  <div class="stagebar">
    <div class="sb-item" :class="{ on: stage === 1, done: stage > 1 }"><span class="dot">{{ stage > 1 ? '✓' : '1' }}</span>任务配置</div>
    <div class="sb-sep"></div>
    <div class="sb-item" :class="{ on: stage === 2, done: stage > 2 }"><span class="dot">{{ stage > 2 ? '✓' : '2' }}</span>执行中</div>
    <div class="sb-sep"></div>
    <div class="sb-item" :class="{ on: stage === 3 }"><span class="dot">3</span>结果复核</div>
  </div>

  <main class="wrap">
    <!-- 1 配置 -->
    <div v-show="stage === 1">
      <section class="card">
        <div class="card-h"><h3>任务模板</h3></div>
        <div class="tpl-name">政策与补贴公告批量解析</div>
        <div class="meta-row">
          <div class="meta-item"><div class="k">模板版本</div><div class="v">v1.2</div></div>
          <div class="meta-item"><div class="k">本次材料</div><div class="v">{{ docs.length }} 份</div></div>
          <div class="meta-item"><div class="k">操作人</div><div class="v">{{ actorNow }}</div></div>
        </div>
      </section>

      <section class="card">
        <div class="card-h"><h3>执行计划</h3></div>
        <div class="plan">
          <div v-for="(name, i) in PLAN" :key="name" class="plan-row">
            <div class="plan-idx">{{ i + 1 }}</div>
            <div class="plan-t">{{ name }}</div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-h">
          <h3>待解析材料</h3>
          <span class="hint">{{ docs.length ? `已就绪 ${docs.length} 份` : '等待载入材料' }}</span>
        </div>
        <div class="files">
          <span v-for="d in docs" :key="d.id" class="fchip"><i></i>{{ d.type }} · {{ d.id }}</span>
          <div v-if="!docs.length" class="files-empty">暂无材料</div>
        </div>
      </section>

      <section class="card">
        <div class="card-h"><h3>按产业自动获取</h3></div>
        <div class="govform">
          <input v-model="gov.company" class="inp" placeholder="输入公司名称" @keydown.enter="fetchGov">
          <input v-model="gov.province" class="inp inp-sm" placeholder="省份（留空按注册地）">
          <button class="btn btn-primary" :disabled="gov.busy" @click="fetchGov">
            {{ gov.busy ? '正在取数…' : '获取政策与补贴公告' }}
          </button>
          <span class="note">{{ gov.note }}</span>
        </div>
        <div v-if="gov.logs.length" ref="govLogsEl" class="govlog">
          <div v-for="(l, i) in gov.logs" :key="i" class="ln" :class="l.kind">{{ l.t }}　{{ l.msg }}</div>
        </div>
      </section>

      <section class="card">
        <div class="card-h"><h3>材料来源</h3></div>
        <div class="srcbar">
          <span class="srcpill">材料总数 <b>{{ docs.length }}</b> 份</span>
          <span v-if="docs.filter((d) => d.real).length" class="srcpill real">真实材料 <b>{{ docs.filter((d) => d.real).length }}</b> 份</span>
          <span v-if="scanInfo?.skipped?.length" class="srcpill demo">未解析 <b>{{ scanInfo.skipped.length }}</b> 份</span>
          <button v-if="docs.some((d) => d.real)" class="btn btn-ghost btn-sm" @click="clearReal">移除真实材料</button>
        </div>
        <div class="dropzone" :class="{ over: dragOver }" @click="fileInput?.click()">
          <div class="dz-ico">＋</div>
          <div class="dz-t">拖入文件或点击选择</div>
          <input
            ref="fileInput" type="file" multiple hidden
            accept=".pdf,.docx,.txt,.md,.markdown,.html,.htm" @change="onPick"
          >
        </div>
        <div class="dz-alt">
          <button class="btn btn-ghost btn-sm" @click.stop="openPaste">粘贴全文解析</button>
          <button class="btn btn-ghost btn-sm" @click.stop="loadFromServer(true)">重新扫描 materials/</button>
        </div>
      </section>

      <div class="actions">
        <button class="btn btn-primary" :disabled="running" @click="startRun">汇总解析结果</button>
      </div>
    </div>

    <!-- 2 执行 -->
    <div v-show="stage === 2" class="runbox">
      <div class="run-sub">{{ runSub }}</div>
      <div class="step" v-for="(s, i) in stepSummary" :key="i">
        <div class="step-ico">✓</div>
        <div class="step-body">
          <div class="step-name">{{ s.name }}</div>
          <div class="step-bar"><div :style="{ width: Math.round((s.total ? s.done / s.total : 0) * 100) + '%' }"></div></div>
        </div>
        <div class="step-count">{{ s.done }} / {{ s.total }}</div>
      </div>
      <div ref="runLogsEl" class="logs runlogs">
        <div v-for="(l, i) in runLogs" :key="i" :class="l.cls">[{{ l.t }}] {{ l.text }}</div>
      </div>
      <div v-if="runErr" class="runerr">
        <span>{{ runErr }}</span>
        <button class="btn btn-ghost btn-sm" @click="resetAll">返回批次清单</button>
        <button class="btn btn-primary btn-sm" @click="startRun">重新执行</button>
      </div>
    </div>

    <!-- 3 结果 -->
    <div v-show="stage === 3">
      <div class="stats">
        <div class="stat"><div class="k">处理材料</div><div class="v">{{ stats.total }}<small>份</small></div></div>
        <div class="stat"><div class="k">完全成功</div><div class="v g">{{ stats.ok }}<small>份</small></div></div>
        <div class="stat"><div class="k">字段抽取</div><div class="v">{{ stats.fields }}<small>个</small></div></div>
        <div class="stat"><div class="k">可定位原文</div><div class="v g">{{ stats.locatable }}<small>个</small></div></div>
        <div class="stat"><div class="k">待人工复核</div><div class="v" :class="stats.pending ? 'a' : 'g'">{{ stats.pending }}<small>项</small></div></div>
      </div>

      <div class="card table-card">
        <div class="tabs">
          <div class="seg">
            <div class="seg-i" :class="{ on: curTab === 'ok' }" @click="curTab = 'ok'">抽取结果<span class="seg-n">{{ docs.length }}</span></div>
            <div class="seg-i" :class="{ on: curTab === 'issue' }" @click="curTab = 'issue'">异常与复核<span class="seg-n">{{ issues.length }}</span></div>
          </div>
        </div>
        <div class="tblwrap">
          <table v-if="curTab === 'ok'">
            <thead>
              <tr>
                <th style="width:34px">#</th><th style="min-width:220px">文件名称</th>
                <th>发文机关</th><th>适用地区</th><th style="min-width:200px">支持标准</th>
                <th>申报截止</th><th style="width:96px">置信度</th><th style="width:74px">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(d, i) in docs" :key="d.id" :class="{ failed: d.status === 'failed' }">
                <td class="muted">{{ String(i + 1).padStart(2, '0') }}</td>
                <td>
                  <span v-if="d.level" class="srctag" :class="{ loc: d.level === '地方' }">{{ d.level }}</span>
                  <a v-if="d.url" class="fname" :href="d.url" target="_blank" rel="noopener" :title="d.name">{{ d.name }}</a>
                  <span v-else class="fname" :title="d.name">{{ d.name }}</span>
                  <span v-if="d.real" class="real-tag">真实</span>
                </td>
                <template v-if="d.status === 'failed'">
                  <td class="muted">—</td><td class="muted">—</td><td class="muted">—</td><td class="muted">—</td>
                  <td class="muted">—</td>
                  <td><span class="pill failed">解析失败</span></td>
                </template>
                <template v-else>
                  <td v-for="k in TABLE_FIELDS" :key="k">
                    <span v-if="!aval(d, k)" class="muted">—</span>
                    <span
                      v-else class="cellv" :class="{ low: d.lowFields?.includes(k) }"
                      @click="openDrawer(d.id, k)"
                    >{{ aval(d, k) }}</span>
                    <span v-if="stateOf(d.id, k) === '确认'" class="cf">✓</span>
                    <span v-else-if="stateOf(d.id, k) === '修正'" class="cf rev">✎</span>
                  </td>
                  <td>
                    <div class="confbar">
                      <div class="conf-track"><div :style="{ width: confOf(d) * 100 + '%', background: confColor(confOf(d)) }"></div></div>
                      <span :style="{ color: confColor(confOf(d)) }">{{ confOf(d).toFixed(2) }}</span>
                    </div>
                  </td>
                  <td><span class="pill" :class="d.status">{{ d.status === 'ok' ? '成功' : '部分成功' }}</span></td>
                </template>
              </tr>
              <tr v-if="!docs.length"><td colspan="8" class="empty">当前批次还没有材料</td></tr>
            </tbody>
          </table>

          <table v-else>
            <thead>
              <tr>
                <th style="width:64px">编号</th><th style="min-width:200px">文件名称</th>
                <th style="width:96px">异常类型</th><th>说明</th><th style="width:80px">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in issues" :key="d.id">
                <td class="muted">{{ d.id }}</td>
                <td>
                  <span v-if="d.level" class="srctag" :class="{ loc: d.level === '地方' }">{{ d.level }}</span>
                  <a v-if="d.url" class="fname" :href="d.url" target="_blank" rel="noopener">{{ d.name }}</a>
                  <span v-else class="fname">{{ d.name }}</span>
                </td>
                <td>
                  <span
                    class="pill" :class="issueResolved(d) ? 'ok' : d.status === 'failed' ? 'failed' : 'partial'"
                  >{{ issueResolved(d) ? '已复核' : issueType(d) }}</span>
                </td>
                <td style="color:#5F6672;line-height:1.6">{{ d.issue || '' }}</td>
                <td><span class="cellv" @click="openIssue(d)">查看</span></td>
              </tr>
              <tr v-if="!issues.length"><td colspan="5" class="empty">本批次没有异常材料</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="actions">
        <button class="btn btn-primary" @click="exportCSV">导出 Excel（CSV）</button>
        <button class="btn btn-ghost" @click="exportJSON">导出 JSON</button>
        <button class="btn btn-ghost" @click="openAudit">审计记录{{ audit.length ? ` (${audit.length})` : '' }}</button>
        <button class="btn btn-ghost" @click="resetAll">回到批次清单</button>
      </div>
    </div>
  </main>

  <!-- 抽屉：原文定位 / 异常材料 / 审计记录 -->
  <SlideDrawer :open="drawerOpen" :title="drawerTitle" :subtitle="drawerSub" @close="drawerOpen = false">
    <template v-if="drawerMode === 'audit'">
      <div class="auditlist">
        <div v-if="!audit.length" class="empty-a">暂无操作记录。</div>
        <div v-for="(a, i) in [...audit].reverse()" :key="i" class="aud">
          <div class="tm">{{ a.time }}</div>
          <div class="bd">
            <div class="hd"><b>{{ a.action }}</b>　{{ a.fname }}</div>
            <div class="mt">{{ a.label }}　|　{{ a.user }}</div>
            <template v-if="a.action === '修正'">
              <div class="delta"><s>{{ a.old }}</s> → <b>{{ a.new }}</b></div>
              <div class="mt">原因：{{ a.reason }}</div>
            </template>
          </div>
        </div>
      </div>
    </template>

    <template v-else-if="curDoc">
      <div v-if="drawerMode === 'field'" class="fieldlist">
        <button
          v-for="f in FIELDS" :key="f.key" class="fbtn" :class="{ on: f.key === curField }"
          type="button" @click="openDrawer(curDoc.id, f.key)"
        >{{ f.label }}{{ aval(curDoc, f.key) ? '' : ' ·缺' }}</button>
      </div>

      <div class="fieldval">
        <div class="k">{{ drawerMode === 'raw' ? '处理结果' : fieldLabel }}</div>
        <div v-if="drawerMode === 'raw'" class="v none err">解析失败，未提取任何字段</div>
        <div v-else-if="curValue" class="v">{{ curValue }}</div>
        <div v-else class="v none">未提取到该字段</div>
        <div v-if="curState" class="state-tag" :class="{ rev: curState === '修正' }">{{ curState === '确认' ? '已确认' : '已修正' }}</div>
      </div>

      <div v-if="editing" class="editform">
        <div>
          <label>修正为</label>
          <input ref="edInput" v-model="edVal" type="text" placeholder="填写正确的字段值">
          <div class="oldv">原值：<b>{{ curValue || '（空）' }}</b></div>
        </div>
        <div>
          <label>修正原因</label>
          <textarea v-model="edReason" rows="2" placeholder="如：原文表述为区间值，按主条款取上限"></textarea>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary btn-sm" @click="saveEdit">保存并留痕</button>
          <button class="btn btn-ghost btn-sm" @click="editing = false">取消</button>
        </div>
      </div>

      <template v-else>
        <div class="paperlabel">{{ drawerMode === 'raw' ? '文件内容' : `原文 · ${curDoc.pageLabel || '第 1 页'}（片段）` }}</div>
        <div class="paper" v-html="paperHtml"></div>
        <div class="basis" v-html="basisHtml"></div>
      </template>
    </template>

    <template #footer>
      <template v-if="drawerMode === 'field' && !editing">
        <button class="btn btn-primary btn-sm" @click="confirmField">确认无误</button>
        <button class="btn btn-ghost btn-sm" @click="startEdit">修正</button>
      </template>
      <button v-else class="btn btn-ghost btn-sm" @click="drawerOpen = false">关闭</button>
    </template>
  </SlideDrawer>

  <!-- 粘贴全文 -->
  <div v-if="pasteOpen" class="modal on" @click.self="pasteOpen = false">
    <div class="modal-box">
      <div class="mb-h">粘贴原文解析</div>
      <div class="mb-b">
        <input id="pasteName" v-model="pasteName" type="text" placeholder="文件名称，例如：2026年广东省智能制造试点示范项目申报通知">
        <textarea v-model="pasteText" placeholder="在此粘贴公告全文"></textarea>
      </div>
      <div class="mb-f">
        <button class="btn btn-primary btn-sm" @click="doPaste">解析并加入</button>
        <button class="btn btn-ghost btn-sm" @click="pasteOpen = false">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stagebar {
  height: 46px; background: rgba(255, 255, 255, .9);
  backdrop-filter: saturate(180%) blur(12px);
  border-bottom: 1px solid var(--line); box-shadow: 0 1px 2px rgba(20, 29, 45, .03);
  display: flex; align-items: center; padding: 0 clamp(20px, 2.4vw, 40px);
  position: sticky; top: 62px; z-index: 9;
}
.sb-item { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--mute); }
.sb-item .dot {
  width: 20px; height: 20px; border-radius: 50%; border: 1px solid var(--line-strong);
  display: flex; align-items: center; justify-content: center; font-size: 10.5px; background: #fff; color: var(--mute);
}
.sb-item.on { color: var(--ink); }
.sb-item.on .dot { background: var(--accent); border-color: var(--accent); color: #fff; }
.sb-item.done { color: #4B5563; }
.sb-item.done .dot { background: #E8F0FB; border-color: #BBD3F3; color: var(--accent); }
.sb-sep { width: 44px; height: 1px; background: var(--line); margin: 0 14px; }

.wrap { width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 64px; }
.card { padding: 20px 22px; margin-bottom: 16px; }
.card-h { margin-bottom: 18px; padding: 0; border: none; }
.tpl-name { font-size: 17px; font-weight: 650; letter-spacing: -.1px; }
.meta-row { display: flex; gap: 32px; margin-top: 16px; padding-top: 16px; border-top: 1px dashed var(--line); flex-wrap: wrap; }
.meta-item .k { font-size: 11.5px; color: var(--mute); margin-bottom: 4px; }
.meta-item .v { font-size: 13.5px; font-weight: 500; }

.plan { display: flex; flex-direction: column; }
.plan-row { display: flex; align-items: flex-start; gap: 12px; padding: 12px 0; position: relative; }
.plan-row:not(:last-child)::after {
  content: ''; position: absolute; left: 11px; top: 36px; bottom: -2px; width: 1px; background: var(--line);
}
.plan-idx {
  width: 23px; height: 23px; border-radius: 50%; background: var(--accent-soft); color: var(--accent);
  display: flex; align-items: center; justify-content: center; font-size: 11.5px; flex: 0 0 auto;
  position: relative; z-index: 1; border: 1px solid var(--accent-line);
}
.plan-t { font-size: 13.5px; font-weight: 550; line-height: 23px; }

.files { display: flex; flex-wrap: wrap; gap: 8px; max-height: 118px; overflow: hidden; }
.fchip {
  font-size: 12px; padding: 5px 10px; border-radius: 6px;
  background: #F7F9FC; border: 1px solid var(--line); color: #4B5563;
  display: flex; align-items: center; gap: 6px;
}
.fchip i { width: 5px; height: 5px; border-radius: 50%; background: #22A06B; display: block; }
.files-empty { font-size: 12.5px; color: var(--mute); line-height: 1.9; padding: 6px 0; }

.govform { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.govform .inp { min-width: 230px; height: 38px; padding: 0 12px; font-size: 13.5px; }
.inp-sm { min-width: 150px; }
.govform .note { font-size: 11.5px; color: #8A9099; }
.govlog { margin-top: 10px; max-height: 180px; overflow: auto; }
.govlog .ln { font-size: 12px; color: #5F6672; line-height: 1.85; font-family: ui-monospace, Consolas, monospace; }
.govlog .ln.ok { color: #12805C; }
.govlog .ln.warn { color: var(--warn); }

.srcbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
.srcpill {
  display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 5px 12px;
  border-radius: 999px; background: #F7F9FC; color: #5F6672; border: 1px solid var(--line);
}
.srcpill b { color: var(--ink); font-weight: 600; }
.srcpill.real { background: #EAF3FE; border-color: #CFE2FB; color: #17518F; }
.srcpill.demo { background: #FFF8E8; border-color: #F3E3BC; color: #8A6A16; }
.dropzone {
  border: 1.5px dashed var(--line-strong); border-radius: 12px; padding: 28px 20px;
  text-align: center; cursor: pointer; background: #FCFDFE; transition: .16s;
}
.dropzone:hover, .dropzone.over { border-color: var(--accent); background: var(--accent-soft); }
.dz-ico { font-size: 24px; color: #9AA1AA; line-height: 1; margin-bottom: 10px; }
.dz-t { font-size: 13.5px; font-weight: 500; color: var(--ink); }
.dz-alt { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }

.actions { display: flex; gap: 10px; align-items: center; margin-top: 20px; flex-wrap: wrap; }

.runbox { margin: 32px 0 0; }
.run-sub { text-align: center; font-size: 13px; color: var(--mute); margin-bottom: 22px; }
.step { display: flex; gap: 14px; padding: 14px 0; align-items: flex-start; }
.step-ico {
  width: 28px; height: 28px; border-radius: 50%; flex: 0 0 auto;
  border: 1px solid #22A06B; background: var(--ok-soft); color: #22A06B;
  display: flex; align-items: center; justify-content: center; font-size: 12.5px;
}
.step-body { flex: 1; }
.step-name { font-size: 13px; font-weight: 500; }
.step-bar { height: 3px; background: #EDEFF2; border-radius: 2px; margin-top: 8px; overflow: hidden; }
.step-bar div { height: 100%; background: #22A06B; border-radius: 2px; transition: width .1s linear; }
.step-count { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: var(--mute); flex: 0 0 auto; padding-top: 3px; }
.runlogs { margin-top: 22px; height: 150px; font-size: 11.5px; }
.runlogs .ok { color: #6FD08C; }
.runlogs .warn { color: #E8B34B; }
.runlogs .err { color: #E97676; }
.runerr {
  margin-top: 12px; padding: 10px 12px; border-radius: 8px; background: #FEF2F2;
  border: 1px solid #FBD5D5; display: flex; align-items: center; gap: 10px;
  font-size: 12.5px; color: #B91C1C; flex-wrap: wrap;
}
.runerr span { flex: 1; line-height: 1.6; min-width: 200px; }

.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 18px; }
@media (max-width: 860px) { .stats { grid-template-columns: repeat(2, 1fr); } }
.stat { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; box-shadow: var(--shadow-card); }
.stat .k { font-size: 12px; color: var(--mute); margin-bottom: 6px; }
.stat .v { font-size: 26px; font-weight: 680; letter-spacing: -.5px; }
.stat .v small { font-size: 12px; font-weight: 400; color: var(--mute); margin-left: 3px; }
.stat .v.g { color: #22A06B; }
.stat .v.a { color: #D97706; }

.table-card { padding: 0; border: none; background: transparent; box-shadow: none; }
.tabs { display: flex; padding: 0 0 10px; }
.tblwrap {
  background: #fff; border: 1px solid var(--line); border-radius: var(--radius);
  overflow: auto; max-height: 520px; box-shadow: var(--shadow-card);
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  position: sticky; top: 0; background: #F8FAFC; text-align: left;
  font-weight: 600; color: var(--mute); font-size: 12px; padding: 10px 12px;
  border-bottom: 1px solid var(--line); white-space: nowrap; z-index: 2;
}
tbody td { padding: 10px 12px; border-bottom: 1px solid #F1F3F6; vertical-align: middle; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #FAFBFD; }
tbody tr.failed td { background: #FEF7F7; }
.fname { color: var(--ink); max-width: 230px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; text-decoration: none; }
a.fname:hover { color: var(--accent); }
.cellv { cursor: pointer; border-bottom: 1px dashed #D5DAE1; padding-bottom: 1px; }
.cellv:hover { color: var(--accent); border-bottom-color: var(--accent); }
.cellv.low { color: #D97706; border-bottom-color: #F0C27B; }
.muted { color: #C4C9D0; }
.confbar { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.conf-track { width: 34px; height: 3px; background: #EDEFF2; border-radius: 2px; overflow: hidden; }
.conf-track div { height: 100%; border-radius: 2px; }
.pill { font-size: 10.5px; padding: 2px 7px; border-radius: 10px; white-space: nowrap; }
.pill.ok { background: #E9F7F0; color: #17805A; }
.pill.partial { background: #FEF5E7; color: #B45309; }
.pill.failed { background: #FDEDED; color: #C0392B; }
.srctag {
  display: inline-block; font-size: 10.5px; padding: 0 5px; border-radius: 3px;
  margin-right: 5px; background: #EEF2FF; color: #3B5BDB; vertical-align: 1px;
}
.srctag.loc { background: #ECFDF3; color: #12805C; }
.real-tag {
  display: inline-block; font-size: 9.5px; padding: 0 4px; border-radius: 3px;
  background: #EAF3FE; color: var(--accent); border: 1px solid #CFE2FB;
  line-height: 14px; margin-left: 5px; vertical-align: 1px;
}
.cf {
  display: inline-block; margin-left: 5px; font-size: 10px; color: #17805A;
  background: #E9F7F0; border-radius: 3px; padding: 0 4px; vertical-align: 1px;
}
.cf.rev { color: #B45309; background: #FEF5E7; }

/* 抽屉内容 */
.fieldlist { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
.fbtn {
  font-size: 12px; padding: 5px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line); background: #fff; color: #4B5563;
}
.fbtn.on { border-color: var(--accent); background: #EAF2FC; color: var(--accent); font-weight: 500; }
.fieldval { background: #F8FAFC; border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; position: relative; }
.fieldval .k { font-size: 11.5px; color: var(--mute); margin-bottom: 6px; }
.fieldval .v { font-size: 15px; font-weight: 500; line-height: 1.5; }
.fieldval .v.none { color: #C4C9D0; font-weight: 400; font-size: 13px; }
.fieldval .v.err { color: #C0392B; }
.state-tag {
  position: absolute; right: 12px; top: 12px; font-size: 10px; padding: 1px 6px;
  border-radius: 3px; background: #E9F7F0; color: #17805A;
}
.state-tag.rev { background: #FEF5E7; color: #B45309; }
.paperlabel { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--mute); margin-bottom: 8px; }
.paperlabel::after { content: ''; flex: 1; height: 1px; background: #EDEFF2; }
.paper {
  background: #FCFCFA; border: 1px solid #E8E8E3; border-radius: 10px;
  padding: 24px 28px; font-family: "Songti SC", "SimSun", "STSong", serif;
  font-size: 13.5px; line-height: 2.05; color: #2A2A28; white-space: pre-wrap; letter-spacing: .3px;
}
.paper :deep(mark) { background: #FDF0A8; color: inherit; padding: 1px 2px; border-radius: 2px; }
.paper :deep(.failed-text) { color: #B0B0AA; font-style: italic; }
.basis { margin-top: 14px; font-size: 12px; color: #6B7280; line-height: 1.8; }
.basis :deep(b) { color: #374151; font-weight: 500; }
.editform { display: flex; flex-direction: column; gap: 12px; }
.editform label { font-size: 11.5px; color: #6B7280; display: block; margin-bottom: 6px; }
.editform input, .editform textarea {
  width: 100%; border: 1px solid #DDE1E7; border-radius: 7px; padding: 8px 10px;
  font-size: 13px; color: var(--ink); background: #fff; resize: vertical; line-height: 1.6;
}
.editform input:focus, .editform textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(26, 109, 213, .1); }
.editform .oldv { font-size: 12px; color: var(--mute); margin-top: 7px; }
.editform .oldv b { color: #6B7280; font-weight: 500; text-decoration: line-through; }
.auditlist { display: flex; flex-direction: column; }
.aud { display: flex; gap: 12px; padding: 13px 0; border-bottom: 1px solid #F1F3F6; }
.aud:last-child { border-bottom: none; }
.aud .tm { font-family: ui-monospace, Consolas, monospace; font-size: 10.5px; color: var(--mute); flex: 0 0 88px; line-height: 1.8; padding-top: 1px; }
.aud .bd { flex: 1; font-size: 12.5px; line-height: 1.75; min-width: 0; }
.aud .hd { color: var(--ink); word-break: break-all; }
.aud .mt { color: var(--mute); font-size: 11.5px; margin-top: 2px; }
.aud .delta { color: #B45309; font-size: 12px; margin-top: 3px; word-break: break-all; }
.aud .delta s { color: var(--mute); }
.empty-a { padding: 44px 10px; text-align: center; color: var(--mute); font-size: 12.5px; line-height: 1.9; }

.modal {
  position: fixed; inset: 0; background: rgba(16, 24, 40, .42);
  display: flex; align-items: center; justify-content: center; z-index: 60;
}
.modal-box { width: 620px; max-width: 92vw; background: #fff; border-radius: var(--radius); box-shadow: var(--shadow-pop); overflow: hidden; }
.mb-h { padding: 16px 20px; border-bottom: 1px solid var(--line); font-size: 14.5px; font-weight: 650; display: flex; align-items: center; }
.mb-b { padding: 18px 20px; }
.mb-b input[type=text], .mb-b textarea {
  width: 100%; border: 1px solid #DEE2E7; border-radius: 8px; padding: 9px 11px;
  font-size: 12.5px; color: var(--ink); outline: none; background: #fff;
}
.mb-b input[type=text]:focus, .mb-b textarea:focus { border-color: var(--accent); }
.mb-b textarea { height: 220px; resize: vertical; line-height: 1.7; margin-top: 10px; }
.mb-f { padding: 12px 20px; border-top: 1px solid var(--line); display: flex; gap: 10px; align-items: center; }

@media (max-width: 1180px) {
  .stagebar { position: static; }
}
@media (max-width: 640px) {
  .wrap { padding: 20px 16px 48px; }
  .card { padding: 16px 18px; }
  .meta-row { gap: 18px 24px; }
}
</style>