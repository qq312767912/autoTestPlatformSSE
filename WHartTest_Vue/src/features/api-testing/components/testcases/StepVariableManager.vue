<script setup lang="ts">
import { ref, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconCode } from '@arco-design/web-vue/es/icon'

interface StepVariable {
  key: string
  value: any
  from: string
}

interface Props {
  modelValue: StepVariable[]
}

const props = defineProps<Props>()
const emit = defineEmits(['update:modelValue'])

const stepVariables = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const stepVariableModalVisible = ref(false)

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
      @click="() => stepVariableModalVisible = true"
      status="normal"
    >
      <template #icon>
        <icon-code class="!text-[rgb(var(--primary-6))]" />
      </template>
      <span class="!text-[rgb(var(--primary-6))]">步骤变量</span>
      <span class="badge-count">
        {{ stepVariables.length }}
      </span>
    </a-button>

    <a-modal
      v-model:visible="stepVariableModalVisible"
      :width="800"
      title="步骤变量"
    >
      <div class="space-y-4">
        <a-table :data="stepVariables" :pagination="false" :bordered="false">
          <template #columns>
            <a-table-column title="变量名" data-index="key" :width="250">
              <template #cell="{ record }">
                <div class="flex items-center gap-2">
                  <span class="font-mono" style="color: rgb(var(--primary-6))">$</span>
                  <span class="font-mono">{{ record.key }}</span>
                  <a-button type="text" size="mini" class="!p-1" @click="() => handleCopyVariable(record.key)">
                    复制
                  </a-button>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="值" data-index="value">
              <template #cell="{ record }">
                <span class="font-mono">{{ record.value }}</span>
              </template>
            </a-table-column>
            <a-table-column title="来源" data-index="from" :width="150">
              <template #cell="{ record }">
                <span class="source-text">{{ record.from }}</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.badge-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgb(var(--primary-6));
  color: #fff;
  border-radius: 4px;
  font-size: 12px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
}
.source-text {
  color: var(--color-text-3);
}
</style>
