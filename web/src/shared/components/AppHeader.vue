<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import HelpDrawer from './HelpDrawer.vue'
import NavIcon from './NavIcon.vue'
import type { NavIconName } from './navIcons'
import { api } from '../api'
import { openHelp } from '../composables/help'

defineProps<{
  badge?: string
  state?: string
  task?: string
}>()

const route = useRoute()

// 导航只有一份定义：改名改这里，所有页面自动一致
const items: { to: string; label: string; icon: NavIconName }[] = [
  { to: '/', label: '公司情报', icon: 'company' },
  { to: '/policy', label: '政策公告', icon: 'policy' },
  { to: '/competitor', label: '竞品周报', icon: 'competitor' },
  { to: '/metrics', label: '指标联动', icon: 'metrics' },
  { to: '/agent', label: '智能体', icon: 'agent' },
  { to: '/settings', label: '模型配置', icon: 'settings' },
]

const path = computed(() => route.path)
function isOn(to: string): boolean {
  return to === '/' ? path.value === '/' : path.value.startsWith(to)
}

/* 服务状态：只做一个轻量心跳，断开时给出明确提示，而不是让页面静默失败 */
const health = ref<{ ok: boolean; version?: string; llm?: boolean } | null>(null)
let pingTimer: number | undefined

async function ping(): Promise<void> {
  try {
    health.value = await api.get<{ ok: boolean; version?: string; llm?: boolean }>('/api/health')
  } catch {
    health.value = { ok: false }
  }
}

onMounted(() => {
  ping()
  pingTimer = window.setInterval(ping, 60000)
})
onBeforeUnmount(() => {
  if (pingTimer) window.clearInterval(pingTimer)
})
</script>

<template>
  <header class="app-header">
    <div class="hwrap">
      <div class="hside">
        <div class="logo">鉴</div>
        <div class="brand">
          明鉴 <span class="en">MingJian</span>
        </div>
      </div>
      <nav class="nav">
        <RouterLink
          v-for="it in items" :key="it.to" :to="it.to"
          :data-tone="it.icon" :class="{ on: isOn(it.to) }"
        >
          <NavIcon :name="it.icon" />
          <span class="nav-label">{{ it.label }}</span>
        </RouterLink>
      </nav>
      <div class="hstate">
        <span
          class="svc" :class="{ off: health && !health.ok, wait: !health }"
          :title="health && !health.ok ? '后端服务已断开，请确认 python -m mingjian serve 仍在运行' : '后端服务正常'"
        ><i></i>{{ health ? (health.ok ? '服务正常' : '服务断开') : '连接中' }}</span>
        <span v-if="badge" class="badge gray">{{ badge }}</span>
        <span v-if="state" class="badge">{{ state }}</span>
        <span v-if="task" class="task-id">{{ task }}</span>
        <button class="help" type="button" title="使用说明" aria-label="使用说明" @click="openHelp">?</button>
      </div>
    </div>
  </header>
  <HelpDrawer :version="health?.version" />
</template>

