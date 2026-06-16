<script setup lang="ts">
import { ref, watch, nextTick, computed } from 'vue'
import { useProjectStore } from '@/store/projectStore'
import { useThemeStore } from '@/store/themeStore'
import { Message } from '@arco-design/web-vue'
import { moduleService } from '../../services/moduleService'
import { interfaceService } from '../../services/interfaceService'
import type { ApiInterface } from '../../types/interface'
import type { ApiModule } from '../../types/module'
import ApiSelectDialogHeader from './ApiSelectDialogHeader.vue'
import ModuleList from './ModuleList.vue'
import InterfaceList from './InterfaceList.vue'
import InterfacePagination from './InterfacePagination.vue'

const props = defineProps<{
  visible: boolean
  testCaseId?: number
  testCase?: {
    name: string
    priority: string
    project: number
    description?: string
    group?: number
    tags?: number[]
    config?: {
      base_url: string
      variables: string | Record<string, any>
      parameters: string | Record<string, any>
      export: string | string[]
      verify: string | boolean
    }
  }
}>()

const emit = defineEmits(['update:visible', 'select'])

const loading = ref(false)
const selectedModuleId = ref<number>()
const expandedModuleIds = ref<number[]>([])

const modules = ref<ApiModule[]>([])
const currentModule = ref<ApiModule | null>(null)
const interfaces = ref<ApiInterface[]>([])
const selectedKeys = ref<number[]>([])

const pagination = ref({
  current: 1,
  pageSize: 10,
  total: 0
})

const projectStore = useProjectStore()
const themeStore = useThemeStore()
const isDarkTheme = computed(() => themeStore.isBlack)

const loadModules = async () => {
  if (!projectStore.currentProjectId) {
    console.error('No project selected')
    return
  }

  loading.value = true
  modules.value = []

  try {
    const res = await moduleService.tree(projectStore.currentProjectId)
    if (res.success && res.data) {
      modules.value = Array.isArray(res.data) ? res.data : (res.data as any).results || []
    }
  } catch (error) {
    console.error('Failed to load modules:', error)
    Message.error('加载模块列表失败')
  } finally {
    loading.value = false
  }
}

const loadModuleInterfaces = async (moduleId: number, page = 1) => {
  if (!projectStore.currentProjectId) {
    Message.error('未选择项目')
    return
  }

  loading.value = true
  if (page === 1) {
    interfaces.value = []
    selectedKeys.value = []
  }

  try {
    const res = await interfaceService.list(projectStore.currentProjectId, {
      module_id: moduleId,
      page: page,
      page_size: pagination.value.pageSize
    })
    if (res.success && res.data) {
      interfaces.value = Array.isArray(res.data) ? res.data : (res.data as any).results || []
      pagination.value.total = (res.data as any).count || interfaces.value.length
      pagination.value.current = page
    }
  } catch (error) {
    console.error('Failed to load interfaces:', error)
    Message.error('加载接口列表失败')
  } finally {
    loading.value = false
  }
}

const handlePageChange = (page: number) => {
  if (currentModule.value) {
    loadModuleInterfaces(currentModule.value.id, page)
  }
}

const handlePageSizeChange = (pageSize: number) => {
  pagination.value.pageSize = pageSize
  if (currentModule.value) {
    loadModuleInterfaces(currentModule.value.id, 1)
  }
}

const handleToggleExpand = (moduleId: number) => {
  const index = expandedModuleIds.value.indexOf(moduleId)
  if (index === -1) {
    expandedModuleIds.value.push(moduleId)
  } else {
    expandedModuleIds.value.splice(index, 1)
  }
}

const handleModuleSelect = async (mod: ApiModule) => {
  try {
    loading.value = true
    selectedModuleId.value = mod.id
    currentModule.value = mod
    selectedKeys.value = []
    await loadModuleInterfaces(mod.id)
  } catch (error) {
    console.error('Failed to load module interfaces:', error)
    selectedModuleId.value = undefined
    currentModule.value = null
  } finally {
    loading.value = false
  }
}

const handleConfirm = () => {
  if (selectedKeys.value.length === 0) {
    Message.warning('请至少选择一个接口')
    return
  }

  const selectedInterfaces = interfaces.value.filter(api => selectedKeys.value.includes(api.id!))
  emit('select', selectedInterfaces)
  selectedKeys.value = []
  emit('update:visible', false)
}

