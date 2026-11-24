<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AppHeader from '../../shared/components/AppHeader.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import { confirmAsk } from '../../shared/composables/confirm'

interface FieldSpec {
  type: string
  desc?: string
  required?: boolean
  enum?: string[]
  min?: number
  max?: number
  max_len?: number
  default?: string | number | boolean
}

interface Skill {
  name: string
  title: string
  kind: string
  kind_label: string
  side_effect: string
  description: string
  schema: Record<string, FieldSpec>
  internal: boolean
  available: boolean
}

interface MemoryItem {
  id: string
  scope: string
  key: string
  kind: string
  kind_label: string
  content: string
  refs: unknown[]
  source: string
  confidence: number | null
  hits: number
  created_at: string
  updated_at: string
  used_at: string | null
}

interface MemoryResp {
  items: MemoryItem[]
  total: number
  stats: { total: number; by_kind: Record<string, number> }
  kinds: string[]
}

interface RunResp {
  ok: boolean
  skill: string
  side_effect: string
  elapsed_ms: number
  data?: unknown
  error?: string
}

interface SkillResp {
  skills: Skill[]
  kinds: Record<string, string>
}

const KIND_LABEL: Record<string, string> = {
  fact: '事实', judgment: '判断', caliber: '口径', preference: '偏好', note: '备注',
}

const skills = ref<Skill[]>([])
const kindLabels = ref<Record<string, string>>({})
const llmActive = ref(false)
const loading = ref(true)
const picked = ref('')
const running = ref(false)
const result = ref<RunResp | null>(null)

const memories = ref<MemoryItem[]>([])
const memTotal = ref(0)
const memStats = ref<{ total: number; by_kind: Record<string, number> }>({ total: 0, by_kind: {} })
const memKinds = ref<string[]>([])

const q = ref('')
const kindFilter = ref('')
const harvestId = ref('')
const harvestBusy = ref(false)

const form = reactive<Record<string, any>>({})
const newMem = reactive({ scope: '', key: '', kind: 'note', content: '', refs: '' })

const groups = computed(() => {
  const order = ['query', 'collect', 'write', 'internal']
  const out: { key: string; label: string; items: Skill[] }[] = []
  for (const key of order) {
    const items = skills.value.filter((s) => s.kind === key)
    if (items.length) out.push({ key, label: kindLabels.value[key] || key, items })
  }
  return out
})

const cur = computed(() => skills.value.find((s) => s.name === picked.value) || null)
const fields = computed(() => Object.entries(cur.value?.schema || {}).map(([name, spec]) => ({ name, spec })))
const badgeText = computed(() => (llmActive.value ? '模型已接入' : '规则引擎'))
const stateText = computed(() => `技能 ${skills.value.length} · 记忆 ${memStats.value.total}`)
const resultText = computed(() => {
  if (!result.value) return ''
  const text = JSON.stringify(result.value, null, 2)
  return text.length > 16000 ? text.slice(0, 16000) + '\n…（输出已截断）' : text
})

function pick(s: Skill): void {
  picked.value = s.name
  for (const k of Object.keys(form)) delete form[k]
  for (const [name, spec] of Object.entries(s.schema)) {
    form[name] = spec.default !== undefined ? spec.default : ''
  }
  result.value = null
}

async function loadSkills(): Promise<void> {
  const res = await api.get<SkillResp>('/api/skills')
  skills.value = res.skills || []
  kindLabels.value = res.kinds || {}
  const first = skills.value.find((s) => !s.internal) || skills.value[0]
  if (first && !skills.value.some((s) => s.name === picked.value)) pick(first)
}

function applyMem(res: MemoryResp): void {
  memories.value = res.items || []
  memTotal.value = res.total || 0
  memStats.value = res.stats || { total: 0, by_kind: {} }
  memKinds.value = res.kinds || []
}

async function loadMem(): Promise<void> {
  try {
    const res = await api.get<MemoryResp>('/api/memory', {
      q: q.value || undefined,
      kind: kindFilter.value || undefined,
      limit: 100,
    })
    applyMem(res)
  } catch (e) {
    toast('读取记忆失败：' + (e as Error).message)
  }
}

