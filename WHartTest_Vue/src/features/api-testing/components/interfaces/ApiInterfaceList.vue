<script setup lang="ts">
import { ref, computed } from 'vue'
import { IconSearch, IconSend, IconEdit, IconDelete, IconClockCircle } from '@arco-design/web-vue/es/icon'
import type { ApiInterface } from '../../services/interfaceService'
import { formatDateTime } from '@/utils/formatters'

const formatRelativeTime = formatDateTime
const formatShortDateTime = formatDateTime

interface Props {
  interfaces: ApiInterface[]
  loading?: boolean
  selectedInterfaceId?: number
  currentModuleName?: string
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  interfaces: () => []
})

const emit = defineEmits<{
  'interface-select': [api: ApiInterface]
  'interface-edit': [api: ApiInterface]
  'interface-delete': [api: ApiInterface]
  'interface-run': [api: ApiInterface]
}>()

// 搜索关键字
const searchKeyword = ref('')

// 过滤后的接口列表
const filteredInterfaces = computed(() => {
  if (!searchKeyword.value) return props.interfaces
  
  const keyword = searchKeyword.value.toLowerCase()
  return props.interfaces.filter(item =>
    item.name.toLowerCase().includes(keyword) ||
    item.url.toLowerCase().includes(keyword) ||
    item.method.toLowerCase().includes(keyword)
  )
})

// 处理接口点击
const handleRowClick = (record: any) => {
  emit('interface-select', record as ApiInterface)
}

// 获取方法颜色
const getMethodColor = (method: string) => {
  const colors: Record<string, string> = {
    GET: 'green',
    POST: 'blue',
    PUT: 'orange',
    DELETE: 'red',
    PATCH: 'arcoblue',
    HEAD: 'purple',
    OPTIONS: 'cyan'
  }
  return colors[method] || 'gray'
}
</script>

