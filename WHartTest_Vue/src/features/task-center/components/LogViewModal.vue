<template>
  <a-modal
    v-model:visible="visible"
    :title="modalText.title"
    :width="660"
    :footer="false"
    :mask-closable="true"
  >
    <a-spin :loading="loading">
      <div class="log-info" v-if="logData">
        <div class="log-meta">
          <a-descriptions :column="3" size="small" bordered>
            <a-descriptions-item :label="modalText.executionId">{{ logData.execution_id }}</a-descriptions-item>
            <a-descriptions-item :label="modalText.status">
              <a-tag :color="logData.status === 'success' ? 'green' : logData.status === 'failed' ? 'red' : 'blue'">
                {{ statusMap[logData.status] || logData.status }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item :label="modalText.duration">{{ logData.duration }}</a-descriptions-item>
          </a-descriptions>
        </div>
        <div class="log-content">
          <div class="log-section-title">{{ modalText.logSection }}</div>
          <pre class="log-text">{{ logData.log || modalText.noLog }}</pre>
        </div>
        <div v-if="logData.error_message" class="log-content error">
          <div class="log-section-title">{{ modalText.errorSection }}</div>
          <pre class="log-text error-text">{{ logData.error_message }}</pre>
        </div>
      </div>
    </a-spin>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useAppI18n } from '@/composables/useAppI18n';
import { getExecutionLog } from '../services/taskService';

const props = defineProps<{
  projectId: number;
}>();

const { isEnglish } = useAppI18n();

const modalText = computed(() => (
  isEnglish.value
    ? {
        title: 'Execution Log',
        executionId: 'Execution ID',
        status: 'Status',
        duration: 'Duration',
        logSection: 'Execution Log',
        noLog: 'No log available',
        errorSection: 'Error Message',
        running: 'Running',
        success: 'Success',
        failed: 'Failed',
        loadLogFailed: 'Failed to load log',
      }
    : {
        title: '执行日志',
        executionId: '执行ID',
        status: '状态',
        duration: '耗时',
        logSection: '执行日志',
        noLog: '暂无日志',
        errorSection: '错误信息',
        running: '执行中',
        success: '成功',
        failed: '失败',
        loadLogFailed: '加载日志失败',
      }
));

const visible = ref(false);
const loading = ref(false);
const logData = ref<{
  execution_id: string;
  log: string;
  error_message: string;
  status: string;
  duration: string;
} | null>(null);

const statusMap = computed<Record<string, string>>(() => ({
  running: modalText.value.running,
  success: modalText.value.success,
  failed: modalText.value.failed,
}));

const open = async (executionId: number) => {
  visible.value = true;
  loading.value = true;
  try {
    logData.value = await getExecutionLog(props.projectId, executionId);
  } catch {
    Message.error(modalText.value.loadLogFailed);
  } finally {
    loading.value = false;
  }
};

defineExpose({ open });
</script>

<style scoped>
.log-info {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.log-meta {
  text-align: center;
  margin-bottom: 4px;
}

.log-meta :deep(.arco-descriptions) {
  display: inline-table;
  width: auto;
}

.log-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-2);
  margin-bottom: 8px;
}

.log-text {
  background: var(--color-fill-2);
  border-radius: 6px;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
}

.error-text {
  color: var(--color-danger-6);
  background: var(--color-danger-1);
}
</style>
