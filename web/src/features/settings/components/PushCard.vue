<script setup lang="ts">
/** 报告推送：自包含配置卡片，渠道读写与测试都留在本组件内。 */
import { computed, onMounted, reactive, ref } from 'vue'
import AppModal from '../../../shared/components/AppModal.vue'
import { api } from '../../../shared/api'
import { toast } from '../../../shared/composables/toast'
import { confirmAsk } from '../../../shared/composables/confirm'
import { actorName } from '../../../shared/composables/identity'
import type { PushChannel, PushConfig, PushTestResp, PushType } from '../types'

const cfg = ref<PushConfig | null>(null)
const err = ref('')
const loading = ref(true)
const busy = ref('')
const open = ref(false)
const editing = ref('')
const testing = ref(false)
const testRes = ref<PushTestResp | null>(null)
const baseUrl = ref('')

const blank = () => ({ type: 'feishu', name: '', url: '', secret: '', chat_id: '', enabled: true })
const form = reactive(blank())

const types = computed(() => cfg.value?.types || [])
const channels = computed(() => cfg.value?.channels || [])
const editRow = computed(() => channels.value.find((c) => c.id === editing.value) || null)
const curType = computed<PushType | null>(() => types.value.find((t) => t.id === form.type) || null)

function apply(next: PushConfig): void {
  cfg.value = next
  baseUrl.value = next.base_url || ''
  testRes.value = null
}

