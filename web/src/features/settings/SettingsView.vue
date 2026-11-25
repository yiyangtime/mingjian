<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import AppHeader from '../../shared/components/AppHeader.vue'
import AppModal from '../../shared/components/AppModal.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import { confirmAsk } from '../../shared/composables/confirm'
import { DEFAULT_ACTOR, actorName, setActorName } from '../../shared/composables/identity'
import ModelProfilesCard from './components/ModelProfilesCard.vue'
import RegistryCard from './components/RegistryCard.vue'
import type { LlmConfig, Profile, Provider, TestResp } from './types'

const providers = ref<Provider[]>([])
const cfg = ref<LlmConfig | null>(null)
const cfgErr = ref('')
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const testRes = ref<TestResp | null>(null)
const picked = ref('')
const busyRow = ref('')

/* 新增 / 编辑配置弹窗 */
const editOpen = ref(false)
const editId = ref('')
const editName = ref('')
const form = reactive({ base: '', key: '', model: '', timeout: 90 })

const actor = ref(actorName())

const groups = computed(() => {
  const out: { name: string; items: Provider[] }[] = []
  for (const p of providers.value) {
    let g = out.find((x) => x.name === p.group)
    if (!g) {
      g = { name: p.group, items: [] }
      out.push(g)
    }
    g.items.push(p)
  }
  return out
})

const profiles = computed(() => cfg.value?.profiles || [])
const editProfile = computed(() => profiles.value.find((p) => p.id === editId.value) || null)
const editing = computed(() => !!editId.value)
const curNote = computed(() => providers.value.find((p) => p.id === picked.value)?.note || '')
const modelList = computed(() => providers.value.find((p) => p.id === picked.value)?.models || [])
const badgeText = computed(() => {
  if (loading.value) return '读取配置…'
  return cfg.value?.active ? '模型已接入' : '规则引擎'
})
const currentModel = computed(() => (cfg.value ? `${cfg.value.model} · ${cfg.value.base}` : ''))

function apply(next: LlmConfig): void {
  cfg.value = next
  providers.value = next.providers || []
  testRes.value = null
}

async function load(): Promise<void> {
  loading.value = true
  try {
    apply(await api.get<LlmConfig>('/api/access/llm/config'))
    cfgErr.value = ''
  } catch (e) {
    cfgErr.value = (e as Error).message
    toast('读取配置失败：' + (e as Error).message)
  } finally {
    loading.value = false
  }
}

function saveActor(): void {
  const v = actor.value.trim() || DEFAULT_ACTOR
  actor.value = v
  if (v === actorName()) return
  setActorName(v)
  toast('操作人已保存：' + v)
}

function resetForm(): void {
  editId.value = ''
  editName.value = ''
  picked.value = ''
  form.base = ''
  form.key = ''
  form.model = ''
  form.timeout = 90
  testRes.value = null
}

function openNew(): void {
  resetForm()
  editOpen.value = true
}

function openEdit(p: Profile): void {
  resetForm()
  editId.value = p.id
  editName.value = p.name
  form.base = p.base
  form.model = p.model
  form.timeout = p.timeout || 90
  picked.value = providers.value.find((x) => x.base === p.base)?.id || ''
  editOpen.value = true
}

function useProvider(id: string): void {
  const p = providers.value.find((x) => x.id === id)
  if (!p) return
  form.base = p.base
  if (!p.models.includes(form.model)) form.model = p.models[0] || ''
  testRes.value = null
}

// 手改 Base URL 后不再冒充某个预设
watch(() => form.base, (v) => {
  const p = providers.value.find((x) => x.id === picked.value)
  if (p && p.base !== v) picked.value = ''
})

