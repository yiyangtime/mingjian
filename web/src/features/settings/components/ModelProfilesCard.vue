<script setup lang="ts">
/** 模型服务列表：只负责展示与派发动作，启用/删除等副作用留在视图。 */
import type { Profile } from '../types'

defineProps<{
  profiles: Profile[]
  loading: boolean
  error: string
  active: boolean
  current: string
  busyRow: string
}>()

const emit = defineEmits<{
  (e: 'retry'): void
  (e: 'create'): void
  (e: 'clear'): void
  (e: 'activate', p: Profile): void
  (e: 'edit', p: Profile): void
  (e: 'remove', p: Profile): void
}>()

function secs(t: number): string {
  const n = Number(t) || 0
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}
</script>

<template>
  <section class="card">
    <div class="card-h">
      <h3>模型服务</h3>
      <span class="cnt">{{ profiles.length }} 套</span>
      <span class="st" :class="{ on: active }">{{ active ? '已启用' : '规则引擎' }}</span>
      <div class="htools">
        <button v-if="active" class="btn btn-ghost btn-sm" @click="emit('clear')">停用当前</button>
        <button class="btn btn-primary btn-sm" @click="emit('create')">＋ 新增配置</button>
      </div>
    </div>

    <div v-if="loading" class="empty">读取配置…</div>
    <div v-else-if="error" class="blank">
      <div class="blank-t">配置读取失败</div>
      <div class="blank-m">{{ error }}</div>
      <button class="btn btn-sm" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!profiles.length" class="blank">
      <div class="blank-t">还没有模型配置</div>
      <button class="btn btn-primary btn-sm" @click="emit('create')">新增第一套配置</button>
    </div>
    <div v-else class="plist">
      <div v-for="p in profiles" :key="p.id" class="prow" :class="{ on: p.active }">
        <div class="pmain">
          <div class="ptop">
            <b>{{ p.name }}</b>
            <span class="ptag" :class="{ on: p.active }">{{ p.active ? '使用中' : '未启用' }}</span>
          </div>
          <div class="pmeta">
            <span class="pmodel">{{ p.model }}</span>
            <span class="pbase">{{ p.base }}</span>
          </div>
          <div class="pmeta2">密钥 {{ p.key_set ? p.key_hint : '未设置' }} · 超时 {{ secs(p.timeout) }} 秒</div>
        </div>
        <div class="pacts">
          <button
            v-if="!p.active" class="btn btn-ghost btn-sm"
            :disabled="!!busyRow" @click="emit('activate', p)"
          >{{ busyRow === 'activate:' + p.id ? '启用中…' : '启用' }}</button>
          <button class="btn btn-ghost btn-sm" :disabled="!!busyRow" @click="emit('edit', p)">编辑</button>
          <button
            class="btn btn-ghost btn-sm danger" :disabled="!!busyRow"
            @click="emit('remove', p)"
          >{{ busyRow === 'delete:' + p.id ? '删除中…' : '删除' }}</button>
        </div>
      </div>
    </div>
    <div class="pfoot">当前：{{ active ? current : '规则引擎' }}</div>
  </section>
</template>

<style scoped>
.card-h { padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.card-h h3 { margin: 0; font-size: 14.5px; font-weight: 650; }
.cnt { font-size: 11.5px; color: var(--mute); }
.st { margin-left: auto; font-size: 11px; padding: 2px 9px; border-radius: 11px; background: #F1F3F6; color: var(--sub); }
.st.on { background: var(--ok-soft); color: var(--ok); }
.htools { display: flex; align-items: center; gap: 8px; }

.plist { padding: 4px 0; }
.prow {
  display: flex; align-items: flex-start; gap: 14px;
  padding: 14px 18px; border-bottom: 1px solid #F1F3F6;
}
.prow:last-child { border-bottom: none; }
.prow.on { background: #FBFDFF; box-shadow: inset 3px 0 0 var(--accent); }
.pmain { flex: 1; min-width: 0; }
.ptop { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ptop b { font-size: 13.5px; font-weight: 650; }
.ptag { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #F1F3F6; color: var(--mute); }
.ptag.on { background: var(--ok-soft); color: var(--ok); }
.pmeta { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-top: 3px; }
.pmodel { font-size: 12px; color: var(--sub); font-family: ui-monospace, Consolas, monospace; }
.pbase { font-size: 11px; color: var(--mute); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.pmeta2 { font-size: 11px; color: var(--mute); margin-top: 2px; }
.pacts { flex: none; display: flex; gap: 6px; align-items: center; }
.pacts .danger { color: var(--err); }
.pfoot {
  padding: 10px 18px 14px; font-size: 11.5px; color: var(--mute);
  line-height: 1.8; border-top: 1px dashed var(--line);
}
.blank { padding: 32px 20px; text-align: center; }
.blank-t { font-size: 14px; font-weight: 650; }
.blank-t + .btn { margin-top: 16px; }
.blank-m {
  font-size: 12px; color: var(--mute); line-height: 1.85;
  max-width: 560px; margin: 8px auto 16px;
}
.empty { padding: 26px 4px; font-size: 12.5px; color: var(--mute); }
</style>