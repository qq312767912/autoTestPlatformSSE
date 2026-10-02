<template>
  <a-drawer :visible="visible" :width="440" unmount-on-close @cancel="close">
    <template #title>
      <span class="sidebar-title">质量反馈 · {{ title || displayId }}</span>
    </template>
    <a-spin :loading="loading" style="width:100%">
      <a-empty v-if="!outputs.length && !loading" description="尚未生成飞轮产出记录" />
      <div v-else class="quality-body">
        <section v-for="output in outputs" :key="output.id" class="output-card">
          <div class="output-head">
            <a-tag color="arcoblue">{{ output.task_type }}</a-tag>
            <span class="hash">{{ output.output_hash.slice(0, 8) }}</span>
            <a-tag :color="output.trace_id ? 'green' : 'gray'" size="small">{{ output.trace_id ? '已关联轨迹' : '无轨迹' }}</a-tag>
          </div>

          <div class="action-bar">
            <a-button size="small" type="primary" @click="submit(output, 'accepted')">采纳</a-button>
            <a-button size="small" status="danger" @click="submit(output, 'rejected')">驳回</a-button>
            <a-button size="small" status="warning" @click="submit(output, 'false_positive')">误报</a-button>
            <a-button size="small" status="warning" @click="submit(output, 'missed')">漏报</a-button>
            <a-button size="small" @click="submit(output, 'defect_confirmed')">缺陷确认</a-button>
          </div>

          <div v-if="feedbackForOutput(output.id).length" class="feedback-list">
            <h4>历史反馈</h4>
            <article v-for="item in feedbackForOutput(output.id)" :key="item.id" class="feedback-item">
              <a-tag :color="signalColor(item.signal)" size="small">{{ signalText(item.signal) }}</a-tag>
              <span class="reason">{{ item.comment || item.reason_code || '无备注' }}</span>
              <small>{{ formatDate(item.created_at) }}</small>
            </article>
          </div>

          <div v-if="attributionsForOutput(output.id).length" class="attribution-list">
            <h4>失败归因</h4>
            <article v-for="item in attributionsForOutput(output.id)" :key="item.id" class="attribution-item">
              <header>
                <a-tag :color="item.source === 'rule' ? 'green' : 'blue'" size="small">{{ item.source === 'rule' ? '确定性' : 'LLM辅助' }}</a-tag>
                <a-tag :color="stateColor(item.state)" size="small">{{ stateText(item.state) }}</a-tag>
              </header>
              <b>{{ categoryText(item.category) }}</b>
              <p>{{ item.hypothesis }}</p>
              <small>置信度 {{ Math.round(item.confidence * 100) }}%</small>
            </article>
          </div>
        </section>

        <div class="footer-link">
          <a-link @click="goToEvolution">在质量飞轮中查看详情</a-link>
        </div>
      </div>
    </a-spin>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useRouter } from 'vue-router';
import {
  getGenerationOutputsByTask,
  listFeedbackEvents,
  listFailureAttributionsForOutput,
  submitFeedbackOutcome,
} from '../service';
import type { FailureAttribution, FeedbackEvent, GenerationOutput } from '../types';

const props = defineProps<{
  visible: boolean;
  taskType: string;
  taskId: string | number;
  projectId: number;
  title?: string;
}>();

const emit = defineEmits<{ (e: 'update:visible', value: boolean): void }>();

const router = useRouter();
const loading = ref(false);
const outputs = ref<GenerationOutput[]>([]);
const feedbackEvents = ref<FeedbackEvent[]>([]);
const attributions = ref<FailureAttribution[]>([]);

const displayId = computed(() => String(props.taskId).slice(0, 12));

function close() {
  emit('update:visible', false);
}

function feedbackForOutput(outputId: string) {
  return feedbackEvents.value.filter(item => item.output === outputId);
}

function attributionsForOutput(outputId: string) {
  return attributions.value.filter(item => item.output === outputId);
}

