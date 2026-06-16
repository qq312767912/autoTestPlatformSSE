<script setup lang="ts">
import { computed } from 'vue'
import type { Environment } from '../../services/environmentService'
import {
  IconPlus,
  IconStorage,
  IconLink,
  IconSearch,
  IconSettings
} from '@arco-design/web-vue/es/icon'

interface Props {
  environments: Environment[]
  selectedEnvironment: Environment | null
  loading: boolean
  searchKeyword: string
  showGlobalHeaders?: boolean
  showDatabaseConfig?: boolean
}

interface Emits {
  (e: 'update:searchKeyword', value: string): void
  (e: 'select', environment: Environment): void
  (e: 'create'): void
  (e: 'selectGlobalHeaders'): void
  (e: 'selectDatabaseConfig'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const filteredEnvironments = computed(() => {
  if (!props.searchKeyword) return props.environments
  const keyword = props.searchKeyword.toLowerCase()
  return props.environments.filter(env => 
    env.name.toLowerCase().includes(keyword) || 
    env.description?.toLowerCase().includes(keyword) ||
    env.base_url.toLowerCase().includes(keyword)
  )
})
</script>

<template>
  <div class="h-full overflow-hidden env-list-container env-list-panel">
    <!-- 环境列表卡片 -->
    <a-card class="h-full !rounded-lg !w-full env-card">
      <!-- 卡片标题区域 -->
      <template #title>
        <div class="flex justify-between items-center py-2">
          <div class="flex items-center gap-2">
            <icon-storage class="text-blue-500" />
            <span class="list-heading font-medium">环境列表</span>
          </div>
          <a-button type="outline" size="small" @click="emit('create')">
            <template #icon><icon-plus /></template>
            新建
          </a-button>
        </div>
      </template>
      
      <div class="card-content">
        <!-- 搜索框 -->
        <div class="search-container">
          <a-input-search
            :model-value="searchKeyword"
            @update:model-value="(val: string) => emit('update:searchKeyword', val)"
            placeholder="搜索环境..."
            allow-clear
            class="w-full"
          >
            <template #prefix>
              <icon-search />
            </template>
          </a-input-search>
        </div>
        
        <!-- 列表内容区域 -->
        <a-spin :loading="loading" dot class="list-spin">
          <div class="content-container custom-scrollbar">
            <!-- 全局请求头卡片 -->
            <div
              class="mb-4 cursor-pointer transition-all w-full card-item"
              @click="emit('selectGlobalHeaders')"
            >
              <div
                class="env-entry-card env-entry-card--teal w-full"
                :class="{ 'is-active is-teal': showGlobalHeaders }"
              >
                <div class="p-4 flex items-center gap-4">
                  <div class="w-10 h-10 rounded-lg bg-teal-500/10 flex items-center justify-center flex-shrink-0">
                    <icon-settings class="text-teal-500 text-xl" />
                  </div>
                  <div class="flex-1 min-w-0 overflow-hidden">
                    <div class="font-medium entry-title truncate max-w-full">全局请求头</div>
                    <div class="mt-1 text-xs entry-subtitle truncate max-w-full">
                      项目级别的全局请求头设置
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 数据库配置卡片 -->
            <div
              class="mb-4 cursor-pointer transition-all w-full card-item"
              @click="emit('selectDatabaseConfig')"
            >
              <div
                class="env-entry-card env-entry-card--purple w-full"
                :class="{ 'is-active is-purple': showDatabaseConfig }"
              >
                <div class="p-4 flex items-center gap-4">
                  <div class="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center flex-shrink-0">
                    <icon-storage class="text-purple-500 text-xl" />
                  </div>
                  <div class="flex-1 min-w-0 overflow-hidden">
                    <div class="font-medium entry-title truncate max-w-full">数据库配置</div>
                    <div class="mt-1 text-xs entry-subtitle truncate max-w-full">
                      项目级别的数据库连接配置
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 环境列表卡片 -->
            <div
              v-for="env in filteredEnvironments"
              :key="env.id"
              class="mb-4 cursor-pointer transition-all w-full card-item"
              @click="emit('select', env)"
            >
              <div 
                class="env-entry-card env-entry-card--blue w-full"
                :class="{ 'is-active is-blue': selectedEnvironment?.id === env.id }"
              >
                <div class="p-4 flex items-center gap-4">
                  <div class="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center flex-shrink-0">
                    <icon-storage class="text-blue-500 text-xl" />
                  </div>
                  <div class="flex-1 min-w-0 overflow-hidden">
                    <div class="flex items-center justify-between gap-2 w-full">
                      <div class="font-medium entry-title truncate max-w-[80%]" :title="env.name">{{ env.name }}</div>
                      <a-tag
                        :color="env.is_active ? 'green' : 'red'"
                        size="small"
                        class="flex-shrink-0"
                      >{{ env.is_active ? '启用' : '禁用' }}</a-tag>
                    </div>
                    <div class="mt-1 flex items-center gap-2 text-xs entry-subtitle w-full">
                      <icon-link class="entry-url-icon flex-shrink-0" />
                      <span class="truncate max-w-[280px] inline-block" :title="env.base_url">{{ env.base_url }}</span>
                    </div>
                  </div>
                </div>
                <div v-if="env.description" class="px-4 pb-3 text-xs entry-note truncate max-w-full" :title="env.description">
                  {{ env.description }}
                </div>
              </div>
            </div>
            
            <!-- 无环境时的空状态 -->
            <div
              v-if="filteredEnvironments.length === 0 && !loading"
              class="text-center py-6 entry-note"
            >
              {{ searchKeyword ? '没有找到匹配的环境' : '暂无环境，请点击"新建"按钮创建' }}
            </div>
          </div>
        </a-spin>
      </div>
    </a-card>
  </div>
</template>

<style lang="postcss" scoped>
.env-list-container {
  display: flex;
  flex-direction: column;
}

:deep(.arco-spin) {
  .arco-spin-mask {
    background-color: transparent !important;
  }
}

.env-card {
  display: flex;
  flex-direction: column;
}

.env-card :deep(.arco-card) {
  background: var(--env-shell-bg) !important;
  border: 1px solid var(--env-shell-border) !important;
  box-shadow: var(--env-shell-shadow);
}

.env-card :deep(.arco-card-header) {
  flex-shrink: 0;
  border-bottom: 1px solid var(--env-header-border);
}

.env-card :deep(.arco-card-body) {
  padding: 0 !important;
  margin: 0 !important;
  overflow: hidden;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.card-content {
  padding: 16px;
  display: flex;
  flex-direction: column;
  height: 100%;
  flex: 1;
  overflow: hidden;
}

.search-container {
  margin-bottom: 16px;
  flex-shrink: 0;
}

.search-container :deep(.arco-input-wrapper) {
  background: var(--env-input-bg) !important;
  border-color: var(--env-input-border) !important;

  &:hover,
  &:focus-within {
    border-color: rgba(var(--theme-accent-rgb), 0.42) !important;
    background: var(--env-input-hover-bg) !important;
  }
}

.search-container :deep(.arco-input),
.search-container :deep(.arco-input::placeholder),
.search-container :deep(.arco-input-prefix) {
  color: var(--env-text-subtle) !important;
}

.search-container :deep(.arco-input) {
  color: var(--env-text) !important;
}

.list-spin {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.content-container {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  width: 100%;
  padding-right: 4px;
  /* 隐藏滚动条但保留滚动功能 */
  scrollbar-width: none; /* Firefox */
  -ms-overflow-style: none; /* IE and Edge */
  &::-webkit-scrollbar {
    display: none; /* Chrome, Safari, Opera*/
  }
}

/* 调整卡片内的内容对齐方式 */
.card-item {
  width: 100%;
  padding: 0;
}

.card-item > div {
  width: 100%;
}

.list-heading,
.entry-title {
  color: var(--env-text);
}

.entry-subtitle,
.entry-note,
.entry-url-icon {
  color: var(--env-text-subtle);
}

.env-entry-card {
  background: color-mix(in srgb, var(--env-shell-bg) 88%, var(--theme-page-bg) 12%);
  border: 1px solid var(--env-block-border);
  border-radius: 0.75rem;
  overflow: hidden;
  box-shadow: 0 10px 22px rgba(15, 23, 42, 0.05);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease, background-color 0.2s ease;
}

.env-entry-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08);
}

.env-entry-card--teal:hover,
.env-entry-card.is-teal {
  border-color: rgb(20 184 166 / 0.65);
}

.env-entry-card--purple:hover,
.env-entry-card.is-purple {
  border-color: rgb(168 85 247 / 0.65);
}

.env-entry-card--blue:hover,
.env-entry-card.is-blue {
  border-color: rgb(59 130 246 / 0.65);
}

.env-entry-card.is-active {
  box-shadow: 0 16px 30px rgba(15, 23, 42, 0.1);
}

/* 确保所有内容容器内部元素都有一致的填充 */
.p-4 {
  padding: 1rem;
}

/* 自定义滚动条样式 */
.custom-scrollbar {
  scrollbar-width: none; /* Firefox */
  -ms-overflow-style: none; /* IE and Edge */
  
  &::-webkit-scrollbar {
    display: none; /* Chrome, Safari, Opera*/
  }
}

/* 强制所有文本内容截断 */
:deep(.truncate) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

/* 限制基础URL宽度 */
.truncate.max-w-\[280px\] {
  max-width: 280px; /* URL显示宽度 */
}
</style> 