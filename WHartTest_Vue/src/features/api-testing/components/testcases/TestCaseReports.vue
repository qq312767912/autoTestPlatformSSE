<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconArrowLeft } from '@arco-design/web-vue/es/icon'
import { testcaseService } from '../../services/testcaseService'
import type { ApiTestCase } from '../../types/testcase'
import { useProjectStore } from '@/store/projectStore'
import TestCaseHistoryReports from './TestCaseHistoryReports.vue'

const props = defineProps<{
  testcaseId: number
}>()

const emit = defineEmits(['back', 'view-report'])

const projectStore = useProjectStore()
const loading = ref(false)
const testcase = ref<ApiTestCase | null>(null)

const pagination = ref({
  current: 1,
  pageSize: 10,
  total: 0,
  showTotal: true,
  showJumper: true,
  showPageSize: true
})

const fetchTestCase = async () => {
  if (!props.testcaseId || !projectStore.currentProjectId) return
  try {
    loading.value = true
    const res = await testcaseService.get(projectStore.currentProjectId, props.testcaseId)
    if (res.success && res.data) {
      testcase.value = res.data as ApiTestCase
    } else {
      throw new Error(res.error || '获取测试用例信息失败')
    }
  } catch (error) {
    console.error('获取测试用例信息失败:', error)
    Message.error(error instanceof Error ? error.message : '获取测试用例信息失败')
  } finally {
    loading.value = false
  }
}

const handleBack = () => {
  emit('back')
}

const handlePageChange = (current: number) => {
  pagination.value.current = current
}

const handleViewReport = (report: any) => {
  emit('view-report', report)
}

watch(() => props.testcaseId, () => {
  fetchTestCase()
})

onMounted(() => {
  fetchTestCase()
})
</script>

<template>
  <div class="testcase-report-wrapper h-full flex flex-col gap-4 p-4">
    <!-- 头部 -->
    <div class="report-card px-6 py-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-4">
          <a-button class="custom-back-button" @click="handleBack">
            <template #icon>
              <icon-arrow-left />
            </template>
            返回
          </a-button>
          <h2 class="report-title">
            测试用例「{{ testcase?.name || '加载中...' }}」历史报告
          </h2>
        </div>
      </div>
    </div>

    <!-- 报告列表 -->
    <div class="flex-1 report-card overflow-hidden">
      <div class="h-full p-6">
        <TestCaseHistoryReports
          v-if="testcase"
          ref="historyReportsRef"
          :testcase-id="testcase.id!"
          :pagination="pagination"
          @update:pagination="(val) => pagination = val"
          @view-report="handleViewReport"
        />
      </div>
    </div>

    <!-- 分页区域 -->
    <div class="report-card px-6 py-5">
      <a-pagination
        v-model:current="pagination.current"
        v-model:pageSize="pagination.pageSize"
        :total="pagination.total"
        show-total
        show-jumper
        show-page-size
        class="flex justify-end"
        @change="handlePageChange"
        @page-size-change="handlePageChange"
      />
    </div>
  </div>
</template>

<style scoped>
@reference "tailwindcss";
.report-card {
  background: color-mix(in srgb, var(--api-report-card-bg) 88%, var(--theme-page-bg) 12%);
  border: 1px solid var(--api-report-shell-border);
  border-radius: 0.75rem;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
}

.report-title {
  @apply text-lg font-medium;
  color: var(--api-report-text);
}

.custom-back-button {
  background: var(--api-report-inline-bg) !important;
  border-color: var(--api-report-inline-border) !important;
  color: var(--api-report-text-muted) !important;

  &:hover {
    background: var(--api-report-card-hover) !important;
    border-color: rgba(var(--theme-accent-rgb), 0.2) !important;
    color: var(--api-report-text) !important;
  }

  &:active {
    background: var(--api-report-card-bg) !important;
    border-color: var(--api-report-inline-border) !important;
    color: var(--api-report-text-muted) !important;
  }
}

:deep(.arco-pagination) {
  .arco-pagination-item {
    color: var(--api-report-text-subtle) !important;
    background: transparent !important;
    border-color: transparent !important;

    &:hover {
      color: var(--theme-accent-hover) !important;
      background: rgba(var(--theme-accent-rgb), 0.1) !important;
    }

    &.arco-pagination-item-active {
      color: var(--theme-accent-hover) !important;
      background: rgba(var(--theme-accent-rgb), 0.14) !important;
      border-color: rgba(var(--theme-accent-rgb), 0.18) !important;
    }
  }

  .arco-pagination-total {
    color: var(--api-report-text-subtle) !important;
  }

  .arco-input,
  .arco-select-view {
    background: var(--api-report-inline-bg) !important;
    border-color: var(--api-report-inline-border) !important;
    color: var(--api-report-text) !important;
  }
}
</style>
