<script setup lang="ts">
import { ref, watch, onMounted, inject } from 'vue'
import { IconDelete, IconPlus, IconCode } from '@arco-design/web-vue/es/icon'
import ResponseJsonViewer from './ResponseJsonViewer.vue'
import { Message } from '@arco-design/web-vue'

interface Props {
  extract?: Record<string, string>
}

const props = defineProps<Props>()

// 获取响应数据
const apiResponse = inject('apiResponse', ref(null))

// 抽屉控制
const drawerVisible = ref(false)
const currentEditingIndex = ref(-1)

interface ExtractRule {
  variable: string
  expression: string
  enabled: boolean
}

const extractRules = ref<ExtractRule[]>([{ variable: '', expression: '', enabled: true }])

// 初始化提取规则
const initExtractRules = () => {
  if (props.extract && Object.keys(props.extract).length > 0) {
    extractRules.value = Object.entries(props.extract).map(([variable, expression]) => ({
      variable,
      expression,
      enabled: true
    }))
  } else {
    extractRules.value = [{ variable: '', expression: '', enabled: true }]
  }
}

// 监听 extract 变化
watch(() => props.extract, (newExtract) => {
  if (newExtract && Object.keys(newExtract).length > 0) {
    extractRules.value = Object.entries(newExtract).map(([variable, expression]) => ({
      variable,
      expression,
      enabled: true
    }))
  } else {
    extractRules.value = [{ variable: '', expression: '', enabled: true }]
  }
})

// 添加提取规则
const addRow = () => {
  extractRules.value.push({ variable: '', expression: '', enabled: true })
}

// 删除提取规则
const removeRow = (index: number) => {
  extractRules.value.splice(index, 1)
  if (extractRules.value.length === 0) {
    extractRules.value.push({ variable: '', expression: '', enabled: true })
  }
}

// 处理从响应中选择路径
const handleSelectPath = (path: string, value: string) => {
  if (currentEditingIndex.value >= 0 && currentEditingIndex.value < extractRules.value.length) {
    // 设置表达式
    extractRules.value[currentEditingIndex.value].expression = path
    // 设置变量名（如果变量名为空）
    if (!extractRules.value[currentEditingIndex.value].variable.trim()) {
      // 基于路径生成变量名
      const pathParts = path.split('.')
      let varName = pathParts[pathParts.length - 1]
      // 处理数组索引，例如 users[0] -> users_0
      varName = varName.replace(/\[(\d+)\]/g, '_$1')
      extractRules.value[currentEditingIndex.value].variable = varName
    }
    Message.success('已设置提取表达式')
  }
}

// 打开响应查看器
const openResponseViewer = (index: number) => {
  currentEditingIndex.value = index
  drawerVisible.value = true
}

// 向父组件暴露提取规则数据
defineExpose({
  getExtractRules: () => {
    const rules: Record<string, string> = {}
    extractRules.value
      .filter(rule => rule.enabled && rule.variable && rule.expression)
      .forEach(rule => {
        rules[rule.variable] = rule.expression
      })
    return rules
  }
})

// 组件加载时初始化数据
onMounted(() => {
  initExtractRules()
})
</script>

<template>
  <div class="h-full flex flex-col p-4 space-y-2">
    <div class="flex-1 min-h-0 overflow-y-auto pr-2">
      <div class="space-y-2">
        <div
          v-for="(rule, index) in extractRules"
          :key="index"
          class="flex items-center gap-2 w-full"
        >
          <a-checkbox v-model="rule.enabled" class="flex-shrink-0" />
          
          <div class="flex flex-1 gap-2">
            <div class="flex relative w-3/5">
              <a-input
                v-model="rule.expression"
                placeholder="提取表达式 (例如: body.data.results[?name=='测试用例执行'].id | [0])"
                allow-clear
                class="w-full"
              />
              <a-button
                type="text"
                class="extract-code-trigger absolute right-0 top-0 bottom-0 hover:text-blue-500"
                @click="openResponseViewer(index)"
                :disabled="!apiResponse"
              >
                <template #icon><icon-code /></template>
              </a-button>
            </div>
            
            <a-input
              v-model="rule.variable"
              placeholder="变量名"
              allow-clear
              class="w-2/5"
            />
          </div>
          
          <a-button
            type="text"
            status="danger"
            @click="removeRow(index)"
            class="flex-shrink-0"
          >
            <template #icon><icon-delete /></template>
          </a-button>
        </div>
      </div>
    </div>
    <div>
      <a-button type="outline" @click="addRow">
        <template #icon><icon-plus /></template>
        添加提取规则
      </a-button>
    </div>
    
    <!-- 响应JSON查看器 -->
    <ResponseJsonViewer
      v-model:visible="drawerVisible"
      :response-data="apiResponse"
      field-type="extract"
      @select-path="handleSelectPath"
    />
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
.extract-code-trigger {
  color: var(--color-text-3);
}

:deep(.arco-input-wrapper) {
  @apply bg-white border-[color:var(--color-border-2)];
  
  input {
    @apply text-[color:var(--color-text-1)] bg-transparent;
    &::placeholder {
      @apply text-[color:var(--color-text-3)];
    }
  }
}

:deep(.arco-checkbox) {
  @apply text-[color:var(--color-text-2)];
}

:deep(.arco-btn-outline) {
  @apply border-[color:var(--color-border-2)] text-[color:var(--color-text-2)];
  
  &:hover {
    @apply border-blue-500 text-blue-500;
  }
}

:global(body.api-testing-theme) .extract-code-trigger {
  color: rgb(156, 163, 175);
}

:global(body.api-testing-theme) :deep(.arco-input-wrapper) {
  @apply bg-gray-900/60 border-gray-700;

  input {
    @apply text-gray-200 bg-transparent;
    &::placeholder {
      @apply text-gray-500;
    }
  }
}

:global(body.api-testing-theme) :deep(.arco-checkbox) {
  @apply text-gray-400;
}

:global(body.api-testing-theme) :deep(.arco-btn-outline) {
  @apply border-gray-600 text-gray-300;
}
</style>