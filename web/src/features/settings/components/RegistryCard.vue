<script setup lang="ts">
/** 工商数据源：自包含配置卡片，读写与连接测试都留在本组件内。 */
import { computed, onMounted, reactive, ref } from 'vue'
import AppModal from '../../../shared/components/AppModal.vue'
import { api } from '../../../shared/api'
import { toast } from '../../../shared/composables/toast'
import { confirmAsk } from '../../../shared/composables/confirm'
import { actorName } from '../../../shared/composables/identity'
import type {
  RegistryConfig, RegistryPreset, RegistryProfile, RegistryTestResp,
} from '../types'

const cfg = ref<RegistryConfig | null>(null)
const err = ref('')
const loading = ref(true)
const busy = ref('')
const open = ref(false)
const editing = ref('')
const advanced = ref(false)
const testing = ref(false)
const testRes = ref<RegistryTestResp | null>(null)

const blank = () => ({
  name: '', preset: '', base: '', path: '', auth: 'header',
  auth_name: 'Authorization', auth_prefix: '', key: '', secret: '',
  items_path: '', code_path: '', msg_path: '', timeout: 8,
  map: {} as Record<string, string>,
})
const form = reactive(blank())

const profiles = computed(() => cfg.value?.profiles || [])
const presets = computed(() => cfg.value?.presets || [])
const fieldNames = computed(() => Object.entries(cfg.value?.fields || {}))
const editRow = computed(() => profiles.value.find((p) => p.id === editing.value) || null)
const presetNote = computed(() => presets.value.find((p) => p.id === form.preset)?.note || '')

function apply(next: RegistryConfig): void {
  cfg.value = next
  testRes.value = null
}

