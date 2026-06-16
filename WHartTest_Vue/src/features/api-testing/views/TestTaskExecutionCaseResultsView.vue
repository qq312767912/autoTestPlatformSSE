<template>
  <div class="api-testing-container" :class="isDarkTheme ? 'api-testing-container--dark' : 'api-testing-container--light'">
    <TestTaskExecutionCaseResults />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useThemeStore } from '@/store/themeStore'
import TestTaskExecutionCaseResults from '../components/testtasks/TestTaskExecutionCaseResults.vue'

const themeStore = useThemeStore()
const isDarkTheme = computed(() => themeStore.isBlack)
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  background: var(--tt-result-page-bg);
  border: 1px solid var(--tt-result-page-border);
  border-radius: 8px;
  overflow: hidden;
}

.api-testing-container--light {
  --tt-result-page-bg: color-mix(in srgb, var(--theme-card-bg) 94%, var(--theme-page-bg) 6%);
  --tt-result-page-border: rgba(148, 163, 184, 0.16);
}

.api-testing-container--dark {
  --tt-result-page-bg: rgb(17, 24, 39);
  --tt-result-page-border: rgba(75, 85, 99, 0.35);
}
</style>
