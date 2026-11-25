import { createRouter, createWebHistory } from 'vue-router'
import CompanyView from '../features/company/CompanyView.vue'

// 六个工作台共用一套后端；路由与后端 SPA 回退一一对应。
// 历史模式：?run=<编号> 这类深链接原样保留在地址栏，便于汇报时直接跳转。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'company', component: CompanyView, meta: { title: '明鉴 · 产业情报' } },
    { path: '/policy', name: 'policy', component: () => import('../features/policy/PolicyView.vue'), meta: { title: '政策公告 · 明鉴' } },
    { path: '/competitor', name: 'competitor', component: () => import('../features/competitor/CompetitorView.vue'), meta: { title: '竞品周报 · 明鉴' } },
    { path: '/metrics', name: 'metrics', component: () => import('../features/metrics/MetricsView.vue'), meta: { title: '指标联动 · 明鉴' } },
    { path: '/agent', name: 'agent', component: () => import('../features/agent/AgentView.vue'), meta: { title: '智能体 · 明鉴' } },
    { path: '/settings', name: 'settings', component: () => import('../features/settings/SettingsView.vue'), meta: { title: '接入配置 · 明鉴' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

router.afterEach((to) => {
  const t = (to.meta.title as string) || '明鉴'
  document.title = t
})

export default router