const handleSelectionChange = (keys: number[]) => {
  selectedKeys.value = keys.map(k => Number(k))
}

const handleRowClick = (record: ApiInterface) => {
  const id = record.id!
  const index = selectedKeys.value.indexOf(id)
  if (index === -1) {
    selectedKeys.value = [...selectedKeys.value, id]
  } else {
    selectedKeys.value = selectedKeys.value.filter(key => key !== id)
  }
}

const handleClose = () => {
  emit('update:visible', false)
}

const resetDialogState = () => {
  selectedKeys.value = []
  interfaces.value = []
  currentModule.value = null
  selectedModuleId.value = undefined
  expandedModuleIds.value = []
  pagination.value = {
    current: 1,
    pageSize: 10,
    total: 0
  }
}

watch(() => props.visible, async (newVal, oldVal) => {
  if (newVal && !oldVal) {
    resetDialogState()
    await nextTick()
    loadModules()
  } else if (!newVal && oldVal) {
    resetDialogState()
  }
}, { immediate: false })

const handleVisibleChange = (value: boolean) => {
  if (!value) {
    emit('update:visible', false)
  }
}
</script>

<template>
  <a-modal
    :visible="visible"
    :width="1000"
    :mask-closable="false"
    :footer="false"
    unmount-on-close
    @update:visible="handleVisibleChange"
    @cancel="handleClose"
  >
    <div class="api-select-dialog" :class="isDarkTheme ? 'api-select-dialog--dark' : 'api-select-dialog--light'">
      <div class="flex flex-col gap-4">
        <ApiSelectDialogHeader @close="handleClose" />

        <div class="flex gap-4">
          <ModuleList
            :modules="modules"
            :expanded-ids="expandedModuleIds"
            :selected-id="selectedModuleId"
            :loading="loading"
            @select="handleModuleSelect"
            @toggle-expand="handleToggleExpand"
          />

          <div class="flex-1 flex flex-col gap-4">
            <InterfaceList
              :interfaces="interfaces"
              :selected-keys="selectedKeys"
              :loading="loading"
              :current-module-name="currentModule?.name"
              @selection-change="handleSelectionChange"
              @row-click="handleRowClick"
              @confirm="handleConfirm"
            />

            <InterfacePagination
              :current="pagination.current"
              :page-size="pagination.pageSize"
              :total="pagination.total"
              @change="handlePageChange"
              @page-size-change="handlePageSizeChange"
            />
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
@reference "tailwindcss";
.api-select-dialog {
  @apply bg-transparent p-6;
  color: var(--asd-text-muted);
  --asd-panel-bg: rgba(255, 255, 255, 0.88);
  --asd-panel-border: rgba(148, 163, 184, 0.16);
  --asd-panel-hover: rgba(148, 163, 184, 0.08);
  --asd-control-bg: #ffffff;
  --asd-control-border: rgba(148, 163, 184, 0.18);
  --asd-control-hover: #f8fafc;
  --asd-text: var(--theme-text);
  --asd-text-muted: var(--theme-text-secondary);
  --asd-text-subtle: var(--theme-text-tertiary);
}

.api-select-dialog--dark {
  --asd-panel-bg: rgba(31, 41, 55, 0.88);
  --asd-panel-border: rgba(75, 85, 99, 0.4);
  --asd-panel-hover: rgba(51, 65, 85, 0.5);
  --asd-control-bg: rgba(15, 23, 42, 0.7);
  --asd-control-border: rgba(75, 85, 99, 0.45);
  --asd-control-hover: rgba(31, 41, 55, 0.92);
  --asd-text: rgb(241, 245, 249);
  --asd-text-muted: rgb(203, 213, 225);
  --asd-text-subtle: rgb(148, 163, 184);
}

:global(.arco-modal) {
  background: #ffffff !important;
  border-radius: 12px !important;
  border: 1px solid rgba(148, 163, 184, 0.16) !important;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.16) !important;
}

:global(.arco-modal-body) {
  background: transparent !important;
}

:global(body.api-testing-theme .arco-modal) {
  background: rgb(17, 24, 39) !important;
  border-color: rgba(75, 85, 99, 0.4) !important;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.08), 0 0 40px rgba(0, 0, 0, 0.55) !important;
}
</style>
