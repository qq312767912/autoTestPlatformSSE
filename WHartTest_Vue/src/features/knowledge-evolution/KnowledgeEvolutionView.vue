<template>
  <div class="ke-page">
    <header class="ke-header">
      <div>
        <span class="eyebrow">KNOWLEDGE FLYWHEEL</span>
        <h1>反馈与测评</h1>
        <p>追踪检索、生成、反馈与评测，驱动知识资产持续进化。</p>
      </div>
    </header>

    <a-tabs v-model="activeTab" type="rounded" class="ke-tabs">
      <a-tab-pane key="suites" title="评测集">
        <div class="tab-toolbar">
          <a-button type="primary" @click="showSuiteModal = true"><template #icon><icon-plus /></template>新建评测集</a-button>
        </div>
        <a-table :data="suites" :loading="suitesLoading" :columns="suiteColumns" :pagination="false">
          <template #is_active="{ record }">
            <a-tag :color="record.is_active ? 'green' : 'gray'">{{ record.is_active ? '启用' : '停用' }}</a-tag>
          </template>
          <template #split_ratio="{ record }">
            <a-space>
              <a-tag v-for="(v, k) in record.split_ratio" :key="k" size="small">{{ k }}: {{ v }}</a-tag>
            </a-space>
          </template>
          <template #actions="{ record }">
            <a-button type="text" size="mini" @click="selectSuite(record)">查看运行</a-button>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="runs" title="评测运行">
        <div class="tab-toolbar">
          <a-button type="primary" :disabled="!selectedSuite" @click="createRun"><template #icon><icon-play-arrow /></template>运行评测</a-button>
          <a-select v-if="suites.length" v-model="selectedSuiteId" placeholder="选择评测集" style="width: 220px" allow-clear @change="loadRuns">
            <a-option v-for="s in suites" :key="s.id" :value="s.id">{{ s.name }}</a-option>
          </a-select>
        </div>
        <a-table :data="runs" :loading="runsLoading" :columns="runColumns" :pagination="false">
          <template #status="{ record }">
            <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
          </template>
          <template #scores="{ record }">
            <a-space>
              <span class="score-chip" :class="scoreClass(record.l0_score)">L0 {{ fmt(record.l0_score) }}</span>
              <span class="score-chip" :class="scoreClass(record.l1_score)">L1 {{ fmt(record.l1_score) }}</span>
              <span class="score-chip" :class="scoreClass(record.l2_score)">L2 {{ fmt(record.l2_score) }}</span>
              <span class="score-chip" :class="scoreClass(record.l3_score)">L3 {{ fmt(record.l3_score) }}</span>
            </a-space>
          </template>
          <template #actions="{ record }">
            <a-button type="text" size="mini" @click="loadFailedResults(record)">失败样本</a-button>
            <a-button type="text" size="mini" :disabled="record.status !== 'completed'" @click="createCandidates(record)">生成候选</a-button>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="results" title="失败样本">
        <div class="tab-toolbar">
          <a-select v-model="selectedRunId" placeholder="选择评测运行" style="width: 220px" allow-clear @change="loadResults">
            <a-option v-for="r in runs.filter(x => x.status === 'completed')" :key="r.id" :value="r.id">{{ r.suite_name }} @ {{ fmtDate(r.created_at) }}</a-option>
          </a-select>
          <a-button type="primary" :disabled="!selectedRunId" @click="createCandidatesFromSelected"><template #icon><icon-plus /></template>生成候选</a-button>
        </div>
        <a-table :data="failedResults" :loading="resultsLoading" :columns="resultColumns" :pagination="{ pageSize: 10 }">
          <template #status="{ record }">
            <a-tag color="red">{{ record.status }}</a-tag>
          </template>
          <template #scores="{ record }">
            <a-space>
              <span class="score-chip" :class="scoreClass(record.l0_score)">L0 {{ fmt(record.l0_score) }}</span>
              <span class="score-chip" :class="scoreClass(record.l1_score)">L1 {{ fmt(record.l1_score) }}</span>
              <span class="score-chip" :class="scoreClass(record.l2_score)">L2 {{ fmt(record.l2_score) }}</span>
              <span class="score-chip" :class="scoreClass(record.l3_score)">L3 {{ fmt(record.l3_score) }}</span>
            </a-space>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="feedback" title="反馈事件">
        <div class="tab-toolbar">
          <a-select v-model="feedbackSignal" placeholder="信号类型" allow-clear style="width: 160px" @change="loadFeedback">
            <a-option value="accepted">采纳</a-option>
            <a-option value="rejected">驳回</a-option>
            <a-option value="edited">编辑</a-option>
            <a-option value="test_passed">测试通过</a-option>
            <a-option value="test_failed">测试失败</a-option>
            <a-option value="defect_confirmed">缺陷确认</a-option>
            <a-option value="false_positive">误报</a-option>
            <a-option value="missed">漏报</a-option>
          </a-select>
          <a-button @click="loadFeedback"><template #icon><icon-refresh /></template>刷新</a-button>
        </div>
        <a-timeline>
          <a-timeline-item v-for="item in feedbackEvents" :key="item.id" :label="fmtDate(item.created_at)">
            <a-card class="feedback-card" size="small">
              <div class="feedback-title">
                <a-tag :color="signalColor(item.signal)">{{ signalText(item.signal) }}</a-tag>
                <span class="feedback-actor">{{ item.actor?.username || item.actor_type }}</span>
              </div>
              <p v-if="item.comment">{{ item.comment }}</p>
              <p v-else-if="item.detail && item.detail.edit">编辑 diff：{{ JSON.stringify(item.detail.edit).slice(0, 120) }}</p>
              <p v-else>{{ JSON.stringify(item.detail).slice(0, 120) }}</p>
            </a-card>
          </a-timeline-item>
        </a-timeline>
      </a-tab-pane>

      <a-tab-pane key="candidates" title="知识候选">
        <div class="tab-toolbar">
          <a-select v-model="candidateState" placeholder="状态" allow-clear style="width: 160px" @change="loadCandidates">
            <a-option value="pending">待处理</a-option>
            <a-option value="awaiting_approval">待审批</a-option>
            <a-option value="accepted">已通过</a-option>
            <a-option value="rejected">已驳回</a-option>
            <a-option value="conflicted">冲突</a-option>
          </a-select>
          <a-button @click="loadCandidates"><template #icon><icon-refresh /></template>刷新</a-button>
        </div>
        <a-list :data="candidates" :loading="candidatesLoading">
          <template #item="{ item }">
            <a-list-item class="candidate-item">
              <a-list-item-meta :title="candidateTitle(item)">
                <template #description>
                  <div class="candidate-desc">
                    <a-tag>{{ item.kind }}</a-tag>
                    <a-tag size="small">来源：{{ item.origin }}</a-tag>
                    <a-tag :color="candidateStateColor(item.state)">{{ candidateStateText(item.state) }}</a-tag>
                    <span>置信度 {{ (item.confidence * 100).toFixed(0) }}%</span>
                  </div>
                  <p>{{ candidateContent(item) }}</p>
                </template>
              </a-list-item-meta>
              <template #actions>
                <a-button type="text" size="mini" :disabled="item.state !== 'awaiting_approval'" @click="approve(item)">通过</a-button>
                <a-button type="text" status="danger" size="mini" :disabled="item.state !== 'awaiting_approval'" @click="reject(item)">驳回</a-button>
              </template>
            </a-list-item>
          </template>
        </a-list>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:visible="showSuiteModal" title="新建评测集" @ok="confirmCreateSuite" @cancel="showSuiteModal = false">
      <a-form :model="suiteForm" layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model="suiteForm.name" placeholder="例如：代码审查评测集" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model="suiteForm.description" placeholder="评测目标与范围" />
        </a-form-item>
        <a-form-item label="任务类型">
          <a-input v-model="suiteForm.task_type" placeholder="例如：code_review" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="showCandidateModal" title="从失败样本生成候选" @ok="confirmCreateCandidates" @cancel="showCandidateModal = false">
      <a-form :model="candidateForm" layout="vertical">
        <a-form-item label="失败阈值">
          <a-slider v-model="candidateForm.threshold" :min="0" :max="1" :step="0.05" />
        </a-form-item>
        <a-form-item label="最小失败样本数">
          <a-input-number v-model="candidateForm.minFailureCount" :min="1" :max="100" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconPlus, IconPlayArrow, IconRefresh } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import {
  listEvaluationSuites,
  createEvaluationSuite,
  listEvaluationRuns,
  createEvaluationRun,
  listEvaluationResults,
  listFeedbackEvents,
  listKnowledgeCandidates,
  updateCandidateState,
  generateCandidatesFromRun,
} from './service';
import type { EvaluationSuite, EvaluationRun, EvaluationResult, FeedbackEvent, KnowledgeCandidate } from './types';