async function runSkill(): Promise<void> {
  if (!cur.value || cur.value.internal) return
  const args: Record<string, unknown> = {}
  for (const [name, spec] of Object.entries(cur.value.schema)) {
    let v = form[name]
    if (v === '' || v === undefined || v === null) continue
    if (spec.type === 'number') v = Number(v)
    if (spec.type === 'array' && typeof v === 'string') {
      v = v.split(/[,，]/).map((x) => x.trim()).filter(Boolean)
    }
    args[name] = v
  }
  running.value = true
  result.value = null
  try {
    const res = await api.post<RunResp>('/api/skills/run', { name: cur.value.name, args })
    result.value = res
    if (!res.ok) toast(res.error || '执行失败')
    else toast(`${cur.value.title} 完成 · ${res.elapsed_ms} ms`)
    await loadMem()
  } catch (e) {
    toast('执行失败：' + (e as Error).message)
  } finally {
    running.value = false
  }
}

async function addMem(): Promise<void> {
  if (!newMem.content.trim()) {
    toast('记忆内容不能为空')
    return
  }
  const refs = newMem.refs
    .split(/[,，]/)
    .map((x) => x.trim())
    .filter(Boolean)
  try {
    await api.post('/api/memory', {
      scope: newMem.scope.trim() || 'global',
      key: newMem.key.trim(),
      kind: newMem.kind,
      content: newMem.content.trim(),
      refs,
      source: 'manual',
    })
    newMem.content = ''
    newMem.refs = ''
    toast('已写入记忆')
    await loadMem()
  } catch (e) {
    toast('写入失败：' + (e as Error).message)
  }
}

