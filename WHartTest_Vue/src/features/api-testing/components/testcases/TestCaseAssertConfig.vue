<script setup lang="ts">
import { ref, watch, onMounted, inject } from 'vue'
import { IconDelete, IconPlus, IconCode } from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'

interface Props {
  validators?: Array<Record<string, [string, string]>>
}

const props = withDefaults(defineProps<Props>(), {
  validators: () => []
})

const apiResponse = inject('apiResponse', ref(null))
const drawerVisible = ref(false)
const currentEditingIndex = ref(-1)

interface AssertRule {
  type: string
  expression: string
  expected: string
  description: string
  enabled: boolean
}

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
  { label: '等于', value: 'eq', category: '基础比较' },
  { label: '不等于', value: 'ne', category: '基础比较' },
  { label: '大于', value: 'gt', category: '基础比较' },
  { label: '大于等于', value: 'ge', category: '基础比较' },
  { label: '小于', value: 'lt', category: '基础比较' },
  { label: '小于等于', value: 'le', category: '基础比较' },
  { label: '包含', value: 'contains', category: '字符串' },
  { label: '被包含', value: 'contained_by', category: '字符串' },
  { label: '以...开头', value: 'startswith', category: '字符串' },
  { label: '以...结尾', value: 'endswith', category: '字符串' },
  { label: '正则匹配', value: 'regex_match', category: '字符串' },
  { label: '字符串等于', value: 'str_eq', category: '字符串' },
  { label: '长度等于', value: 'length_equal', category: '长度' },
  { label: '长度大于', value: 'length_greater_than', category: '长度' },
  { label: '长度小于', value: 'length_less_than', category: '长度' },
  { label: '长度大于等于', value: 'length_greater_or_equals', category: '长度' },
  { label: '长度小于等于', value: 'length_less_or_equals', category: '长度' },
  { label: '类型匹配', value: 'type_match', category: '其他' }
] as const

const assertRules = ref<AssertRule[]>([
  { type: 'eq', expression: '', expected: '', description: '', enabled: true }
])

const initAssertRules = () => {
  if (props.validators && props.validators.length > 0) {
    assertRules.value = props.validators.map(validator => {
      const [type, [expression, expected]] = Object.entries(validator)[0] as [string, [string, string]]
      return { type, expression, expected, description: '', enabled: true }
    })
  } else {
    assertRules.value = [{ type: 'eq', expression: '', expected: '', description: '', enabled: true }]
  }
}

watch(() => props.validators, (newValidators) => {
  if (newValidators && newValidators.length > 0) {
    assertRules.value = newValidators.map(validator => {
      const [type, [expression, expected]] = Object.entries(validator)[0] as [string, [string, string]]
      return { type, expression, expected, description: '', enabled: true }
    })
  } else {
    assertRules.value = [{ type: 'eq', expression: '', expected: '', description: '', enabled: true }]
  }
})

const addRow = () => {
  assertRules.value.push({ type: 'eq', expression: '', expected: '', description: '', enabled: true })
}

const removeRow = (index: number) => {
  assertRules.value.splice(index, 1)
  if (assertRules.value.length === 0) {
    assertRules.value.push({ type: 'eq', expression: '', expected: '', description: '', enabled: true })
  }
}

const getJsonPaths = (obj: any, prefix = ''): { path: string; value: any }[] => {
  const paths: { path: string; value: any }[] = []
  if (obj === null || obj === undefined) return paths
  if (typeof obj === 'object') {
    for (const key of Object.keys(obj)) {
      const fullPath = prefix ? `${prefix}.${key}` : key
      paths.push({ path: fullPath, value: obj[key] })
      if (typeof obj[key] === 'object' && obj[key] !== null) {
        paths.push(...getJsonPaths(obj[key], fullPath))
      }
    }
  }
  return paths
}

const handleSelectPath = (path: string, value: string) => {
  if (currentEditingIndex.value >= 0 && currentEditingIndex.value < assertRules.value.length) {
    assertRules.value[currentEditingIndex.value].expression = path
    if (!assertRules.value[currentEditingIndex.value].expected.trim()) {
      assertRules.value[currentEditingIndex.value].expected = value
    }
    Message.success('已设置断言表达式')
  }
  drawerVisible.value = false
}

const openResponseViewer = (index: number) => {
  currentEditingIndex.value = index
  drawerVisible.value = true
}

const getAssertRules = () => {
  return assertRules.value
    .filter(rule => rule.enabled && rule.expression && rule.expected)
    .map(rule => ({ [rule.type]: [rule.expression, rule.expected] }))
}