const projectStore = useProjectStore();
const activeTab = ref('suites');

const suites = ref<EvaluationSuite[]>([]);
const suitesLoading = ref(false);
const showSuiteModal = ref(false);
const suiteForm = ref({ name: '', description: '', task_type: 'code_review' });
const selectedSuite = ref<EvaluationSuite | null>(null);
const selectedSuiteId = ref<string>('');

const runs = ref<EvaluationRun[]>([]);
const runsLoading = ref(false);

const results = ref<EvaluationResult[]>([]);
const resultsLoading = ref(false);
const selectedRunId = ref<string>('');
const failedResults = computed(() => results.value.filter(r => r.status !== 'passed'));

const feedbackEvents = ref<FeedbackEvent[]>([]);
const feedbackSignal = ref<string>('');

const candidates = ref<KnowledgeCandidate[]>([]);
const candidatesLoading = ref(false);
const candidateState = ref<string>('');

const showCandidateModal = ref(false);
const candidateForm = ref({ threshold: 0.5, minFailureCount: 1 });
const candidateRunId = ref<string>('');

const suiteColumns = [
  { title: '名称', dataIndex: 'name' },
  { title: '任务类型', dataIndex: 'task_type' },
  { title: '用例数', dataIndex: 'case_count' },
  { title: 'split', slotName: 'split_ratio' },
  { title: '状态', slotName: 'is_active' },
  { title: '操作', slotName: 'actions' },
];

