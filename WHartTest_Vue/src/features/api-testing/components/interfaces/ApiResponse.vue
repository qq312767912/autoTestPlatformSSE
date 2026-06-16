<script setup lang="ts">
import { ref, computed } from 'vue'
import { useClipboard } from '@vueuse/core'
import { Message } from '@arco-design/web-vue'
import { IconCopy } from '@arco-design/web-vue/es/icon'

interface Response {
  status: number | null
  time: number | null
  size: number | null
  data: any
  request: any
  response: any
  validation_results: any
  extracted_variables: any
}

interface Props {
  response: Response
}

const props = defineProps<Props>()

const selectedContentType = ref('json')

const { copy } = useClipboard()

// 复制内容到剪贴板
const copyContent = async (content: string) => {
  await copy(content)
  Message.success('复制成功')
}

// 格式化JSON：处理 object 和 string 两种情况
const formatJson = (data: any): string => {
  if (!data) return ''
  if (typeof data === 'object') {
    return JSON.stringify(data, null, 2)
  }
  // 如果是字符串，尝试解析为JSON后格式化
  if (typeof data === 'string') {
    try {
      const parsed = JSON.parse(data)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return data
    }
  }
  return String(data)
}

// 计算响应体的内容
const responseContent = computed(() => {
  return formatJson(props.response.data?.response?.content)
})

// 计算请求信息的内容
const requestContent = computed(() => {
  return formatJson(props.response.data?.request)
})

// 计算响应头的内容
const responseHeadersContent = computed(() => {
  return formatJson(props.response.data?.response?.headers)
})

// 获取响应状态码
const statusCode = computed(() => {
  return props.response.data?.response?.status_code || props.response.data?.status_code
})

// 计算提取变量的内容
const extractedContent = computed(() => {
  return formatJson(props.response.data?.extracted_variables)
})

// 计算完整数据的内容
const completeContent = computed(() => {
  return formatJson(props.response.data)
})

// 判断响应内容的语言类型
const responseLanguage = computed(() => {
  if (!props.response.data?.response) return 'text'
  const contentType = props.response.data.response.headers?.['Content-Type']?.toLowerCase() || ''
  if (contentType.includes('application/json')) return 'json'
  if (contentType.includes('text/html')) return 'html'
  if (contentType.includes('text/xml')) return 'xml'
  if (contentType.includes('text/css')) return 'css'
  if (contentType.includes('javascript')) return 'javascript'
  return 'text'
})

// 顶部显示的响应消息
const responseMessage = computed(() => {
  if (props.response.response?.content?.detail) {
    return props.response.response.content.detail
  }
  return props.response.data?.message || ''
})
</script>

