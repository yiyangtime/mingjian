<script setup lang="ts">
/** 监测名单：只负责展示与派发，增删改与档案补全的副作用留在视图。 */
import type { Monitor } from '../types'
import { monitorType, siteLabel, siteUrl } from '../monitor'

defineProps<{
  monitors: Monitor[]
  enriching: boolean
  max: number
}>()

const emit = defineEmits<{
  (e: 'edit', m: Monitor): void
  (e: 'remove', name: string): void
  (e: 'add'): void
}>()
</script>

<template>
  <div class="mon-box">
    <div class="mon-table">
      <div class="mon-tr mon-th">
        <span>公司名</span>
        <span>公司类型</span>
        <span>所处产业</span>
        <span>官网</span>
        <span class="mc-act">操作</span>
      </div>
      <div v-for="m in monitors" :key="m.name" class="mon-tr">
        <span class="mc-name">
          <b>{{ m.name }}</b>
          <small v-if="m.full_name && m.full_name !== m.name" :title="m.full_name">{{ m.full_name }}</small>
        </span>
        <span>
          <span class="mtag" :class="m.code ? 'listed' : 'unlisted'">{{ monitorType(m) }}</span>
        </span>
        <span class="mc-ind" :title="m.business || m.industry || ''">
          {{ m.industry || (enriching ? '补全中…' : '未标注') }}
        </span>
        <span class="mc-site">
          <a
            v-if="siteUrl(m.website)" :href="siteUrl(m.website)" target="_blank"
            rel="noopener noreferrer" :title="m.website"
          >{{ siteLabel(m.website) }}</a>
          <span v-else class="none" title="编辑监测对象可补填官网">—</span>
        </span>
        <span class="mc-act">
          <button type="button" class="btn btn-ghost btn-xs" @click="emit('edit', m)">编辑</button>
          <button
            type="button" class="mon-x" :aria-label="'移除 ' + m.name"
            :title="'移除 ' + m.name" @click="emit('remove', m.name)"
          >×</button>
        </span>
      </div>
      <div v-if="!monitors.length" class="mon-empty">暂无监测对象</div>
    </div>
    <button
      type="button" class="btn btn-ghost btn-sm mon-add" title="新增监测对象" aria-label="新增监测对象"
      :disabled="monitors.length >= max" @click="emit('add')"
    >＋ 新增</button>
  </div>
</template>

<style scoped>
.mon-box {
  display: flex; align-items: flex-start; gap: 12px;
  border: 1px solid var(--line-strong); border-radius: 12px; background: #fff;
  padding: 0 12px 0 0; min-height: 46px; overflow: hidden; box-shadow: var(--shadow-card);
}
.mon-table { flex: 1; min-width: 0; overflow-x: auto; }
.mon-tr {
  display: grid;
  grid-template-columns: minmax(160px, 1.5fr) 132px minmax(120px, 1fr) minmax(120px, 1fr) 96px;
  gap: 12px; align-items: center; min-width: 680px;
  padding: 10px 12px; border-bottom: 1px solid #F1F3F6; font-size: 13px;
}
.mon-tr:last-child { border-bottom: none; }
.mon-tr:not(.mon-th):hover { background: #FBFCFE; }
.mon-th {
  font-size: 11.5px; color: var(--mute); font-weight: 550; background: #F8FAFC;
  border-bottom: 1px solid var(--line); padding-top: 9px; padding-bottom: 9px;
}
.mc-name { min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.mc-name b { font-size: 13.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mc-name small { font-size: 10.5px; color: var(--mute); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mtag { display: inline-block; font-size: 11px; border-radius: 5px; padding: 2px 7px; white-space: nowrap; }
.mtag.listed { color: #1D4ED8; background: #EFF3FF; }
.mtag.unlisted { color: #5B6472; background: #F2F4F7; }
.mc-ind { color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mc-site { min-width: 0; display: flex; align-items: center; }
.mc-site a {
  font-size: 12.5px; color: var(--accent); text-decoration: none;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mc-site a:hover { text-decoration: underline; }
.mc-site .none { color: #B0B7C3; }
.mc-act { display: flex; justify-content: flex-end; align-items: center; gap: 7px; }
.mon-x {
  flex: none; width: 22px; height: 22px; border: none; border-radius: 6px;
  background: #F2F4F7; color: var(--mute); font-size: 14px;
  line-height: 1; cursor: pointer;
}
.mon-x:hover { background: #fff; color: var(--err); box-shadow: inset 0 0 0 1px #F3C2C2; }
.mon-empty { font-size: 12.5px; color: var(--mute); line-height: 28px; padding: 10px 12px; }
.mon-add { flex: none; margin-top: 9px; }
</style>