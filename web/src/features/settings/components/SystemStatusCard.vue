<script setup lang="ts">
/** 系统与服务状态：纯展示，数据由视图拉取，刷新用事件回抛。 */
import { durationText, fmtBytes } from '../../../shared/lib/format'
import type { SystemInfo } from '../types'

defineProps<{
  sys: SystemInfo | null
  error: string
}>()

const emit = defineEmits<{ (e: 'refresh'): void }>()
</script>

<template>
  <section class="card section-gap">
    <div class="card-h">
      <h3>系统与服务状态</h3>
      <span class="st" :class="{ on: !error }">{{ error ? '服务未连接' : '服务正常' }}</span>
      <button class="btn btn-ghost btn-sm" @click="emit('refresh')">刷新</button>
    </div>
    <div v-if="error" class="syserr">读取失败：{{ error }}</div>
    <div v-else-if="sys" class="sysgrid">
      <div class="sys"><div class="k">版本</div><div class="v">v{{ sys.version }} · {{ sys.server }}</div></div>
      <div class="sys"><div class="k">运行时长</div><div class="v">{{ durationText(sys.uptime_sec) }}</div></div>
      <div class="sys"><div class="k">Python / 平台</div><div class="v">{{ sys.python }} · {{ sys.platform }}</div></div>
      <div class="sys"><div class="k">定时调度</div><div class="v">{{ sys.scheduler.running ? '线程运行中' : '未启动' }}</div></div>
      <div class="sys wide"><div class="k">数据库</div><div class="v">{{ sys.db_backend }}<template v-if="sys.db_bytes"> · {{ fmtBytes(sys.db_bytes) }}</template></div></div>
      <div class="sys wide">
        <div class="k">数据规模</div>
        <div class="v">
          运行 {{ sys.counts.runs || 0 }} · 材料 {{ sys.counts.documents || 0 }} ·
          事实 {{ sys.counts.facts || 0 }} · 审计 {{ sys.counts.audit || 0 }} · 记忆 {{ sys.counts.memory || 0 }}
        </div>
      </div>
      <div class="sys wide">
        <div class="k">模型</div>
        <div class="v">{{ sys.llm.active ? sys.llm.model + ' · ' + sys.llm.base : '规则引擎（未接入模型）' }}</div>
      </div>
      <div class="sys wide"><div class="k">产出</div><div class="v">报告 {{ sys.output.reports }} · 事实表 {{ sys.output.tables }}</div></div>
    </div>
  </section>
</template>

<style scoped>
.card-h { padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.st { margin-left: auto; font-size: 11px; padding: 2px 9px; border-radius: 11px; background: #F1F3F6; color: var(--sub); }
.st.on { background: var(--ok-soft); color: var(--ok); }
.section-gap { margin-top: 18px; }

.sysgrid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px 22px; padding: 18px 20px 12px; }
@media (max-width: 1000px) { .sysgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 640px) { .sysgrid { grid-template-columns: 1fr; } .sys.wide { grid-column: span 1; } }
.sys .k { font-size: 11.5px; color: var(--mute); }
.sys .v { font-size: 13px; margin-top: 3px; line-height: 1.7; word-break: break-all; }
.sys.wide { grid-column: span 2; }
.syserr {
  margin: 14px 18px; padding: 10px 12px; border-radius: 8px;
  background: var(--err-soft); color: var(--err); font-size: 12px; line-height: 1.7;
}
</style>