async function saveProfile(): Promise<void> {
  if (!form.base.trim() || !form.model.trim()) {
    toast('Base URL 与模型名不能为空')
    return
  }
  if (!editing.value && !form.key.trim()) {
    toast('新增配置需要填写 API Key')
    return
  }
  if (editing.value && !form.key.trim() && !editProfile.value?.key_set) {
    toast('这套配置还没有密钥，请填写 API Key')
    return
  }
  saving.value = true
  try {
    apply(await api.post<LlmConfig>('/api/access/llm/config', {
      action: 'save',
      id: editId.value || undefined,
      name: editName.value.trim() || form.model.trim(),
      base: form.base.trim(),
      key: form.key.trim(),
      model: form.model.trim(),
      timeout: form.timeout,
      actor: actorName(),
    }))
    editOpen.value = false
    toast('已保存并启用，后续运行立即使用该模型')
  } catch (e) {
    toast('保存失败：' + (e as Error).message)
  } finally {
    saving.value = false
  }
}

async function testProfile(): Promise<void> {
  if (!editing.value && !form.key.trim()) {
    toast('先填 API Key 再测试连接')
    return
  }
  testing.value = true
  testRes.value = null
  try {
    testRes.value = await api.post<TestResp>('/api/access/llm/test', {
      base: form.base.trim(),
      key: form.key.trim(),
      model: form.model.trim(),
      timeout: form.timeout,
      profile_id: editId.value,
    })
  } catch (e) {
    testRes.value = { ok: false, ms: 0, error: (e as Error).message }
  } finally {
    testing.value = false
  }
}

async function activateProfile(p: Profile): Promise<void> {
  if (p.active || busyRow.value) return
  busyRow.value = 'activate:' + p.id
  try {
    apply(await api.post<LlmConfig>('/api/access/llm/config', {
      action: 'activate', id: p.id, name: p.name, actor: actorName(),
    }))
    toast(`已启用：${p.name}`)
  } catch (e) {
    toast('启用失败：' + (e as Error).message)
  } finally {
    busyRow.value = ''
  }
}

async function deleteProfile(p: Profile): Promise<void> {
  const ok = await confirmAsk({
    title: `删除配置「${p.name}」？`,
    message: p.active
      ? '这是当前使用中的配置：删除后会自动切到下一条；没有其它配置则回到规则引擎。已生成的结果与审计记录不受影响。'
      : '删除后不再出现在列表里；已生成的结果与审计记录不受影响。',
    confirmText: '删除配置',
    danger: true,
  })
  if (!ok) return
  busyRow.value = 'delete:' + p.id
  try {
    apply(await api.post<LlmConfig>('/api/access/llm/config', {
      action: 'delete', id: p.id, name: p.name, actor: actorName(),
    }))
    toast('已删除：' + p.name)
  } catch (e) {
    toast('删除失败：' + (e as Error).message)
  } finally {
    busyRow.value = ''
  }
}

async function clearActive(): Promise<void> {
  const ok = await confirmAsk({
    title: '停用当前模型配置？',
    message: '停用后立即回到规则引擎；配置仍留在列表里，随时可以重新启用。',
    confirmText: '停用',
    danger: true,
  })
  if (!ok) return
  try {
    apply(await api.post<LlmConfig>('/api/access/llm/config', {
      action: 'clear', clear: true, actor: actorName(),
    }))
    toast('已停用，已回到规则引擎')
  } catch (e) {
    toast('停用失败：' + (e as Error).message)
  }
}

onMounted(() => {
  load()
})
</script>