const signalText = (v: string) => ({
  accepted: '采纳', rejected: '驳回', edited: '编辑', test_passed: '测试通过',
  test_failed: '测试失败', defect_confirmed: '缺陷确认', false_positive: '误报',
  missed: '漏报', merged: '已合并', reverted: '已回退',
} as Record<string, string>)[v] || v;

const signalColor = (v: string) => {
  if (['accepted', 'test_passed', 'merged'].includes(v)) return 'green';
  if (['rejected', 'test_failed', 'defect_confirmed'].includes(v)) return 'red';
  if (['false_positive', 'missed'].includes(v)) return 'orange';
  return 'blue';
};

const stateText = (v: string) => ({ proposed: '待确认', confirmed: '已确认', rejected: '已排除' } as Record<string, string>)[v] || v;
const stateColor = (v: string) => ({ proposed: 'orange', confirmed: 'green', rejected: 'red' } as Record<string, string>)[v] || 'gray';
const categoryText = (v: string) => ({
  intent_error: '意图理解失败', planning_error: '复杂规划失败', knowledge_missing: '知识缺失',
  knowledge_stale: '知识过期', retrieval_error: '检索失败', prompt_error: 'Prompt失败',
  tool_error: '工具失败', generation_error: '生成失败', downstream_execution_error: '下游执行失败',
} as Record<string, string>)[v] || v;

function formatDate(v?: string) {
  return v ? new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
}

async function load() {
  if (!props.visible || !props.projectId || !props.taskId) return;
  loading.value = true;
  try {
    outputs.value = await getGenerationOutputsByTask(props.projectId, props.taskType, String(props.taskId));
    if (outputs.value.length) {
      const outputIds = outputs.value.map(o => o.id);
      const [feedback, attrs] = await Promise.all([
        listFeedbackEvents({ project: props.projectId }),
        Promise.all(outputIds.map(id => listFailureAttributionsForOutput(id))),
      ]);
      feedbackEvents.value = feedback.filter(item => item.output && outputIds.includes(item.output));
      attributions.value = attrs.flat();
    } else {
      feedbackEvents.value = [];
      attributions.value = [];
    }
  } catch (error: any) {
    Message.error(error?.message || '加载质量数据失败');
  } finally {
    loading.value = false;
  }
}

async function submit(output: GenerationOutput, signal: FeedbackEvent['signal']) {
  try {
    await submitFeedbackOutcome({
      output_id: output.id,
      signal,
      comment: signal === 'rejected' ? '业务侧驳回该产出' : undefined,
    });
    Message.success('反馈已提交');
    await load();
  } catch (error: any) {
    Message.error(error?.message || '提交反馈失败');
  }
}

function goToEvolution() {
  router.push('/knowledge-evolution');
  close();
}

watch(() => [props.visible, props.taskId], () => load(), { immediate: true });
</script>

<style scoped>
.sidebar-title { font-weight: 500; font-size: 15px; }
.quality-body { padding: 4px 0 16px; }
.output-card { border: 1px solid var(--color-border-2); border-radius: 10px; padding: 14px; margin-bottom: 14px; background: var(--color-bg-2); }
.output-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.output-head .hash { font-family: monospace; color: var(--color-text-3); font-size: 12px; }
.action-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.feedback-list, .attribution-list { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--color-border-2); }
.feedback-list h4, .attribution-list h4 { margin: 0 0 8px; font-size: 13px; color: var(--color-text-2); }
.feedback-item { display: flex; align-items: center; gap: 8px; padding: 7px 0; font-size: 13px; }
.feedback-item .reason { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.attribution-item { padding: 9px; border-radius: 8px; background: var(--color-fill-1); margin-bottom: 8px; }
.attribution-item header { display: flex; gap: 8px; margin-bottom: 6px; }
.attribution-item b { font-size: 13px; }
.attribution-item p { margin: 6px 0; color: var(--color-text-2); font-size: 13px; line-height: 1.5; }
.attribution-item small { color: var(--color-text-3); }
.footer-link { text-align: center; margin-top: 8px; }
</style>
