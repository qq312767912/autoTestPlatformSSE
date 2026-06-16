<template>
  <div class="api-testing-container test-report-detail-view" :class="isDarkTheme ? 'test-report-detail-view--dark' : 'test-report-detail-view--light'">
    <a-spin :loading="loading" class="h-full">
      <div v-if="report" class="report-detail-scroll h-full overflow-auto">
        <div class="report-detail-stack p-4">
          <ReportHeader :report="(report as any)" :loading="loading" @back="handleBack" @export="handleExportReport" />
          <BasicInfo :report="(report as any)" />
          <StatusCards :report="(report as any)" :total-steps="totalSteps" :fail-rate="failRate" :error-rate="errorRate" />
          <ExecutionSteps :report="(report as any)" />
        </div>
      </div>
      <div v-else-if="!loading" class="empty-text flex items-center justify-center h-full">
        {{ pageText.notFound }}
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { useAppI18n } from '@/composables/useAppI18n'
import { useProjectStore } from '@/store/projectStore'
import { useThemeStore } from '@/store/themeStore'
import { testReportService } from '../services/testReportService'
import type { ApiTestReport } from '../types/testcase'
import ReportHeader from '../components/test-reports/ReportHeader.vue'
import BasicInfo from '../components/test-reports/BasicInfo.vue'
import StatusCards from '../components/test-reports/StatusCards.vue'
import ExecutionSteps from '../components/test-reports/ExecutionSteps.vue'

const props = defineProps<{ id: string | number }>()
const router = useRouter()
const route = useRoute()
const { isEnglish } = useAppI18n()
const projectStore = useProjectStore()
const themeStore = useThemeStore()
const projectId = computed(() => projectStore.currentProjectId)
const report = ref<ApiTestReport | null>(null)
const loading = ref(false)
const isDarkTheme = computed(() => themeStore.isBlack)

const totalSteps = computed(() => {
  if (!report.value) return 0
  return (report.value.success_count || 0) + (report.value.fail_count || 0) + (report.value.error_count || 0)
})
const failRate = computed(() => totalSteps.value ? Math.round(((report.value?.fail_count || 0) / totalSteps.value) * 100) : 0)
const errorRate = computed(() => totalSteps.value ? Math.round(((report.value?.error_count || 0) / totalSteps.value) * 100) : 0)

const pageText = computed(() => isEnglish.value
  ? { notFound: 'Report not found' }
  : { notFound: '未找到报告' }
)

const handleExportReport = () => {
  Message.info(isEnglish.value ? 'Export is under development...' : '导出功能开发中...')
}

const handleBack = () => {
  const returnTo = typeof route.query.returnTo === 'string' ? route.query.returnTo : 'reports'

  if (returnTo === 'testcaseReports') {
    const testcaseId = Number(route.query.testcaseId)
    if (Number.isInteger(testcaseId) && testcaseId > 0) {
      router.push({ name: 'ApiTestCaseReports', params: { id: testcaseId } })
      return
    }
  }

  router.push({
    path: '/api-testing',
    query: {
      tab: typeof route.query.tab === 'string' ? route.query.tab : 'reports'
    }
  })
}

onMounted(async () => {
  if (!projectId.value) return
  loading.value = true
  try {
    const res = await testReportService.get(projectId.value, Number(props.id))
    if (res.success && res.data) report.value = res.data as ApiTestReport
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  border-radius: 8px;
  overflow: hidden;
}

.test-report-detail-view {
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

.test-report-detail-view--dark {
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

.report-detail-scroll {
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.report-detail-scroll::-webkit-scrollbar {
  display: none;
}

.report-detail-stack {
  display: flex;
  min-height: 100%;
  flex-direction: column;
  gap: 16px;
}
</style>
