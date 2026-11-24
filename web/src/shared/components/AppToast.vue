<script setup lang="ts">
import { toastState } from '../composables/toast'
</script>

<template>
  <div class="toast" :class="{ on: toastState.on }">{{ toastState.msg }}</div>
</template>

<style scoped>
.toast {
  position: fixed; left: 50%; bottom: 32px;
  transform: translateX(-50%) translateY(10px);
  background: #1B2130; color: #fff;
  padding: 11px 20px; border-radius: 10px; font-size: 13px;
  box-shadow: 0 12px 30px rgba(16, 24, 40, .28);
  opacity: 0; pointer-events: none; transition: .22s; z-index: 90;
  max-width: 76vw; text-align: center; line-height: 1.65;
}
.toast.on { opacity: 1; transform: translateX(-50%); }
</style>