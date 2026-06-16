<script setup lang="ts">
import { ref, watch } from 'vue'
import { IconDelete, IconPlus } from '@arco-design/web-vue/es/icon'
import type { KeyValuePair } from '../../services/interfaceService'

interface Props {
  params?: KeyValuePair[]
}

const props = defineProps<Props>()

const params = ref<KeyValuePair[]>([{ key: '', value: '', description: '', enabled: true }])

// 监听props变化
watch(() => props.params, (newParams) => {
  if (newParams && newParams.length > 0) {
    params.value = newParams
  } else {
    params.value = [{ key: '', value: '', description: '', enabled: true }]
  }
}, { immediate: true })

// 添加参数行
const addRow = () => {
  params.value.push({ key: '', value: '', description: '', enabled: true })
}

// 删除参数行
const removeRow = (index: number) => {
  params.value.splice(index, 1)
  // 如果删除后没有行了，添加一个空行
  if (params.value.length === 0) {
    params.value.push({ key: '', value: '', description: '', enabled: true })
  }
}

// 向父组件暴露参数数据
defineExpose({
  getParams: () => params.value.filter(param => param.key || param.value)
})
</script>

<template>
  <div class="h-full flex flex-col p-4 space-y-2">
    <div class="flex-1 min-h-0 overflow-y-auto pr-2">
      <div class="space-y-2">
        <div
          v-for="(param, index) in params"
          :key="index"
          class="flex items-center gap-2"
        >
          <a-checkbox v-model="param.enabled" />
          <a-input
            v-model="param.key"
            placeholder="Key"
            allow-clear
          />
          <a-input
            v-model="param.value"
            placeholder="Value"
            allow-clear
          />
          <a-input
            v-model="param.description"
            placeholder="Description"
            allow-clear
          />
          <a-button
            type="text"
            status="danger"
            @click="removeRow(index)"
          >
            <template #icon><icon-delete /></template>
          </a-button>
        </div>
      </div>
    </div>
    <div>
      <a-button type="outline" @click="addRow">
        <template #icon><icon-plus /></template>
        添加参数
      </a-button>
    </div>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
:deep(.arco-input-wrapper) {
  @apply bg-white border-[color:var(--color-border-2)];
  
  input {
    @apply text-[color:var(--color-text-1)] bg-transparent;
    &::placeholder {
      @apply text-[color:var(--color-text-3)];
    }
  }
}

:deep(.arco-checkbox) {
  @apply text-[color:var(--color-text-2)];
}

:deep(.arco-btn-outline) {
  @apply border-[color:var(--color-border-2)] text-[color:var(--color-text-2)];
  
  &:hover {
    @apply border-blue-500 text-blue-500;
  }
}

:global(body.api-testing-theme) :deep(.arco-input-wrapper) {
  @apply bg-gray-900/60 border-gray-700;

  input {
    @apply text-gray-200 bg-transparent;
    &::placeholder {
      @apply text-gray-500;
    }
  }
}

:global(body.api-testing-theme) :deep(.arco-checkbox) {
  @apply text-gray-400;
}

:global(body.api-testing-theme) :deep(.arco-btn-outline) {
  @apply border-gray-600 text-gray-300;
}
</style>