<script setup lang="ts">
/** 审计日志：筛选条件用 v-model 回写视图，读取仍由视图负责。 */
import { computed, nextTick } from 'vue'
import type { AuditRow } from '../types'

const props = defineProps<{
  rows: AuditRow[]
  total: number
  actions: string[]
  busy: boolean
  q: string
  action: string
}>()

const emit = defineEmits<{
  (e: 'update:q', v: string): void
  (e: 'update:action', v: string): void
  (e: 'refresh'): void
}>()

const qModel = computed({ get: () => props.q, set: (v: string) => emit('update:q', v) })
const actionModel = computed({ get: () => props.action, set: (v: string) => emit('update:action', v) })

// 等 v-model 把筛选条件写回父级后再拉取，避免用上一次的条件查
function refresh(): void {
  nextTick(() => emit('refresh'))
}

const ACTION_LABEL: Record<string, string> = {
  llm_config: '保存模型配置', llm_clear: '清除模型配置', llm_activate: '启用模型配置',
  llm_delete: '删除模型配置',
  registry_config: '保存工商数据源', registry_activate: '启用工商数据源',
  registry_delete: '删除工商数据源', registry_clear: '停用工商数据源',
  skill_run: '执行技能', memory_write: '写入记忆', memory_delete: '删除记忆',
  memory_harvest: '沉淀记忆', cancel: '取消运行', resume: '断点续跑',
  enqueue: '定时入队', run: '自动生成', confirm: '确认事实', correct: '修正字段',
  reject: '事实存疑', note: '备注', parse: '解析材料', gov_fetch: '抓取政策',
  subject_create: '新建主体档案', subject_save: '保存主体档案',
  schedule_save: '保存定时任务', schedule_delete: '删除定时任务',
  schedule_toggle: '启停定时任务',
}

function actionLabel(a?: string): string {
  return ACTION_LABEL[a || ''] || a || '—'
}
</script>

<template>
  <section class="card">
    <div class="card-h">
      <h3>审计日志</h3>
      <span class="cnt">{{ rows.length < total ? `共 ${total} 条 · 显示最近 ${rows.length} 条` : `共 ${total} 条` }}</span>
      <div class="atools">
        <input v-model="qModel" type="search" placeholder="搜动作 / 对象 / 原因 / 操作人" @keydown.enter="refresh">
        <select v-model="actionModel" @change="refresh">
          <option value="">全部动作</option>
          <option v-for="a in actions" :key="a" :value="a">{{ actionLabel(a) }}</option>
        </select>
        <button class="btn btn-sm" :disabled="busy" @click="refresh">{{ busy ? '读取中…' : '刷新' }}</button>
      </div>
    </div>
    <div class="alist">
      <div v-if="!rows.length" class="empty">{{ busy ? '读取中…' : '还没有审计记录' }}</div>
      <div v-for="a in rows" :key="a.id" class="arow">
        <span class="atime">{{ a.ts }}</span>
        <span class="atag">{{ actionLabel(a.action) }}</span>
        <div class="abody">
          <div class="at">
            {{ a.target || a.entity_key || '—' }}
            <span v-if="a.reason" class="areason"> · {{ a.reason }}</span>
          </div>
          <div class="am">
            <span>{{ a.actor || '—' }}</span>
            <RouterLink v-if="a.run_id" :to="'/?run=' + a.run_id" class="alink">运行 {{ a.run_id }}</RouterLink>
            <span v-if="a.old_value && a.new_value && a.old_value !== a.new_value" class="adelta">{{ a.old_value }} → {{ a.new_value }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.card-h { padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.cnt { font-size: 11.5px; color: var(--mute); }

.atools { margin-left: auto; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.atools input { width: 240px; }
.alist { max-height: 420px; overflow: auto; padding: 4px 20px 10px; }
.arow { display: flex; gap: 12px; align-items: flex-start; padding: 11px 0; border-bottom: 1px solid #F1F3F6; }
.arow:last-child { border-bottom: none; }
.atime {
  flex: 0 0 138px; font-family: ui-monospace, Consolas, monospace;
  font-size: 11px; color: var(--mute); line-height: 1.9;
}
.atag {
  flex: none; font-size: 10.5px; padding: 2px 8px; border-radius: 10px;
  background: var(--accent-soft); color: var(--accent); margin-top: 1px; white-space: nowrap;
}
.abody { flex: 1; min-width: 0; }
.at { font-size: 13px; line-height: 1.7; word-break: break-word; }
.areason { color: var(--sub); }
.am { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 3px; font-size: 11px; color: var(--mute); }
.alink { color: var(--accent); text-decoration: none; }
.alink:hover { text-decoration: underline; }
.adelta { color: var(--warn); }
.empty { padding: 26px 4px; font-size: 12.5px; color: var(--mute); }
</style>