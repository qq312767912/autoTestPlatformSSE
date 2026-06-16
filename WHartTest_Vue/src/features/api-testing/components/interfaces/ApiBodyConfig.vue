<script setup lang="ts">
import { ref, watch } from 'vue'
import type { FileItem } from '@arco-design/web-vue'
import { IconDelete, IconPlus, IconUpload } from '@arco-design/web-vue/es/icon'
import type { KeyValuePair } from '../../services/interfaceService'
import MonacoEditor from '@guolao/vue-monaco-editor'
import { useThemeStore } from '@/store/themeStore'

interface Props {
  body?: {
    type: 'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary'
    content: KeyValuePair[] | string | null
  }
}

const props = defineProps<Props>()
const emit = defineEmits(['update:body'])
const themeStore = useThemeStore()

// 请求体类型
type BodyType = 'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary'
const bodyType = ref<BodyType>('none')

// 请求体内容
const formDataList = ref<KeyValuePair[]>([{ key: '', value: '', description: '', enabled: true }])
const urlEncodedList = ref<KeyValuePair[]>([{ key: '', value: '', description: '', enabled: true }])
const rawContent = ref('')
const binaryFile = ref<File | null>(null)

// raw 类型的语言选择
const rawLanguage = ref('json')

// 初始化表单数据
const initFormData = () => {
  if (props.body?.content) {
    if (Array.isArray(props.body.content)) {
      if (bodyType.value === 'form-data') {
        formDataList.value = props.body.content.length > 0 ? props.body.content : [{ key: '', value: '', description: '', enabled: true }]
      } else if (bodyType.value === 'x-www-form-urlencoded') {
        urlEncodedList.value = props.body.content.length > 0 ? props.body.content : [{ key: '', value: '', description: '', enabled: true }]
      }
    } else if (typeof props.body.content === 'object' && props.body.content !== null) {
      const list = Object.entries(props.body.content).map(([key, value]) => ({
        key,
        value: typeof value === 'string' ? value : JSON.stringify(value),
        description: '',
        enabled: true
      }))
      
      if (bodyType.value === 'form-data') {
        formDataList.value = list.length > 0 ? list : [{ key: '', value: '', description: '', enabled: true }]
      } else if (bodyType.value === 'x-www-form-urlencoded') {
        urlEncodedList.value = list.length > 0 ? list : [{ key: '', value: '', description: '', enabled: true }]
      }
    }
  }
  
  // 确保至少有一个空行
  if (bodyType.value === 'form-data' && formDataList.value.length === 0) {
    formDataList.value = [{ key: '', value: '', description: '', enabled: true }]
  } else if (bodyType.value === 'x-www-form-urlencoded' && urlEncodedList.value.length === 0) {
    urlEncodedList.value = [{ key: '', value: '', description: '', enabled: true }]
  }
}

// 监听body数据变化
watch(() => props.body, (newBody) => {
  // 重置所有状态
  bodyType.value = 'none'
  formDataList.value = [{ key: '', value: '', description: '', enabled: true }]
  urlEncodedList.value = [{ key: '', value: '', description: '', enabled: true }]
  rawContent.value = ''
  rawLanguage.value = 'json'
  binaryFile.value = null

  if (newBody) {
    bodyType.value = newBody.type || 'none'
    
    if (newBody.type === 'form-data' || newBody.type === 'x-www-form-urlencoded') {
      if (Array.isArray(newBody.content)) {
        if (newBody.type === 'form-data') {
          formDataList.value = newBody.content.length > 0 ? JSON.parse(JSON.stringify(newBody.content)) : [{ key: '', value: '', description: '', enabled: true }]
        } else {
          urlEncodedList.value = newBody.content.length > 0 ? JSON.parse(JSON.stringify(newBody.content)) : [{ key: '', value: '', description: '', enabled: true }]
        }
      } else if (typeof newBody.content === 'object' && newBody.content !== null) {
        const list = Object.entries(newBody.content).map(([key, value]) => ({
          key,
          value: typeof value === 'string' ? value : JSON.stringify(value),
          description: '',
          enabled: true
        }))
        
        if (newBody.type === 'form-data') {
          formDataList.value = list.length > 0 ? list : [{ key: '', value: '', description: '', enabled: true }]
        } else {
          urlEncodedList.value = list.length > 0 ? list : [{ key: '', value: '', description: '', enabled: true }]
        }
      }
    } else if (newBody.type === 'raw') {
      if (typeof newBody.content === 'object' && newBody.content !== null) {
        rawContent.value = JSON.stringify(newBody.content, null, 2)
        rawLanguage.value = 'json'
      } else if (typeof newBody.content === 'string') {
        rawContent.value = newBody.content
        try {
          JSON.parse(newBody.content)
          rawLanguage.value = 'json'
        } catch {
          rawLanguage.value = 'text'
        }
      }
    } else if (newBody.type === 'binary') {
      if (newBody.content) {
        binaryFile.value = newBody.content as any
      }
    }
  }
}, { immediate: true, deep: true })

