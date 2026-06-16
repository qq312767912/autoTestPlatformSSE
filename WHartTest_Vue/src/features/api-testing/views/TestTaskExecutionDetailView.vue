<template>
  <div class="api-testing-container" :class="isDarkTheme ? 'api-testing-container--dark' : 'api-testing-container--light'">
    <TestTaskExecutionDetail />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useThemeStore } from '@/store/themeStore'
import TestTaskExecutionDetail from '../components/testtasks/TestTaskExecutionDetail.vue'

const themeStore = useThemeStore()
const isDarkTheme = computed(() => themeStore.isBlack)
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  background: var(--tt-execution-page-bg);
  border: 1px solid var(--tt-execution-page-border);
  border-radius: 8px;
  overflow: hidden;
}

.api-testing-container--light {
  --tt-execution-page-bg: color-mix(in srgb, var(--theme-card-bg) 94%, var(--theme-page-bg) 6%);
  --tt-execution-page-border: rgba(148, 163, 184, 0.16);
}

.api-testing-container--dark {
  --tt-execution-page-bg: rgb(17, 24, 39);
  --tt-execution-page-border: rgba(75, 85, 99, 0.35);
}
</style>
