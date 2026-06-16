<template>
  <div class="case-step-list">
    <div class="step-header">
      <span class="step-title">{{ pageText.stepListTitle }}</span>
      <a-button type="primary" size="small" @click="showAddModal">
        <template #icon><icon-plus /></template>
        {{ pageText.addStep }}
      </a-button>
    </div>

    <a-spin :loading="loading">
      <div v-if="stepData.length === 0" class="empty-tips">
        <a-empty :description="pageText.emptyState" />
      </div>
      <draggable
        v-else
        v-model="stepData"
        item-key="id"
        handle=".drag-handle"
        @end="onDragEnd"
      >
        <template #item="{ element, index }">
          <div class="step-card">
            <div class="step-left">
              <div class="drag-handle">
                <icon-drag-dot-vertical />
              </div>
              <div class="step-index">{{ index + 1 }}</div>
            </div>
            <div class="step-content">
              <span class="info-item">
                <span class="info-label">{{ pageText.stepLabel }}</span>
                <span class="step-name">{{ element.page_step_name }}</span>
              </span>
              <a-tag v-if="element.switch_step_open_url" color="orange" size="small">{{ pageText.switchUrl }}</a-tag>
              <a-tag v-if="element.error_retry > 0" color="blue" size="small">{{ pageText.retryLabel(element.error_retry) }}</a-tag>
              <a-tag :color="statusColors[element.status as ExecutionStatus]" size="small">
                {{ formatStatusLabel(element.status as ExecutionStatus) }}
              </a-tag>
            </div>
            <div class="step-actions">
              <a-button type="text" size="mini" @click="editStep(element)">
                <template #icon><icon-edit /></template>
              </a-button>
              <a-popconfirm :content="pageText.deleteStepConfirm" @ok="deleteStep(element)">
                <a-button type="text" status="danger" size="mini">
                  <template #icon><icon-delete /></template>
                </a-button>
              </a-popconfirm>
            </div>
          </div>
        </template>
      </draggable>
    </a-spin>

    <!-- 添加/编辑步骤弹窗 -->
    <a-modal
      v-model:visible="modalVisible"
      :title="caseStepModalTitle"
      :ok-loading="submitting"
      @before-ok="handleSubmit"
      @cancel="handleCancel"
    >
      <a-form ref="formRef" :model="formData" :rules="rules" layout="vertical">
        <a-form-item field="page_step" :label="pageText.selectPageStep" required>
          <a-select
            v-model="formData.page_step"
            :placeholder="pageText.selectPageStepPlaceholder"
            allow-search
          >
            <a-option
              v-for="step in pageStepOptions"
              :key="step.id"
              :value="step.id"
            >
              {{ step.page_name }} - {{ step.name }}
            </a-option>
          </a-select>
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item field="switch_step_open_url" :label="pageText.switchPageUrl">
              <a-switch v-model="formData.switch_step_open_url" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item field="error_retry" :label="pageText.retryCount">
              <a-input-number v-model="formData.error_retry" :min="0" :max="5" />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconPlus, IconEdit, IconDelete, IconDragDotVertical } from '@arco-design/web-vue/es/icon'
import draggable from 'vuedraggable'
import { caseStepsApi, pageStepsApi } from '../api'
import type { UiCaseStepsDetailed, UiPageSteps, UiTestCase, ExecutionStatus } from '../types'
import { STATUS_LABELS, extractListData } from '../types'
import { useProjectStore } from '@/store/projectStore'
import { useAppI18n } from '@/composables/useAppI18n'

const props = defineProps<{ testCase: UiTestCase }>()

const projectStore = useProjectStore()
const projectId = computed(() => projectStore.currentProject?.id)
const { isEnglish, tl } = useAppI18n()

const pageText = computed(() => (
  isEnglish.value
    ? {
        stepListTitle: 'Step list (drag to reorder)',
        addStep: 'Add step',
        emptyState: 'No steps yet. Please add page steps',
        stepLabel: 'Step:',
        switchUrl: 'Switch URL',
        retryLabel: (count: number) => `Retry ${count} times`,
        deleteStepConfirm: 'Delete this step?',
        editStep: 'Edit step',
        selectPageStep: 'Select page step',
        selectPageStepPlaceholder: 'Select page step',
        switchPageUrl: 'Switch page URL',
        retryCount: 'Retry count on failure',
        selectPageStepRequired: 'Select page step',
        fetchStepListFailed: 'Failed to fetch step list',
        fetchPageStepListFailed: 'Failed to fetch page step list',
        fillRequired: 'Fill in the required fields',
        updateSuccess: 'Updated successfully',
        addSuccess: 'Added successfully',
        updateFailed: 'Update failed',
        addFailed: 'Add failed',
        deleteSuccess: 'Deleted successfully',
        deleteFailed: 'Delete failed',
        sortSaved: 'Order saved',
        sortSaveFailed: 'Failed to save order',
      }
    : {
        stepListTitle: '步骤列表（拖拽可排序）',
        addStep: '添加步骤',
        emptyState: '暂无步骤，请添加页面步骤',
        stepLabel: '步骤:',
        switchUrl: '切换URL',
        retryLabel: (count: number) => `重试${count}次`,
        deleteStepConfirm: '确定删除该步骤？',
        editStep: '编辑步骤',
        selectPageStep: '选择页面步骤',
        selectPageStepPlaceholder: '请选择页面步骤',
        switchPageUrl: '切换页面URL',
        retryCount: '失败重试次数',
        selectPageStepRequired: '请选择页面步骤',
        fetchStepListFailed: '获取步骤列表失败',
        fetchPageStepListFailed: '获取页面步骤列表失败',
        fillRequired: '请填写必填项',
        updateSuccess: '更新成功',
        addSuccess: '添加成功',
        updateFailed: '更新失败',
        addFailed: '添加失败',
        deleteSuccess: '删除成功',
        deleteFailed: '删除失败',
        sortSaved: '排序已保存',
        sortSaveFailed: '保存排序失败',
      }
))