async function load(): Promise<void> {
  loading.value = true
  try {
    apply(await api.get<PushConfig>('/api/access/push/config'))
    err.value = ''
  } catch (e) {
    err.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function reset(): void {
  Object.assign(form, blank())
  editing.value = ''
  testRes.value = null
}

function openNew(): void {
  reset()
  const first = types.value.find((t) => t.id === 'feishu') || types.value[0]
  if (first) form.type = first.id
  open.value = true
}

function openEdit(c: PushChannel): void {
  reset()
  editing.value = c.id
  form.type = c.type
  form.name = c.name
  form.chat_id = c.chat_id
  form.enabled = c.enabled
  open.value = true
}

function hasField(name: string): boolean {
  return !!curType.value?.fields.includes(name)
}

function typeName(tid: string): string {
  return types.value.find((t) => t.id === tid)?.name || tid
}

function targetOf(c: PushChannel): string {
  if (c.type === 'telegram') {
    return `Chat ${c.chat_id || '未设置'} · Token ${c.secret_set ? c.secret_hint : '未设置'}`
  }
  const parts = [c.url_hint || '地址未设置']
  if (c.secret_set) parts.push(`签名 ${c.secret_hint}`)
  return parts.join(' · ')
}

function onTypeChange(): void {
  testRes.value = null
}

function payload(): Record<string, unknown> {
  return {
    id: editing.value || undefined,
    type: form.type,
    name: form.name.trim(),
    url: form.url.trim(),
    secret: form.secret.trim(),
    chat_id: form.chat_id.trim(),
    enabled: form.enabled,
    actor: actorName(),
  }
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    toast('请填写渠道名称')
    return
  }
  busy.value = 'save'
  try {
    apply(await api.post<PushConfig>('/api/access/push/config', { ...payload(), action: 'save' }))
    open.value = false
    toast(`已保存：${form.name.trim()}`)
  } catch (e) {
    toast('保存失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function test(): Promise<void> {
  testing.value = true
  testRes.value = null
  try {
    testRes.value = await api.post<PushTestResp>('/api/access/push/test', payload())
  } catch (e) {
    testRes.value = { ok: false, ms: 0, error: (e as Error).message }
  } finally {
    testing.value = false
  }
}

async function testSaved(c: PushChannel): Promise<void> {
  if (busy.value) return
  busy.value = 'test:' + c.id
  try {
    const res = await api.post<PushTestResp>('/api/access/push/test', { id: c.id, type: c.type })
    toast(res.ok ? `测试消息已送达（${res.ms} ms）` : `发送失败：${res.error}`)
  } catch (e) {
    toast('发送失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function toggle(c: PushChannel): Promise<void> {
  if (busy.value) return
  busy.value = 'toggle:' + c.id
  try {
    apply(await api.post<PushConfig>('/api/access/push/config', {
      action: 'toggle', id: c.id, enabled: !c.enabled, name: c.name, actor: actorName(),
    }))
    toast(`${c.enabled ? '已停用' : '已启用'}：${c.name}`)
  } catch (e) {
    toast('操作失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function remove(c: PushChannel): Promise<void> {
  const ok = await confirmAsk({
    title: `删除渠道「${c.name}」？`,
    message: '删除后不再向该地址推送；已发送的消息不受影响。',
    confirmText: '删除渠道',
    danger: true,
  })
  if (!ok) return
  busy.value = 'delete:' + c.id
  try {
    apply(await api.post<PushConfig>('/api/access/push/config', {
      action: 'delete', id: c.id, name: c.name, actor: actorName(),
    }))
    toast('已删除：' + c.name)
  } catch (e) {
    toast('删除失败：' + (e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function toggleAuto(e: Event): Promise<void> {
  const next = (e.target as HTMLInputElement).checked
  try {
    apply(await api.post<PushConfig>('/api/access/push/config', {
      action: 'options', auto: next, base_url: baseUrl.value, actor: actorName(),
    }))
    toast(next ? '已开启：运行结束后自动推送' : '已关闭自动推送')
  } catch (ex) {
    toast('保存失败：' + (ex as Error).message)
    await load()
  }
}

async function saveBase(): Promise<void> {
  try {
    apply(await api.post<PushConfig>('/api/access/push/config', {
      action: 'options', base_url: baseUrl.value, actor: actorName(),
    }))
    toast('服务地址已保存')
  } catch (e) {
    toast('保存失败：' + (e as Error).message)
  }
}

onMounted(load)
</script>

<template>
  <section class="card">
    <div class="card-h">
      <h3>报告推送</h3>
      <span class="cnt">{{ channels.length }} 个渠道</span>
      <span class="st" :class="{ on: cfg?.enabled }">{{ cfg?.enabled ? '推送已就绪' : '未启用渠道' }}</span>
      <div class="htools">
        <button class="btn btn-primary btn-sm" @click="openNew">＋ 新增渠道</button>
      </div>
    </div>

    <div v-if="loading" class="empty">读取配置…</div>
    <div v-else-if="err" class="blank">
      <div class="blank-t">配置读取失败</div>
      <div class="blank-m">{{ err }}</div>
      <button class="btn btn-sm" @click="load">重试</button>
    </div>
    <div v-else-if="!channels.length" class="blank">
      <div class="blank-t">还没有推送渠道</div>
      <div class="blank-m">
        支持飞书、钉钉、企业微信、Telegram 与通用 Webhook。配置后可选择运行结束自动推送报告摘要，
        也可以在企业洞察页对任一历史报告手动推送。
      </div>
      <button class="btn btn-primary btn-sm" @click="openNew">添加第一个渠道</button>
    </div>
    <div v-else class="plist">
      <div v-for="c in channels" :key="c.id" class="prow" :class="{ on: c.enabled }">
        <div class="pmain">
          <div class="ptop">
            <b>{{ c.name }}</b>
            <span class="ptag">{{ typeName(c.type) }}</span>
            <span class="ptag" :class="{ on: c.enabled }">{{ c.enabled ? '已启用' : '已停用' }}</span>
          </div>
          <div class="pmeta">
            <span class="pmodel">{{ targetOf(c) }}</span>
          </div>
        </div>
        <div class="pacts">
          <button
            class="btn btn-ghost btn-sm" :disabled="!!busy"
            @click="testSaved(c)"
          >{{ busy === 'test:' + c.id ? '发送中…' : '测试' }}</button>
          <button class="btn btn-ghost btn-sm" :disabled="!!busy" @click="toggle(c)">
            {{ c.enabled ? '停用' : '启用' }}
          </button>
          <button class="btn btn-ghost btn-sm" :disabled="!!busy" @click="openEdit(c)">编辑</button>
          <button
            class="btn btn-ghost btn-sm danger" :disabled="!!busy"
            @click="remove(c)"
          >{{ busy === 'delete:' + c.id ? '删除中…' : '删除' }}</button>
        </div>
      </div>
    </div>

    <div class="pfoot">
      <label class="auto">
        <input type="checkbox" :checked="cfg?.auto" @change="toggleAuto">
        运行结束后自动推送报告摘要
      </label>
      <div class="base">
        <span>服务地址</span>
        <input
          v-model.trim="baseUrl" type="text" spellcheck="false"
          placeholder="https://mingjian.example.com（可选，用于生成报告深链接）"
        >
        <button class="btn btn-ghost btn-sm" @click="saveBase">保存</button>
      </div>
    </div>
  </section>

  <AppModal
    :open="open" :title="editing ? '编辑推送渠道' : '新增推送渠道'"
    width="560px" @close="open = false"
  >
    <div class="mform">
      <div class="frow">
        <label>渠道类型</label>
        <select v-model="form.type" @change="onTypeChange">
          <option v-for="t in types" :key="t.id" :value="t.id">{{ t.group }} · {{ t.name }}</option>
        </select>
      </div>
      <div class="frow">
        <label>渠道名称</label>
        <input v-model.trim="form.name" type="text" maxlength="40" placeholder="例如：产业研究群">
      </div>
      <div v-if="hasField('url')" class="frow">
        <label>Webhook 地址</label>
        <input
          v-model.trim="form.url" type="text" spellcheck="false"
          :placeholder="editing && editRow?.url_set ? `已保存 ${editRow.url_hint} · 留空沿用` : 'https://open.feishu.cn/open-apis/bot/v2/hook/…'"
        >
      </div>
      <div v-if="hasField('secret')" class="frow">
        <label>{{ form.type === 'telegram' ? 'Bot Token' : '签名密钥（可选）' }}</label>
        <input
          v-model.trim="form.secret" type="password" autocomplete="off"
          :placeholder="editing && editRow?.secret_set ? `已保存 ${editRow.secret_hint} · 留空沿用` : (form.type === 'telegram' ? '123456:ABC…' : '钉钉 / 飞书加签密钥')"
        >
      </div>
      <div v-if="hasField('chat_id')" class="frow">
        <label>Chat ID</label>
        <input v-model.trim="form.chat_id" type="text" spellcheck="false" placeholder="-1001234567890 或 @channel">
      </div>
      <div v-if="curType?.note" class="pnote">{{ curType.note }}</div>
    </div>

    <div v-if="testRes" class="test" :class="testRes.ok ? 'ok' : 'err'">
      <template v-if="testRes.ok">测试消息已送达 · {{ testRes.ms }} ms</template>
      <template v-else>发送失败 · {{ testRes.ms }} ms · {{ testRes.error }}</template>
    </div>

    <template #footer>
      <button class="btn btn-primary" :disabled="busy === 'save'" @click="save">
        <span v-if="busy === 'save'" class="spin"></span>保存渠道
      </button>
      <button class="btn" :disabled="testing" @click="test">
        <span v-if="testing" class="spin"></span>发送测试
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
.pacts { flex: none; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.pacts .danger { color: var(--err); }
.pfoot { padding: 10px 18px 14px; font-size: 11.5px; color: var(--mute); line-height: 1.8; border-top: 1px dashed var(--line); display: flex; flex-direction: column; gap: 8px; }
.auto { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.base { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.base input { flex: 1; min-width: 260px; }
.blank { padding: 32px 20px; text-align: center; }
.blank-t { font-size: 14px; font-weight: 650; }
.blank-t + .btn { margin-top: 16px; }
.blank-m { font-size: 12px; color: var(--mute); line-height: 1.85; max-width: 560px; margin: 8px auto 16px; }
.empty { padding: 26px 4px; font-size: 12.5px; color: var(--mute); }

.mform { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 18px; }
.mform .frow { grid-column: span 2; }
.frow { display: flex; flex-direction: column; gap: 6px; }
.frow label { font-size: 12px; color: var(--mute); }
.frow input, .frow select { width: 100%; }
.pnote { grid-column: span 2; font-size: 11.5px; color: var(--warn); background: var(--warn-soft); border-radius: 6px; padding: 7px 10px; }
.test { margin-top: 14px; font-size: 12px; border-radius: 8px; padding: 9px 12px; line-height: 1.7; }
.test.ok { background: var(--ok-soft); color: var(--ok); }
.test.err { background: var(--err-soft); color: var(--err); word-break: break-all; }
.btn .spin { border-color: rgba(26, 109, 213, .25); border-top-color: var(--accent); }
</style>