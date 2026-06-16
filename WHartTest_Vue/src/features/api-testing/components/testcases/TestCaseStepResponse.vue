<script setup lang="ts">
import { ref, computed } from 'vue'
import { useClipboard } from '@vueuse/core'
import { Message } from '@arco-design/web-vue'
import { IconCopy } from '@arco-design/web-vue/es/icon'
import { useAppI18n } from '@/composables/useAppI18n'

interface ResponseData {
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
  response: ResponseData | null
}

const props = defineProps<Props>()
const { copy } = useClipboard()
const { tl } = useAppI18n()

const copyContent = async (content: string) => {
  await copy(content)
  Message.success(tl('复制成功'))
}

const responseContent = computed(() => {
  const content = props.response?.data?.response?.content
  if (!content) return ''
  if (typeof content === 'object') return JSON.stringify(content, null, 2)
  return content
})

const requestContent = computed(() => {
  if (!props.response?.data?.request) return ''
  return JSON.stringify(props.response.data.request, null, 2)
})

const responseHeadersContent = computed(() => {
  if (!props.response?.data?.response?.headers) return ''
  return JSON.stringify(props.response.data.response.headers, null, 2)
})

const statusCode = computed(() => props.response?.data?.response?.status_code)

const completeContent = computed(() => {
  if (!props.response) return ''
  return JSON.stringify(props.response, null, 2)
})

const responseActiveTab = ref('response')
</script>

<template>
  <div v-if="response" class="testcase-step-response h-full flex flex-col">
    <!-- 顶部响应概要 -->
    <div class="response-summary flex items-center gap-4 px-4 pt-4 pb-2 border-t border-b">
      <div class="response-summary-text">{{ tl('响应内容') }}</div>
      <div class="flex-1"></div>
      <div class="flex items-center gap-4 flex-shrink-0">
        <a-tag v-if="statusCode" :color="statusCode === 200 ? 'green' : 'red'" class="w-10 flex justify-center items-center">
          {{ statusCode }}
        </a-tag>
        <span v-if="response.time" class="response-summary-text">{{ response.time.toFixed(3) }} ms</span>
        <span v-if="response.size" class="response-summary-text">{{ response.size }} bytes</span>
      </div>
    </div>

    <!-- 响应内容页签 -->
    <div class="flex-1 overflow-hidden">
      <a-tabs v-model:active-key="responseActiveTab" class="h-full">
        <a-tab-pane key="response" :title="tl('响应体')">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.response?.content" class="response-code-block rounded-lg shadow-inner relative group">
                <div class="copy-button" @click="copyContent(responseContent)" :title="tl('复制')">
                  <icon-copy />
                </div>
                <pre class="response-code-text p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ responseContent }}</pre>
              </div>
              <a-empty v-else :description="tl('暂无响应数据')" />
            </div>
          </div>
        </a-tab-pane>

        <a-tab-pane key="headers" :title="tl('响应头')">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.response?.headers" class="response-code-block rounded-lg shadow-inner relative group">
                <div class="copy-button" @click="copyContent(responseHeadersContent)" :title="tl('复制')">
                  <icon-copy />
                </div>
                <pre class="response-code-text p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ responseHeadersContent }}</pre>
              </div>
              <a-empty v-else :description="tl('暂无响应头数据')" />
            </div>
          </div>
        </a-tab-pane>

        <a-tab-pane key="request" :title="tl('请求信息')">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.request" class="response-code-block rounded-lg shadow-inner relative group">
                <div class="copy-button" @click="copyContent(requestContent)" :title="tl('复制')">
                  <icon-copy />
                </div>
                <pre class="response-code-text p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ requestContent }}</pre>
              </div>
              <a-empty v-else :description="tl('暂无请求数据')" />
            </div>
          </div>
        </a-tab-pane>

        <a-tab-pane key="validation" :title="tl('验证结果')">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.validation_results?.length" class="response-code-block rounded-lg shadow-inner p-4">
                <div v-for="(result, index) in response.data.validation_results" :key="index"
                  class="flex flex-col p-3 rounded-md mb-3"
                  :class="{'bg-green-900/10': result.check_result === 'pass', 'bg-red-900/10': result.check_result !== 'pass'}"
                >
                  <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                      <a-tag :color="result.check_result === 'pass' ? 'green' : 'red'" class="!font-medium !flex-shrink-0">
                        {{ result.check_result === 'pass' ? tl('通过') : tl('失败') }}
                      </a-tag>
                      <span class="response-code-text">{{ result.comparator }}: {{ result.check }}</span>
                    </div>
                  </div>
                  <div class="ml-1 flex flex-col gap-2">
                    <div class="flex flex-col gap-1">
                      <div class="response-summary-text text-sm">{{ tl('实际值:') }}
                        <span class="response-code-text font-mono">{{ result.check_value }}</span>
                      </div>
                    </div>
                    <div class="flex flex-col gap-1">
                      <div class="response-summary-text text-sm">{{ tl('期望值:') }}
                        <span class="response-code-text font-mono">{{ result.expect_value }}</span>
                      </div>
                    </div>
                    <div v-if="result.message" class="mt-1 text-red-400 text-sm">{{ result.message }}</div>
                  </div>
                </div>
              </div>
              <a-empty v-else :description="tl('暂无验证结果')" />
            </div>
          </div>
        </a-tab-pane>

        <a-tab-pane key="variables" :title="tl('提取变量')">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response.data?.extracted_variables" class="response-code-block rounded-lg shadow-inner p-4">
                <div v-for="(value, key) in response.data.extracted_variables" :key="key"
                  class="response-var-item flex flex-col p-3 rounded-md mb-3"
                >
                  <div class="flex items-center">
                    <span class="text-blue-400 font-medium font-mono">${{ key }}</span>
                  </div>
                  <div class="response-code-text mt-2 font-mono text-sm break-all">{{ value }}</div>
                </div>
              </div>
              <a-empty v-else :description="tl('暂无提取变量')" />
            </div>
          </div>
        </a-tab-pane>

        <a-tab-pane key="complete" :title="tl('完整数据')">
          <div class="h-full overflow-auto text-left">
            <div class="p-4">
              <div v-if="response" class="response-code-block rounded-lg shadow-inner relative group">
                <div class="copy-button" @click="copyContent(completeContent)" :title="tl('复制')">
                  <icon-copy />
                </div>
                <pre class="response-code-text p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-all text-left">{{ completeContent }}</pre>
              </div>
              <a-empty v-else :description="tl('暂无响应数据')" />
            </div>
          </div>
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>

  <!-- 无响应时的提示 -->
  <div v-else class="testcase-step-response h-full flex flex-col">
    <div class="response-summary flex items-center gap-4 px-4 pt-4 pb-2 border-t border-b">
      <div class="response-summary-text">{{ tl('响应内容') }}</div>
    </div>
    <div class="flex-1 flex items-center justify-center">
      <div class="response-empty flex flex-col items-center justify-center">
        <div class="w-16 h-16 mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1Z" />
          </svg>
        </div>
        <div class="text-base">{{ tl('暂无响应数据') }}</div>
        <div class="text-sm mt-2">{{ tl('点击调试按钮发送请求') }}</div>
      </div>
    </div>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