<template>
  <div class="api-interface-list h-full flex flex-col">
    <!-- 搜索区域 -->
    <div class="p-4">
      <div class="flex items-center justify-between gap-4">
        <div class="flex items-center gap-3 flex-shrink-0">
          <span class="list-module-name whitespace-nowrap">{{ currentModuleName || '全部接口' }}</span>
          <a-tag size="small" class="flex-shrink-0">{{ filteredInterfaces.length }} 个接口</a-tag>
        </div>
        <a-input-search
          v-model="searchKeyword"
          placeholder="搜索接口名称、URL或方法"
          class="w-64"
          allow-clear
        />
      </div>
    </div>

    <!-- 表格区域 -->
    <div class="flex-1 overflow-hidden">
      <a-spin :loading="loading" dot class="h-full">
        <a-table
          :data="filteredInterfaces"
          :pagination="false"
          :scroll="{ y: 'calc(100vh - 360px)' }"
          class="custom-table"
          row-key="id"
          :row-class="(record) => record.id === selectedInterfaceId ? 'selected-row' : ''"
          @row-click="handleRowClick"
        >
          <template #columns>
            <a-table-column title="ID" data-index="id" :width="70" align="center" />
            <a-table-column title="请求方法" data-index="method" :width="100" align="center">
              <template #cell="{ record }">
                <a-tag
                  :color="getMethodColor(record.method)"
                  size="small"
                  class="!font-medium"
                >
                  {{ record.method }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="接口名称" data-index="name" align="center">
              <template #cell="{ record }">
                <span class="list-name-link cursor-pointer">{{ record.name }}</span>
              </template>
            </a-table-column>
            <a-table-column title="URL" data-index="url">
              <template #cell="{ record }">
                <span class="list-url-text">{{ record.url }}</span>
              </template>
            </a-table-column>
            <a-table-column title="创建时间" data-index="created_at" :width="150" align="center">
              <template #cell="{ record }">
                <a-tooltip v-if="record.created_at" :content="formatShortDateTime(record.created_at)">
                  <div class="flex items-center gap-1 justify-center">
                    <icon-clock-circle class="list-time-icon" :size="14" />
                    <span class="list-time-text text-xs">
                      {{ formatRelativeTime(record.created_at) }}
                    </span>
                  </div>
                </a-tooltip>
                <span v-else class="list-time-text">-</span>
              </template>
            </a-table-column>
            <a-table-column title="更新时间" data-index="updated_at" :width="150" align="center">
              <template #cell="{ record }">
                <a-tooltip v-if="record.updated_at" :content="formatShortDateTime(record.updated_at)">
                  <div class="flex items-center gap-1 justify-center">
                    <icon-clock-circle class="list-time-icon list-time-icon--updated" :size="14" />
                    <span class="list-time-text text-xs">
                      {{ formatRelativeTime(record.updated_at) }}
                    </span>
                  </div>
                </a-tooltip>
                <span v-else class="list-time-text">-</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" align="center" :width="150">
              <template #cell="{ record }">
                <div class="flex justify-center gap-1">
                  <a-button
                    type="text"
                    size="mini"
                    @click.stop="$emit('interface-run', record)"
                    title="调试接口"
                  >
                    <template #icon><icon-send /></template>
                  </a-button>
                  <a-button
                    type="text"
                    size="mini"
                    @click.stop="$emit('interface-edit', record)"
                    title="编辑接口"
                  >
                    <template #icon><icon-edit /></template>
                  </a-button>
                  <a-button
                    type="text"
                    size="mini"
                    status="danger"
                    @click.stop="$emit('interface-delete', record)"
                    title="删除接口"
                  >
                    <template #icon><icon-delete /></template>
                  </a-button>
                </div>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>
    </div>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
.api-interface-list {
  --interface-list-text: var(--color-text-2);
  --interface-list-muted: var(--color-text-3);
  --interface-list-header-bg: rgba(248, 250, 252, 0.96);
  --interface-list-row-hover: rgba(59, 130, 246, 0.06);
  --interface-list-row-selected: rgba(59, 130, 246, 0.12);
  --interface-list-empty: rgba(100, 116, 139, 0.9);
}

.list-module-name,
.list-url-text,
.list-time-text {
  color: var(--interface-list-muted);
}

.list-name-link {
  color: var(--interface-list-text);
}

.list-name-link:hover {
  color: rgb(96, 165, 250);
}

.list-time-icon {
  color: rgba(100, 116, 139, 0.9);
}

.list-time-icon--updated {
  color: rgb(96, 165, 250);
}

:global(body.api-testing-theme) .api-interface-list {
  --interface-list-text: rgb(229, 231, 235);
  --interface-list-muted: rgb(156, 163, 175);
  --interface-list-header-bg: rgba(30, 41, 59, 0.5);
  --interface-list-row-hover: rgba(59, 130, 246, 0.05);
  --interface-list-row-selected: rgba(59, 130, 246, 0.1);
  --interface-list-empty: rgb(107, 114, 128);
}

.custom-table {
  @apply h-full;
}

.custom-table :deep(.arco-table) {
  background-color: transparent !important;
}

.custom-table :deep(.arco-table-container) {
  background-color: transparent !important;
  border: none !important;
}

.custom-table :deep(.arco-table-body) {
  background-color: transparent !important;
}

/* 隐藏所有滚动条但保留滚动功能 */
.custom-table :deep(*::-webkit-scrollbar) {
  width: 0 !important;
  height: 0 !important;
  display: none !important;
}

/* Firefox */
.custom-table :deep(*) {
  scrollbar-width: none !important;
}

/* IE 和 Edge */
.custom-table :deep(*) {
  -ms-overflow-style: none !important;
}

.custom-table :deep(.arco-table-header) {
  background-color: var(--interface-list-header-bg) !important;
  position: sticky;
  top: 0;
  z-index: 2;
}

.custom-table :deep(.arco-table-th) {
  color: var(--interface-list-text) !important;
}

.custom-table :deep(.arco-table-td) {
  color: var(--interface-list-text) !important;
}

.custom-table :deep(.arco-table-content) {
  background-color: transparent !important;
}

.custom-table :deep(.arco-spin) {
  @apply h-full flex flex-col;
}

.custom-table :deep(.arco-spin-children) {
  @apply h-full flex flex-col;
}

/* 选中行样式 */
.custom-table :deep(.selected-row) {
  background-color: var(--interface-list-row-selected) !important;
}

.custom-table :deep(.arco-table-tr:hover) {
  background-color: var(--interface-list-row-hover) !important;
  cursor: pointer;
}

/* 空状态样式 */
:deep(.arco-empty) {
  color: var(--interface-list-empty);
}

:deep(.arco-input-wrapper),
:deep(.arco-input-wrapper input) {
  color: var(--interface-list-text) !important;
}

:deep(.arco-input-wrapper input::placeholder),
:deep(.arco-input-search-prefix),
:deep(.arco-input-search-suffix) {
  color: var(--interface-list-muted) !important;
}
</style>