<template>
  <div class="api-testing-container" :class="isDarkTheme ? 'api-testing-container--dark' : 'api-testing-container--light'">
    <TestCaseForm
      v-if="projectId"
      :project-id="projectId"
      mode="create"
      @success="handleSuccess"
      @cancel="goBackToTestCases"
    />
    <div v-else class="flex items-center justify-center h-full text-gray-500">
      请先选择项目
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/store/projectStore'
import { useThemeStore } from '@/store/themeStore'
import TestCaseForm from '../components/testcases/TestCaseForm.vue'

const router = useRouter()
const route = useRoute()
const projectStore = useProjectStore()
const themeStore = useThemeStore()
const projectId = computed(() => projectStore.currentProjectId)
const isDarkTheme = computed(() => themeStore.isBlack)

const getReturnQuery = () => ({
  tab: typeof route.query.tab === 'string' ? route.query.tab : 'testcases'
})

const goBackToTestCases = () => {
  router.push({ path: '/api-testing', query: getReturnQuery() })
}

const handleSuccess = (payload: { id: number }) => {
  router.replace({
    name: 'ApiTestCaseEdit',
    params: { id: payload.id },
    query: getReturnQuery()
  })
}
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  background: var(--tc-create-page-bg);
  border: 1px solid var(--tc-create-page-border);
  border-radius: 8px;
  overflow: hidden;
}

.api-testing-container--light {
  --tc-create-page-bg: color-mix(in srgb, var(--theme-card-bg) 94%, var(--theme-page-bg) 6%);
  --tc-create-page-border: rgba(148, 163, 184, 0.16);
}

.api-testing-container--dark {
  --tc-create-page-bg: rgb(17, 24, 39);
  --tc-create-page-border: rgba(75, 85, 99, 0.35);
}
</style>