async function load(): Promise<void> {
  loading.value = true
  try {
    apply(await api.get<RegistryConfig>('/api/registry/config'))
    err.value = ''
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function fill(p: RegistryPreset): void {
  form.base = p.base
  form.path = p.path
  form.auth = p.auth || 'header'
  form.auth_name = p.auth_name || ''
  form.auth_prefix = p.auth_prefix || ''
  form.items_path = p.items_path || ''
  form.code_path = p.code_path || ''
  form.msg_path = p.msg_path || ''
  form.map = { ...p.map }
  if (!form.name.trim()) form.name = p.name
}

function usePreset(id: string): void {
  const p = presets.value.find((x) => x.id === id)
  if (p) fill(p)
  testRes.value = null
}

function reset(): void {
  Object.assign(form, blank())
  editing.value = ''
  advanced.value = false
  testRes.value = null
}

function openNew(): void {
  reset()
  const first = presets.value[0]
  if (first) {
    form.preset = first.id
    fill(first)
  }
  open.value = true
}

function openEdit(p: RegistryProfile): void {
  reset()
  editing.value = p.id
  form.name = p.name
  form.preset = p.preset
  form.base = p.base
  form.path = p.path
  form.auth = p.auth
  form.auth_name = p.auth_name
  form.auth_prefix = p.auth_prefix
  form.items_path = p.items_path
  form.code_path = p.code_path
  form.msg_path = p.msg_path
  form.timeout = p.timeout || 8
  form.map = { ...p.map }
  open.value = true
}

function payload(): Record<string, unknown> {
  const p = presets.value.find((x) => x.id === form.preset)
  return {
    id: editing.value || undefined,
    name: form.name.trim(),
    preset: form.preset || 'custom',
    base: form.base.trim(),
    path: form.path.trim(),
    auth: form.auth,
    auth_name: form.auth_name.trim(),
    auth_prefix: form.auth_prefix,
    key: form.key.trim(),
    secret: form.secret.trim(),
    fields: p?.fields || editRow.value?.fields || {},
    items_path: form.items_path.trim(),
    code_path: form.code_path.trim(),
    msg_path: form.msg_path.trim(),
    ok_codes: p?.ok_codes || editRow.value?.ok_codes || [],
    empty_codes: p?.empty_codes || editRow.value?.empty_codes || [],
    map: { ...form.map },
    timeout: form.timeout,
    actor: actorName(),
  }
}

async function save(): Promise<void> {
  if (!form.base.trim() || !form.path.trim()) {
    toast('接口地址与请求路径不能为空')
    return
  }
  if (!editing.value && !form.key.trim()) {
    toast('新增配置需要填写 API Key')
    return
  }
  busy.value = 'save'
  try {
    apply(await api.post<RegistryConfig>('/api/registry/config', payload()))
    open.value = false
    toast('已保存并启用，搜索时会查询工商登记')
  } catch (e) {
    toast('保存失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function probe(): Promise<void> {
  testing.value = true
  testRes.value = null
  try {
    testRes.value = await api.post<RegistryTestResp>('/api/registry/test', payload())
  } catch (e) {
    testRes.value = {
      ok: false, status: 'error', ms: 0, count: 0,
      fields_hit: [], sample: {}, error: (e as Error).message,
    }
  } finally {
    testing.value = false
  }
}

async function activate(p: RegistryProfile): Promise<void> {
  if (p.active || busy.value) return
  busy.value = 'activate:' + p.id
  try {
    apply(await api.post<RegistryConfig>('/api/registry/config', {
      action: 'activate', id: p.id, name: p.name, actor: actorName(),
    }))
    toast(`已启用：${p.name}`)
  } catch (e) {
    toast('启用失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function remove(p: RegistryProfile): Promise<void> {
  const ok = await confirmAsk({
    title: `删除配置「${p.name}」？`,
    message: '删除后不再查询该接口；已缓存的工商档案仍可离线检索。',
    confirmText: '删除配置',
    danger: true,
  })
  if (!ok) return
  busy.value = 'delete:' + p.id
  try {
    apply(await api.post<RegistryConfig>('/api/registry/config', {
      action: 'delete', id: p.id, name: p.name, actor: actorName(),
    }))
    toast('已删除：' + p.name)
  } catch (e) {
    toast('删除失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function stop(): Promise<void> {
  const ok = await confirmAsk({
    title: '停用工商数据源？',
    message: '停用后搜索回到证券主体库与资讯线索；配置与本地缓存都保留。',
    confirmText: '停用',
    danger: true,
  })
  if (!ok) return
  busy.value = 'clear'
  try {
    apply(await api.post<RegistryConfig>('/api/registry/config', {
      action: 'clear', actor: actorName(),
    }))
    toast('已停用工商数据源')
  } catch (e) {
    toast('停用失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="card">
    <div class="card-h">
      <h3>工商数据源</h3>
      <span class="cnt">{{ profiles.length }} 套</span>
      <span class="st" :class="{ on: cfg?.enabled }">{{ cfg?.enabled ? '已接入' : '未接入' }}</span>
      <div class="htools">
        <button v-if="cfg?.enabled" class="btn btn-ghost btn-sm" @click="stop">停用</button>
        <button class="btn btn-primary btn-sm" @click="openNew">＋ 新增接口</button>
      </div>
    </div>

    <div v-if="loading" class="empty">读取配置…</div>
    <div v-else-if="err" class="blank">
      <div class="blank-t">配置读取失败</div>
      <div class="blank-m">{{ err }}</div>
      <button class="btn btn-sm" @click="load">重试</button>
    </div>
    <div v-else-if="!profiles.length" class="blank">
      <div class="blank-t">还没有工商接口</div>
      <button class="btn btn-primary btn-sm" @click="openNew">接入第一套接口</button>
    </div>
    <div v-else class="plist">
      <div v-for="p in profiles" :key="p.id" class="prow" :class="{ on: p.active }">
        <div class="pmain">
          <div class="ptop">
            <b>{{ p.name }}</b>
            <span class="ptag" :class="{ on: p.active }">{{ p.active ? '使用中' : '未启用' }}</span>
          </div>
          <div class="pmeta">
            <span class="pmodel">{{ p.base }}{{ p.path }}</span>
          </div>
          <div class="pmeta2">
            密钥 {{ p.key_set ? p.key_hint : '未设置' }} · 超时 {{ p.timeout }} 秒
          </div>
        </div>
        <div class="pacts">
          <button
            v-if="!p.active" class="btn btn-ghost btn-sm"
            :disabled="!!busy" @click="activate(p)"
          >{{ busy === 'activate:' + p.id ? '启用中…' : '启用' }}</button>
          <button class="btn btn-ghost btn-sm" :disabled="!!busy" @click="openEdit(p)">编辑</button>
          <button
            class="btn btn-ghost btn-sm danger" :disabled="!!busy"
            @click="remove(p)"
          >{{ busy === 'delete:' + p.id ? '删除中…' : '删除' }}</button>
        </div>
      </div>
    </div>
    <div class="pfoot">
      本地工商档案 {{ cfg?.cache.total || 0 }} 家<template v-if="cfg?.cache.last_at"> · 最近抓取 {{ cfg.cache.last_at }}</template>
    </div>
  </section>

  <AppModal
    :open="open" :title="editing ? '编辑工商接口' : '新增工商接口'"
    width="720px" @close="open = false"
  >
    <div class="rform">
      <div class="frow">
        <label>配置名称</label>
        <input v-model.trim="form.name" type="text" maxlength="40" placeholder="例如：生产环境 / 企查查">
      </div>
      <div class="frow">
        <label>预设</label>
        <select v-model="form.preset" @change="usePreset(form.preset)">
          <option value="custom">自定义</option>
          <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="frow span2">
        <label>接口地址</label>
        <input v-model.trim="form.base" type="text" spellcheck="false" placeholder="https://open.api.example.com">
      </div>
      <div class="frow span2">
        <label>请求路径</label>
        <input v-model.trim="form.path" type="text" spellcheck="false" placeholder="/services/open/search/2.0">
      </div>
      <div class="frow">
        <label>鉴权方式</label>
        <select v-model="form.auth">
          <option value="header">请求头</option>
          <option value="query">查询参数</option>
          <option value="qcc">企查查签名</option>
        </select>
      </div>
      <div class="frow">
        <label>API Key</label>
        <input
          v-model.trim="form.key" type="password" autocomplete="off"
          :placeholder="editing && editRow?.key_set ? `已保存 ${editRow.key_hint} · 留空沿用` : '填写开放平台密钥'"
        >
      </div>
      <template v-if="form.auth === 'header'">
        <div class="frow">
          <label>请求头名</label>
          <input v-model.trim="form.auth_name" type="text" spellcheck="false" placeholder="Authorization">
        </div>
        <div class="frow">
          <label>前缀</label>
          <input v-model.trim="form.auth_prefix" type="text" spellcheck="false" placeholder="Bearer ">
        </div>
      </template>
      <template v-else-if="form.auth === 'query'">
        <div class="frow">
          <label>参数名</label>
          <input v-model.trim="form.auth_name" type="text" spellcheck="false" placeholder="key">
        </div>
      </template>
      <template v-else>
        <div class="frow">
          <label>Secret</label>
          <input
            v-model.trim="form.secret" type="password" autocomplete="off"
            :placeholder="editing && editRow?.secret_set ? '已保存 · 留空沿用' : '填写 Secret'"
          >
        </div>
      </template>
      <div class="frow">
        <label>超时（秒）</label>
        <input v-model.number="form.timeout" type="number" min="3" max="30">
      </div>

      <div class="span2 adv">
        <button type="button" class="btn btn-ghost btn-xs" @click="advanced = !advanced">
          {{ advanced ? '收起字段映射' : '字段映射' }}
        </button>
      </div>
      <template v-if="advanced">
        <div class="frow">
          <label>结果数组路径</label>
          <input v-model.trim="form.items_path" type="text" spellcheck="false" placeholder="result.items">
        </div>
        <div class="frow">
          <label>错误码路径</label>
          <input v-model.trim="form.code_path" type="text" spellcheck="false" placeholder="error_code">
        </div>
        <div class="frow">
          <label>错误信息路径</label>
          <input v-model.trim="form.msg_path" type="text" spellcheck="false" placeholder="reason">
        </div>
        <div v-for="[k, label] in fieldNames" :key="k" class="frow">
          <label>{{ label }}</label>
          <input v-model.trim="form.map[k]" type="text" spellcheck="false" :placeholder="k">
        </div>
      </template>
    </div>

    <div v-if="presetNote" class="pnote">{{ presetNote }}</div>

    <div v-if="testRes" class="test" :class="testRes.ok ? 'ok' : 'err'">
      <template v-if="testRes.ok">
        状态 {{ testRes.status }} · {{ testRes.ms }} ms · 返回 {{ testRes.count }} 条
        <template v-if="testRes.fields_hit.length"> · 命中 {{ testRes.fields_hit.join('、') }}</template>
        <div v-if="testRes.sample.name" class="sample">
          {{ testRes.sample.name }}<template v-if="testRes.sample.credit_code"> · {{ testRes.sample.credit_code }}</template>
        </div>
      </template>
      <template v-else>
        连接失败 · {{ testRes.ms }} ms · {{ testRes.error || testRes.status }}
      </template>
    </div>

    <template #footer>
      <button class="btn btn-primary" :disabled="!!busy" @click="save">
        <span v-if="busy === 'save'" class="spin"></span>保存并启用
      </button>
      <button class="btn" :disabled="testing" @click="probe">
        <span v-if="testing" class="spin"></span>测试连接
      </button>
      <button class="btn btn-ghost" @click="open = false">取消</button>
    </template>
  </AppModal>
</template>

<style scoped>
.card-h { padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.cnt { font-size: 11.5px; color: var(--mute); }
.st { margin-left: auto; font-size: 11px; padding: 2px 9px; border-radius: 11px; background: #F1F3F6; color: var(--sub); }
.st.on { background: var(--ok-soft); color: var(--ok); }
.htools { display: flex; align-items: center; gap: 8px; }

.plist { padding: 4px 0; }
.prow { display: flex; align-items: flex-start; gap: 14px; padding: 14px 18px; border-bottom: 1px solid #F1F3F6; }
.prow:last-child { border-bottom: none; }
.prow.on { background: #FBFDFF; box-shadow: inset 3px 0 0 var(--accent); }
.pmain { flex: 1; min-width: 0; }
.ptop { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ptop b { font-size: 13.5px; font-weight: 650; }
.ptag { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #F1F3F6; color: var(--mute); }
.ptag.on { background: var(--ok-soft); color: var(--ok); }
.pmeta { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-top: 3px; }
.pmodel { font-size: 11.5px; color: var(--sub); font-family: ui-monospace, Consolas, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.pmeta2 { font-size: 11px; color: var(--mute); margin-top: 2px; }
.pacts { flex: none; display: flex; gap: 6px; align-items: center; }
.pacts .danger { color: var(--err); }
.pfoot { padding: 10px 18px 14px; font-size: 11.5px; color: var(--mute); line-height: 1.8; border-top: 1px dashed var(--line); }
.blank { padding: 32px 20px; text-align: center; }
.blank-t { font-size: 14px; font-weight: 650; }
.blank-t + .btn { margin-top: 16px; }
.blank-m { font-size: 12px; color: var(--mute); line-height: 1.85; max-width: 560px; margin: 8px auto 16px; }
.empty { padding: 26px 4px; font-size: 12.5px; color: var(--mute); }

.rform { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 18px; }
.rform .span2 { grid-column: span 2; }
.frow { display: flex; flex-direction: column; gap: 6px; }
.frow label { font-size: 12px; color: var(--mute); }
.frow input, .frow select { width: 100%; }
.adv { display: flex; justify-content: flex-start; border-top: 1px dashed var(--line); padding-top: 10px; }
.pnote { margin-top: 12px; font-size: 11.5px; color: var(--warn); background: var(--warn-soft); border-radius: 6px; padding: 7px 10px; }
.test { margin-top: 14px; font-size: 12px; border-radius: 8px; padding: 9px 12px; line-height: 1.7; }
.test.ok { background: var(--ok-soft); color: var(--ok); }
.test.err { background: var(--err-soft); color: var(--err); word-break: break-all; }
.sample { margin-top: 4px; font-family: ui-monospace, Consolas, monospace; }
.btn .spin { border-color: rgba(26, 109, 213, .25); border-top-color: var(--accent); }

@media (max-width: 640px) {
  .rform { grid-template-columns: 1fr; }
  .rform .span2 { grid-column: span 1; }
}
</style>