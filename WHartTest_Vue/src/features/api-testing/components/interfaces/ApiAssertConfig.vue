<script setup lang="ts">
import { ref, watch, onMounted, inject } from 'vue'
import { IconDelete, IconPlus, IconCode } from '@arco-design/web-vue/es/icon'
import type { ApiValidator } from '../../services/interfaceService'
import ResponseJsonViewer from './ResponseJsonViewer.vue'
import { Message } from '@arco-design/web-vue'

interface Props {
  validators?: Array<Record<keyof ApiValidator, [string, string]>>
}

const props = defineProps<Props>()

// 获取响应数据
const apiResponse = inject('apiResponse', ref(null))

// 抽屉控制
const drawerVisible = ref(false)
const currentEditingIndex = ref(-1)

interface AssertRule {
  type: keyof ApiValidator
  expression: string
  expected: string
  enabled: boolean
}

// 数据类型选项（用于 type_match 断言）
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

const validatorTypes = [
  // 基础比较断言
  { label: '等于', value: 'eq', category: '基础比较' },
  { label: '不等于', value: 'ne', category: '基础比较' },
  { label: '大于', value: 'gt', category: '基础比较' },
  { label: '大于等于', value: 'ge', category: '基础比较' },
  { label: '小于', value: 'lt', category: '基础比较' },
  { label: '小于等于', value: 'le', category: '基础比较' },
  
  // 字符串相关断言
  { label: '包含', value: 'contains', category: '字符串' },
  { label: '被包含', value: 'contained_by', category: '字符串' },
  { label: '以...开头', value: 'startswith', category: '字符串' },
  { label: '以...结尾', value: 'endswith', category: '字符串' },
  { label: '正则匹配', value: 'regex_match', category: '字符串' },
  { label: '字符串等于', value: 'str_eq', category: '字符串' },
  
  // 长度相关断言
  { label: '长度等于', value: 'length_equal', category: '长度' },
  { label: '长度大于', value: 'length_greater_than', category: '长度' },
  { label: '长度小于', value: 'length_less_than', category: '长度' },
  { label: '长度大于等于', value: 'length_greater_or_equals', category: '长度' },
  { label: '长度小于等于', value: 'length_less_or_equals', category: '长度' },
  
  // 其他断言
  { label: '类型匹配', value: 'type_match', category: '其他' }
] as const

const assertRules = ref<AssertRule[]>([
  { type: 'eq', expression: '', expected: '', enabled: true }
])

// 初始化断言规则
const initAssertRules = () => {
  if (props.validators && props.validators.length > 0) {
    assertRules.value = props.validators.map(validator => {
      const [type, [expression, expected]] = Object.entries(validator)[0] as [keyof ApiValidator, [string, string]]
      return {
        type,
        expression,
        expected,
        enabled: true
      }
    })
  } else {
    assertRules.value = [{ type: 'eq', expression: '', expected: '', enabled: true }]
  }
}

// 监听 validators 变化
watch(() => props.validators, (newValidators) => {
  if (newValidators && newValidators.length > 0) {
    assertRules.value = newValidators.map(validator => {
      const [type, [expression, expected]] = Object.entries(validator)[0] as [keyof ApiValidator, [string, string]]
      return {
        type,
        expression,
        expected,
        enabled: true
      }
    })
  } else {
    assertRules.value = [{ type: 'eq', expression: '', expected: '', enabled: true }]
  }
})

// 添加断言规则
const addRow = () => {
  assertRules.value.push({ type: 'eq', expression: '', expected: '', enabled: true })
}

// 删除断言规则
const removeRow = (index: number) => {
  assertRules.value.splice(index, 1)
  if (assertRules.value.length === 0) {
    assertRules.value.push({ type: 'eq', expression: '', expected: '', enabled: true })
  }
}

// 向父组件暴露断言规则数据
defineExpose({
  getAssertRules: () => assertRules.value
    .filter(rule => rule.enabled && rule.expression && rule.expected)
    .map(rule => ({
      [rule.type]: [rule.expression, rule.expected]
    }))
})

// 组件加载时初始化数据
onMounted(() => {
  initAssertRules()
})

