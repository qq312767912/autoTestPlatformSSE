<script setup lang="ts">
interface Validator {
  check: string
  expect: any
  comparator: string
}

interface Props {
  modelValue: Validator[]
}

const props = defineProps<Props>()
const emit = defineEmits(['update:modelValue'])

const dataTypes = [
  { label: '整数', value: 'int' },
  { label: '浮点数', value: 'float' },
  { label: '字符串', value: 'str' },
  { label: '布尔值', value: 'bool' },
  { label: '列表', value: 'list' },
  { label: '字典', value: 'dict' },
  { label: '对象', value: 'object' },
  { label: '数组', value: 'array' },
  { label: '空值', value: 'None' }
]

const handleDelete = (index: number) => {
  const newValidators = [...props.modelValue]
  newValidators.splice(index, 1)
  emit('update:modelValue', newValidators)
}

const handleAdd = () => {
  emit('update:modelValue', [
    ...props.modelValue,
    { check: 'status_code', expect: 200, comparator: 'eq' }
  ])
}

const updateValidator = (index: number, key: keyof Validator, value: any) => {
  const newValidators = [...props.modelValue]
  newValidators[index] = { ...newValidators[index], [key]: value }
  emit('update:modelValue', newValidators)
}
</script>

<template>
  <div class="space-y-4">
    <div v-for="(validator, index) in modelValue" :key="index" class="flex gap-2">
      <a-input
        :model-value="validator.check"
        @update:model-value="val => updateValidator(index, 'check', val)"
        placeholder="检查项"
        class="flex-1"
      >
        <template #prefix>$.</template>
      </a-input>
      <a-select
        :model-value="validator.comparator"
        @update:model-value="val => updateValidator(index, 'comparator', val)"
        placeholder="断言方式"
        class="w-32"
      >
        <a-optgroup label="基础比较">
          <a-option value="eq">等于</a-option>
          <a-option value="ne">不等于</a-option>
          <a-option value="gt">大于</a-option>
          <a-option value="ge">大于等于</a-option>
          <a-option value="lt">小于</a-option>
          <a-option value="le">小于等于</a-option>
        </a-optgroup>
        <a-optgroup label="字符串">
          <a-option value="contains">包含</a-option>
          <a-option value="contained_by">被包含</a-option>
          <a-option value="startswith">以...开头</a-option>
          <a-option value="endswith">以...结尾</a-option>
          <a-option value="regex_match">正则匹配</a-option>
          <a-option value="str_eq">字符串等于</a-option>
        </a-optgroup>
        <a-optgroup label="长度">
          <a-option value="length_equal">长度等于</a-option>
          <a-option value="length_greater_than">长度大于</a-option>
          <a-option value="length_less_than">长度小于</a-option>
          <a-option value="length_greater_or_equals">长度大于等于</a-option>
          <a-option value="length_less_or_equals">长度小于等于</a-option>
        </a-optgroup>
        <a-optgroup label="其他">
          <a-option value="type_match">类型匹配</a-option>
        </a-optgroup>
      </a-select>
      <a-select
        v-if="validator.comparator === 'type_match'"
        :model-value="validator.expect"
        @update:model-value="val => updateValidator(index, 'expect', val)"
        placeholder="选择类型"
        class="flex-1"
      >
        <a-option v-for="dtype in dataTypes" :key="dtype.value" :value="dtype.value">{{ dtype.label }}</a-option>
      </a-select>
      <a-input
        v-else
        :model-value="validator.expect"
        @update:model-value="val => updateValidator(index, 'expect', val)"
        placeholder="期望值"
        class="flex-1"
      />
      <a-button status="danger" @click="handleDelete(index)">删除</a-button>
    </div>
    <div class="flex justify-between items-center">
      <a-button @click="handleAdd">添加断言</a-button>
      <span class="hint-text">提示: 使用 JSONPath 语法检查响应数据</span>
    </div>
  </div>
</template>

<style scoped>
.arco-input-wrapper,
.arco-select-view {
  background-color: rgba(30, 41, 59, 0.5) !important;
}

.arco-input-wrapper:hover,
.arco-input-wrapper:focus-within,
.arco-select-view:hover,
.arco-select-view:focus-within {
  border-color: #60a5fa !important;
}

.arco-input,
.arco-select-view-value {
  color: #e2e8f0 !important;
}
</style>
