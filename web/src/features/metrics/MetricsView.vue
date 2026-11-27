<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from '../../shared/components/AppHeader.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import { esc } from '../../shared/lib/format'
import { readRaw, writeRaw } from '../../shared/lib/storage'

interface Indicator {
  k: string
  name: string
  val: number | string
  unit: string
  digits?: number
  yoy: number | null
  period: string
  caliber: string
  formula: string
  src: string
  freq: string
  owner: string
  trend: number[]
  trend_label: string
  events?: string[]
  lineage?: [string, string][]
  conf?: string
}

interface EventItem {
  t: string
  d?: string
  kind?: string
  src?: string
}

interface MetricsResp {
  company: string
  entity?: { code?: string; market?: string }
  indicators?: Indicator[]
  events?: Record<string, EventItem>
  pending?: string[]
  gaps?: string[]
  structure?: Structure
}

interface TopicFact {
  id: string
  text: string
  tag?: string
  src: string
  src_title?: string
  url?: string
}

interface TopicJudge {
  text: string
  basis: string
  conf: string
  note?: string
}

interface Topic {
  topic_id?: string
  template?: string
  template_reason?: string
  period?: string
  question?: string
  refs?: { indicators?: number; facts?: number }
  trace?: { total?: number; with_url?: number }
  confidence?: string
  facts?: TopicFact[]
  judgments?: TopicJudge[]
  advice?: string[]
  pending?: string[]
}

interface StructureRow {
  name: string
  income: number | null
  ratio: number | null
  gm: number | null
}

interface Structure {
  latest?: Record<string, StructureRow[]>
  prev?: Record<string, StructureRow[]>
  latest_date?: string
  prev_date?: string
}

interface QRow {
  period: string
  value: number
  unit: string
  yoy: number | null
}

interface LastQ {
  m: Indicator
  rows: QRow[]
}

const route = useRoute()

const company = ref('')
const searched = ref(false)
const loadErr = ref('')
const entity = ref<{ code?: string; market?: string }>({})
const metrics = ref<Indicator[]>([])
const events = ref<Record<string, EventItem>>({})
const topic = ref<Topic | null>(null)
const gaps = ref<string[]>([])
const structure = ref<Structure>({})
const loading = ref(false)

const cur = ref('')
const pane = ref<'cal' | 'ev' | 'ln'>('cal')
const sKind = ref<'prod' | 'reg'>('prod')

const focusIds = ref<string[]>([])
const extraFacts = ref<{ id: string; html: string; src: string }[]>([])

const qMetric = ref('')
const qPeriods = ref('4')
const qBusy = ref(false)
const lastQ = ref<LastQ | null>(null)
const queryEl = ref<HTMLElement | null>(null)

