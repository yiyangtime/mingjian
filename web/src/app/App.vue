<script setup lang="ts">
import AppToast from '../shared/components/AppToast.vue'
import ConfirmHost from '../shared/components/ConfirmHost.vue'
</script>

<template>
  <router-view />
  <AppToast />
  <ConfirmHost />
</template>