// 监听请求体类型变化
watch(bodyType, (newType) => {
  // 根据新类型初始化数据
  if (newType === 'none') {
    rawContent.value = ''
    binaryFile.value = null
  } else if (newType === 'form-data' || newType === 'x-www-form-urlencoded') {
    rawContent.value = ''
    binaryFile.value = null
    // 如果之前是相同类型，保留数据
    if (props.body?.type === newType) {
      initFormData()
    }
  } else if (newType === 'raw') {
    // 如果之前是 raw 类型，保留数据
    if (props.body?.type === 'raw') {
      if (typeof props.body.content === 'object' && props.body.content !== null) {
        rawContent.value = JSON.stringify(props.body.content, null, 2)
      } else if (typeof props.body.content === 'string') {
        rawContent.value = props.body.content
      } else {
        rawContent.value = rawLanguage.value === 'json' ? '{}' : ''
      }
    } else {
      rawContent.value = rawLanguage.value === 'json' ? '{}' : ''
    }
  } else if (newType === 'binary') {
    rawContent.value = ''
    // 如果之前是 binary 类型，保留数据
    if (props.body?.type === 'binary') {
      binaryFile.value = props.body.content as any
    } else {
      binaryFile.value = null
    }
  }

  // 发送更新事件
  emit('update:body', getBody())
})

// 添加新的键值对
const addKeyValuePair = (list: KeyValuePair[]) => {
  list.push({ key: '', value: '', description: '', enabled: true })
}

// 删除键值对
const removeKeyValuePair = (list: KeyValuePair[], index: number) => {
  list.splice(index, 1)
  // 如果删除后没有行了，添加一个空行
  if (list.length === 0) {
    list.push({ key: '', value: '', description: '', enabled: true })
  }
  
  // 发送更新事件
  emit('update:body', getBody())
}

// 处理文件选择
const handleFileSelect = (fileList: FileItem[], fileItem: FileItem) => {
  if (fileItem.file) {
    binaryFile.value = fileItem.file
    // 发送更新事件
    emit('update:body', getBody())
  }
}

// 获取请求体
const getBody = () => {
  const body: Record<string, any> = {
    type: bodyType.value
  }

  switch (bodyType.value) {
    case 'form-data':
      body.content = formDataList.value.filter(item => item.key || item.value)
      break
    case 'x-www-form-urlencoded':
      body.content = urlEncodedList.value.filter(item => item.key || item.value)
      break
    case 'raw':
      body.content = rawContent.value
      break
    case 'binary':
      body.content = binaryFile.value
      break
    default:
      body.content = null
  }

  return body
}

// 向父组件暴露body数据
defineExpose({
  getBody
})
</script>

