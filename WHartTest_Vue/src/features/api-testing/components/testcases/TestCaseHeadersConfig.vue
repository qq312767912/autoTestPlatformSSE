<script setup lang="ts">
import { ref, watch } from 'vue'
import { IconDelete, IconPlus } from '@arco-design/web-vue/es/icon'
import { useAppI18n } from '@/composables/useAppI18n'

interface KeyValuePair {
  key: string
  value: string
  description: string
  enabled: boolean
}

interface Props {
  headers?: Record<string, any> | KeyValuePair[]
}

const props = withDefaults(defineProps<Props>(), {
  headers: () => []
})

const emit = defineEmits(['update:headers'])
const { tl } = useAppI18n()

const headersList = ref<KeyValuePair[]>([{ key: '', value: '', description: '', enabled: true }])

const initHeadersList = () => {
  if (Array.isArray(props.headers)) {
    headersList.value = [...props.headers]
  } else {
    headersList.value = Object.entries(props.headers || {}).map(([key, value]) => ({
      key,
      value: String(value),
      description: '',
      enabled: true
    }))
  }
  if (headersList.value.length === 0) {
    headersList.value.push({ key: '', value: '', description: '', enabled: true })
  }
}

watch(() => props.headers, () => {
  initHeadersList()
}, { immediate: true, deep: true })

const addHeader = () => {
  headersList.value.push({ key: '', value: '', description: '', enabled: true })
}

const deleteHeader = (index: number) => {
  headersList.value.splice(index, 1)
  if (headersList.value.length === 0) {
    headersList.value.push({ key: '', value: '', description: '', enabled: true })
  }
}

const getHeaders = () => {
  const headers: Record<string, string> = {}
  for (const header of headersList.value) {
    if (header.enabled && header.key) {
      headers[header.key] = header.value
    }
  }
  return headers
}

defineExpose({ getHeaders })
</script>

<template>
  <div class="h-full flex flex-col p-4 space-y-2">
    <div class="flex-1 min-h-0 overflow-y-auto pr-2">
      <div class="space-y-2">
        <div
          v-for="(header, index) in headersList"
          :key="index"
          class="flex items-center gap-2"
        >
          <a-checkbox v-model="header.enabled" />
          <a-input v-model="header.key" :placeholder="tl('Key')" allow-clear />
          <a-input v-model="header.value" :placeholder="tl('Value')" allow-clear />
          <a-input v-model="header.description" :placeholder="tl('Description')" allow-clear />
          <a-button type="text" status="danger" @click="deleteHeader(index)">
            <template #icon><icon-delete /></template>
          </a-button>
        </div>
      </div>
    </div>
    <div>
      <a-button type="outline" @click="addHeader">
        <template #icon><icon-plus /></template>
        {{ tl('添加请求头') }}
      </a-button>
    </div>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
:deep(.arco-input-wrapper) {
  background: var(--tcf-control-bg) !important;
  border-color: var(--tcf-control-border) !important;

  input {
    color: var(--tcf-text) !important;
    background: transparent !important;
    &::placeholder {
      color: var(--tcf-text-subtle) !important;
    }
  }
}

:deep(.arco-checkbox) {
  color: var(--tcf-text-subtle) !important;
}

:deep(.arco-btn-outline) {
  border-color: var(--tcf-control-border) !important;
  color: var(--tcf-text-muted) !important;

  &:hover {
    border-color: rgba(59, 130, 246, 0.32) !important;
    color: rgb(59, 130, 246) !important;
  }
}

:deep(.arco-btn-text) {
  color: var(--tcf-text-subtle) !important;

  &:hover {
    color: rgb(239, 68, 68) !important;
    background: rgba(239, 68, 68, 0.08) !important;
  }
}
</style>