// 处理从响应中选择路径
const handleSelectPath = (path: string, value: string) => {
  if (currentEditingIndex.value >= 0 && currentEditingIndex.value < assertRules.value.length) {
    // 设置表达式
    assertRules.value[currentEditingIndex.value].expression = path
    
    // 设置预期结果，如果结果为空
    if (!assertRules.value[currentEditingIndex.value].expected.trim()) {
      // 使用当前值作为预期结果
      assertRules.value[currentEditingIndex.value].expected = value
    }
    
    Message.success('已设置断言表达式')
  }
}

// 打开响应查看器
const openResponseViewer = (index: number) => {
  currentEditingIndex.value = index
  drawerVisible.value = true
}
</script>

<template>
  <div class="h-full flex flex-col p-4 space-y-2">
    <div class="flex-1 min-h-0 overflow-y-auto pr-2">
      <div class="space-y-2">
        <div
          v-for="(rule, index) in assertRules"
          :key="index"
          class="flex items-center gap-2 w-full"
        >
          <a-checkbox v-model="rule.enabled" class="flex-shrink-0" />
          
          <div class="flex flex-1 gap-2">
            <div class="flex relative w-3/5">
              <a-input
                v-model="rule.expression"
                placeholder="断言表达式 (例如: body.data.code)"
                allow-clear
                class="w-full"
              />
              <a-button
                type="text"
                class="assert-code-trigger absolute right-0 top-0 bottom-0 hover:text-blue-500"
                @click="openResponseViewer(index)"
                :disabled="!apiResponse"
              >
                <template #icon><icon-code /></template>
              </a-button>
            </div>
            
            <div class="flex gap-2 w-2/5">
              <a-select
                v-model="rule.type"
                class="w-2/5"
                placeholder="断言类型"
              >
                <a-optgroup
                  v-for="category in ['基础比较', '字符串', '长度', '其他']"
                  :key="category"
                  :label="category"
                >
                  <a-option
                    v-for="type in validatorTypes.filter(t => t.category === category)"
                    :key="type.value"
                    :value="type.value"
                  >
                    {{ type.label }}
                  </a-option>
                </a-optgroup>
              </a-select>
              
              <!-- 根据断言类型显示不同的输入控件 -->
              <a-select
                v-if="rule.type === 'type_match'"
                v-model="rule.expected"
                placeholder="选择类型"
                allow-clear
                class="w-3/5"
              >
                <a-option
                  v-for="type in dataTypes"
                  :key="type.value"
                  :value="type.value"
                >
                  {{ type.label }}
                </a-option>
              </a-select>
              
              <a-input
                v-else
                v-model="rule.expected"
                placeholder="预期结果"
                allow-clear
                class="w-3/5"
              />
            </div>
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
        添加断言规则
      </a-button>
    </div>

    <!-- 响应JSON查看器 -->
    <ResponseJsonViewer
      v-model:visible="drawerVisible"
      :response-data="apiResponse"
      field-type="assert"
      @select-path="handleSelectPath"
    />
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
.assert-code-trigger {
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

:deep(.arco-select-view) {
  @apply bg-white border-[color:var(--color-border-2)];
}

:deep(.arco-select-view-value) {
  @apply text-[color:var(--color-text-1)];
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

:global(body.api-testing-theme) .assert-code-trigger {
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

:global(body.api-testing-theme) :deep(.arco-select-view) {
  @apply bg-gray-900/60 border-gray-700;
}

:global(body.api-testing-theme) :deep(.arco-select-view-value) {
  @apply text-gray-200;
}

:global(body.api-testing-theme) :deep(.arco-checkbox) {
  @apply text-gray-400;
}

:global(body.api-testing-theme) :deep(.arco-btn-outline) {
  @apply border-gray-600 text-gray-300;
}

:global(body.api-testing-theme .arco-select-dropdown) {
  @apply bg-gray-800 border-gray-700;

  .arco-select-option {
    @apply text-gray-300;

    &:hover {
      @apply bg-gray-700;
    }

    &.arco-select-option-active {
      @apply bg-blue-500/20 text-blue-500;
    }
  }
}
</style>