/* ---------- 取数 ---------- */
async function loadCompany(name?: string, force = false): Promise<void> {
  const n = (name ?? company.value).trim()
  if (!n) {
    toast('请输入公司名称')
    return
  }
  loading.value = true
  searched.value = true
  loadErr.value = ''
  metrics.value = []
  topic.value = null
  events.value = {}
  gaps.value = []
  structure.value = {}
  extraFacts.value = []
  focusIds.value = []
  lastQ.value = null
  try {
    const [d, t] = await Promise.all([
      api.get<MetricsResp>('/api/metric-insight/overview', { company: n, refresh: force ? 1 : undefined }),
      // 专题生成失败不应拖垮整页：指标照常展示
      api.get<Topic>('/api/metric-insight/topic', { company: n, refresh: force ? 1 : undefined }).catch(() => null),
    ])
    company.value = d.company || n
    entity.value = d.entity || {}
    metrics.value = d.indicators || []
    events.value = d.events || {}
    gaps.value = d.gaps || []
    structure.value = d.structure || {}
    topic.value = t
    qMetric.value = metrics.value[0]?.k || ''
    cur.value = metrics.value[0]?.k || ''
    writeRaw('mj_metrics_company', company.value)
    if (location.hash === '#query') {
      nextTick(() => queryEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
      setTimeout(runQuery, 300)
    }
  } catch (e) {
    loadErr.value = (e as Error).message
    toast('取数失败：' + (e as Error).message)
  } finally {
    loading.value = false
  }
}

const entityText = computed(() => {
  const name = company.value.trim()
  if (!name) return ''
  const e = entity.value
  const id = (e.code ? e.code + ' · ' : '') + (e.market || '')
  return name + ' · ' + (id || '非上市/未解析')
})

/* ---------- 专题头 ---------- */
const topicHead = computed(() => {
  const t = topic.value
  if (!t) {
    return {
      id: '专题 —',
      tpl: '研究类型：—',
      tplTitle: '',
      q: '—',
      ind: '—', fact: '—', trace: '—', conf: '—',
    }
  }
  const tr = t.trace || {}
  return {
    id: '专题 ' + (t.topic_id || '—'),
    tpl: '研究类型：' + (t.template || '专题研究') + '｜期间 ' + (t.period || '—'),
    tplTitle: t.template_reason || '',
    q: t.question || '',
    ind: String(t.refs?.indicators || 0),
    fact: String(t.refs?.facts || 0),
    trace: tr.total ? `${tr.with_url}/${tr.total}` : '—',
    conf: t.confidence || '—',
  }
})

const gapItems = computed(() => [
  ...(topic.value?.pending || []),
  ...gaps.value,
])

/* ---------- 指标格式化 ---------- */
function fmtNum(v: number | string, digits = 2): string {
  return typeof v === 'number' ? v.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : String(v)
}

function fmtVal(m: Indicator): string {
  return fmtNum(m.val, m.digits ?? 2) + (m.unit || '')
}

function yoyText(m: Indicator): string {
  if (m.yoy === null || m.yoy === undefined) return '同比不适用'
  return (m.yoy > 0 ? '同比 +' : '同比 ') + m.yoy + (m.unit === '%' ? ' 个百分点' : '%')
}

function deltaCls(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return 'flat'
  return v > 0 ? 'up' : 'down'
}

function sparkPoints(data?: number[]): string {
  if (!data || data.length < 2) return ''
  const w = 48
  const h = 18
  const pad = 1
  const min = Math.min(...data)
  const max = Math.max(...data)
  const span = (max - min) || 1
  return data.map((v, i) => {
    const x = pad + i * (w - pad * 2) / (data.length - 1)
    const y = h - pad - ((v - min) / span) * (h - pad * 2)
    return x.toFixed(1) + ',' + y.toFixed(1)
  }).join(' ')
}

function sparkColor(m: Indicator): string {
  return (m.yoy || 0) >= 0 ? '#D93025' : '#0E9F6E'
}

const curMetric = computed(() => metrics.value.find((m) => m.k === cur.value) || null)

const curEvents = computed(() => {
  const m = curMetric.value
  if (!m) return []
  return (m.events || [])
    .map((id) => ({ id, ...(events.value[id] || { t: '' }) }))
    .filter((e) => e.t)
})

function selectMetric(k: string): void {
  cur.value = k
}

function onFactsClick(e: MouseEvent): void {
  const el = (e.target as HTMLElement).closest('.mref') as HTMLElement | null
  const k = el?.dataset.k
  if (!k) return
  selectMetric(k)
  toast('已定位指标：' + (metrics.value.find((m) => m.k === k)?.name || k))
}

function focusBasis(basis: string): void {
  focusIds.value = String(basis).split(/[\/、,，\s]+/).filter(Boolean)
  toast('依据链：' + basis)
}

function factFocus(id: string): boolean {
  return focusIds.value.includes(id)
}

/* ---------- 产业结构 ---------- */
const structNote = computed(() => {
  if (!structRows.value.length) return '无数据'
  return `期间 ${structure.value.latest_date || '—'}`
})

const structRows = computed(() => {
  const key = sKind.value === 'reg' ? '按地区' : '按产品'
  const lat = (structure.value.latest || {})[key] || []
  const prevMap: Record<string, StructureRow> = {}
  ;((structure.value.prev || {})[key] || []).forEach((r) => { prevMap[r.name] = r })
  return lat.slice()
    .sort((a, b) => (b.ratio || 0) - (a.ratio || 0))
    .map((r) => {
      const p = prevMap[r.name]
      const dgm = r.gm !== null && p && p.gm !== null && p.gm !== undefined
        ? r.gm - (p.gm as number) : null
      const dr = r.ratio !== null && p && p.ratio !== null && p.ratio !== undefined
        ? r.ratio - (p.ratio as number) : null
      return { name: r.name, income: r.income, ratio: r.ratio, gm: r.gm, dgm, dr }
    })
})

function num(v: number | null | undefined, d = 2): string {
  return v === null || v === undefined ? '—' : v.toLocaleString('zh-CN', { maximumFractionDigits: d })
}

function pct(v: number | null): string {
  return v === null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + ' 个百分点'
}

/* ---------- 场景化取数 ---------- */
async function runQuery(): Promise<void> {
  const m = metrics.value.find((x) => x.k === qMetric.value)
  if (!m) {
    toast('先选择指标，再取数')
    return
  }
  qBusy.value = true
  try {
    const d = await api.post<{ ok: boolean; rows?: QRow[]; note?: string }>('/api/metric-insight/query', {
      company: company.value.trim(),
      metric: m.k,
      periods: Number(qPeriods.value) || 4,
    })
    lastQ.value = { m, rows: d.rows || [] }
    toast(`已取数：${m.name} · ${(d.rows || []).length} 期`)
  } catch (e) {
    toast('取数失败：' + (e as Error).message)
  } finally {
    qBusy.value = false
  }
}

function addToReport(): void {
  const lq = lastQ.value
  if (!lq) {
    toast('请先取数，再并入报告')
    return
  }
  if (!lq.rows.length) {
    toast('没有可并入的报告期')
    return
  }
  const { m, rows } = lq
  const id = 'Q' + String(extraFacts.value.length + 1).padStart(2, '0')
  const first = rows[rows.length - 1]
  const last = rows[0]
  const html = `<b class="mref" data-k="${esc(m.k)}">${esc(first.period)}</b> 至 `
    + `<b class="mref" data-k="${esc(m.k)}">${esc(last.period)}</b>，`
    + `${esc(m.name)}由 ${esc(fmtNum(first.value))}${esc(m.unit)} 变为 ${esc(fmtNum(last.value))}${esc(m.unit)}，`
    + `共 ${rows.length} 期；口径与主表一致。`
  extraFacts.value.push({ id, html, src: m.src })
  toast(`已作为 ${id} 并入「一、事实」`)
}

onMounted(() => {
  const c = route.query.c
  if (c) company.value = String(c)
  else company.value = readRaw('mj_metrics_company')
  if (company.value.trim()) loadCompany()
})
</script>

<template>
  <AppHeader :task="entityText" />

  <div class="wrap">
    <!-- 研究主体 -->
    <section class="card qbar">
      <div class="k">研究主体</div>
      <div class="qrow">
        <input
          v-model="company" type="text" class="company-input"
          placeholder="输入公司名"
          @keydown.enter="loadCompany()"
        >
        <button class="btn btn-primary" :disabled="loading || !company.trim()" @click="loadCompany()">
          <span v-if="loading" class="spin"></span>生成专题
        </button>
        <button class="btn btn-ghost" :disabled="loading || !company.trim()" @click="loadCompany(company, true)">
          刷新数据
        </button>
      </div>
    </section>

    <!-- 取数失败 -->
    <section v-if="loadErr && !loading" class="card err-start">
      <h2>没有取到「{{ company }}」的数据</h2>
      <p class="lead">{{ loadErr }}</p>
      <button class="btn btn-primary" @click="loadCompany()">重试</button>
    </section>

    <!-- 专题头 -->
    <section v-if="(searched || loading) && !loadErr" class="card topic-head">
      <div class="th-l">
        <div class="th-row">
          <span class="th-id">{{ topicHead.id }}</span>
          <span class="th-tpl" :title="topicHead.tplTitle">{{ topicHead.tpl }}</span>
        </div>
        <div class="th-q">{{ topicHead.q }}</div>
      </div>
      <div class="th-r">
        <div class="th-stat"><b>{{ topicHead.ind }}</b><span>引用指标</span></div>
        <div class="th-stat"><b>{{ topicHead.fact }}</b><span>引用事实</span></div>
        <div class="th-stat"><b>{{ topicHead.trace }}</b><span>可点开原文</span></div>
        <div class="th-stat"><b>{{ topicHead.conf }}</b><span>结论置信度</span></div>
      </div>
    </section>

    <div v-if="(searched || loading) && !loadErr" class="grid3">
      <!-- 指标列 -->
      <section class="card">
        <div class="sec-h2">
          <span class="bar" style="background: #1A6DD5"></span>经营指标
          <span class="n">{{ metrics.length ? metrics.length + ' 项' : '—' }}</span>
        </div>
        <div v-if="loading" class="empty">正在取公开财报与主营构成…</div>
        <div v-else-if="!metrics.length" class="empty">暂无公开财报指标</div>
        <div class="m-list">
          <div
            v-for="m in metrics" :key="m.k" class="m-item"
            :class="{ on: cur === m.k }" title="点击查看口径、来源与关联情报"
            @click="selectMetric(m.k)"
          >
            <div class="m-top">
              <div class="m-name">{{ m.name }}</div>
              <div class="m-val">{{ fmtNum(m.val, m.digits ?? 2) }}<small>{{ m.unit }}</small></div>
            </div>
            <div class="m-sub">
              <span :class="deltaCls(m.yoy)">{{ yoyText(m) }}</span>
            </div>
            <svg v-if="(m.trend || []).length >= 2" class="m-spark" width="48" height="18" viewBox="0 0 48 18">
              <polyline
                :points="sparkPoints(m.trend)" fill="none" :stroke="sparkColor(m)"
                stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"
              />
            </svg>
          </div>
        </div>
      </section>

      <!-- 专题报告 -->
      <section class="card">
        <div class="sec-h2">
          <span class="bar" style="background: #E0A400"></span>专题报告
        </div>
        <div class="pane-body">
          <h4 class="ph">一、事实</h4>
          <div @click="onFactsClick">
            <div v-if="loading" class="fact">正在生成专题…</div>
            <template v-else>
              <div
                v-for="f in topic?.facts || []" :key="f.id" class="fact"
                :class="{ focus: factFocus(f.id) }" :id="'f-' + f.id"
              >
                <span class="idx">{{ f.id }}</span>{{ f.text }}
                <span v-if="f.tag" class="ftag">{{ f.tag }}</span>
                <a
                  v-if="f.url" class="src src-a" :href="f.url" target="_blank" rel="noopener"
                  :title="'打开原文：' + (f.src_title || f.src)"
                >{{ f.src }} ↗</a>
                <span v-else class="src src-none" title="该来源未定位到原文，需人工核对">{{ f.src }}（无原文链接）</span>
              </div>
              <div v-for="f in extraFacts" :key="f.id" class="fact">
                <span class="idx">{{ f.id }}</span><span v-html="f.html"></span>
                <span class="ftag">取数并入</span>
                <span class="src src-none" :title="f.src">{{ f.src }}</span>
              </div>
              <div v-if="!((topic?.facts || []).length || extraFacts.length)" class="fact">暂无事实条目。</div>
            </template>
          </div>

          <h4 class="ph">二、判断</h4>
          <div v-for="(j, i) in topic?.judgments || []" :key="i" class="judge">
            <span class="idx2">J{{ i + 1 }}</span>{{ j.text }}
            <span class="conf" :class="j.conf === '中' ? 'mid' : j.conf === '低' ? 'low' : ''">置信度 {{ j.conf }}</span>
            <span class="src" @click="focusBasis(j.basis)">依据 {{ j.basis }}</span>
            <div v-if="j.note" class="jnote">{{ j.note }}</div>
          </div>
          <div v-if="!loading && !(topic?.judgments || []).length" class="fact">本期没有可下判断的内容。</div>

          <h4 class="ph">三、建议</h4>
          <ul v-if="topic?.advice?.length" class="advice-list">
            <li v-for="(a, i) in topic.advice" :key="i">{{ a }}</li>
          </ul>
          <div v-else class="fact">本期不给建议。</div>

          <h4 class="ph">四、待验证与缺口</h4>
          <ul v-if="gapItems.length" class="open-q">
            <li v-for="(p, i) in gapItems" :key="i">{{ p }}</li>
          </ul>
          <div v-else class="fact">无</div>
        </div>
      </section>

      <!-- 右侧：口径 / 关联情报 / 血缘 -->
      <section class="card">
        <div class="seg fill ptabs">
          <div class="seg-i" :class="{ on: pane === 'cal' }" @click="pane = 'cal'">口径定义</div>
          <div class="seg-i" :class="{ on: pane === 'ev' }" @click="pane = 'ev'">关联情报</div>
          <div class="seg-i" :class="{ on: pane === 'ln' }" @click="pane = 'ln'">数据血缘</div>
        </div>
        <div v-if="!curMetric" class="empty">未选择指标</div>
        <template v-else>
          <div v-show="pane === 'cal'" class="pane-body">
            <div class="m-title">
              {{ curMetric.name }}
              <span class="m-title-sub">最新 {{ fmtVal(curMetric) }}（{{ curMetric.period }}）</span>
            </div>
            <div class="kv"><div class="k">口径定义</div><div class="v">{{ curMetric.caliber }}</div></div>
            <div class="kv"><div class="k">计算式</div><div class="v"><code>{{ curMetric.formula }}</code></div></div>
            <div class="kv"><div class="k">数据来源</div><div class="v">{{ curMetric.src }}</div></div>
            <div class="kv"><div class="k">更新频率</div><div class="v">{{ curMetric.freq }}</div></div>
            <div class="kv"><div class="k">责任人</div><div class="v">{{ curMetric.owner }}</div></div>
            <div class="kv">
              <div class="k">趋势口径</div>
              <div class="v">{{ curMetric.trend_label }}</div>
            </div>
          </div>

          <div v-show="pane === 'ev'" class="pane-body">
            <div v-for="e in curEvents" :key="e.id" class="ev">
              <div class="ev-t">{{ e.t }}</div>
              <div class="ev-m">{{ e.d }}　·　{{ e.kind }}　·　{{ e.src }}</div>
              <div class="ev-l">{{ e.id }}</div>
            </div>
            <div v-if="!curEvents.length" class="empty">本期无关联事件</div>
          </div>

          <div v-show="pane === 'ln'" class="pane-body">
            <div v-for="(l, i) in curMetric.lineage || []" :key="i">
              <div class="ln-row">
                <span class="ln-i">{{ i + 1 }}</span>
                <span class="ln-n">{{ l[0] }}</span>
                <span class="ln-s">{{ l[1] }}</span>
              </div>
              <div v-if="i < (curMetric.lineage || []).length - 1" class="ln-arrow">↓</div>
            </div>
          </div>
        </template>
      </section>
    </div>

    <!-- 产业结构 -->
    <section v-if="(searched || loading) && !loadErr" class="card section-gap">
      <div class="sec-h2">
        <span class="bar" style="background: #7C4DFF"></span>产业结构
        <span class="n">{{ structNote }}</span>
      </div>
      <div class="pane-body">
        <div class="seg ptabs inline">
          <div class="seg-i" :class="{ on: sKind === 'prod' }" @click="sKind = 'prod'">按产品</div>
          <div class="seg-i" :class="{ on: sKind === 'reg' }" @click="sKind = 'reg'">按地区</div>
        </div>
        <div class="st-wrap">
          <div v-if="!structRows.length" class="fact">未取到主营业务构成</div>
          <table v-else>
            <thead>
              <tr>
                <th style="min-width: 160px">{{ sKind === 'reg' ? '地区' : '产品' }}</th>
                <th style="width: 110px">收入（亿元）</th>
                <th style="width: 180px">收入占比</th>
                <th style="width: 100px">毛利率</th>
                <th style="width: 140px">毛利率同比</th>
                <th style="width: 140px">占比同比</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in structRows" :key="r.name">
                <td class="st-name">{{ r.name }}</td>
                <td>{{ num(r.income) }}</td>
                <td>
                  <div class="st-seg">
                    <div class="st-bar"><i :style="{ width: Math.min(100, r.ratio || 0) + '%' }"></i></div>
                    <span>{{ num(r.ratio) }}%</span>
                  </div>
                </td>
                <td>{{ r.gm === null ? '—' : num(r.gm) + '%' }}</td>
                <td :class="deltaCls(r.dgm)">{{ pct(r.dgm) }}</td>
                <td :class="deltaCls(r.dr)">{{ pct(r.dr) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- 场景化取数 -->
    <section v-if="(searched || loading) && !loadErr" ref="queryEl" class="card section-gap">
      <div class="sec-h2">
        <span class="bar" style="background: #0E9F6E"></span>场景化取数
      </div>
      <div class="pane-body">
        <div class="qform">
          <div class="field">
            <label>指标</label>
            <select v-model="qMetric">
              <option v-for="m in metrics" :key="m.k" :value="m.k">{{ m.name }}</option>
            </select>
          </div>
          <div class="field">
            <label>取最近</label>
            <select v-model="qPeriods">
              <option value="4">4 个报告期</option>
              <option value="6">6 个报告期</option>
              <option value="8">8 个报告期</option>
            </select>
          </div>
          <button class="btn btn-primary" :disabled="qBusy || !metrics.length" @click="runQuery">
            <span v-if="qBusy" class="spin"></span>取数
          </button>
          <button class="btn" :disabled="!metrics.length" @click="addToReport">取数结果并入报告</button>
        </div>
        <div v-if="lastQ" class="qres">
          <table>
            <thead>
              <tr>
                <th>报告期</th>
                <th style="text-align: right">数值</th>
                <th style="text-align: right">同比</th>
                <th>口径与来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in lastQ.rows" :key="row.period">
                <td>{{ row.period }}</td>
                <td class="qnum">{{ row.value.toLocaleString('zh-CN', { maximumFractionDigits: lastQ.m.digits ?? 2 }) }}{{ row.unit }}</td>
                <td class="qnum" :class="deltaCls(row.yoy)">
                  {{ row.yoy === null ? '—' : (row.yoy > 0 ? '+' : '') + row.yoy + '%' }}
                </td>
                <td class="qsrc">{{ lastQ.m.caliber }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.wrap { width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 72px; }
.section-gap { margin-top: 18px; }

/* 研究主体 */
.qbar { padding: 18px 20px; margin-bottom: 16px; }
.qbar .k { font-size: 11.5px; color: var(--mute); margin-bottom: 8px; letter-spacing: .05em; }
.qrow { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

/* 取数失败 */
.err-start { padding: 24px 26px; margin-top: 14px; }
.err-start h2 { margin: 0 0 10px; font-size: 15px; color: var(--err); }
.err-start .lead { margin: 0 0 16px; font-size: 12.5px; color: var(--sub); line-height: 1.9; max-width: 760px; }
.company-input { min-width: 300px; }

/* 专题头 */
.topic-head { display: flex; gap: 22px; align-items: flex-start; justify-content: space-between; padding: 20px 22px; margin-bottom: 16px; }
.th-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.th-id {
  font-size: 11.5px; padding: 3px 9px; border-radius: 5px;
  background: var(--accent-soft); color: var(--accent); font-family: var(--mono);
}
.th-tpl { font-size: 12px; color: var(--mute); }
.th-q { font-size: 17px; font-weight: 650; line-height: 1.65; margin-top: 10px; max-width: 820px; letter-spacing: -.15px; }
.th-r { display: flex; gap: 30px; flex: none; padding-top: 4px; }
.th-stat { text-align: center; }
.th-stat b { display: block; font-size: 22px; font-weight: 680; line-height: 1.3; letter-spacing: -.3px; }
.th-stat span { font-size: 11.5px; color: var(--mute); }
@media (max-width: 980px) { .topic-head { flex-direction: column; } }

/* 三栏 */
.grid3 { display: grid; grid-template-columns: 296px minmax(0, 1fr) 360px; gap: 16px; align-items: start; }
@media (max-width: 1180px) { .grid3 { grid-template-columns: 1fr; } }

.sec-h2 {
  display: flex; align-items: center; gap: 9px; padding: 13px 18px;
  border-bottom: 1px solid var(--line); font-size: 13px; font-weight: 650;
}
.sec-h2 .bar { width: 3px; height: 14px; border-radius: 2px; flex: none; }
.sec-h2 .n { font-weight: 400; color: var(--mute); font-size: 11.5px; margin-left: auto; text-align: right; }
.pane-body { padding: 16px 18px 18px; }

/* 指标列表 */
.m-list { padding: 4px 0 6px; }
.m-item {
  padding: 12px 16px 14px; border-bottom: 1px solid #F1F3F6; cursor: pointer;
  transition: .14s; position: relative;
}
.m-item:last-child { border-bottom: none; }
.m-item:hover { background: #FAFBFC; }
.m-item.on { background: #F5F9FF; }
.m-item.on::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--accent); }
.m-top { display: flex; align-items: baseline; gap: 6px; }
.m-name { font-size: 12.5px; color: #4A5058; flex: 1; }
.m-val { font-size: 17px; font-weight: 680; letter-spacing: -.3px; }
.m-val small { font-size: 10.5px; font-weight: 400; color: var(--mute); margin-left: 1px; }
.m-sub { display: flex; gap: 10px; margin-top: 4px; font-size: 11px; padding-right: 56px; }
.up { color: #D93025; }
.down { color: #0E9F6E; }
.m-spark { position: absolute; right: 12px; bottom: 10px; opacity: .5; }
.m-item.on .m-spark { opacity: 1; }

/* 专题报告 */
.ph { font-size: 13.5px; font-weight: 650; margin: 20px 0 10px; }
.ph:first-child { margin-top: 2px; }
.fact, .judge {
  position: relative; padding: 10px 14px 10px 36px; border-radius: 8px;
  margin-bottom: 8px; line-height: 1.85; font-size: 13px;
}
.fact { background: #F8FAFC; }
.judge { background: #FFFBF0; }
.fact.focus { box-shadow: inset 0 0 0 1.5px #E0A400; }
.fact .idx, .judge .idx2 {
  position: absolute; left: 11px; top: 11px; font-size: 10px; font-weight: 650;
  width: 17px; height: 17px; border-radius: 4px; display: flex; align-items: center; justify-content: center;
}
.fact .idx { background: #E8F0FA; color: var(--accent); }
.judge .idx2 { background: #FBEFD2; color: #8A6A16; }
.src {
  display: inline-flex; align-items: center; gap: 4px; font-size: 10.5px; color: #6B7280;
  background: #fff; border: 1px solid var(--line); border-radius: 4px; padding: 1px 6px;
  margin-left: 4px; vertical-align: 1px; cursor: pointer; white-space: nowrap;
}
.src:hover { border-color: var(--accent); color: var(--accent); }
a.src-a { text-decoration: none; }
a.src-a:hover { background: #F2F7FE; }
.src-none { color: #9A8B6E; border-style: dashed; cursor: help; }
.src-none:hover { border-color: #C9B98E; color: #9A8B6E; }
/* v-html 里拼出来的取数事实不在 scoped 作用域内，用 :deep 命中 */
:deep(.mref) {
  color: var(--accent); border-bottom: 1px dashed #9FC2EA; cursor: pointer;
  font-variant-numeric: tabular-nums; font-weight: 600;
}
.conf {
  font-size: 10.5px; padding: 1px 6px; border-radius: 4px; margin-left: 6px;
  background: #F1F3F6; color: #6B7280; vertical-align: 1px; white-space: nowrap;
}
.conf.mid { background: #FFF4E0; color: #8A6A16; }
.conf.low { background: #FDECEA; color: #B3261E; }
.ftag {
  display: inline-block; font-size: 10px; padding: 0 5px; border-radius: 3px;
  background: #F1F3F6; color: #6B7280; margin-left: 6px; vertical-align: 1px;
}
.jnote { font-size: 11px; color: var(--mute); margin-top: 4px; line-height: 1.7; }
.advice-list { margin: 0; padding-left: 18px; }
.advice-list li { font-size: 12.5px; line-height: 1.95; color: #33383F; margin-bottom: 7px; }
.advice-list li::marker { color: #0E9F6E; }
.open-q { margin: 0; padding-left: 18px; font-size: 12px; color: #5F6672; line-height: 1.9; }
.open-q li { margin-bottom: 6px; }
.open-q li::marker { color: var(--mute); }

/* 右侧面板 */
.ptabs { margin: 8px 10px 6px; }
.ptabs .seg-i { font-size: 12.5px; padding: 6px 0; }
.ptabs.inline { width: fit-content; margin: 0 0 12px; }
.ptabs.inline .seg-i { flex: none; padding: 5px 12px; }
.m-title { font-size: 13.5px; font-weight: 650; margin-bottom: 12px; }
.m-title-sub { font-weight: 400; color: var(--mute); font-size: 11px; margin-left: 6px; }
.kv { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px dashed #F1F3F6; font-size: 12.5px; line-height: 1.75; }
.kv:last-of-type { border-bottom: none; }
.kv .k { flex: 0 0 64px; color: var(--mute); }
.kv .v { flex: 1; color: var(--ink); word-break: break-word; }
.kv .v code {
  font-family: ui-monospace, Consolas, monospace; font-size: 11.5px;
  background: #F6F7F9; padding: 1px 5px; border-radius: 3px;
}
.ev { padding: 9px 11px; border: 1px solid #F1F3F6; border-radius: 7px; margin-bottom: 8px; position: relative; }
.ev-t { font-size: 12.5px; line-height: 1.75; padding-right: 34px; }
.ev-m { font-size: 11px; color: var(--mute); margin-top: 4px; }
.ev-l { position: absolute; right: 9px; top: 9px; font-size: 10px; color: #B0B7C3; }
.ln-row { display: flex; align-items: center; gap: 8px; padding: 7px 0; font-size: 12px; }
.ln-i {
  width: 18px; height: 18px; border-radius: 50%; background: #EFF3FF; color: var(--accent);
  font-size: 10.5px; display: grid; place-items: center; flex: none;
}
.ln-n { flex: 1; }
.ln-s { color: var(--mute); font-size: 11px; }
.ln-arrow { color: #C4CAD2; font-size: 11px; padding-left: 5px; line-height: 1; }

/* 产业结构 */
.st-wrap { overflow-x: auto; }
.st-wrap table { width: 100%; border-collapse: collapse; font-size: 13px; }
.st-wrap th {
  text-align: left; font-weight: 600; color: var(--mute); font-size: 12px;
  padding: 9px 12px; background: #F8FAFC; border-bottom: 1px solid var(--line); white-space: nowrap;
}
.st-wrap td { padding: 10px 12px; border-bottom: 1px solid #F1F3F6; }
.st-wrap tr:last-child td { border-bottom: none; }
.st-name { font-weight: 550; }
.st-seg { display: flex; align-items: center; gap: 8px; }
.st-bar { height: 5px; border-radius: 3px; background: #EEF2FF; overflow: hidden; width: 90px; flex: none; }
.st-bar i { display: block; height: 100%; background: #4C6FFF; }

/* 场景化取数 */
.qform { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field label { font-size: 11px; color: var(--mute); }
.field select { min-width: 148px; }
.qres { margin-top: 16px; }
.qres table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.qres th {
  text-align: left; font-weight: 600; color: var(--mute); font-size: 11.5px;
  padding: 9px 12px; border-bottom: 1px solid var(--line); background: #F8FAFC; white-space: nowrap;
}
.qres td { padding: 10px 12px; border-bottom: 1px solid #F1F3F6; font-variant-numeric: tabular-nums; }

@media (max-width: 640px) {
  .wrap { padding: 20px 16px 48px; }
  .qbar { padding: 16px; }
  .company-input { min-width: 0; width: 100%; }
  .fact, .judge { padding-left: 32px; }
}
.qnum { text-align: right; }
.qsrc { font-size: 11px; color: var(--mute); }
</style>