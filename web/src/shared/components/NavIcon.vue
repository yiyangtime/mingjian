<script setup lang="ts">
import { computed } from 'vue'
import { NAV_ICON_PATHS, type NavIconName } from './navIcons'

const props = withDefaults(defineProps<{ name: NavIconName; size?: number }>(), { size: 18 })

const paths = computed(() => NAV_ICON_PATHS[props.name] || [])
</script>

<template>
  <svg
    class="nav-icon" :width="size" :height="size" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"
  >
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>

<style scoped>
.nav-icon { display: block; flex: none; }
</style>