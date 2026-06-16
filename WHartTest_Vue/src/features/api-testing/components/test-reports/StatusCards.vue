<template>
  <div class="status-cards-shell">
    <!-- 成功步骤 -->
    <div class="status-card rounded-lg">
      <div class="status-card__icon status-card__icon--success">
        <icon-check-circle class="text-[30px] text-green-500/80" />
      </div>
      <div class="status-card__body">
        <p class="status-card__label text-green-400">{{ text.passedSteps }}</p>
        <h3 class="status-card__value text-green-500">{{ report?.success_count }}</h3>
        <p class="status-card__meta">{{ formatShare(Math.round(Number(report?.success_rate || 0) * 100)) }}</p>
      </div>
    </div>

    <!-- 失败步骤 -->
    <div class="status-card rounded-lg">
      <div class="status-card__icon status-card__icon--failure">
        <icon-close-circle class="text-[30px] text-red-500/80" />
      </div>
      <div class="status-card__body">
        <p class="status-card__label text-red-400">{{ text.failedSteps }}</p>
        <h3 class="status-card__value text-red-500">{{ report?.fail_count }}</h3>
        <p class="status-card__meta">{{ formatShare(failRate) }}</p>
      </div>
    </div>

    <!-- 错误步骤 -->
    <div class="status-card rounded-lg">
      <div class="status-card__icon status-card__icon--error">
        <icon-exclamation-circle class="text-[30px] text-orange-500/80" />
      </div>
      <div class="status-card__body">
        <p class="status-card__label text-orange-400">{{ text.errorSteps }}</p>
        <h3 class="status-card__value text-orange-500">{{ report?.error_count }}</h3>
        <p class="status-card__meta">{{ formatShare(errorRate) }}</p>
      </div>
    </div>

    <!-- 总步骤 -->
    <div class="status-card rounded-lg">
      <div class="status-card__icon status-card__icon--total">
        <icon-list class="text-[30px] text-blue-500/80" />
      </div>
      <div class="status-card__body">
        <p class="status-card__label text-blue-400">{{ text.totalSteps }}</p>
        <h3 class="status-card__value text-blue-500">{{ totalSteps }}</h3>
        <p class="status-card__meta">{{ text.completed }}</p>
      </div>
    </div>

    <!-- 成功率卡片 -->
    <div class="status-card status-card--rate rounded-lg">
      <div class="relative h-20 w-20 flex-shrink-0">
        <svg class="h-full w-full -rotate-90 transform">
          <circle
            cx="40"
            cy="40"
            r="34"
            stroke-width="7"
            :stroke="progressTrackColor"
            fill="none"
          />
          <circle
            cx="40"
            cy="40"
            r="34"
            stroke-width="7"
            :stroke="progressColor"
            fill="none"
            stroke-linecap="round"
            :style="{
              strokeDasharray: `${2 * Math.PI * 34}`,
              strokeDashoffset: `${2 * Math.PI * 34 * (1 - (report?.success_rate || 0))}`,
              transition: 'stroke-dashoffset 0.5s ease'
            }"
          />
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-lg font-bold" :class="progressTextColor">
            {{ Math.round((report?.success_rate || 0) * 100) }}%
          </span>
          <span class="text-[11px] text-gray-400">{{ text.successRate }}</span>
        </div>
      </div>
      <div class="status-card__body">
        <p class="status-card__label" :class="progressTextColor">{{ text.passRate }}</p>
        <h3 class="status-card__value" :class="progressTextColor">
          {{ Math.round((report?.success_rate || 0) * 100) }}%
        </h3>
        <p class="status-card__meta">{{ text.testPassRate }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useAppI18n } from '@/composables/useAppI18n'
import { useThemeStore } from '@/store/themeStore'
import { 
  IconCheckCircle,
  IconCloseCircle,
  IconExclamationCircle,
  IconList
} from '@arco-design/web-vue/es/icon'
import type { TestReportResponse } from './TestReportDetail.vue'

const props = defineProps<{
  report: TestReportResponse | null
  totalSteps: number
  failRate: number
  errorRate: number
}>()

const themeStore = useThemeStore()
const { isEnglish } = useAppI18n()

const reportStatus = computed(() => props.report?.status)

const text = computed(() => isEnglish.value
  ? {
      passedSteps: 'Passed Steps',
      failedSteps: 'Failed Steps',
      errorSteps: 'Error Steps',
      totalSteps: 'Total Steps',
      completed: 'Completed',
      successRate: 'Success rate',
      passRate: 'Pass Rate',
      testPassRate: 'Test Pass Rate',
    }
  : {
      passedSteps: '成功步骤',
      failedSteps: '失败步骤',
      errorSteps: '错误步骤',
      totalSteps: '总步骤',
      completed: '执行完成',
      successRate: '成功率',
      passRate: '通过率',
      testPassRate: '测试通过率',
    }
)

const formatShare = (percent: number) => (
  isEnglish.value ? `Share ${percent}%` : `占比 ${percent}%`
)

const progressColor = computed(() => {
  if (reportStatus.value === 'success') return '#22c55e'
  if (reportStatus.value === 'failure') return '#ef4444'
  if (reportStatus.value === 'error') return '#f97316'

  const rate = props.report?.success_rate || 0
  return rate > 0 ? '#22c55e' : '#ef4444'
})

const progressTrackColor = computed(() => themeStore.isBlack ? 'rgba(75, 85, 99, 0.3)' : 'rgba(148, 163, 184, 0.28)')

const progressTextColor = computed(() => {
  if (reportStatus.value === 'success') return 'text-green-500'
  if (reportStatus.value === 'failure') return 'text-red-500'
  if (reportStatus.value === 'error') return 'text-orange-500'

  const rate = props.report?.success_rate || 0
  return rate > 0 ? 'text-green-500' : 'text-red-500'
})
</script>

<style scoped>
@reference "tailwindcss";
circle {
  transition: stroke-dashoffset 0.5s ease;
}

.status-cards-shell {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
}

.status-cards-shell .text-gray-400 {
  color: var(--api-report-text-subtle);
}

.status-card {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 124px;
  padding: 18px;
  background: color-mix(in srgb, var(--api-report-card-bg) 88%, var(--theme-page-bg) 12%);
  border: 1px solid var(--api-report-shell-border);
  backdrop-filter: blur(10px);
  transition: transform 0.2s ease, border-color 0.2s ease, background 0.2s ease;
}

.status-card:hover {
  background: var(--api-report-card-hover);
  border-color: rgba(var(--theme-accent-rgb), 0.12);
  transform: translateY(-1px);
}

.status-card--rate {
  justify-content: space-between;
}

.status-card__icon {
  display: flex;
  height: 56px;
  width: 56px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
  border: 1px solid var(--api-report-inline-border);
}

.status-card__icon--success {
  background: rgba(34, 197, 94, 0.08);
}

.status-card__icon--failure {
  background: rgba(239, 68, 68, 0.08);
}

.status-card__icon--error {
  background: rgba(249, 115, 22, 0.08);
}

.status-card__icon--total {
  background: rgba(59, 130, 246, 0.08);
}

.status-card__body {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.status-card__label {
  font-size: 13px;
  font-weight: 500;
}

.status-card__value {
  font-size: 28px;
  line-height: 1.1;
  font-weight: 700;
}

.status-card__meta {
  font-size: 12px;
  color: var(--api-report-text-subtle);
}

@media (max-width: 767px) {
  .status-card {
    min-height: 108px;
    padding: 16px;
  }

  .status-card--rate {
    justify-content: flex-start;
  }
}
</style> 