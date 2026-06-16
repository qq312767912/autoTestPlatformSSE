<script setup lang="ts">
import { computed } from 'vue'
import type { NewEnvironmentVariableData, VariableType } from '../../services/environmentService'
import { VARIABLE_TYPES } from '../../services/environmentService'
import { useAppI18n } from '@/composables/useAppI18n'
import {
  IconPlus,
  IconCode,
  IconEdit,
  IconInfoCircle,
  IconLock,
} from '@arco-design/web-vue/es/icon'

interface Props {
  modelValue: NewEnvironmentVariableData
}

interface Emits {
  (e: 'update:modelValue', value: NewEnvironmentVariableData): void
  (e: 'submit'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()
const { isEnglish } = useAppI18n()

const text = computed(() => isEnglish.value
  ? {
      addNewVariable: 'Add New Variable',
      variableName: 'Variable Name',
      variableValue: 'Variable Value',
      selectVariableType: 'Select Variable Type',
      variableDescriptionOptional: 'Variable Description (optional)',
      sensitiveVariable: 'Sensitive Variable',
      saveVariable: 'Save Variable',
    }
  : {
      addNewVariable: '添加新变量',
      variableName: '变量名',
      variableValue: '变量值',
      selectVariableType: '选择变量类型',
      variableDescriptionOptional: '变量描述（选填）',
      sensitiveVariable: '敏感变量',
      saveVariable: '保存变量',
    }
)

const updateField = (field: keyof NewEnvironmentVariableData, value: string | number | boolean) => {
  emit('update:modelValue', {
    ...props.modelValue,
    [field]: value
  })
}

const handleSubmit = () => {
  if (!props.modelValue.name || !props.modelValue.value) {
    return
  }
  emit('submit')
}
</script>

<template>
  <div class="variable-form-shell p-5 rounded-lg space-y-4 transition-colors">
    <div class="flex items-center gap-2">
      <div class="w-6 h-6 rounded bg-purple-500/10 flex items-center justify-center">
        <icon-plus class="text-purple-400 text-sm" />
      </div>
      <span class="text-sm font-medium variable-form-title">{{ text.addNewVariable }}</span>
    </div>
    
    <div class="space-y-3">
      <a-input
        :model-value="modelValue.name"
        @update:model-value="val => updateField('name', val)"
        :placeholder="text.variableName"
        allow-clear
        class="variable-field-control"
      >
        <template #prefix>
          <icon-code class="text-purple-400" />
        </template>
      </a-input>
      <a-input
        :model-value="modelValue.value"
        @update:model-value="val => updateField('value', val)"
        :placeholder="text.variableValue"
        allow-clear
        class="variable-field-control"
      >
        <template #prefix>
          <icon-edit class="text-purple-400" />
        </template>
      </a-input>

      <!-- 变量类型选择 -->
      <a-select
        :model-value="modelValue.type"
        @update:model-value="val => updateField('type', val)"
        :placeholder="text.selectVariableType"
        class="variable-field-control"
      >
        <a-option
          v-for="item in VARIABLE_TYPES"
          :key="item.value"
          :value="item.value"
        >
          {{ item.label }}
        </a-option>
      </a-select>

      <a-input
        :model-value="modelValue.description"
        @update:model-value="val => updateField('description', val)"
        :placeholder="text.variableDescriptionOptional"
        allow-clear
        class="variable-field-control"
      >
        <template #prefix>
          <icon-info-circle class="text-purple-400" />
        </template>
      </a-input>

      <!-- 敏感变量开关 -->
      <div class="variable-toggle-card flex items-center gap-3 p-3 rounded-lg">
        <a-switch
          :model-value="modelValue.is_sensitive"
          @update:model-value="val => updateField('is_sensitive', val)"
          class="!scale-110"
        />
        <div class="flex items-center gap-2">
          <icon-lock class="text-purple-400" />
          <span class="variable-form-title">{{ text.sensitiveVariable }}</span>
        </div>
      </div>
    </div>

    <div class="flex justify-end">
      <a-button
        type="outline"
        status="success"
        @click="handleSubmit"
      >
        <template #icon><icon-plus /></template>
        {{ text.saveVariable }}
      </a-button>
    </div>
  </div>
</template> 

<style lang="postcss" scoped>
.variable-form-shell {
  border: 1px dashed var(--env-header-border);
  background: color-mix(in srgb, var(--env-shell-bg) 80%, var(--theme-page-bg) 20%);
}

.variable-form-shell:hover {
  border-color: var(--env-block-border);
}

.variable-form-title {
  color: var(--env-text);
}

.variable-toggle-card {
  background: color-mix(in srgb, var(--env-block-bg) 86%, var(--theme-page-bg) 14%);
  border: 1px solid var(--env-block-border);
}

:deep(.variable-field-control .arco-input-wrapper),
:deep(.variable-field-control .arco-select-view) {
  background: var(--env-input-bg) !important;
  border-color: var(--env-input-border) !important;

  &:hover,
  &:focus-within {
    border-color: rgba(var(--theme-accent-rgb), 0.42) !important;
    background: var(--env-input-hover-bg) !important;
  }
}

:deep(.variable-field-control .arco-input),
:deep(.variable-field-control .arco-select-view-value) {
  color: var(--env-text) !important;
}

:deep(.variable-field-control .arco-input::placeholder),
:deep(.variable-field-control .arco-select-view-placeholder),
:deep(.variable-field-control .arco-input-prefix),
:deep(.variable-field-control .arco-select-view-suffix) {
  color: var(--env-text-subtle) !important;
}
</style>