<template>
  <AppHeader :badge="badgeText" />

  <div class="wrap">
    <section class="card who">
      <div class="who-l">
        <div class="who-k">操作人署名</div>
      </div>
      <input
        v-model="actor" class="who-i" type="text" maxlength="24"
        placeholder="例如：王研究员 · 战略研究部"
        @keydown.enter="saveActor" @blur="saveActor"
      >
      <button class="btn btn-ghost btn-sm" @click="saveActor">保存署名</button>
    </section>

    <ModelProfilesCard
      :profiles="profiles" :loading="loading" :error="cfgErr"
      :active="!!cfg?.active" :current="currentModel" :busy-row="busyRow"
      @retry="load" @create="openNew" @clear="clearActive"
      @activate="activateProfile" @edit="openEdit" @remove="deleteProfile"
    />

    <AppModal
      :open="editOpen" :title="editing ? '编辑模型配置' : '新增模型配置'"
      width="640px" @close="editOpen = false"
    >
      <div class="mform">
        <div class="frow">
          <label>配置名称</label>
          <input v-model.trim="editName" type="text" maxlength="40" placeholder="例如：生产环境 / 内网网关（留空用模型名）">
        </div>
        <div class="frow">
          <label>服务商预设</label>
          <select v-model="picked" @change="useProvider(picked)">
            <option value="">自定义 / 手填</option>
            <optgroup v-for="g in groups" :key="g.name" :label="g.name">
              <option v-for="p in g.items" :key="p.id" :value="p.id">{{ p.name }}</option>
            </optgroup>
          </select>
        </div>
        <div class="frow">
          <label>Base URL</label>
          <input v-model.trim="form.base" type="text" spellcheck="false" placeholder="https://api.deepseek.com/v1">
        </div>
        <div class="frow">
          <label>API Key</label>
          <input
            v-model.trim="form.key" type="password" autocomplete="off"
            :placeholder="editing && editProfile?.key_set ? `已保存 ${editProfile.key_hint} · 留空沿用` : 'sk-...'"
          >
        </div>
        <div class="frow">
          <label>模型名</label>
          <input v-model.trim="form.model" type="text" spellcheck="false" list="llm-models" placeholder="deepseek-chat">
          <datalist id="llm-models">
            <option v-for="m in modelList" :key="m" :value="m" />
          </datalist>
        </div>
        <div class="frow">
          <label>超时（秒）</label>
          <input v-model.number="form.timeout" type="number" min="5" max="300">
        </div>
      </div>

      <div v-if="curNote" class="pnote">{{ curNote }}</div>

      <div v-if="testRes" class="test" :class="testRes.ok ? 'ok' : 'err'">
        <template v-if="testRes.ok">
          连接正常 · {{ testRes.ms }} ms · 返回「{{ testRes.reply || '空' }}」
        </template>
        <template v-else>
          连接失败 · {{ testRes.ms }} ms · {{ testRes.error }}
        </template>
      </div>

      <template #footer>
        <button class="btn btn-primary" :disabled="saving" @click="saveProfile">
          <span v-if="saving" class="spin"></span>保存并启用
        </button>
        <button class="btn" :disabled="testing" @click="testProfile">
          <span v-if="testing" class="spin"></span>测试连接
        </button>
        <button class="btn btn-ghost" @click="editOpen = false">取消</button>
      </template>
    </AppModal>

    <RegistryCard />
  </div>
</template>

<style scoped>
.wrap { width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 72px; }

/* 操作人署名 */
.who { display: flex; align-items: center; gap: 14px; padding: 16px 20px; margin-bottom: 16px; flex-wrap: wrap; }
.who-l { flex: 1; min-width: 220px; }
.who-k { font-size: 14px; font-weight: 650; }
.who-i { width: 280px; }

/* 新增 / 编辑弹窗 */
.mform { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 20px; }
.mform .frow:first-child { grid-column: span 2; }
@media (max-width: 640px) {
  .mform { grid-template-columns: 1fr; }
  .mform .frow:first-child { grid-column: span 1; }
}
.frow { display: flex; flex-direction: column; gap: 6px; }
.frow label { font-size: 12px; color: var(--mute); }
.frow input, .frow select { width: 100%; }

.pnote { margin-top: 12px; font-size: 11.5px; color: var(--warn); background: var(--warn-soft); border-radius: 6px; padding: 7px 10px; }
.btn .spin { border-color: rgba(26, 109, 213, .25); border-top-color: var(--accent); }

.test { margin-top: 14px; font-size: 12px; border-radius: 8px; padding: 9px 12px; line-height: 1.7; }
.test.ok { background: var(--ok-soft); color: var(--ok); }
.test.err { background: var(--err-soft); color: var(--err); word-break: break-all; }

@media (max-width: 640px) {
  .wrap { padding: 20px 16px 48px; }
  .who { padding: 14px 16px; }
  .who-i { width: 100%; }
}
</style>