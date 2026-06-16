<template>
  <div class="h-full flex flex-col">
    <!-- 头部导航 -->
    <ReportHeader
      :report="report"
      :loading="loading"
      @back="router.back()"
      @export="handleExportReport"
    />

    <!-- 内容区域 -->
    <div class="flex-1 overflow-auto">
      <div class="min-h-full p-6">
        <!-- 主卡片容器 -->
        <div class="report-detail-shell w-full shadow-xl rounded-xl">
          <a-spin :loading="loading" dot class="w-full">
            <!-- 状态卡片区域 -->
            <div class="p-4 w-full flex flex-wrap gap-4">
              <!-- 成功步骤 -->
              <a-card class="stat-card">
                <div class="flex flex-col items-center h-full py-3">
                  <div class="flex items-center justify-center w-full mb-4">
                    <div class="flex items-center gap-2">
                      <icon-check-circle class="text-2xl text-green-500/70" />
                      <span class="text-green-400 text-base whitespace-nowrap">成功步骤</span>
                    </div>
                  </div>
                  <div class="flex-1 flex flex-col items-center justify-center">
                    <div class="text-2xl font-bold text-green-500 mb-2">
                      {{ Number(report?.success_count || 0) }}
                    </div>
                    <div class="text-sm text-gray-400">
                      占比 {{ Math.round(Number(report?.success_rate || 0) * 100) }}%
                    </div>
                  </div>
                </div>
              </a-card>

              <!-- 失败步骤 -->
              <a-card class="stat-card">
                <div class="flex flex-col items-center h-full py-3">
                  <div class="flex items-center justify-center w-full mb-4">
                    <div class="flex items-center gap-2">
                      <icon-close-circle class="text-2xl text-red-500/70" />
                      <span class="text-red-400 text-base whitespace-nowrap">失败步骤</span>
                    </div>
                  </div>
                  <div class="flex-1 flex flex-col items-center justify-center">
                    <div class="text-2xl font-bold text-red-500 mb-2">
                      {{ Number(report?.fail_count || 0) }}
                    </div>
                    <div class="text-sm text-gray-400">
                      占比 {{ failRate }}%
                    </div>
                  </div>
                </div>
              </a-card>

              <!-- 错误步骤 -->
              <a-card class="stat-card">
                <div class="flex flex-col items-center h-full py-3">
                  <div class="flex items-center justify-center w-full mb-4">
                    <div class="flex items-center gap-2">
                      <icon-exclamation-circle class="text-2xl text-orange-500/70" />
                      <span class="text-orange-400 text-base whitespace-nowrap">错误步骤</span>
                    </div>
                  </div>
                  <div class="flex-1 flex flex-col items-center justify-center">
                    <div class="text-2xl font-bold text-orange-500 mb-2">
                      {{ Number(report?.error_count || 0) }}
                    </div>
                    <div class="text-sm text-gray-400">
                      占比 {{ errorRate }}%
                    </div>
                  </div>
                </div>
              </a-card>

              <!-- 总步骤 -->
              <a-card class="stat-card">
                <div class="flex flex-col items-center h-full py-3">
                  <div class="flex items-center justify-center w-full mb-4">
                    <div class="flex items-center gap-2">
                      <icon-list class="text-2xl text-blue-500/70" />
                      <span class="text-blue-400 text-base whitespace-nowrap">总步骤</span>
                    </div>
                  </div>
                  <div class="flex-1 flex flex-col items-center justify-center">
                    <div class="text-2xl font-bold text-blue-500 mb-2">
                      {{ getTotalSteps }}
                    </div>
                    <div class="text-sm text-gray-400">
                      执行完成
                    </div>
                  </div>
                </div>
              </a-card>

              <!-- 成功率卡片 -->
              <a-card class="stat-card">
                <div class="flex flex-col items-center h-full py-3">
                  <div class="flex items-center justify-center w-full mb-4">
                    <div class="flex items-center gap-2">
                      <icon-check class="text-2xl" :class="progressTextColor" />
                      <span :class="[progressTextColor, 'text-base whitespace-nowrap']">成功率</span>
                    </div>
                  </div>
                  <div class="flex-1 flex flex-col items-center justify-center">
                    <div class="text-2xl font-bold mb-2" :class="progressTextColor">
                      {{ Math.round(Number(report?.success_rate || 0) * 100) }}%
                    </div>
                    <div class="text-sm text-gray-400">
                      测试通过率
                    </div>
                  </div>
                </div>
              </a-card>

              <!-- 执行耗时 -->
              <a-card class="stat-card">
                <div class="flex flex-col items-center h-full py-3">
                  <div class="flex items-center justify-center w-full mb-4">
                    <div class="flex items-center gap-2">
                      <icon-timer class="text-2xl text-purple-500/70" />
                      <span class="text-purple-400 text-base whitespace-nowrap">执行耗时</span>
                    </div>
                  </div>
                  <div class="flex-1 flex flex-col items-center justify-center">
                    <div class="text-2xl font-bold text-purple-500 mb-2">
                      {{ formatDuration(report?.duration || 0) }}
                    </div>
                    <div class="text-sm text-gray-400">
                      总耗时
                    </div>
                  </div>
                </div>
              </a-card>
            </div>

            <!-- 基本信息和配置信息 -->
            <div class="report-detail-divider border-t">
              <div class="px-4">
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <BasicInfo :report="report" />
                  <ConfigInfo :report="report" />
                </div>
              </div>
            </div>

            <!-- 执行步骤和执行日志 -->
            <div class="space-y-4 p-4">
              <!-- 执行步骤 -->
              <ExecutionSteps :report="report" />

              <!-- 执行日志 -->
              <ExecutionLog :report="report" />
            </div>
          </a-spin>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { getTestReportDetail } from '../../services/testReportService'
