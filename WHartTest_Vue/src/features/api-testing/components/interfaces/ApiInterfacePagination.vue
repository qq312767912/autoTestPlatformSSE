<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  total: number
  pageSize: number
  currentPage: number
  showTotal?: boolean
  showSizeChanger?: boolean
  pageSizeOptions?: number[]
}

const props = withDefaults(defineProps<Props>(), {
  showTotal: true,
  showSizeChanger: true,
  pageSizeOptions: () => [10, 20, 30, 50, 100]
})

const emit = defineEmits<{
  'page-change': [page: number]
  'page-size-change': [pageSize: number]
}>()

// 计算总页数
const totalPages = computed(() => Math.ceil(props.total / props.pageSize))

// 处理页码变化
const handlePageChange = (page: number) => {
  emit('page-change', page)
}

// 处理每页数量变化
const handlePageSizeChange = (value: string | number | boolean | Record<string, any> | (string | number | boolean | Record<string, any>)[]) => {
  emit('page-size-change', Number(value))
}
</script>

<template>
  <div class="api-interface-pagination flex items-center justify-between px-4 py-3 border-t">
    <!-- 左侧信息 -->
    <div v-if="showTotal" class="pagination-total text-sm">
      共 {{ total }} 条数据
    </div>

    <!-- 右侧分页控件 -->
    <div class="flex items-center gap-4">
      <!-- 每页数量选择器 -->
      <div v-if="showSizeChanger" class="flex items-center gap-2">
        <span class="pagination-size-label text-sm">每页</span>
        <a-select
          :model-value="pageSize"
          :options="pageSizeOptions.map(v => ({ label: `${v} 条`, value: v }))"
          size="small"
          class="!w-24"
          @change="handlePageSizeChange"
        />
      </div>

      <!-- 分页器 -->
      <a-pagination
        :current="currentPage"
        :total="total"
        :page-size="pageSize"
        :show-total="false"
        :show-jumper="totalPages > 10"
        size="small"
        @change="handlePageChange"
      />
    </div>
  </div>
</template>

<style lang="postcss" scoped>
.api-interface-pagination {
  border-color: rgba(148, 163, 184, 0.18);
  --pagination-text: var(--color-text-3);
  --pagination-input-bg: rgba(255, 255, 255, 0.96);
  --pagination-input-border: rgba(148, 163, 184, 0.22);
}

.pagination-total,
.pagination-size-label {
  color: var(--pagination-text);
}

:global(body.api-testing-theme) .api-interface-pagination {
  border-color: rgb(55, 65, 81);
  --pagination-text: rgb(148, 163, 184);
  --pagination-input-bg: rgba(30, 41, 59, 0.5);
  --pagination-input-border: rgba(148, 163, 184, 0.1);
}

/* 分页样式 - 参考项目管理页面 */
:deep(.arco-pagination) {
  .arco-pagination-item {
    border-radius: 6px !important;
    color: var(--pagination-text) !important;
    background-color: transparent !important;
    
    &:hover {
      color: #60a5fa !important;
      background-color: rgba(59, 130, 246, 0.1) !important;
    }
    
    &.arco-pagination-item-active {
      background-color: rgba(59, 130, 246, 0.2) !important;
      color: #60a5fa !important;
    }
  }

  .arco-pagination-jumper {
    .arco-input {
      border-radius: 6px !important;
      background-color: var(--pagination-input-bg) !important;
      border-color: var(--pagination-input-border) !important;
      color: var(--color-text-1) !important;

      &:hover, &:focus {
        border-color: #60a5fa !important;
      }
    }
  }

  .arco-pagination-total {
    color: var(--pagination-text) !important;
  }
}

/* 选择器样式 */
:deep(.arco-select) {
  background-color: var(--pagination-input-bg) !important;
  border-color: var(--pagination-input-border) !important;
  
  &:hover, &:focus-within {
    border-color: #60a5fa !important;
  }
  
  .arco-select-view-value {
    color: var(--color-text-1) !important;
  }
}

:deep(.arco-select-dropdown) {
  background-color: #1e293b !important;
  border-color: rgba(148, 163, 184, 0.1) !important;
  
  .arco-select-option {
    color: #e2e8f0 !important;
    
    &:hover {
      background-color: rgba(59, 130, 246, 0.1) !important;
    }
    
    &.arco-select-option-selected {
      background-color: rgba(59, 130, 246, 0.2) !important;
      color: #60a5fa !important;
    }
  }
}
</style>