<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  sub?: string
  width?: string
  closeOnMask?: boolean
}>(), { width: '560px', closeOnMask: true })

const emit = defineEmits<{ (e: 'close'): void }>()

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') emit('close')
}

watch(() => props.open, (v) => {
  if (typeof document === 'undefined') return
  if (v) {
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
  } else {
    document.removeEventListener('keydown', onKey)
    document.body.style.overflow = ''
  }
}, { immediate: true })

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <Transition name="mj-modal">
      <div v-if="open" class="mj-mask" @click.self="closeOnMask && emit('close')">
        <div class="mj-box" :style="{ width }" role="dialog" aria-modal="true">
          <div class="mj-hd">
            <div class="mj-ht">
              <h3>{{ title }}</h3>
              <div v-if="sub" class="mj-sub">{{ sub }}</div>
            </div>
            <button class="mj-x" type="button" aria-label="关闭" @click="emit('close')">×</button>
          </div>
          <div class="mj-bd"><slot /></div>
          <div v-if="$slots.footer" class="mj-ft"><slot name="footer" /></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.mj-mask {
  position: fixed; inset: 0; z-index: 90;
  background: rgba(17, 24, 39, .44);
  backdrop-filter: blur(2px);
  display: flex; align-items: flex-start; justify-content: center;
  padding: 8vh 16px 32px; overflow: auto;
}
.mj-box {
  max-width: 94vw; background: #fff; border-radius: var(--radius);
  box-shadow: 0 28px 70px rgba(15, 23, 42, .26), 0 2px 8px rgba(15, 23, 42, .08);
  display: flex; flex-direction: column; max-height: 82vh;
}
.mj-hd {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 18px 20px 14px; border-bottom: 1px solid var(--line);
}
.mj-ht { flex: 1; min-width: 0; }
.mj-hd h3 { margin: 0; font-size: 15px; font-weight: 650; letter-spacing: -.1px; }
.mj-sub { margin-top: 4px; font-size: 12px; color: var(--mute); line-height: 1.65; }
.mj-x {
  flex: none; width: 30px; height: 30px; border: 1px solid var(--line); border-radius: 8px;
  background: #fff; color: var(--sub); font-size: 18px; line-height: 1;
  cursor: pointer; transition: .14s;
}
.mj-x:hover { background: #F5F7FA; border-color: var(--line-strong); color: var(--ink); }
.mj-bd { padding: 18px 20px; overflow: auto; }
.mj-ft {
  padding: 14px 20px; border-top: 1px solid var(--line);
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}

.mj-modal-enter-active, .mj-modal-leave-active { transition: opacity .16s ease; }
.mj-modal-enter-from, .mj-modal-leave-to { opacity: 0; }
.mj-modal-enter-active .mj-box { transition: transform .16s ease; }
.mj-modal-enter-from .mj-box { transform: translateY(-6px); }
</style>