<style scoped>
.app-header {
  position: sticky; top: 0; z-index: 20;
  background: rgba(255, 255, 255, .88);
  backdrop-filter: saturate(180%) blur(14px);
  border-bottom: 1px solid rgba(229, 233, 240, .9);
  box-shadow: 0 1px 2px rgba(20, 29, 45, .03);
}
.hwrap {
  width: 100%; min-height: 62px; padding: 8px clamp(18px, 2.2vw, 32px);
  display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center; gap: 18px;
}
.hside { display: flex; align-items: center; gap: 11px; min-width: 0; }
.hstate {
  justify-self: end; display: flex; align-items: center; gap: 8px;
  min-width: 0; flex-wrap: wrap; justify-content: flex-end;
}
.logo {
  width: 34px; height: 34px; border-radius: 10px;
  background: linear-gradient(145deg, #3A7DE4, var(--accent) 58%, #1655B4);
  color: #fff; display: grid; place-items: center; font-weight: 700; font-size: 16px;
  box-shadow: 0 4px 10px rgba(31, 102, 208, .28), inset 0 1px 0 rgba(255, 255, 255, .28);
}
.brand { font-weight: 650; letter-spacing: .02em; font-size: 15px; white-space: nowrap; }
.brand .en {
  font-weight: 500; color: var(--mute); font-size: 10px; margin-left: 6px;
  letter-spacing: .10em; text-transform: uppercase;
}
.badge {
  font-size: 11.5px; font-weight: 500; padding: 4px 10px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent); white-space: nowrap;
  border: 1px solid var(--accent-line);
}
.badge.gray { background: #F4F6FA; color: var(--sub); border-color: var(--line); }
.svc {
  display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 500;
  color: var(--ok); white-space: nowrap; padding: 4px 10px; border-radius: 999px;
  background: #fff; border: 1px solid var(--line);
}
.svc i { width: 6px; height: 6px; border-radius: 50%; background: var(--ok); display: block; }
.svc.off { color: var(--err); }
.svc.off i { background: var(--err); }
.svc.wait { color: var(--mute); }
.svc.wait i { background: var(--mute); }
.help {
  width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--line-strong);
  background: #fff; color: var(--sub); cursor: pointer; font-size: 13px; font-weight: 600;
  display: grid; place-items: center; transition: .14s; flex: 0 0 auto;
}
.help:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
.task-id {
  font-family: var(--mono); font-size: 11.5px; color: var(--mute);
  max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.nav {
  display: flex; gap: 4px; justify-self: center; max-width: 100%;
  overflow-x: auto; scrollbar-width: none;
}
.nav::-webkit-scrollbar { display: none; }
/* 六个入口各用一组渐变：文字、图标与底色同色系，互不混淆 */
.nav a {
  --c1: #1D4ED8;
  --c2: #0369A1;
  --tint: color-mix(in srgb, var(--c1) 9%, transparent);
  --tint2: color-mix(in srgb, var(--c2) 5%, transparent);
  --tint-strong: color-mix(in srgb, var(--c1) 16%, transparent);
  --tint2-strong: color-mix(in srgb, var(--c2) 8%, transparent);
  --edge: color-mix(in srgb, var(--c1) 26%, transparent);
  position: relative;
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 500; color: var(--c1); text-decoration: none;
  padding: 8px 13px; border: 1px solid transparent; border-radius: 10px;
  background: linear-gradient(135deg, var(--tint), var(--tint2));
  white-space: nowrap;
  transition: background .16s, border-color .16s, box-shadow .16s;
}
.nav a[data-tone="company"] { --c1: #1D4ED8; --c2: #0369A1; }
.nav a[data-tone="policy"] { --c1: #6D28D9; --c2: #7E22CE; }
.nav a[data-tone="competitor"] { --c1: #C2410C; --c2: #BE123C; }
.nav a[data-tone="metrics"] { --c1: #047857; --c2: #0F766E; }
.nav a[data-tone="agent"] { --c1: #BE185D; --c2: #A21CAF; }
.nav a[data-tone="settings"] { --c1: #B45309; --c2: #A16207; }
.nav a .nav-label { color: var(--c1); }
@supports ((-webkit-background-clip: text) or (background-clip: text)) {
  .nav a .nav-label {
    background-image: linear-gradient(100deg, var(--c1), var(--c2));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    -webkit-text-fill-color: transparent;
  }
}
.nav a :deep(.nav-icon) { opacity: .86; transition: opacity .16s; }
.nav a:hover :deep(.nav-icon), .nav a.on :deep(.nav-icon) { opacity: 1; }
.nav a:hover, .nav a.on {
  background: linear-gradient(135deg, var(--tint-strong), var(--tint2-strong));
  border-color: var(--edge);
  box-shadow: 0 3px 10px var(--tint);
}
.nav a.on { font-weight: 600; }
.nav a.on::after {
  content: '';
  position: absolute; left: 13px; right: 13px; bottom: 3px; height: 2px;
  border-radius: 2px; background: linear-gradient(90deg, var(--c1), var(--c2));
}
.nav a:focus-visible { outline: 2px solid var(--c1); outline-offset: 2px; }
@media (max-width: 1180px) {
  .hwrap { grid-template-columns: minmax(0, 1fr) auto; row-gap: 6px; }
  .hside { grid-column: 1; }
  .hstate { grid-column: 2; }
  .nav {
    grid-column: 1 / -1; justify-self: stretch; justify-content: flex-start;
    padding-bottom: 2px; border-top: 1px solid #F0F3F7; padding-top: 5px;
  }
  .nav a { padding: 6px 11px; font-size: 12.5px; }
  .task-id { display: none; }
  .svc { display: none; }
}
@media (max-width: 640px) {
  .brand .en { display: none; }
  .badge { display: none; }
}
</style>