<script setup lang="ts">
import { computed } from 'vue'
import { IconEmpty, IconPlus } from '@arco-design/web-vue/es/icon'
import type { TableColumnData } from '@arco-design/web-vue'
import type { ApiSyncConfig } from '../../services/syncService'
import { useAppI18n } from '@/composables/useAppI18n'
import { useThemeStore } from '@/store/themeStore'

const themeStore = useThemeStore()
const { isEnglish } = useAppI18n()
const isDarkTheme = computed(() => themeStore.isBlack)

const props = defineProps<{
  loading: boolean
  configs: ApiSyncConfig[]
  selectedRowKeys: number[]
  fieldOptions: { label: string; value: string }[]
}>()

const emit = defineEmits<{
  (e: 'update:selectedRowKeys', value: number[]): void
  (e: 'sync', record: ApiSyncConfig): void
  (e: 'edit', record: ApiSyncConfig): void
  (e: 'view', record: ApiSyncConfig): void
  (e: 'delete', record: ApiSyncConfig): void
  (e: 'create'): void
}>()

const text = computed(() => isEnglish.value
  ? {
      index: 'No.',
      interfaceName: 'Interface Name',
      testCaseName: 'Test Case Name',
      stepName: 'Step Name',
      syncFields: 'Sync Fields',
      syncMode: 'Sync Mode',
      autoSync: 'Auto Sync',
      manualSync: 'Manual Sync',
      status: 'Status',
      createdInfo: 'Created Info',
      operations: 'Actions',
      emptyConfigs: 'No sync configs yet',
      createConfig: 'Create Config',
      enabled: 'Enabled',
      disabled: 'Disabled',
      createdBy: 'Created by:',
      createdAt: 'Created at:',
      sync: 'Sync',
      edit: 'Edit',
      details: 'Details',
      delete: 'Delete',
    }
  : {
      index: '序号',
      interfaceName: '接口名称',
      testCaseName: '用例名称',
      stepName: '步骤名称',
      syncFields: '同步字段',
      syncMode: '同步模式',
      autoSync: '自动同步',
      manualSync: '手动同步',
      status: '状态',
      createdInfo: '创建信息',
      operations: '操作',
      emptyConfigs: '暂无同步配置',
      createConfig: '新建配置',
      enabled: '已启用',
      disabled: '已禁用',
      createdBy: '创建者：',
      createdAt: '时间：',
      sync: '同步',
      edit: '编辑',
      details: '详情',
      delete: '删除',
    }
)

const columns = computed<TableColumnData[]>(() => [
  {
    title: text.value.index,
    width: 70,
    align: 'center',
    slotName: 'index'
  },
  {
    title: text.value.interfaceName,
    width: isEnglish.value ? 170 : 150,
    align: 'center',
    render: ({ record }) => record.interface_info?.name || '-'
  },
  {
    title: text.value.testCaseName,
    width: isEnglish.value ? 170 : 150,
    align: 'center',
    render: ({ record }) => record.testcase_info?.name || '-'
  },
  {
    title: text.value.stepName,
    width: isEnglish.value ? 160 : 150,
    align: 'center',
    render: ({ record }) => record.step_info?.name || '-'
  },
  {
    title: text.value.syncFields,
    width: isEnglish.value ? 420 : 390,
    align: 'center',
    slotName: 'sync_fields'
  },
  {
    title: text.value.syncMode,
    width: isEnglish.value ? 120 : 100,
    align: 'center',
    render: ({ record }) => record.sync_mode === 'auto' ? text.value.autoSync : text.value.manualSync
  },
  {
    title: text.value.status,
    width: isEnglish.value ? 90 : 60,
    align: 'center',
    slotName: 'status'
  },
  {
    title: text.value.createdInfo,
    width: isEnglish.value ? 250 : 210,
    align: 'center',
    slotName: 'created_info'
  },
  {
    title: text.value.operations,
    width: isEnglish.value ? 150 : 120,
    align: 'center',
    slotName: 'operations'
  }
])

const handleSelectionChange = (selectedKeys: (string | number)[]) => {
  emit('update:selectedRowKeys', selectedKeys.map(key => Number(key)))
}
</script>

