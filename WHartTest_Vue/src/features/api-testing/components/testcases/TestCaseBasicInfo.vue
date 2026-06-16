<script setup lang="ts">
import { computed } from 'vue'
import { IconEdit, IconFile } from '@arco-design/web-vue/es/icon'

interface BasicInfo {
  name: string
  description: string
}

interface Props {
  modelValue: BasicInfo
  readonly?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits(['update:modelValue'])

const name = computed({
  get: () => props.modelValue.name,
  set: (value) => updateValue('name', value)
})

const description = computed({
  get: () => props.modelValue.description,
  set: (value) => updateValue('description', value)
})

const updateValue = (key: keyof BasicInfo, value: any) => {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}
</script>

<template>
  <div class="flex items-center gap-4">
    <!-- 用例名称 -->
    <a-input
      v-model="name"
      placeholder="请输入用例名称"
      class="w-64"
      :readonly="readonly"
    >
      <template #prefix>
        <icon-edit />
      </template>
    </a-input>

    <!-- 描述 -->
    <a-input
      v-model="description"
      placeholder="请输入用例描述"
      class="w-56"
      :readonly="readonly"
    >
      <template #prefix>
        <icon-file />
      </template>
    </a-input>
  </div>
</template>
