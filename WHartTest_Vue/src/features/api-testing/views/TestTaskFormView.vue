<template>
  <div class="api-testing-container" :class="isDarkTheme ? 'api-testing-container--dark' : 'api-testing-container--light'">
    <TestTaskForm :mode="mode" :id="id" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useThemeStore } from '@/store/themeStore'
import TestTaskForm from '../components/testtasks/TestTaskForm.vue'

const props = defineProps<{ id?: string | number }>()
const themeStore = useThemeStore()
const mode = computed(() => props.id ? 'edit' : 'create')
const isDarkTheme = computed(() => themeStore.isBlack)
</script>

<style scoped>
.api-testing-container {
  height: 100%;
  background: var(--tt-form-page-bg);
  border: 1px solid var(--tt-form-page-border);
  border-radius: 8px;
  overflow: hidden;
}

.api-testing-container--light {
  --tt-form-page-bg: color-mix(in srgb, var(--theme-card-bg) 94%, var(--theme-page-bg) 6%);
  --tt-form-page-border: rgba(148, 163, 184, 0.16);
}

.api-testing-container--dark {
  --tt-form-page-bg: rgb(17, 24, 39);
  --tt-form-page-border: rgba(75, 85, 99, 0.35);
}
</style>
