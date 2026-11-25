<script setup lang="ts">
import { ref, watch } from 'vue'
import AppModal from '../../../shared/components/AppModal.vue'
import { api } from '../../../shared/api'
import type { JudgmentTrail } from '../../../shared/types'

const props = defineProps<{ open: boolean; rid: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const data = ref<JudgmentTrail | null>(null)
const busy = ref(false)
const err = ref('')

watch([() => props.open, () => props.rid], async ([open, rid]) => {
  if (!open || !rid) return
  busy.value = true
  err.value = ''
  try {
    data.value = await api.get<JudgmentTrail>(`/api/company/judgments/${rid}`)
  } catch (e) {
    data.value = null
    err.value = (e as Error).message
  } finally {
    busy.value = false
  }
})

function srcLabel(j: { source?: string; support?: number }): string {
  const base = j.source === 'llm' ? '模型改写' : '规则生成'
  return typeof j.support === 'number' ? `${base} · 表述重合 ${Math.round(j.support * 100)}%` : base
}
</script>

<template>
  <AppModal
    :open="open"
    title="判断留痕"
    :sub="data ? `规则 ${data.requested ?? data.rule.length} 条，采用 ${data.accepted ?? 0} 条，拦截 ${data.rejected.length} 条 ｜ 模型：${data.model}` : '规则版与改写版逐条对照'"
    width="760px"
    @close="emit('close')"
  >
    <div v-if="busy" class="mute">加载中…</div>
    <div v-else-if="err" class="mute">{{ err }}</div>
    <template v-else-if="data">
      <div class="grp">
        <h4>最终采用 <span class="n">{{ data.final.length }}</span></h4>
        <div v-for="(j, i) in data.final" :key="i" class="item">
          <div class="tx">{{ j.text }}</div>
          <div class="mt">
            <span class="tag">{{ j.citations.join('、') }}</span>
            <span class="mute">{{ srcLabel(j) }}</span>
          </div>
        </div>
        <div v-if="!data.final.length" class="mute">没有可用判断。</div>
      </div>

      <div v-if="data.rejected.length" class="grp">
        <h4>被拦截 <span class="n bad">{{ data.rejected.length }}</span></h4>
        <div v-for="(r, i) in data.rejected" :key="i" class="item bad">
          <div class="tx">{{ r.text }}</div>
          <div class="mt bad">{{ r.reason }}</div>
        </div>
      </div>

      <div class="grp">
        <h4>规则原句 <span class="n">{{ data.rule.length }}</span></h4>
        <div v-for="(j, i) in data.rule" :key="i" class="item rule">
          <div class="tx">{{ i + 1 }}. {{ j.text }}</div>
          <div class="mt"><span class="tag">{{ j.citations.join('、') }}</span></div>
        </div>
      </div>

      <details v-if="data.raw" class="grp">
        <summary>模型原始输出（截断）</summary>
        <pre class="raw">{{ data.raw }}</pre>
      </details>
    </template>
  </AppModal>
</template>

<style scoped>
.mute { color: var(--mute); font-size: 13px; line-height: 1.7; }
.grp { margin-bottom: 18px; }
.grp h4 { margin: 0 0 8px; font-size: 13px; font-weight: 650; }
.n { margin-left: 4px; font-weight: 500; color: var(--mute); }
.n.bad { color: #B45309; }
.item { padding: 9px 11px; border: 1px solid var(--line); border-radius: 9px; margin-bottom: 8px; }
.item.bad { border-color: #F3D6B4; background: #FFFBF4; }
.item.rule { background: #FAFBFC; }
.tx { font-size: 13px; line-height: 1.68; }
.mt { margin-top: 5px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; }
.mt.bad { color: #B45309; }
.tag { padding: 1px 7px; border-radius: 6px; background: #F1F4F8; color: var(--sub); font-size: 11px; }
.raw { margin: 6px 0 0; padding: 10px; background: #F7F8FA; border-radius: 8px; font-size: 11px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
summary { cursor: pointer; font-size: 13px; font-weight: 650; }
</style>