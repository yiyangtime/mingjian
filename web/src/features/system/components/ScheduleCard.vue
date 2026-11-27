<script setup lang="ts">
/** 定时任务集中管理：创建入口留在业务页，治理页负责全局查看、启停与清理。 */
import { onMounted, ref } from 'vue'
import { api } from '../../../shared/api'
import { confirmAsk } from '../../../shared/composables/confirm'
import { toast } from '../../../shared/composables/toast'
import type { Schedule } from '../../../shared/types'

const rows = ref<Schedule[]>([])
const busy = ref(false)
const loaded = ref(false)
const acting = ref('')

const emit = defineEmits<{ (e: 'changed'): void }>()

const KIND_LABEL: Record<string, string> = { run: '企业洞察', compare: '竞品追踪' }

async function load(): Promise<void> {
  busy.value = true
  try {
    const d = await api.get<{ schedules: Schedule[] }>('/api/governance/schedules')
    rows.value = d.schedules || []
  } catch (e) {
    toast('读取定时任务失败：' + (e as Error).message)
  } finally {
    busy.value = false
    loaded.value = true
  }
}

async function act(action: 'run' | 'toggle' | 'del', s: Schedule): Promise<void> {
  if (action === 'del') {
    const ok = await confirmAsk({
      title: '删除这个定时任务？',
      message: `${s.name} 删除后不再自动触发；已生成的历史运行不受影响。`,
      confirmText: '删除任务', danger: true,
    })
    if (!ok) return
  }
  acting.value = s.id
  try {
    const body: Record<string, unknown> = { id: s.id }
    if (action === 'toggle') body.enabled = !s.enabled
    const url = {
      run: '/api/governance/schedules/run',
      toggle: '/api/governance/schedules/toggle',
      del: '/api/governance/schedules/delete',
    }[action]
    const d = await api.post<{ run_id?: string }>(url, body)
    if (action === 'run') toast('已触发运行：' + (d.run_id || '—'))
    else toast(action === 'del' ? '已删除' : s.enabled ? '已停用' : '已启用')
    load()
    emit('changed')
  } catch (e) {
    toast('操作失败：' + (e as Error).message)
  } finally {
    acting.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="card">
    <div class="card-h">
      <h3>定时任务</h3>
      <span class="cnt">共 {{ rows.length }} 个</span>
      <button class="btn btn-sm refresh" :disabled="busy" @click="load">{{ busy ? '读取中…' : '刷新' }}</button>
    </div>
    <div class="slist">
      <div v-if="!loaded" class="empty">加载中…</div>
      <div v-else-if="!rows.length" class="empty">还没有定时任务</div>
      <div v-for="s in rows" :key="s.id" class="srow">
        <div class="smain">
          <div class="sname">
            {{ s.name }}
            <span class="kind">{{ KIND_LABEL[s.kind] || s.kind }}</span>
            <span class="st" :class="{ off: !s.enabled }">{{ s.enabled ? '已启用' : '已停用' }}</span>
          </div>
          <div class="smeta">
            {{ s.describe || '—' }} · 下次 {{ s.enabled ? (s.next_run_at || '—') : '已停用' }}
            <template v-if="s.last_run_id"> · 最近 {{ s.last_run_id }}</template>
          </div>
        </div>
        <div class="sacts" :aria-busy="acting === s.id">
          <button class="btn btn-ghost btn-sm" :disabled="acting === s.id" @click="act('run', s)">立即运行</button>
          <button class="btn btn-ghost btn-sm" :disabled="acting === s.id" @click="act('toggle', s)">{{ s.enabled ? '停用' : '启用' }}</button>
          <button class="btn btn-ghost btn-sm" :disabled="acting === s.id" @click="act('del', s)">删除</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.card-h { padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.cnt { font-size: 11.5px; color: var(--mute); }
.refresh { margin-left: auto; }
.slist { padding: 4px 20px 10px; }
.srow { display: flex; gap: 12px; align-items: flex-start; padding: 11px 0; border-bottom: 1px solid #F1F3F6; }
.srow:last-child { border-bottom: none; }
.smain { flex: 1; min-width: 0; }
.sname { font-size: 13px; line-height: 1.7; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.kind { font-size: 10.5px; padding: 2px 8px; border-radius: 10px; background: var(--accent-soft); color: var(--accent); }
.st { font-size: 10.5px; padding: 2px 8px; border-radius: 10px; background: #E8F5EC; color: #1B7A3D; }
.st.off { background: #F1F3F6; color: var(--mute); }
.smeta { font-size: 11px; color: var(--mute); line-height: 1.7; word-break: break-word; }
.sacts { flex: none; display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.empty { padding: 26px 4px; font-size: 12.5px; color: var(--mute); }
@media (max-width: 720px) {
  .srow { flex-direction: column; }
  .sacts { justify-content: flex-start; }
}
</style>