const loading = ref(false)
const submitting = ref(false)
const stepData = ref<UiCaseStepsDetailed[]>([])
const pageStepOptions = ref<UiPageSteps[]>([])
const modalVisible = ref(false)
const isEdit = ref(false)
const currentStep = ref<UiCaseStepsDetailed | null>(null)
const formRef = ref()

const formData = reactive({
  page_step: undefined as number | undefined,
  switch_step_open_url: false,
  error_retry: 0,
})

const rules = computed(() => ({
  page_step: [{ required: true, message: pageText.value.selectPageStepRequired }],
}))

const statusColors: Record<ExecutionStatus, string> = { 0: 'gray', 1: 'blue', 2: 'green', 3: 'red' }
const caseStepModalTitle = computed(() => (isEdit.value ? pageText.value.editStep : pageText.value.addStep))
const formatStatusLabel = (status: ExecutionStatus) => tl(STATUS_LABELS[status])

const fetchSteps = async () => {
  loading.value = true
  try {
    const res = await caseStepsApi.list({ test_case: props.testCase.id })
    stepData.value = extractListData<UiCaseStepsDetailed>(res)
  } catch {
    Message.error(pageText.value.fetchStepListFailed)
  } finally {
    loading.value = false
  }
}

const fetchPageSteps = async () => {
  if (!projectId.value) return
  try {
    const res = await pageStepsApi.list({ project: projectId.value })
    pageStepOptions.value = extractListData<UiPageSteps>(res)
  } catch {
    Message.error(pageText.value.fetchPageStepListFailed)
  }
}

const resetForm = () => {
  Object.assign(formData, { page_step: undefined, switch_step_open_url: false, error_retry: 0 })
  formRef.value?.clearValidate()
}

const showAddModal = () => {
  isEdit.value = false
  resetForm()
  modalVisible.value = true
}

const editStep = (step: UiCaseStepsDetailed) => {
  isEdit.value = true
  currentStep.value = step
  Object.assign(formData, {
    page_step: step.page_step,
    switch_step_open_url: step.switch_step_open_url,
    error_retry: step.error_retry,
  })
  modalVisible.value = true
}

const handleSubmit = async (done: (closed: boolean) => void) => {
  try {
    await formRef.value?.validate()
  } catch {
    Message.warning(pageText.value.fillRequired)
    done(false)
    return
  }
  submitting.value = true
  try {
    if (isEdit.value && currentStep.value?.id) {
      await caseStepsApi.update(currentStep.value.id, formData)
      Message.success(pageText.value.updateSuccess)
    } else {
      await caseStepsApi.create({
        test_case: props.testCase.id,
        page_step: formData.page_step,
        case_sort: stepData.value.length,
        switch_step_open_url: formData.switch_step_open_url,
        error_retry: formData.error_retry,
      })
      Message.success(pageText.value.addSuccess)
    }
    done(true)
    fetchSteps()
  } catch (error: unknown) {
    const err = error as { errors?: Record<string, string[]>; error?: string }
    const errors = err?.errors
    if (errors && typeof errors === 'object' && !('error' in errors) && !('message' in errors)) {
      const messages = Object.entries(errors)
        .map(([field, msgs]) => `${field}: ${Array.isArray(msgs) ? msgs.join(', ') : msgs}`)
        .join('\n')
      Message.error({ content: messages, duration: 5000 })
    } else {
      Message.error(err?.error || (isEdit.value ? pageText.value.updateFailed : pageText.value.addFailed))
    }
    done(false)
  } finally {
    submitting.value = false
  }
}

const handleCancel = () => {
  modalVisible.value = false
}

const deleteStep = async (step: UiCaseStepsDetailed) => {
  if (!step.id) return
  try {
    await caseStepsApi.delete(step.id)
    Message.success(pageText.value.deleteSuccess)
    fetchSteps()
  } catch {
    Message.error(pageText.value.deleteFailed)
  }
}

const onDragEnd = async () => {
  try {
    const steps = stepData.value.map((s, idx) => ({
      page_step: s.page_step,
      case_sort: idx,
      switch_step_open_url: s.switch_step_open_url,
      error_retry: s.error_retry,
    }))
    await caseStepsApi.batchUpdate(props.testCase.id, steps)
    Message.success(pageText.value.sortSaved)
  } catch {
    Message.error(pageText.value.sortSaveFailed)
    fetchSteps()
  }
}

watch(() => props.testCase, () => {
  fetchSteps()
  fetchPageSteps()
}, { immediate: true })
</script>

<style scoped>
.case-step-list {
  padding: 8px 0;
  overflow-x: hidden;
}
.case-step-list :deep(.arco-spin) {
  width: 100%;
}
.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.step-title {
  font-weight: 500;
}
.empty-tips {
  padding: 40px 0;
}
.step-card {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--color-bg-2);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  box-sizing: border-box;
  min-height: 48px;
  font-size: 13px;
  gap: 12px;
}
.step-left {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}
.drag-handle {
  cursor: move;
  color: var(--color-text-3);
  width: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 8px;
}
.step-index {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(var(--primary-6));
  color: #fff;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 500;
}
.step-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.info-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.info-label {
  color: var(--color-text-3);
  font-size: 12px;
  flex-shrink: 0;
}
.step-name {
  font-weight: 500;
  color: rgb(var(--primary-6));
}
.step-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
</style>