<template>
  <div class="h-full flex flex-col" :class="themeStore.isBlack ? 'api-body-config--dark' : 'api-body-config--light'">
    <!-- 顶部类型选择 -->
    <div
      class="flex-shrink-0 border-b"
      :class="themeStore.isBlack ? 'bg-gray-800 border-gray-700' : 'bg-[var(--color-fill-2)] border-[var(--color-border-2)]'"
    >
      <a-radio-group v-model="bodyType" type="button" class="p-4 w-full">
        <a-radio value="none">none</a-radio>
        <a-radio value="form-data">form-data</a-radio>
        <a-radio value="x-www-form-urlencoded">x-www-form-urlencoded</a-radio>
        <a-radio value="raw">raw</a-radio>
        <a-radio value="binary">binary</a-radio>
      </a-radio-group>
    </div>

    <!-- 内容区域 -->
    <div class="flex-1 min-h-0 p-4">
      <!-- form-data输入 -->
      <div v-if="bodyType === 'form-data'" class="h-[400px] flex flex-col">
        <div class="flex-1 min-h-0 overflow-y-auto space-y-2">
          <div v-for="(item, index) in formDataList" :key="index" class="flex items-center gap-2">
            <a-checkbox v-model="item.enabled" />
            <a-input 
              v-model="item.key" 
              placeholder="Key" 
              allow-clear
              class="!w-[200px]"
            />
            <a-input 
              v-model="item.value" 
              placeholder="Value" 
              allow-clear
              class="!w-[200px]"
            />
            <a-input 
              v-model="item.description" 
              placeholder="Description" 
              allow-clear
              class="!flex-1"
            />
            <a-button type="text" status="danger" @click="removeKeyValuePair(formDataList, index)">
              <template #icon><icon-delete /></template>
            </a-button>
          </div>
        </div>
        <div class="flex-shrink-0 mt-2">
          <a-button type="outline" @click="addKeyValuePair(formDataList)">
            <template #icon><icon-plus /></template>
            添加字段
          </a-button>
        </div>
      </div>

      <!-- x-www-form-urlencoded输入 -->
      <div v-if="bodyType === 'x-www-form-urlencoded'" class="h-[400px] flex flex-col">
        <div class="flex-1 min-h-0 overflow-y-auto space-y-2">
          <div v-for="(item, index) in urlEncodedList" :key="index" class="flex items-center gap-2">
            <a-checkbox v-model="item.enabled" />
            <a-input 
              v-model="item.key" 
              placeholder="Key" 
              allow-clear
              class="!w-[200px]"
            />
            <a-input 
              v-model="item.value" 
              placeholder="Value" 
              allow-clear
              class="!w-[200px]"
            />
            <a-input 
              v-model="item.description" 
              placeholder="Description" 
              allow-clear
              class="!flex-1"
            />
            <a-button type="text" status="danger" @click="removeKeyValuePair(urlEncodedList, index)">
              <template #icon><icon-delete /></template>
            </a-button>
          </div>
        </div>
        <div class="flex-shrink-0 mt-2">
          <a-button type="outline" @click="addKeyValuePair(urlEncodedList)">
            <template #icon><icon-plus /></template>
            添加字段
          </a-button>
        </div>
      </div>

      <!-- raw输入 -->
      <div v-if="bodyType === 'raw'" class="flex-1 min-h-0">
        <div class="h-full flex flex-col gap-2">
          <div class="flex-shrink-0">
            <a-radio-group v-model="rawLanguage" type="button" size="small">
              <a-radio value="json">JSON</a-radio>
              <a-radio value="text">Text</a-radio>
              <a-radio value="javascript">JavaScript</a-radio>
              <a-radio value="html">HTML</a-radio>
              <a-radio value="xml">XML</a-radio>
            </a-radio-group>
          </div>
          <div class="flex-1 min-h-0">
            <div
              class="editor-shell h-full overflow-hidden rounded-lg border"
              :class="themeStore.isBlack ? 'bg-gray-900/60 border-gray-700' : 'bg-white border-[var(--color-border-2)] shadow-sm'"
            >
              <MonacoEditor
                v-model:value="rawContent"
                :language="rawLanguage"
                :theme="themeStore.isBlack ? 'vs-dark' : 'vs'"
                class="h-full min-h-[100px]"
                @change="emit('update:body', getBody())"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- binary文件上传 -->
      <div v-if="bodyType === 'binary'" class="h-[400px] flex justify-center items-center">
        <div class="relative">
          <a-upload
            action="/"
            :auto-upload="false"
            :limit="1"
            @change="handleFileSelect"
          >
            <template #upload-button>
              <div
                class="border-2 border-dashed rounded-lg p-8 text-center"
                :class="themeStore.isBlack ? 'border-gray-600' : 'border-[var(--color-border-2)] bg-[var(--color-fill-1)]'"
              >
                <icon-upload class="text-3xl mb-2" :class="themeStore.isBlack ? 'text-gray-500' : 'text-[var(--color-text-3)]'" />
                <p :class="themeStore.isBlack ? 'text-gray-400' : 'text-[var(--color-text-2)]'">点击或拖拽文件到此处上传</p>
              </div>
            </template>
          </a-upload>
          <div v-if="binaryFile" class="mt-4 text-center" :class="themeStore.isBlack ? 'text-gray-400' : 'text-[var(--color-text-2)]'">
            已选择文件: {{ binaryFile.name }} ({{ (binaryFile.size / 1024).toFixed(2) }} KB)
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
.api-body-config--dark :deep(.arco-radio-group-button) {
  @apply bg-gray-900/60 border-gray-700;
  
  .arco-radio-button {
    @apply border-gray-700 text-gray-400;
    
    &.arco-radio-button-checked {
      @apply text-blue-500 bg-blue-500/10;
    }
  }
}

.api-body-config--light :deep(.arco-radio-group-button) {
  background-color: #eef2f7;
  border-color: rgba(15, 23, 42, 0.1);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.72);

  .arco-radio-button {
    color: var(--color-text-2);
    border-color: rgba(15, 23, 42, 0.08);
    background-color: transparent;

    &.arco-radio-button-checked {
      color: rgb(var(--primary-6));
      background-color: #fff;
      box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
    }
  }
}

.api-body-config--dark :deep(.arco-input-wrapper) {
  @apply bg-gray-900/60 border-gray-700;
  
  input {
    @apply text-gray-200 bg-transparent;
    &::placeholder {
      @apply text-gray-500;
    }
  }
}

.api-body-config--light :deep(.arco-input-wrapper) {
  background-color: var(--color-bg-1);
  border-color: var(--color-border-2);

  input {
    color: var(--color-text-1);
    background: transparent;

    &::placeholder {
      color: var(--color-text-3);
    }
  }
}

.api-body-config--dark :deep(.arco-checkbox) {
  @apply text-gray-400;
}

.api-body-config--light :deep(.arco-checkbox) {
  color: var(--color-text-3);
}

:deep(.arco-upload) {
  @apply rounded p-4;
}

.editor-shell :deep(.monaco-editor),
.editor-shell :deep(.monaco-editor-background),
.editor-shell :deep(.monaco-editor .margin) {
  border-radius: 8px;
}
</style>