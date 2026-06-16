<template>
  <div class="api-testing-container" :class="isDarkTheme ? 'api-testing-container--dark' : 'api-testing-container--light'">
    <a-spin :loading="loading" class="h-full">
      <div v-if="suite" class="h-full overflow-auto p-4">
        <TestTaskExecutionHistory :suite="suite" :project-id="projectId!" />
      </div>
      <div v-else-if="!loading" class="empty-state flex items-center justify-center h-full">
        {{ pageText.notFound }}
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useAppI18n } from '@/composables/useAppI18n'
import { useProjectStore } from '@/store/projectStore'
import { useThemeStore } from '@/store/themeStore'
import { testTaskService } from '../services/testTaskService'
import type { ApiTestTaskSuite } from '../types/testtask'
import TestTaskExecutionHistory from '../components/testtasks/TestTaskExecutionHistory.vue'

const props = defineProps<{ id: string | number }>()
const { isEnglish } = useAppI18n()
const projectStore = useProjectStore()
const themeStore = useThemeStore()
const projectId = computed(() => projectStore.currentProjectId)
const isDarkTheme = computed(() => themeStore.isBlack)
const suite = ref<ApiTestTaskSuite | null>(null)
const loading = ref(false)

const pageText = computed(() => isEnglish.value
  ? { notFound: 'Task not found' }
  : { notFound: '未找到任务' }
)

onMounted(async () => {
  if (!projectId.value) return
  loading.value = true
  try {
    const res = await testTaskService.getSuite(projectId.value, Number(props.id))
    if (res.success && res.data) suite.value = res.data as ApiTestTaskSuite
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  background: var(--tt-history-page-bg);
  border: 1px solid var(--tt-history-page-border);
  border-radius: 8px;
  overflow: hidden;
}

.api-testing-container--light {
  --tt-history-page-bg: color-mix(in srgb, var(--theme-card-bg) 94%, var(--theme-page-bg) 6%);
  --tt-history-page-border: rgba(148, 163, 184, 0.16);
  --tt-history-empty-text: var(--theme-text-tertiary);
}

.api-testing-container--dark {
  --tt-history-page-bg: rgb(17, 24, 39);
  --tt-history-page-border: rgba(75, 85, 99, 0.35);
  --tt-history-empty-text: rgb(148, 163, 184);
}

.empty-state {
  color: var(--tt-history-empty-text);
}
</style>
