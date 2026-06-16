<template>
  <div class="api-testing-container testcase-reports-view" :class="isDarkTheme ? 'testcase-reports-view--dark' : 'testcase-reports-view--light'">
    <TestCaseReports
      :testcase-id="Number(id)"
      @back="handleBack"
      @view-report="handleViewReport"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useThemeStore } from '@/store/themeStore'
import TestCaseReports from '../components/testcases/TestCaseReports.vue'

const props = defineProps<{ id: string | number }>()
const router = useRouter()
const themeStore = useThemeStore()
const isDarkTheme = computed(() => themeStore.isBlack)

const handleBack = () => {
  router.push({ path: '/api-testing', query: { tab: 'testcases' } })
}

const handleViewReport = (report: { id: number }) => {
  router.push({
    name: 'ApiTestReportDetail',
    params: { id: report.id },
    query: {
      tab: 'testcases',
      returnTo: 'testcaseReports',
      testcaseId: String(props.id)
    }
  })
}
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  border-radius: 8px;
  overflow: hidden;
}

.testcase-reports-view {
  --api-report-shell-bg: color-mix(in srgb, var(--theme-card-bg) 92%, var(--theme-page-bg) 8%);
  --api-report-shell-border: rgba(148, 163, 184, 0.16);
  --api-report-shell-shadow: 0 14px 30px rgba(15, 23, 42, 0.08);
  --api-report-card-bg: rgba(255, 255, 255, 0.76);
  --api-report-card-hover: rgba(255, 255, 255, 0.92);
  --api-report-inline-bg: rgba(248, 250, 252, 0.92);
  --api-report-inline-border: rgba(148, 163, 184, 0.14);
  --api-report-header-bg: rgba(255, 255, 255, 0.82);
  --api-report-table-header-bg: color-mix(in srgb, var(--theme-card-bg) 76%, var(--theme-page-bg) 24%);
  --api-report-table-row-hover: rgba(15, 23, 42, 0.05);
  --api-report-drawer-bg: color-mix(in srgb, var(--theme-card-bg) 88%, var(--theme-page-bg) 12%);
  --api-report-text: var(--theme-text);
  --api-report-text-muted: var(--theme-text-secondary);
  --api-report-text-subtle: var(--theme-text-tertiary);
  background: var(--api-report-shell-bg);
  border: 1px solid var(--api-report-shell-border);
  box-shadow: var(--api-report-shell-shadow);
}

.testcase-reports-view--dark {
  --api-report-shell-bg: rgba(17, 24, 39, 0.96);
  --api-report-shell-border: rgba(55, 65, 81, 0.72);
  --api-report-shell-shadow: 0 18px 34px rgba(2, 6, 23, 0.28);
  --api-report-card-bg: rgba(17, 24, 39, 0.55);
  --api-report-card-hover: rgba(17, 24, 39, 0.72);
  --api-report-inline-bg: rgba(17, 24, 39, 0.78);
  --api-report-inline-border: rgba(75, 85, 99, 0.3);
  --api-report-header-bg: rgba(31, 41, 55, 0.72);
  --api-report-table-header-bg: rgba(31, 41, 55, 0.56);
  --api-report-table-row-hover: rgba(31, 41, 55, 0.5);
  --api-report-drawer-bg: rgba(31, 41, 55, 1);
}

.empty-text {
  color: var(--api-report-text-subtle);
}
</style>
