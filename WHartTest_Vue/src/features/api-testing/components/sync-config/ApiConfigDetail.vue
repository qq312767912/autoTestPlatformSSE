<script setup lang="ts">
import { computed } from 'vue'
import type { ApiSyncConfig } from '../../services/syncService'
import { useAppI18n } from '@/composables/useAppI18n'
import { useThemeStore } from '@/store/themeStore'

const props = defineProps<{
  visible: boolean
  config: ApiSyncConfig | null
  fieldOptions: { label: string; value: string }[]
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const themeStore = useThemeStore()
const { isEnglish } = useAppI18n()
const isDarkTheme = computed(() => themeStore.isBlack)

const text = computed(() => isEnglish.value
  ? {
      detailTitle: 'Sync Config Details',
      basicInfo: 'Basic Info',
      interfaceName: 'Interface Name:',
      testCaseName: 'Test Case Name:',
      stepName: 'Step Name:',
      createdBy: 'Created by:',
      statusInfo: 'Status Info',
      syncMode: 'Sync Mode:',
      autoSync: 'Auto Sync',
      manualSync: 'Manual Sync',
      syncStatus: 'Sync Status:',
      enabled: 'Enabled',
      disabled: 'Disabled',
      createdAt: 'Created At:',
      updatedAt: 'Updated At:',
      syncFields: 'Sync Fields',
      watchFields: 'Watch Fields',
    }
  : {
      detailTitle: '同步配置详情',
      basicInfo: '基本信息',
      interfaceName: '接口名称：',
      testCaseName: '用例名称：',
      stepName: '步骤名称：',
      createdBy: '创建者：',
      statusInfo: '状态信息',
      syncMode: '同步模式：',
      autoSync: '自动同步',
      manualSync: '手动同步',
      syncStatus: '同步状态：',
      enabled: '已启用',
      disabled: '已禁用',
      createdAt: '创建时间：',
      updatedAt: '更新时间：',
      syncFields: '同步字段',
      watchFields: '监视字段',
    }
)

const detailWideLabelClass = computed(() => isEnglish.value ? 'w-[7rem]' : 'w-[4.5rem]')
const detailCreatorLabelClass = computed(() => isEnglish.value ? 'w-[6rem]' : 'w-[3.75rem]')
const detailTimeLabelClass = computed(() => isEnglish.value ? 'w-[6.25rem]' : 'w-[4.25rem]')

const handleClose = () => {
  emit('update:visible', false)
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="text.detailTitle"
    :width="780"
    :modal-class="isDarkTheme ? 'api-config-detail-modal api-config-detail-modal--dark' : 'api-config-detail-modal api-config-detail-modal--light'"
    @ok="handleClose"
    @cancel="handleClose"
    @close="handleClose"
  >
    <div class="detail-card p-4 rounded-lg mb-6">
      <div class="grid grid-cols-2 gap-8">
        <div>
          <div class="detail-title text-base font-medium mb-4">{{ text.basicInfo }}</div>
          <div class="space-y-4">
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailWideLabelClass">{{ text.interfaceName }}</span>
              <span class="detail-value">{{ config?.interface_info?.name }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailWideLabelClass">{{ text.testCaseName }}</span>
              <span class="detail-value">{{ config?.testcase_info?.name }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailWideLabelClass">{{ text.stepName }}</span>
              <span class="detail-value">{{ config?.step_info?.name }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailCreatorLabelClass">{{ text.createdBy }}</span>
              <span class="detail-value">{{ config?.created_by_info?.username || '-' }}</span>
            </div>
          </div>
        </div>

        <div>
          <div class="detail-title text-base font-medium mb-4">{{ text.statusInfo }}</div>
          <div class="space-y-4">
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailWideLabelClass">{{ text.syncMode }}</span>
              <a-tag color="arcoblue" size="medium" class="rounded-md">
                {{ config?.sync_mode === 'auto' ? text.autoSync : text.manualSync }}
              </a-tag>
            </div>
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailWideLabelClass">{{ text.syncStatus }}</span>
              <a-tag :color="config?.sync_enabled ? 'green' : 'red'" size="medium" class="rounded-md">
                {{ config?.sync_enabled ? text.enabled : text.disabled }}
              </a-tag>
            </div>
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailTimeLabelClass">{{ text.createdAt }}</span>
              <span class="detail-value">{{ config?.created_at ? new Date(config.created_at).toLocaleString() : '-' }}</span>
            </div>
            <div class="flex items-center gap-3">
              <span class="detail-label" :class="detailTimeLabelClass">{{ text.updatedAt }}</span>
              <span class="detail-value">{{ config?.updated_at ? new Date(config.updated_at).toLocaleString() : '-' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template v-if="config">
      <div class="space-y-6">
        <div class="detail-card p-4 rounded-lg">
          <div class="detail-title text-base font-medium mb-4">{{ text.syncFields }}</div>
          <div class="flex flex-wrap gap-2">
            <template v-for="field in config.sync_fields" :key="field">
              <a-tag color="arcoblue" size="medium" class="rounded-md">
                {{ fieldOptions.find(opt => opt.value === field)?.label || field }}
              </a-tag>
            </template>
          </div>
        </div>

        <div v-if="config.sync_mode === 'auto'" class="detail-card p-4 rounded-lg">
          <div class="detail-title text-base font-medium mb-4">{{ text.watchFields }}</div>
          <div class="flex flex-wrap gap-2">
            <template v-for="field in config.sync_trigger?.fields_to_watch" :key="field">
              <a-tag color="arcoblue" size="medium" class="rounded-md">
                {{ fieldOptions.find(opt => opt.value === field)?.label || field }}
              </a-tag>
            </template>
          </div>
        </div>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
@reference "tailwindcss";
:deep(.api-config-detail-modal--light) {
  --api-detail-bg: rgba(255, 255, 255, 0.98);
  --api-detail-border: rgba(148, 163, 184, 0.18);
  --api-detail-card-bg: rgba(248, 250, 252, 0.96);
  --api-detail-text: var(--color-text-1);
  --api-detail-subtle: var(--color-text-3);
}

:deep(.api-config-detail-modal--dark) {
  --api-detail-bg: rgba(31, 41, 55, 1);
  --api-detail-border: rgba(55, 65, 81, 1);
  --api-detail-card-bg: rgba(17, 24, 39, 0.5);
  --api-detail-text: rgb(229, 231, 235);
  --api-detail-subtle: rgb(156, 163, 175);
}

:deep(.api-config-detail-modal .arco-modal) {
  background: var(--api-detail-bg);
  border: 1px solid var(--api-detail-border);
}

:deep(.api-config-detail-modal .arco-modal-header) {
  background: var(--api-detail-bg);
  border-color: var(--api-detail-border);
  padding-bottom: 1rem;
}

:deep(.api-config-detail-modal .arco-modal-title) {
  color: var(--api-detail-text);
  font-size: 1.125rem;
  font-weight: 500;
}

:deep(.api-config-detail-modal .arco-modal-footer) {
  background: var(--api-detail-bg);
  border-color: var(--api-detail-border);
  margin-top: 1.5rem;
}

:deep(.api-config-detail-modal .arco-modal-body) {
  padding: 1.5rem;
}

.detail-card {
  background: var(--api-detail-card-bg);
}

.detail-title,
.detail-value {
  color: var(--api-detail-text);
}

.detail-label {
  color: var(--api-detail-subtle);
}
</style> 