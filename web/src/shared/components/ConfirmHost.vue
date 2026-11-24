<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { confirmState, settleConfirm } from '../composables/confirm'

const okEl = ref<HTMLButtonElement | null>(null)

function onKey(e: KeyboardEvent): void {
  if (!confirmState.open) return
  if (e.key === 'Escape') {
    e.preventDefault()
    settleConfirm(false)
  }
}

watch(
  () => confirmState.open,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
    if (open) nextTick(() => okEl.value?.focus())
  },
)

onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div v-if="confirmState.open" class="cf-ov" @click.self="settleConfirm(false)">
      <div class="cf" role="dialog" aria-modal="true" :aria-label="confirmState.title">
        <div class="cf-t">{{ confirmState.title }}</div>
        <div v-if="confirmState.message" class="cf-m">{{ confirmState.message }}</div>
        <div class="cf-f">
          <button class="btn btn-ghost btn-sm" @click="settleConfirm(false)">{{ confirmState.cancelText }}</button>
          <button
            ref="okEl" class="btn btn-sm" :class="confirmState.danger ? 'btn-danger' : 'btn-primary'"
            @click="settleConfirm(true)"
          >{{ confirmState.confirmText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.cf-ov {
  position: fixed; inset: 0; background: rgba(16, 24, 40, .42);
  display: flex; align-items: center; justify-content: center; z-index: 70;
}
.cf {
  width: 396px; max-width: 92vw; background: #fff; border-radius: var(--radius);
  box-shadow: 0 24px 60px rgba(16, 24, 40, .24); padding: 22px 22px 18px;
}
.cf-t { font-size: 15px; font-weight: 650; line-height: 1.5; letter-spacing: -.1px; }
.cf-m { margin-top: 8px; font-size: 12.5px; color: var(--sub); line-height: 1.85; white-space: pre-wrap; }
.cf-f { margin-top: 20px; display: flex; justify-content: flex-end; gap: 8px; }
.btn-danger { background: var(--err); border-color: var(--err); color: #fff; }
.btn-danger:hover:not(:disabled) { background: #961B12; border-color: #961B12; }
</style>