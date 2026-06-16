<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconCode, IconPlus, IconDelete } from '@arco-design/web-vue/es/icon'
import type { Variable } from '../../types/testcase'

interface Props {
  modelValue: Variable[]
  readonly?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits(['update:modelValue'])

const variables = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const variableModalVisible = ref(false)
const isEditMode = ref(false)
const editingVariable = ref<Variable | null>(null)

const newVariable = reactive<Variable>({
  key: '',
  value: '',
  type: 'static',
  functionId: undefined
})

const handleOpenVariableModal = () => {
  variableModalVisible.value = true
  isEditMode.value = false
  editingVariable.value = null
  resetVariableForm()
}

const handleVariableSelect = (variable: Variable) => {
  variableModalVisible.value = false
}

const handleCreateVariable = () => {
  isEditMode.value = false
  editingVariable.value = null
  resetVariableForm()
}

const handleEditVariable = (variable: Variable) => {
  isEditMode.value = true
  editingVariable.value = variable
  Object.assign(newVariable, variable)
}

const handleDeleteVariable = (variable: Variable) => {
  const index = variables.value.findIndex(v => v.key === variable.key)
  if (index !== -1) {
    const newValue = [...variables.value]
    newValue.splice(index, 1)
    variables.value = newValue
    Message.success('删除成功')
  }
}

const resetVariableForm = () => {
  newVariable.key = ''
  newVariable.value = ''
  newVariable.type = 'static'
  newVariable.functionId = undefined
}

const handleVariableSubmit = () => {
  if (!newVariable.key.trim()) {
    Message.error('请输入变量名')
    return false
  }

  if (newVariable.type === 'static' && !newVariable.value.trim()) {
    Message.error('请输入变量值')
    return false
  }

  if (newVariable.type === 'function' && !newVariable.functionId) {
    Message.error('请选择关联函数')
    return false
  }

  if (isEditMode.value && editingVariable.value) {
    // 编辑模式
    const index = variables.value.findIndex(v => v.key === editingVariable.value?.key)
    if (index !== -1) {
      const newValue = [...variables.value]
      newValue[index] = { ...newVariable }
      variables.value = newValue
      Message.success('更新成功')
    }
  } else {
    // 创建模式
    if (variables.value.some(v => v.key === newVariable.key)) {
      Message.error('变量名已存在')
      return false
    }
    variables.value = [...variables.value, { ...newVariable }]
    Message.success('创建成功')
  }

  resetVariableForm()
  return true
}

const handleCopyVariable = async (key: string) => {
  try {
    const text = '${' + key + '}'
    await navigator.clipboard.writeText(text)
    Message.success('已复制到剪贴板')
  } catch (err) {
    console.error('Copy failed:', err)
    Message.error('复制失败')
  }
}
</script>

<template>
  <div class="flex items-center gap-2">
    <a-button
      class="!flex !items-center !gap-1"
      type="outline"
      size="small"
      @click="handleOpenVariableModal"
      :disabled="readonly"
      status="normal"
    >
      <template #icon>
        <icon-code class="!text-[#165DFF]" />
      </template>
      <span class="!text-[#165DFF]">变量</span>
      <span class="inline-flex items-center justify-center bg-[#165DFF] text-white rounded text-xs min-w-[16px] h-4 px-1">
        {{ variables.length }}
      </span>
    </a-button>

    <!-- 变量弹窗 -->
    <a-modal
      v-model:visible="variableModalVisible"
      :width="800"
      title="用例配置"
      :mask-style="{ backgroundColor: 'rgba(0, 0, 0, 0.65)' }"
    >
      <div class="space-y-4">
        <!-- 变量表格 -->
        <a-table :data="variables" :pagination="false" :bordered="false">
          <template #columns>
            <a-table-column title="变量名" data-index="key" :width="250">
              <template #cell="{ record }">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-[#165DFF]">$</span>
                  <a-input
                    v-model="record.key"
                    placeholder="请输入变量名"
                    size="mini"
                    class="!w-32 !font-mono"
                    allow-clear
                  />
                  <a-button
                    type="text"
                    size="mini"
                    class="!p-1"
                    @click="() => handleCopyVariable(record.key)"
                  >
                    复制
                  </a-button>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="类型" data-index="type" :width="150">
              <template #cell="{ record }">
                <a-select
                  v-model="record.type"
                  size="mini"
                  class="!w-full"
                >
                  <a-option value="static">静态值</a-option>
                  <a-option value="function">关联函数</a-option>
                </a-select>
              </template>
            </a-table-column>
            <a-table-column title="值/关联函数" data-index="value">
              <template #cell="{ record }">
                <div v-if="record.type === 'static'" class="flex items-center gap-2">
                  <a-textarea
                    v-model="record.value"
                    placeholder="请输入变量值，支持任意类型"
                    size="mini"
                    :auto-size="{ minRows: 1, maxRows: 3 }"
                    class="!w-full"
                  />
                </div>
                <div v-else class="flex items-center gap-2">
                  <a-select
                    v-model="record.functionId"
                    placeholder="请选择函数"
                    size="mini"
                    class="!w-full"
                  >
                    <a-option :value="1">random_priority</a-option>
                    <a-option :value="2">get_current_time</a-option>
                  </a-select>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="操作" align="right" :width="80">
              <template #cell="{ record }">
                <div class="flex items-center justify-end">
                  <a-button type="text" size="mini" status="danger" @click="handleDeleteVariable(record)">
                    <template #icon>
                      <icon-delete />
                    </template>
                  </a-button>
                </div>
              </template>
            </a-table-column>
          </template>
        </a-table>

        <!-- 添加变量按钮 -->
        <div class="flex justify-center">
          <a-button type="dashed" size="small" @click="() => variables = [...variables, { key: '新变量', value: '', type: 'static' }]">
            <template #icon>
              <icon-plus />
            </template>
            添加变量
          </a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template> 