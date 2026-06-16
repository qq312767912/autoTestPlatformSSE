<script setup lang="ts">
import { ref } from 'vue'
import type { EnvironmentVariable } from '../../services/environmentService'
import {
  IconCode,
  IconEdit,
  IconDelete,
  IconInfoCircle,
  IconLock,
} from '@arco-design/web-vue/es/icon'

interface Props {
  variables: EnvironmentVariable[]
}

interface Emits {
  (e: 'edit', index: number): void
  (e: 'delete', index: number): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const getTypeLabel = (type: string) => {
  const types: Record<string, string> = {
    string: 'String',
    integer: 'Integer',
    float: 'Float',
    boolean: 'Boolean',
    json: 'JSON',
    list: 'List',
    dict: 'Dict',
  }
  return types[type] || type
}
</script>

<template>
  <div class="env-variable-list overflow-y-auto max-h-[calc(100vh-24rem)]">
    <div
      v-for="(variable, index) in variables"
      :key="index"
      class="variable-list-item flex items-start gap-3 p-3 rounded-lg transition-colors mb-2 group"
    >
      <!-- 左侧图标 -->
      <div class="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center">
        <icon-code class="text-purple-400" />
      </div>

      <!-- 中间内容 -->
      <div class="flex-1 min-w-0">
        <!-- 标题和描述 -->
        <div class="mb-2">
          <div class="flex items-center gap-2">
            <span class="text-sm font-medium variable-list-title">变量 #{{ index + 1 }}</span>
            <span class="text-xs variable-list-subtle">·</span>
            <span class="text-xs variable-list-subtle truncate">{{ variable.description || '暂无描述' }}</span>
            <a-tag v-if="variable.is_sensitive" size="small" status="danger">
              <template #icon><icon-lock /></template>
              敏感
            </a-tag>
          </div>
        </div>

        <!-- 变量信息 -->
        <div class="grid grid-cols-3 gap-4">
          <div class="space-y-1">
            <div class="text-xs variable-list-subtle">变量名</div>
            <div class="text-sm variable-list-title truncate">{{ variable.name }}</div>
          </div>
          <div class="space-y-1">
            <div class="text-xs variable-list-subtle">变量值</div>
            <div class="text-sm variable-list-title truncate">
              <span v-if="variable.is_sensitive">******</span>
              <span v-else>{{ variable.value }}</span>
            </div>
          </div>
          <div class="space-y-1">
            <div class="text-xs variable-list-subtle">类型</div>
            <div class="text-sm variable-list-title">{{ getTypeLabel(variable.type) }}</div>
          </div>
        </div>
      </div>

      <!-- 右侧操作按钮 -->
      <div class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
        <a-button
          type="text"
          size="mini"
          @click="emit('edit', index)"
        >
          <template #icon><icon-edit class="variable-list-subtle" /></template>
        </a-button>
        <a-button
          type="text"
          status="danger"
          size="mini"
          @click="emit('delete', index)"
        >
          <template #icon><icon-delete /></template>
        </a-button>
      </div>
    </div>
  </div>
</template> 

<style lang="postcss" scoped>
.env-variable-list {
  scrollbar-width: none;
  -ms-overflow-style: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.variable-list-item {
  background: color-mix(in srgb, var(--env-block-bg) 88%, var(--theme-page-bg) 12%);
  border: 1px solid var(--env-block-border);
}

.variable-list-item:hover {
  border-color: rgba(148, 163, 184, 0.3);
}

.variable-list-title {
  color: var(--env-text);
}

.variable-list-subtle {
  color: var(--env-text-subtle);
}
</style>