<template>
  <div class="api-config-table-shell rounded-lg shadow-xl border" :class="isDarkTheme ? 'api-config-table-shell--dark' : 'api-config-table-shell--light'">
    <a-table
      :loading="loading"
      :data="configs"
      :columns="columns"
      :pagination="false"
      :bordered="false"
      :stripe="true"
      row-key="id"
      :row-selection="{
        type: 'checkbox',
        showCheckedAll: true,
        selectedRowKeys,
        onlyCurrent: false
      }"
      @selection-change="handleSelectionChange"
      class="custom-table"
    >
      <template #empty>
        <div class="flex flex-col items-center justify-center py-16">
          <icon-empty class="empty-icon w-16 h-16 mb-4" />
          <div class="empty-text mb-4">{{ text.emptyConfigs }}</div>
          <a-button type="outline" size="large" @click="emit('create')">
            <template #icon>
              <icon-plus />
            </template>
            {{ text.createConfig }}
          </a-button>
        </div>
      </template>

      <template #sync_fields="{ record }">
        <div class="flex flex-col items-center justify-center gap-2 min-h-[56px]">
          <!-- 第一行：请求相关字段 -->
          <div class="flex flex-wrap justify-center gap-1.5">
            <template v-for="field in record.sync_fields" :key="field">
              <a-tag
                v-if="['method', 'url', 'headers', 'params', 'body'].includes(field)"
                color="arcoblue"
                size="medium"
                class="rounded-md"
              >
                {{ fieldOptions.find(opt => opt.value === field)?.label || field }}
              </a-tag>
            </template>
          </div>
          <!-- 第二行：钩子和其他字段 -->
          <div class="flex flex-wrap justify-center gap-1.5">
            <template v-for="field in record.sync_fields" :key="field">
              <a-tag
                v-if="['setup_hooks', 'teardown_hooks', 'variables', 'validators', 'extract'].includes(field)"
                color="arcoblue"
                size="medium"
                class="rounded-md"
              >
                {{ fieldOptions.find(opt => opt.value === field)?.label || field }}
              </a-tag>
            </template>
          </div>
        </div>
      </template>

      <template #status="{ record }">
        <a-tag 
          :color="record.sync_enabled ? 'green' : 'red'"
          size="medium"
          class="rounded-md"
        >
          {{ record.sync_enabled ? text.enabled : text.disabled }}
        </a-tag>
      </template>

      <template #created_info="{ record }">
        <div class="flex flex-col gap-1 text-sm">
          <div class="flex items-center gap-1">
            <span class="config-meta-label">{{ text.createdBy }}</span>
            <span>{{ record.created_by_info?.username || '-' }}</span>
          </div>
          <div class="flex items-center gap-1">
            <span class="config-meta-label">{{ text.createdAt }}</span>
            <span>{{ record.created_at ? new Date(record.created_at).toLocaleString() : '-' }}</span>
          </div>
        </div>
      </template>

      <template #index="{ rowIndex }">
        <span class="config-index-text">{{ rowIndex + 1 }}</span>
      </template>

      <template #operations="{ record }">
        <div class="flex items-center justify-center gap-0">
          <a-button
            type="text"
            size="mini"
            class="h-6 leading-6 px-2"
            :loading="loading"
            @click="emit('sync', record)"
          >
            {{ text.sync }}
          </a-button>
          <a-button
            type="text"
            size="mini"
            class="h-6 leading-6 px-2"
            :loading="loading"
            @click="emit('edit', record)"
          >
            {{ text.edit }}
          </a-button>
          <a-button
            type="text"
            size="mini"
            class="h-6 leading-6 px-2"
            :loading="loading"
            @click="emit('view', record)"
          >
            {{ text.details }}
          </a-button>
          <a-button
            type="text"
            size="mini"
            class="h-6 leading-6 px-2 mt-0.5"
            status="danger"
            :loading="loading"
            @click="emit('delete', record)"
          >
            {{ text.delete }}
          </a-button>
        </div>
      </template>
    </a-table>
  </div>
</template>

<style scoped>
@reference "tailwindcss";
.api-config-table-shell {
  --api-table-bg: rgba(255, 255, 255, 0.96);
  --api-table-border: rgba(148, 163, 184, 0.18);
  --api-table-shadow: 0 12px 26px rgba(15, 23, 42, 0.08);
  --api-table-header-bg: rgba(248, 250, 252, 0.96);
  --api-table-header-text: var(--color-text-1);
  --api-table-cell-text: var(--color-text-2);
  --api-table-meta-text: var(--color-text-3);
  --api-table-row-hover: rgba(15, 23, 42, 0.04);
  --api-table-empty-text: var(--color-text-3);
  --api-table-empty-icon: rgba(148, 163, 184, 0.72);
}

.api-config-table-shell--dark {
  --api-table-bg: rgba(31, 41, 55, 1);
  --api-table-border: rgba(55, 65, 81, 0.5);
  --api-table-shadow: 0 12px 26px rgba(2, 6, 23, 0.28);
  --api-table-header-bg: rgba(17, 24, 39, 0.5);
  --api-table-header-text: rgb(209, 213, 219);
  --api-table-cell-text: rgb(209, 213, 219);
  --api-table-meta-text: rgb(156, 163, 175);
  --api-table-row-hover: rgba(55, 65, 81, 0.3);
  --api-table-empty-text: rgb(156, 163, 175);
  --api-table-empty-icon: rgb(75, 85, 99);
}

.api-config-table-shell {
  background: var(--api-table-bg);
  border-color: var(--api-table-border);
  box-shadow: var(--api-table-shadow);
}

.empty-icon,
.empty-text,
.config-meta-label,
.config-index-text {
  color: var(--api-table-meta-text);
}

:deep(.custom-table) {
  background: transparent;
}

:deep(.custom-table .arco-table-container) {
  border: 0;
}

:deep(.custom-table .arco-table-th) {
  background: var(--api-table-header-bg);
  color: var(--api-table-header-text);
  border-color: var(--api-table-border);
  font-weight: 500;
  font-size: 0.875rem;
  padding-top: 1rem;
  padding-bottom: 1rem;
}

:deep(.custom-table .arco-table-td) {
  background: transparent;
  color: var(--api-table-cell-text);
  border-color: var(--api-table-border);
}

:deep(.custom-table .arco-table-tr:hover .arco-table-td) {
  background: var(--api-table-row-hover);
}
</style> 