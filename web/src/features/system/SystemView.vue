<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppHeader from '../../shared/components/AppHeader.vue'
import { api } from '../../shared/api'
import { toast } from '../../shared/composables/toast'
import SystemStatusCard from './components/SystemStatusCard.vue'
import ScheduleCard from './components/ScheduleCard.vue'
import AuditCard from './components/AuditCard.vue'
import type { AuditRow, SystemInfo } from './types'

const sys = ref<SystemInfo | null>(null)
const sysErr = ref('')

const auditRows = ref<AuditRow[]>([])
const auditTotal = ref(0)
const auditActions = ref<string[]>([])
const auditQ = ref('')
const auditAction = ref('')
const auditBusy = ref(false)

async function loadSystem(): Promise<void> {
  try {
    sys.value = await api.get<SystemInfo>('/api/governance/system')
    sysErr.value = ''
  } catch (e) {
    sys.value = null
    sysErr.value = (e as Error).message
  }
}

async function loadAudit(): Promise<void> {
  auditBusy.value = true
  try {
    const d = await api.get<{ audit: AuditRow[]; total: number; actions: string[] }>('/api/governance/audit', {
      limit: 200,
      q: auditQ.value.trim() || undefined,
      action: auditAction.value || undefined,
    })
    auditRows.value = d.audit || []
    auditTotal.value = typeof d.total === 'number' ? d.total : auditRows.value.length
    if ((d.actions || []).length) auditActions.value = d.actions
  } catch (e) {
    toast('读取审计日志失败：' + (e as Error).message)
    auditRows.value = []
    auditTotal.value = 0
  } finally {
    auditBusy.value = false
  }
}

onMounted(() => {
  loadSystem()
  loadAudit()
})

// 定时任务增删启停都会落审计，状态卡的数据规模也一变同步刷新
function reloadCards(): void {
  loadSystem()
  loadAudit()
}
</script>

<template>
  <AppHeader />

  <div class="wrap">
    <SystemStatusCard :sys="sys" :error="sysErr" @refresh="loadSystem" />

    <ScheduleCard @changed="reloadCards" />

    <AuditCard
      v-model:q="auditQ" v-model:action="auditAction"
      :rows="auditRows" :total="auditTotal" :actions="auditActions" :busy="auditBusy"
      @refresh="loadAudit"
    />
  </div>
</template>

<style scoped>
.wrap {
  width: 100%; margin: 0; padding: 26px clamp(20px, 2.4vw, 40px) 72px;
  display: flex; flex-direction: column; gap: 18px;
}
@media (max-width: 640px) {
  .wrap { padding: 20px 16px 48px; }
}
</style>