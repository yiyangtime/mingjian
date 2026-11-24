<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'

const props = defineProps<{ open: boolean; title?: string; subtitle?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

// 抽屉打开时支持 Esc 关闭，和原多页版本的手感保持一致
function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape' && props.open) emit('close')
}
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))

// 打开抽屉时锁住页面滚动，关闭后恢复
watch(
  () => props.open,
  (v) => {
    document.body.style.overflow = v ? 'hidden' : ''
  },
)
onBeforeUnmount(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div class="dw-ov" :class="{ on: open }" @click="emit('close')"></div>
    <aside class="dw" :class="{ on: open }">
      <div class="dw-head">
        <div>
          <div class="t">{{ title }}</div>
          <div v-if="subtitle" class="s">{{ subtitle }}</div>
        </div>
        <button class="dw-x" type="button" @click="emit('close')">×</button>
      </div>
      <div class="dw-body"><slot /></div>
      <div v-if="$slots.footer" class="dw-foot"><slot name="footer" /></div>
    </aside>
  </Teleport>
</template>

<style scoped>
.dw-ov {
  position: fixed; inset: 0; background: rgba(18, 23, 33, .36); backdrop-filter: blur(1.5px);
  opacity: 0; pointer-events: none; transition: .2s; z-index: 40;
}
.dw-ov.on { opacity: 1; pointer-events: auto; }
.dw {
  position: fixed; top: 0; right: 0; height: 100%; width: 560px; max-width: 94vw;
  background: #fff; box-shadow: -16px 0 52px rgba(18, 23, 33, .18);
  transform: translateX(102%); transition: .26s cubic-bezier(.4, 0, .2, 1);
  z-index: 41; display: flex; flex-direction: column;
}
.dw.on { transform: none; }
.dw-head {
  padding: 18px 22px; border-bottom: 1px solid var(--line);
  display: flex; gap: 12px; align-items: flex-start;
}
.dw-head .t { font-weight: 650; font-size: 15px; letter-spacing: -.1px; }
.dw-head .s { font-size: 12px; color: var(--mute); margin-top: 3px; line-height: 1.65; }
.dw-x {
  margin-left: auto; width: 30px; height: 30px; flex: none;
  display: grid; place-items: center; background: #fff; border: 1px solid var(--line);
  border-radius: 8px; font-size: 18px; color: var(--sub); cursor: pointer; line-height: 1; transition: .14s;
}
.dw-x:hover { background: #F5F7FA; border-color: var(--line-strong); color: var(--ink); }
.dw-body { flex: 1; overflow: auto; padding: 20px 22px; }
.dw-foot {
  padding: 14px 22px; border-top: 1px solid var(--line);
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
}
</style>