const runColumns = [
  { title: '评测集', dataIndex: 'suite_name' },
  { title: '状态', slotName: 'status' },
  { title: '策略版本', dataIndex: 'policy_version' },
  { title: '模型', dataIndex: 'model_name' },
  { title: '分数', slotName: 'scores' },
  { title: '成本($)', dataIndex: 'cost_usd' },
  { title: '操作', slotName: 'actions' },
];

const resultColumns = [
  { title: 'ID', dataIndex: 'id', ellipsis: true, tooltip: true },
  { title: '状态', slotName: 'status' },
  { title: '分数', slotName: 'scores' },
  { title: '错误日志', dataIndex: 'error_log', ellipsis: true, tooltip: true },
];

async function loadSuites() {
  if (!projectStore.currentProjectId) return;
  suitesLoading.value = true;
  try {
    suites.value = await listEvaluationSuites(projectStore.currentProjectId);
  } catch (e) {
    Message.error('加载评测集失败');
  } finally {
    suitesLoading.value = false;
  }
}

async function confirmCreateSuite() {
  if (!projectStore.currentProjectId) return;
  try {
    await createEvaluationSuite({ ...suiteForm.value, project_id: projectStore.currentProjectId });
    Message.success('评测集创建成功');
    showSuiteModal.value = false;
    suiteForm.value = { name: '', description: '', task_type: 'code_review' };
    await loadSuites();
  } catch (e) {
    Message.error('创建评测集失败');
  }
}

function selectSuite(suite: EvaluationSuite) {
  selectedSuite.value = suite;
  selectedSuiteId.value = suite.id;
  activeTab.value = 'runs';
  loadRuns();
}

async function loadRuns() {
  runsLoading.value = true;
  try {
    runs.value = await listEvaluationRuns(selectedSuiteId.value || undefined);
  } catch (e) {
    Message.error('加载评测运行失败');
  } finally {
    runsLoading.value = false;
  }
}

async function createRun() {
  if (!selectedSuite.value) return;
  try {
    await createEvaluationRun({
      suite: selectedSuite.value.id,
      policy_version: 'default',
      model_name: 'default',
    });
    Message.success('评测运行已启动');
    await loadRuns();
  } catch (e) {
    Message.error('启动评测运行失败');
  }
}

async function loadResults() {
  if (!selectedRunId.value) return;
  resultsLoading.value = true;
  try {
    results.value = await listEvaluationResults(selectedRunId.value);
  } catch (e) {
    Message.error('加载失败样本失败');
  } finally {
    resultsLoading.value = false;
  }
}

async function loadFailedResults(run: EvaluationRun) {
  selectedRunId.value = run.id;
  activeTab.value = 'results';
  await loadResults();
}

async function loadFeedback() {
  try {
    const res = await listFeedbackEvents({
      project: projectStore.currentProjectId || undefined,
      signal: feedbackSignal.value || undefined,
    });
    feedbackEvents.value = res.results;
  } catch (e) {
    Message.error('加载反馈事件失败');
  }
}

async function loadCandidates() {
  if (!projectStore.currentProjectId) return;
  candidatesLoading.value = true;
  try {
    const res = await listKnowledgeCandidates({
      project: projectStore.currentProjectId,
      state: candidateState.value || undefined,
    });
    candidates.value = res.results;
  } catch (e) {
    Message.error('加载知识候选失败');
  } finally {
    candidatesLoading.value = false;
  }
}

async function approve(item: KnowledgeCandidate) {
  try {
    await updateCandidateState(item.id, 'accepted', '前端审批通过');
    Message.success('已通过');
    await loadCandidates();
  } catch (e) {
    Message.error('审批失败');
  }
}