onMounted(() => { initAssertRules() })
defineExpose({ getAssertRules })
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
              <a-input v-model="rule.expression" placeholder="断言表达式 (例如: body.data.code)" allow-clear class="w-full" />
              <a-button type="text" class="absolute right-0 top-0 bottom-0" @click="openResponseViewer(index)" :disabled="!apiResponse">
                <template #icon><icon-code /></template>
              </a-button>
            </div>
            <div class="flex gap-2 w-2/5">
              <a-select v-model="rule.type" class="w-2/5" placeholder="断言类型">
                <a-optgroup v-for="category in ['基础比较', '字符串', '长度', '其他']" :key="category" :label="category">
                  <a-option v-for="vtype in validatorTypes.filter(t => t.category === category)" :key="vtype.value" :value="vtype.value">
                    {{ vtype.label }}
                  </a-option>
                </a-optgroup>
              </a-select>
              <a-select v-if="rule.type === 'type_match'" v-model="rule.expected" placeholder="选择类型" allow-clear class="w-3/5">
                <a-option v-for="dtype in dataTypes" :key="dtype.value" :value="dtype.value">{{ dtype.label }}</a-option>
              </a-select>
              <a-input v-else v-model="rule.expected" placeholder="预期结果" allow-clear class="w-3/5" />
            </div>
          </div>
          <a-button type="text" status="danger" @click="removeRow(index)" class="flex-shrink-0">
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

    <!-- 响应JSON路径选择抽屉 -->
    <a-drawer v-model:visible="drawerVisible" title="从响应中选择路径" :width="500" unmount-on-close>
      <div v-if="apiResponse" class="space-y-1">
        <div
          v-for="item in getJsonPaths(apiResponse)"
          :key="item.path"
          class="json-path-item"
          @click="handleSelectPath(item.path, typeof item.value === 'object' ? JSON.stringify(item.value) : String(item.value))"
        >
          <span class="json-path-key">{{ item.path }}</span>
          <span class="json-path-value">{{ typeof item.value === 'object' ? JSON.stringify(item.value) : String(item.value) }}</span>
        </div>
      </div>
      <a-empty v-else description="暂无响应数据，请先调试接口" />
    </a-drawer>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
:deep(.arco-input-wrapper) {
  background: var(--tcf-control-bg) !important;
  border-color: var(--tcf-control-border) !important;

  input {
    color: var(--tcf-text) !important;
    background: transparent !important;
    &::placeholder {
      color: var(--tcf-text-subtle) !important;
    }
  }
}

:deep(.arco-select-view) {
  background: var(--tcf-control-bg) !important;
  border-color: var(--tcf-control-border) !important;
}

:deep(.arco-select-view-value) {
  color: var(--tcf-text) !important;
}

:deep(.arco-checkbox) {
  color: var(--tcf-text-subtle) !important;
}

:deep(.arco-btn-outline) {
  border-color: var(--tcf-control-border) !important;
  color: var(--tcf-text-muted) !important;

  &:hover {
    border-color: rgba(59, 130, 246, 0.4) !important;
    color: rgb(59, 130, 246) !important;
  }
}

:global(.arco-select-dropdown) {
  background: #ffffff !important;
  border: 1px solid rgba(148, 163, 184, 0.16) !important;
  border-radius: 10px !important;

  .arco-select-option {
    color: #334155 !important;

    &:hover {
      background: #f8fafc !important;
    }

    &.arco-select-option-active,
    &.arco-select-option-selected {
      background: rgba(59, 130, 246, 0.12) !important;
      color: #2563eb !important;
    }
  }
}

:global(body.api-testing-theme .arco-select-dropdown) {
  background: rgb(31, 41, 55) !important;
  border-color: rgba(75, 85, 99, 0.4) !important;
}

:global(body.api-testing-theme .arco-select-dropdown .arco-select-option) {
  color: rgb(203, 213, 225) !important;

  &:hover {
    background: rgba(51, 65, 85, 0.9) !important;
  }
}

:deep(.arco-btn-text) {
  color: var(--tcf-text-subtle) !important;

  &:hover {
    color: rgb(59, 130, 246) !important;
    background: rgba(59, 130, 246, 0.1) !important;
  }

  &.arco-btn-status-danger {
    &:hover {
      color: rgb(239, 68, 68) !important;
      background: rgba(239, 68, 68, 0.1) !important;
    }
  }
}
</style>
