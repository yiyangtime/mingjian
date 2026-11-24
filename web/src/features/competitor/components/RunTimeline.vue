<script setup lang="ts">
/** 执行时间轴：步骤条 + 日志窗。日志窗只在组件内部滚到底，不向上抛状态。 */
import { nextTick, ref, watch } from 'vue'
import type { LogLine, StepRow } from '../types'

const props = defineProps<{
  steps: StepRow[]
  logs: LogLine[]
}>()

const logsEl = ref<HTMLElement | null>(null)

watch(
  () => props.logs.length,
  () => nextTick(() => {
    if (logsEl.value) logsEl.value.scrollTop = logsEl.value.scrollHeight
  }),
)
</script>

<template>
  <div class="steps">
    <div v-if="!steps.length" class="empty">正在提交任务…</div>
    <div v-for="r in steps" :key="r.key" class="step">
      <span class="dot" :class="r.status === 'done' ? 'done' : 'run'">{{ r.status === 'done' ? '✓' : '…' }}</span>
      <span class="nm">{{ r.name }}</span>
      <span class="dt">{{ r.detail }}</span>
    </div>
  </div>
  <div ref="logsEl" class="logs logs-box">
    <div v-for="(l, i) in logs" :key="i">
      <span class="t">{{ l.ts }}</span>
      <span :class="{ w: l.level === 'warn', e: l.level === 'error', o: l.level === 'ok' }">{{ l.msg }}</span>
    </div>
  </div>
</template>

<style scoped>
.steps { padding: 12px 20px 10px; }
.step { display: flex; gap: 10px; align-items: center; padding: 8px 0; font-size: 13.5px; }
.step .dot {
  width: 23px; height: 23px; border-radius: 50%; flex: 0 0 23px;
  display: grid; place-items: center; font-size: 11px; font-weight: 600;
  background: #EFF3FF; color: #1D4ED8; transition: .2s;
}
.step .dot.done { background: var(--ok-soft); color: var(--ok); }
.step .nm { flex: 1; }
.step .dt { font-size: 11.5px; color: var(--mute); }
.logs-box { height: 250px; margin: 0 20px 20px; }
</style>