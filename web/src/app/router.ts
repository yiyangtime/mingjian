import { createRouter, createWebHistory } from 'vue-router'
import CompanyView from '../features/company/CompanyView.vue'

// 七个工作台共用一套后端；路由与后端 SPA 回退一一对应。
// 历史模式：?run=<编号> 这类深链接原样保留在地址栏，便于汇报时直接跳转。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'company', component: CompanyView, meta: { title: '企业洞察 · 明鉴' } },
    { path: '/policy-radar', name: 'policyRadar', component: () => import('../features/policy/PolicyView.vue'), meta: { title: '政策雷达 · 明鉴' } },
    { path: '/competitor-tracking', name: 'competitorTracking', component: () => import('../features/competitor/CompetitorView.vue'), meta: { title: '竞品追踪 · 明鉴' } },
    { path: '/metric-insight', name: 'metricInsight', component: () => import('../features/metrics/MetricsView.vue'), meta: { title: '指标透视 · 明鉴' } },
    { path: '/research-assistant', name: 'researchAssistant', component: () => import('../features/agent/AgentView.vue'), meta: { title: '研究助理 · 明鉴' } },
    { path: '/governance', name: 'governance', component: () => import('../features/system/SystemView.vue'), meta: { title: '运行治理 · 明鉴' } },
    { path: '/access-center', name: 'accessCenter', component: () => import('../features/settings/SettingsView.vue'), meta: { title: '接入中心 · 明鉴' } },
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