import type { TestReportDetail } from '../../services/testReportService'
import ReportHeader from './ReportHeader.vue'
import StatusCards from './StatusCards.vue'
import BasicInfo from './BasicInfo.vue'
import ConfigInfo from './ConfigInfo.vue'
import ExecutionSteps from './ExecutionSteps.vue'
import ExecutionLog from './ExecutionLog.vue'
import { formatDateTime, formatDuration } from '@/utils/formatters'
import type { ApiResponse } from '../../types/common'

// 类型定义
export interface TestReportStep {
  id: number
  step_name: string
  success: boolean
  elapsed: number
  request: {
    method: string
    url: string
    headers: Record<string, string>
    body: any
  }
  response: {
    status_code: number
    headers: Record<string, string>
    body: any
    content_size: number
    response_time_ms: number
  }
  validators: {
    success: boolean
    validate_extractor: Array<{
      check: string
      expect: any
      message: string
      comparator: string
      check_value: any
      check_result: 'pass' | 'fail'
      expect_value: any
    }>
  }
  extracted_variables: Record<string, any>
  attachment: string
}

export interface TestReportResponse extends Omit<TestReportDetail, 'details'> {
  environment_info?: {
    id: number
    name: string
    base_url: string
    description: string
    project: {
      id: number
      name: string
    }
  }
  executed_by_info?: {
    id: number
    username: string
    email: string
    first_name: string
    last_name: string
  }
  summary: {
    name: string
    success: boolean
    time: {
      start_at: string
      duration: number
    }
    in_out: {
      config_vars: any
      export_vars: any
    }
    log: string
  }
  details: TestReportStep[]
}

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const report = ref<TestReportResponse | null>(null)

// 计算属性
const getTotalSteps = computed(() => {
  if (!report.value) return 0
  return Number(report.value.success_count || 0) + 
         Number(report.value.fail_count || 0) + 
         Number(report.value.error_count || 0)
})

const getFailRate = computed(() => {
  if (!report.value || getTotalSteps.value === 0) return 0
  const failCount = parseInt(String(report.value.fail_count))
  return parseFloat(((failCount / getTotalSteps.value) * 100).toFixed(1))
})

const getErrorRate = computed(() => {
  if (!report.value || getTotalSteps.value === 0) return 0
  const errorCount = parseInt(String(report.value.error_count))
  return parseFloat(((errorCount / getTotalSteps.value) * 100).toFixed(1))
})

const failRate = computed(() => {
  if (!report.value || getTotalSteps.value === 0) return 0
  return Math.round((Number(report.value.fail_count || 0) / getTotalSteps.value) * 100)
})

const errorRate = computed(() => {
  if (!report.value || getTotalSteps.value === 0) return 0
  return Math.round((Number(report.value.error_count || 0) / getTotalSteps.value) * 100)
})

const progressColor = computed(() => {
  const rate = Number(report.value?.success_rate || 0);
  if (rate >= 0.8) return 'rgb(34, 197, 94)';  // 绿色
  if (rate >= 0.6) return 'rgb(234, 179, 8)';  // 黄色
  return 'rgb(239, 68, 68)';  // 红色
});