async function delMem(m: MemoryItem): Promise<void> {
  const ok = await confirmAsk({
    title: '删除这条记忆？',
    message: `${m.kind_label || m.kind}：${m.content.slice(0, 80)}${m.content.length > 80 ? '…' : ''}\n\n删除后不会影响已生成的报告，但后续分析不会再带上这条记忆。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await api.post('/api/memory/delete', { id: m.id })
    toast('已删除')
    await loadMem()
  } catch (e) {
    toast('删除失败：' + (e as Error).message)
  }
}

async function harvest(): Promise<void> {
  const rid = harvestId.value.trim()
  if (!rid) return
  harvestBusy.value = true
  try {
    const res = await api.post<{ written: number }>('/api/memory/harvest', { run_id: rid })
    toast(`已写入 ${res.written} 条记忆`)
    harvestId.value = ''
    await loadMem()
  } catch (e) {
    toast('沉淀失败：' + (e as Error).message)
  } finally {
    harvestBusy.value = false
  }
}

function refUrl(m: MemoryItem): string {
  for (const r of m.refs || []) {
    if (typeof r === 'string' && /^https?:\/\//.test(r)) return r
    if (r && typeof r === 'object' && typeof (r as { url?: string }).url === 'string') {
      const u = (r as { url: string }).url
      if (/^https?:\/\//.test(u)) return u
    }
  }
  return ''
}

onMounted(async () => {
  try {
    await Promise.all([loadSkills(), loadMem()])
  } catch (e) {
    toast('读取失败：' + (e as Error).message)
  } finally {
    loading.value = false
  }
  api.get<{ active: boolean }>('/api/llm/config')
    .then((c) => { llmActive.value = !!c.active })
    .catch(() => { /* 旧服务没有该接口时保持规则引擎标识 */ })
})
</script>

<template>
  <AppHeader :badge="badgeText" :state="stateText" />

  <div class="wrap">
    <div class="grid">
      <section class="card side">
        <div class="card-h">
          <h3>技能</h3>
          <span class="cnt">{{ skills.length }}</span>
        </div>
        <div class="slist">
          <template v-for="g in groups" :key="g.key">
            <div class="gname">{{ g.label }}</div>
            <button
              v-for="s in g.items" :key="s.name" type="button"
              class="sitem" :class="{ on: picked === s.name, dis: s.internal }"
              @click="pick(s)"
            >
              <span class="srow">
                <span class="sn">{{ s.title }}</span>
                <span class="se">{{ s.side_effect }}</span>
              </span>
              <span class="sc">{{ s.name }}</span>
            </button>
          </template>
          <div v-if="!skills.length" class="empty">{{ loading ? '读取中…' : '无技能' }}</div>
        </div>
      </section>

      <section class="card main">
        <div class="card-h">
          <h3 :title="cur?.description">{{ cur?.title || '技能' }}</h3>
          <span v-if="cur" class="ktag">{{ cur.kind_label }}</span>
          <span v-if="cur?.internal" class="ktag warn">内部</span>
          <span v-if="result" class="rt" :class="{ err: !result.ok }">
            {{ result.ok ? '成功' : '失败' }} · {{ result.elapsed_ms }} ms
          </span>
        </div>
        <div v-if="cur" class="body">
          <div v-if="fields.length" class="fields">
            <div v-for="f in fields" :key="f.name" class="frow">
              <label>
                {{ f.name }}<span v-if="f.spec.required" class="req">*</span>
              </label>
              <select v-if="f.spec.enum" v-model="form[f.name]">
                <option v-for="opt in f.spec.enum" :key="opt" :value="opt">{{ opt }}</option>
              </select>
              <input
                v-else-if="f.spec.type === 'number'" v-model.number="form[f.name]"
                type="number" :min="f.spec.min" :max="f.spec.max"
              >
              <input
                v-else-if="f.spec.type === 'array'" v-model="form[f.name]"
                type="text" spellcheck="false" placeholder="逗号分隔"
              >
              <input v-else v-model="form[f.name]" type="text" spellcheck="false" :placeholder="f.spec.desc">
            </div>
          </div>
          <div v-else class="nofield">无参数</div>

          <div class="acts">
            <button class="btn btn-primary" :disabled="running || cur.internal" @click="runSkill">
              <span v-if="running" class="spin"></span>执行
            </button>
          </div>

          <pre v-if="result" class="out" :class="{ err: !result.ok }">{{ resultText }}</pre>
        </div>
      </section>
    </div>

    <section class="card mem">
      <div class="card-h">
        <h3>记忆</h3>
        <span class="cnt">{{ memTotal }}</span>
        <div class="tools">
          <input v-model.trim="q" type="search" placeholder="检索词" @keyup.enter="loadMem">
          <select v-model="kindFilter" @change="loadMem">
            <option value="">全部类型</option>
            <option v-for="k in memKinds" :key="k" :value="k">{{ KIND_LABEL[k] || k }}</option>
          </select>
          <button class="btn btn-sm" @click="loadMem">检索</button>
          <input v-model.trim="harvestId" class="rid" type="text" placeholder="运行编号" spellcheck="false">
          <button class="btn btn-sm" :disabled="harvestBusy || !harvestId" @click="harvest">从运行写入</button>
        </div>
      </div>

      <div class="newbar">
        <input v-model.trim="newMem.scope" type="text" placeholder="作用域（实体 key / global）" spellcheck="false">
        <input v-model.trim="newMem.key" type="text" placeholder="主题">
        <select v-model="newMem.kind">
          <option v-for="k in memKinds" :key="k" :value="k">{{ KIND_LABEL[k] || k }}</option>
        </select>
        <input v-model.trim="newMem.content" class="wide" type="text" placeholder="记忆内容">
        <input v-model.trim="newMem.refs" type="text" placeholder="引用 run_id / URL，逗号分隔" spellcheck="false">
        <button class="btn" @click="addMem">写入</button>
      </div>

      <div class="mlist">
        <div v-for="m in memories" :key="m.id" class="mitem">
          <span class="mtag" :class="m.kind">{{ m.kind_label }}</span>
          <div class="mc">
            <div class="mt">{{ m.content }}</div>
            <div class="mm">
              <span v-if="m.scope" class="mono">{{ m.scope }}</span>
              <span v-if="m.key">{{ m.key }}</span>
              <span>命中 {{ m.hits }}</span>
              <span>{{ m.updated_at }}</span>
              <a v-if="refUrl(m)" :href="refUrl(m)" target="_blank" rel="noreferrer">来源</a>
            </div>
          </div>
          <button class="del" type="button" @click="delMem(m)">删除</button>
        </div>
        <div v-if="!memories.length" class="empty">{{ loading ? '读取中…' : (q ? '无命中' : '无记忆') }}</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.wrap { width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 72px; }
.grid { display: grid; grid-template-columns: 400px minmax(0, 1fr); gap: 18px; align-items: start; }
@media (max-width: 1000px) {
  .grid { grid-template-columns: 1fr; }
}

.card-h { padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; }
.card-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.cnt { font-size: 11.5px; color: var(--mute); }
.rt { margin-left: auto; font-size: 11.5px; color: var(--ok); }
.rt.err { color: var(--err); }
.ktag {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: var(--accent-soft); color: var(--accent);
}
.ktag.warn { background: var(--warn-soft); color: var(--warn); }

.slist { padding: 10px; max-height: calc(100vh - 260px); overflow: auto; }
.gname {
  font-size: 11px; color: var(--mute); padding: 12px 10px 6px; letter-spacing: .05em;
  position: sticky; top: 0; background: var(--card); z-index: 1;
}
.sitem {
  width: 100%; text-align: left; border: 1px solid transparent; background: none;
  border-radius: 10px; padding: 10px 12px; cursor: pointer; display: block;
}
.sitem:hover { background: #F7F9FC; }
.sitem.on { background: var(--accent-soft); border-color: var(--accent-line); }
.sitem.dis { opacity: .72; }
.srow { display: flex; align-items: center; gap: 8px; }
.sn { font-size: 13px; font-weight: 550; color: var(--ink); }
.sitem.on .sn { color: var(--accent); }
.se { margin-left: auto; font-size: 10px; color: var(--mute); }
.sc {
  display: block; font-size: 10.5px; color: var(--mute); margin-top: 2px;
  font-family: ui-monospace, Consolas, monospace;
}

.body { padding: 22px 24px 26px; }
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 20px; }
@media (max-width: 760px) {
  .fields { grid-template-columns: 1fr; }
}
.frow { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.frow label { font-size: 12.5px; color: var(--mute); }
.req { color: var(--err); margin-left: 2px; }
.frow input, .frow select, .tools input, .tools select, .newbar input, .newbar select { min-width: 0; }
.nofield { font-size: 12.5px; color: var(--mute); }

.acts { display: flex; align-items: center; gap: 10px; margin-top: 20px; }
.btn .spin { border-color: rgba(26, 109, 213, .25); border-top-color: var(--accent); }

.out {
  margin: 18px 0 0; padding: 14px 16px; border-radius: 11px; background: #121A28; color: #D7E2F0;
  font-size: 12px; line-height: 1.75; max-height: 440px; overflow: auto;
  white-space: pre-wrap; word-break: break-word;
}
.out.err { color: #FFC9C4; }

.mem { margin-top: 16px; }
.tools { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tools input { width: 160px; }
.tools input.rid { width: 150px; font-family: ui-monospace, Consolas, monospace; }
.tools select { width: 108px; }

.newbar {
  display: grid; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--line);
  grid-template-columns: 210px 150px 110px minmax(0, 1fr) 220px auto;
  align-items: center; background: #FCFDFE;
}
@media (max-width: 1180px) {
  .newbar { grid-template-columns: 1fr 1fr; }
  .newbar .wide { grid-column: 1 / -1; }
}
@media (max-width: 760px) {
  .newbar { grid-template-columns: 1fr; }
}

.mlist { padding: 4px 18px 14px; max-height: calc(100vh - 350px); overflow: auto; }
.mitem { display: flex; align-items: flex-start; gap: 12px; padding: 13px 0; border-bottom: 1px solid #F1F3F6; }
.mitem:last-child { border-bottom: none; }
.mtag {
  flex: none; font-size: 10.5px; padding: 2px 8px; border-radius: 10px;
  background: #F1F3F6; color: var(--sub); margin-top: 1px;
}
.mtag.fact { background: var(--ok-soft); color: var(--ok); }
.mtag.judgment { background: var(--accent-soft); color: var(--accent); }
.mtag.caliber { background: var(--warn-soft); color: var(--warn); }
.mc { flex: 1; min-width: 0; }
.mt { font-size: 13px; line-height: 1.7; color: var(--ink); word-break: break-word; }
.mm {
  display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px;
  font-size: 11.5px; color: var(--mute);
}
.mm a { color: var(--accent); text-decoration: none; }
.mono { font-family: ui-monospace, Consolas, monospace; }
.del {
  flex: none; border: none; background: none; padding: 2px; cursor: pointer;
  font-size: 11.5px; color: var(--mute);
}
.del:hover { color: var(--err); }
.empty { padding: 30px 4px; font-size: 12.5px; color: var(--mute); }

@media (max-width: 640px) {
  .wrap { padding: 20px 16px 48px; }
  .body { padding: 18px 18px 22px; }
  .newbar { padding: 12px 16px; }
}
</style>