.testcase-step-response {
  color: var(--tcf-text-muted);
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
    border-bottom: 1px solid var(--tcf-panel-border) !important;
  }

  .arco-tabs-nav-tab {
    @apply border-b-0;
  }

  .arco-tabs-tab {
    color: var(--tcf-text-subtle) !important;

    &.arco-tabs-tab-active {
      color: rgb(59, 130, 246) !important;
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
  background: var(--tcf-control-bg) !important;
  color: var(--tcf-text-muted) !important;
  @apply p-2 rounded;

  &:hover {
    background: rgba(59, 130, 246, 0.12) !important;
    color: rgb(59, 130, 246) !important;
  }
}

/* 复制按钮样式 */
.copy-button {
  @apply opacity-0 transition-opacity duration-300;
  background: var(--tcf-control-bg) !important;
  color: var(--tcf-text-muted) !important;
  @apply flex items-center justify-center w-8 h-8 rounded;

  &:hover {
    background: rgba(59, 130, 246, 0.12) !important;
    color: rgb(59, 130, 246) !important;
  }

  :deep(svg) {
    @apply w-5 h-5;
    color: var(--tcf-text-muted) !important;
  }
}

.response-summary {
  border-color: var(--tcf-panel-border) !important;
}

.response-summary-text,
.response-empty {
  color: var(--tcf-text-subtle) !important;
}

.response-code-block {
  background: var(--tcf-control-bg) !important;
  border: 1px solid var(--tcf-control-border) !important;
}

.response-code-text {
  color: var(--tcf-text-muted) !important;
}

.response-var-item {
  background: var(--tcf-section-bg) !important;

  &:hover {
    background: var(--tcf-section-hover) !important;
  }
}

/* 当鼠标悬停在代码区域上时显示复制按钮 */
.group:hover .copy-button {
  @apply opacity-100;
}
</style>