const progressTextColor = computed(() => {
  const rate = Number(report.value?.success_rate || 0);
  if (rate >= 0.8) return 'text-cyan-400';
  if (rate >= 0.6) return 'text-amber-400';
  return 'text-rose-400';
});

// 方法
const handleExportReport = () => {
  Message.info('导出功能开发中...')
}

const fetchReportDetail = async () => {
  const id = Number(route.params.id)
  if (!id) {
    Message.error('无效的报告ID')
    return
  }

  try {
    loading.value = true
    const response = await getTestReportDetail(id)
    console.log('API响应:', response)
    
    // 正确处理 Axios 响应数据
    if (response && response.data) {
      report.value = response.data as unknown as TestReportResponse
      console.log('报告数据:', {
        id: report.value?.id,
        name: report.value?.name,
        status: report.value?.status,
        success_count: report.value?.success_count,
        fail_count: report.value?.fail_count,
        error_count: report.value?.error_count,
        success_rate: report.value?.success_rate,
        total: getTotalSteps.value
      })
    } else {
      console.error('API响应格式错误:', response)
      Message.error('获取测试报告详情失败')
    }
  } catch (error) {
    console.error('获取报告失败:', error)
    Message.error('获取测试报告详情失败')
  } finally {
    loading.value = false
  }
}

// 监听数据变化
watch(report, (newVal) => {
  console.log('报告数据更新:', {
    success_count: newVal?.success_count,
    fail_count: newVal?.fail_count,
    error_count: newVal?.error_count,
    success_rate: newVal?.success_rate,
    total: getTotalSteps.value
  })
}, { deep: true })

onMounted(() => {
  fetchReportDetail()
})
</script> 

<style scoped>
@reference "tailwindcss";
.report-detail-shell {
  background: color-mix(in srgb, var(--api-report-card-bg) 88%, var(--theme-page-bg) 12%);
  border: 1px solid var(--api-report-shell-border);
  backdrop-filter: blur(10px);
}

.report-detail-divider {
  border-color: var(--api-report-shell-border);
}

.report-detail-shell .text-gray-400 {
  color: var(--api-report-text-subtle);
}

/* 滚动条样式 */
.overflow-auto {
  scrollbar-width: none;
  -ms-overflow-style: none;
  &::-webkit-scrollbar {
    display: none;
  }
}

/* 容器响应式宽度 */
.container {
  @apply w-full;
}

/* 卡片容器样式 */
.flex.flex-wrap {
  @apply gap-4 w-full; /* 保持1rem的间距，并确保全宽 */
  box-sizing: border-box !important;
  width: 100% !important;
}

/* 统计卡片样式 */
:deep(.stat-card) {
  @apply rounded-lg transition-all duration-300;
  background: color-mix(in srgb, var(--api-report-card-bg) 90%, var(--theme-page-bg) 10%);
  border: 1px solid var(--api-report-inline-border);
  backdrop-filter: blur(10px);
  box-sizing: border-box !important;
  flex: 0 0 calc((100% - 5 * 1rem) / 6) !important;
  width: calc((100% - 5 * 1rem) / 6) !important;
  min-width: calc((100% - 5 * 1rem) / 6) !important;
  max-width: calc((100% - 5 * 1rem) / 6) !important;
  margin: 0 !important;
  padding: 0 !important;
  
  &:hover {
    background: var(--api-report-card-hover);
    border-color: rgba(var(--theme-accent-rgb), 0.12);
    @apply transform scale-[1.02];
  }

  /* 覆盖 Arco Card 的默认样式 */
  &.arco-card {
    background: color-mix(in srgb, var(--api-report-card-bg) 90%, var(--theme-page-bg) 10%);
    width: calc((100% - 5 * 1rem) / 6) !important;
    margin: 0 !important;
    padding: 0 !important;
  }

  :deep(.arco-card-body) {
    @apply !p-4 !h-full;
    margin: 0 !important;
    box-sizing: border-box !important;
  }
}

/* 修复卡片高度 */
.stat-card {
  @apply h-[160px]; /* 稍微减小高度 */
  
  :deep(.arco-card-body) {
    @apply flex items-stretch;
  }
}

/* 进度条样式 */
:deep(.arco-progress-circle) {
  @apply flex items-center justify-center;
  
  .arco-progress-text {
    @apply !text-2xl !font-bold;
    color: inherit;
  }
}

/* 覆盖默认样式 */
:deep(.arco-card) {
  background: color-mix(in srgb, var(--api-report-card-bg) 90%, var(--theme-page-bg) 10%);
  height: 100%;
}
</style> 