async function reject(item: KnowledgeCandidate) {
  try {
    await updateCandidateState(item.id, 'rejected', '前端手动驳回');
    Message.success('已驳回');
    await loadCandidates();
  } catch (e) {
    Message.error('驳回失败');
  }
}

function createCandidates(run: EvaluationRun) {
  candidateRunId.value = run.id;
  showCandidateModal.value = true;
}

function createCandidatesFromSelected() {
  if (!selectedRunId.value) return;
  candidateRunId.value = selectedRunId.value;
  showCandidateModal.value = true;
}

async function confirmCreateCandidates() {
  try {
    const res = await generateCandidatesFromRun(candidateRunId.value, {
      thresholds: { l0: candidateForm.value.threshold, l1: candidateForm.value.threshold },
      min_failure_count: candidateForm.value.minFailureCount,
    });
    Message.success(`已生成 ${res.created_count} 个候选`);
    showCandidateModal.value = false;
    activeTab.value = 'candidates';
    await loadCandidates();
  } catch (e) {
    Message.error('生成候选失败');
  }
}

function fmt(v?: number) {
  return v === undefined || v === null ? '-' : v.toFixed(2);
}

function fmtDate(v?: string) {
  return v ? new Date(v).toLocaleString('zh-CN') : '-';
}

function statusText(s: string) {
  const map: Record<string, string> = { pending: '待执行', running: '运行中', completed: '已完成', failed: '失败' };
  return map[s] || s;
}

function statusColor(s: string) {
  const map: Record<string, string> = { pending: 'orange', running: 'blue', completed: 'green', failed: 'red' };
  return map[s] || 'gray';
}

function scoreClass(v?: number) {
  if (v === undefined || v === null) return '';
  if (v >= 0.8) return 'score-high';
  if (v >= 0.5) return 'score-mid';
  return 'score-low';
}

function signalText(s: string) {
  const map: Record<string, string> = {
    accepted: '采纳', rejected: '驳回', edited: '编辑', test_passed: '测试通过', test_failed: '测试失败',
    defect_confirmed: '缺陷确认', false_positive: '误报', missed: '漏报', merged: '已合并', reverted: '已回退',
  };
  return map[s] || s;
}

function signalColor(s: string) {
  const map: Record<string, string> = {
    accepted: 'green', rejected: 'red', edited: 'blue', test_passed: 'green', test_failed: 'red',
    defect_confirmed: 'red', false_positive: 'orange', missed: 'orange', merged: 'purple', reverted: 'gray',
  };
  return map[s] || 'gray';
}

function candidateStateText(s: string) {
  const map: Record<string, string> = {
    pending: '待处理', conflicted: '冲突', evaluating: '评测中', awaiting_approval: '待审批',
    accepted: '已通过', rejected: '已驳回', merged: '已合并',
  };
  return map[s] || s;
}

function candidateStateColor(s: string) {
  const map: Record<string, string> = {
    pending: 'orange', conflicted: 'red', evaluating: 'blue', awaiting_approval: 'cyan',
    accepted: 'green', rejected: 'red', merged: 'purple',
  };
  return map[s] || 'gray';
}

function candidateTitle(item: KnowledgeCandidate): string {
  const p = item.payload || {};
  return String(p.title || p.name || p.statement || `${item.kind}-${item.id.slice(0, 8)}`);
}

function candidateContent(item: KnowledgeCandidate): string {
  const p = item.payload || {};
  const text = String(p.content || p.description || p.summary || JSON.stringify(p));
  return text.slice(0, 200) + (text.length > 200 ? '...' : '');
}

onMounted(() => {
  loadSuites();
  loadFeedback();
  loadCandidates();
});
</script>

<style scoped>
.ke-page { min-height: calc(100vh - 58px); padding: 22px; background: #f2f4f7; }
.ke-header { margin-bottom: 16px; }
.ke-header h1 { margin: 4px 0; font-size: 28px; }
.ke-header p { margin: 0; color: #4e5969; }
.eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 0.14em; color: #86909c; }
.ke-tabs { background: #fff; padding: 16px; border-radius: 4px; }
.tab-toolbar { display: flex; gap: 12px; margin-bottom: 16px; }
.score-chip { padding: 2px 8px; border-radius: 4px; font-size: 12px; background: #f2f4f7; }
.score-high { background: #e8ffea; color: #00b42a; }
.score-mid { background: #fff7e8; color: #ff7d00; }
.score-low { background: #ffe8e8; color: #f53f3f; }
.feedback-card { margin-bottom: 8px; }
.feedback-title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.feedback-actor { color: #86909c; font-size: 12px; margin-left: auto; }
.candidate-item { padding: 12px; }
.candidate-desc { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
</style>
