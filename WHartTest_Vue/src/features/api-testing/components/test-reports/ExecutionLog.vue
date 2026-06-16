<template>
  <div class="execution-log-shell rounded-lg border">
    <div class="execution-log-header px-4 py-3 border-b">
      <span class="execution-log-title text-base font-medium">执行日志</span>
    </div>
    <div class="p-4">
      <div class="execution-log-card rounded-lg border transition-all duration-200">
        <pre class="execution-log-pre p-4 text-sm font-mono overflow-x-auto whitespace-pre-wrap">{{ report?.summary?.log }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { TestReportResponse } from './TestReportDetail.vue'

defineProps<{
  report: TestReportResponse | null
}>()
</script>

<style scoped>
.execution-log-shell {
  background: color-mix(in srgb, var(--api-report-card-bg) 88%, var(--theme-page-bg) 12%);
  border-color: var(--api-report-shell-border);
  backdrop-filter: blur(10px);
}

.execution-log-header {
  border-color: var(--api-report-shell-border);
}

.execution-log-title {
  color: var(--api-report-text-muted);
}

.execution-log-card {
  background: var(--api-report-inline-bg);
  border-color: var(--api-report-inline-border);
}

.execution-log-card:hover {
  background: var(--api-report-card-hover);
  border-color: rgba(var(--theme-accent-rgb), 0.12);
}

.execution-log-pre {
  color: var(--api-report-text-muted);
}

pre {
  scrollbar-width: none;
  -ms-overflow-style: none;
  &::-webkit-scrollbar {
    display: none;
  }
}
</style> 