<template>
  <div class="api-response h-full flex flex-col">
    <!-- 顶部响应概要 -->
    <div class="response-summary flex items-center gap-4 px-4 pt-4 pb-2 border-t border-b">
      <div class="response-summary-label">响应内容</div>
      <div class="flex-1"></div>
      <div class="flex items-center gap-4 flex-shrink-0">
        <a-tag v-if="statusCode" :color="statusCode === 200 ? 'green' : 'red'" class="w-10 flex justify-center items-center">
          {{ statusCode }}
        </a-tag>
        <span v-if="response.time" class="response-summary-meta">{{ response.time.toFixed(3) }} ms</span>
        <span v-if="response.size" class="response-summary-meta">{{ response.size }} bytes</span>
      </div>
    </div>

    <!-- 响应内容页签 -->
    <div class="flex-1 overflow-hidden">
      <a-tabs class="h-full">
        <!-- 响应体 -->
        <a-tab-pane key="response" title="响应体">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="responseContent" class="response-code-shell rounded-lg shadow-inner relative group">
                <div
                  class="absolute right-2 top-2 cursor-pointer copy-button"
                  @click="copyContent(responseContent)"
                  title="复制"
                >
                  <icon-copy />
                </div>
                <pre class="response-pre p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ responseContent }}</pre>
              </div>
              <a-empty v-else description="暂无响应数据" />
            </div>
          </div>
        </a-tab-pane>

        <!-- 响应头 -->
        <a-tab-pane key="headers" title="响应头">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="responseHeadersContent" class="response-code-shell rounded-lg shadow-inner relative group">
                <div
                  class="absolute right-2 top-2 cursor-pointer copy-button"
                  @click="copyContent(responseHeadersContent)"
                  title="复制"
                >
                  <icon-copy />
                </div>
                <pre class="response-pre p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ responseHeadersContent }}</pre>
              </div>
              <a-empty v-else description="暂无响应头数据" />
            </div>
          </div>
        </a-tab-pane>

        <!-- 请求信息 -->
        <a-tab-pane key="request" title="请求信息">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="requestContent" class="response-code-shell rounded-lg shadow-inner relative group">
                <div
                  class="absolute right-2 top-2 cursor-pointer copy-button"
                  @click="copyContent(requestContent)"
                  title="复制"
                >
                  <icon-copy />
                </div>
                <pre class="response-pre p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ requestContent }}</pre>
              </div>
              <a-empty v-else description="暂无请求数据" />
            </div>
          </div>
        </a-tab-pane>

        <!-- 验证结果 -->
        <a-tab-pane key="validation" title="验证结果">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.validation_results?.length" class="response-code-shell rounded-lg shadow-inner p-4">
                <div v-for="(result, index) in response.data.validation_results" :key="index"
                  class="validation-item flex flex-col p-3 rounded-md mb-3"
                  :class="{'bg-green-900/10': result.check_result === 'pass', 'bg-red-900/10': result.check_result !== 'pass'}"
                >
                  <!-- 验证结果标题栏 -->
                  <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                      <a-tag :color="result.check_result === 'pass' ? 'green' : 'red'" class="!font-medium !flex-shrink-0">
                        {{ result.check_result === 'pass' ? '通过' : '失败' }}
                      </a-tag>
                      <span class="response-pre font-medium">{{ result.comparator }}</span>
                    </div>
                  </div>

                  <!-- 验证详细信息 -->
                  <div class="ml-1 flex flex-col gap-2">
                    <!-- 实际值 -->
                    <div class="flex flex-col gap-1">
                      <div class="response-summary-meta text-sm">实际值:
                        <span class="response-pre font-mono">{{ result.check_value }}</span>
                      </div>
                    </div>

                    <!-- 期望值 -->
                    <div class="flex flex-col gap-1">
                      <div class="response-summary-meta text-sm">期望值:
                        <span class="response-pre font-mono">{{ result.expect_value }}</span>
                      </div>
                    </div>

                    <!-- 错误信息 -->
                    <div v-if="result.message" class="mt-1 text-red-400 text-sm">
                      {{ result.message }}
                    </div>
                  </div>
                </div>
              </div>
              <a-empty v-else description="暂无验证结果" />
            </div>
          </div>
        </a-tab-pane>

        <!-- 提取变量 -->
        <a-tab-pane key="variables" title="提取变量">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.extracted_variables && Object.keys(response.data.extracted_variables).length" class="response-code-shell rounded-lg shadow-inner p-4">
                <div v-for="(value, key) in response.data.extracted_variables" :key="key"
                  class="extracted-item flex flex-col p-3 rounded-md mb-3"
                >
                  <div class="flex items-center">
                    <span class="text-blue-400 font-medium font-mono">${{ key }}</span>
                  </div>
                  <div class="response-pre mt-2 font-mono text-sm break-all">{{ value }}</div>
                </div>
              </div>
              <a-empty v-else description="暂无提取变量" />
            </div>
          </div>
        </a-tab-pane>

        <!-- 完整数据 -->
        <a-tab-pane key="complete" title="完整数据">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data" class="response-code-shell rounded-lg shadow-inner relative group">
                <div
                  class="absolute right-2 top-2 cursor-pointer copy-button"
                  @click="copyContent(completeContent)"
                  title="复制"
                >
                  <icon-copy />
                </div>
                <pre class="response-pre p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ completeContent }}</pre>
              </div>
              <a-empty v-else description="暂无响应数据" />
            </div>
          </div>
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
.api-response {
  --response-summary-border: rgba(148, 163, 184, 0.16);
  --response-summary-text: var(--color-text-3);
  --response-shell-bg: rgba(248, 250, 252, 0.98);
  --response-shell-text: var(--color-text-2);
  --response-tab-border: rgba(148, 163, 184, 0.16);
  --response-tab-text: var(--color-text-3);
  --response-tab-active: rgb(var(--primary-6));
  --response-copy-bg: rgba(241, 245, 249, 0.98);
  --response-copy-hover-bg: rgba(226, 232, 240, 1);
  --response-copy-icon: var(--color-text-2);
  --response-extracted-bg: rgba(248, 250, 252, 0.92);
  --response-extracted-hover-bg: rgba(241, 245, 249, 1);
}

.response-summary {
  border-color: var(--response-summary-border);
}

.response-summary-label,
.response-summary-meta {
  color: var(--response-summary-text);
}

.response-code-shell {
  background: var(--response-shell-bg);
}

.response-pre {
  color: var(--response-shell-text);
}

.extracted-item {
  background: var(--response-extracted-bg);
}

.extracted-item:hover {
  background: var(--response-extracted-hover-bg);
}

:global(body.api-testing-theme) .api-response {
  --response-summary-border: rgb(55, 65, 81);
  --response-summary-text: rgb(156, 163, 175);
  --response-shell-bg: rgba(17, 24, 39, 0.5);
  --response-shell-text: rgb(209, 213, 219);
  --response-tab-border: rgb(55, 65, 81);
  --response-tab-text: rgb(156, 163, 175);
  --response-tab-active: rgb(var(--primary-6));
  --response-copy-bg: rgba(31, 41, 55, 0.82);
  --response-copy-hover-bg: rgba(55, 65, 81, 1);
  --response-copy-icon: rgb(209, 213, 219);
  --response-extracted-bg: rgba(31, 41, 55, 0.3);
  --response-extracted-hover-bg: rgba(31, 41, 55, 0.5);
}

:deep(.arco-tabs) {
  @apply h-full flex flex-col;

  .arco-tabs-content {
    @apply flex-1 min-h-0;
  }

  .arco-tabs-content-item {
    @apply text-left;
  }

  .arco-tabs-header {
    border-bottom: 1px solid var(--response-tab-border);
  }

  .arco-tabs-nav-tab {
    @apply border-b-0;
  }

  .arco-tabs-tab {
    color: var(--response-tab-text);

    &.arco-tabs-tab-active {
      color: var(--response-tab-active);
    }
  }
}

:deep(.arco-tag) {
  &.arco-tag-green {
    @apply bg-green-500/20 text-green-500 border-green-500/20;
  }

  &.arco-tag-red {
    @apply bg-red-500/20 text-red-500 border-red-500/20;
  }
}

:deep(.arco-empty) {
  @apply py-8;
}

:deep(.arco-btn-text) {
  @apply p-2 rounded hover:text-blue-500;
  background: var(--response-copy-bg);
}

/* 新的复制按钮样式 */
.copy-button {
  @apply flex items-center justify-center w-8 h-8 rounded;
  background: var(--response-copy-bg);

  &:hover {
    background: var(--response-copy-hover-bg);
  }

  :deep(svg) {
    @apply w-5 h-5 hover:text-white;
    color: var(--response-copy-icon);
  }
}
</style>
