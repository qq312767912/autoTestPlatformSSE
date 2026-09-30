<template>
  <div class="ke-page">
    <!-- 未选择项目时的平台标准空态 -->
    <div v-if="!projectStore.currentProjectId" class="ke-no-project">
      <a-empty description="请先在顶部选择项目，再使用反馈与测评" />
    </div>

    <template v-else>
      <!-- 页头 -->
      <header class="ke-header">
        <div class="ke-header-title">
          <h2>
            <icon-loop />
            反馈与测评
          </h2>
          <p class="ke-header-desc">知识数据飞轮 · 人机协作工作台（AI 执行，关键节点人工决策）</p>
        </div>
        <div class="ke-header-actions">
          <a-tag color="blue" size="small">L3 模式</a-tag>
          <a-tag size="small">
            <icon-storage />
            {{ projectName }}
          </a-tag>
          <a-button size="small" @click="showOverview = true">
            <template #icon><icon-apps /></template>
            任务概览
          </a-button>
        </div>
      </header>

      <!-- 阶段导航条 -->
      <nav class="ke-stagebar">
        <template v-for="(s, i) in stageNav" :key="s.key">
          <button
            type="button"
            class="ke-stagechip"
            :class="{ 'is-current': currentStage === i + 1 }"
            @click="currentStage = i + 1"
          >
            <span class="ke-chip-no">{{ i + 1 }}</span>
            <span class="ke-chip-label">{{ s.name }}</span>
            <a-tag :color="s.tagColor" size="small">{{ s.badge }}</a-tag>
          </button>
          <span v-if="i < stageNav.length - 1" class="ke-chip-arrow">
            <icon-right />
          </span>
        </template>
      </nav>

      <div class="ke-body">
        <!-- 左栏：评测集 -->
        <aside class="ke-rail">
          <div class="ke-rail-head">
            <span>评测集</span>
            <a-button size="mini" type="text" @click="showSuiteModal = true">
              <template #icon><icon-plus /></template>
              新建
            </a-button>
          </div>
          <div class="ke-rail-list">
            <button
              v-for="s in suites"
              :key="s.id"
              type="button"
              class="ke-suite"
              :class="{ 'is-active': selectedSuiteId === s.id }"
              @click="selectSuite(s)"
            >
              <span class="ke-suite-name">{{ s.name }}</span>
              <span class="ke-suite-meta">{{ s.case_count }} 用例 · {{ s.is_active ? '启用' : '停用' }}</span>
            </button>
            <p v-if="suitesLoading" class="ke-empty">加载中…</p>
            <p v-else-if="!suites.length" class="ke-empty">暂无评测集，点击上方新建</p>
          </div>
          <div class="ke-rail-foot">
            <span class="ke-live-dot"></span>
            <span>{{ suites.length }} 个评测集 · {{ runs.length }} 次运行</span>
          </div>
        </aside>

        <!-- 中栏：阶段时间轴 -->
        <main class="ke-flow">
          <!-- 阶段 1：反馈采集 -->
          <section class="ke-card" :class="{ 'is-current': currentStage === 1 }">
            <header class="ke-card-head">
              <span class="ke-card-no">1</span>
              <h3>反馈采集阶段</h3>
              <a-tag :color="feedbackEvents.length ? 'orange' : 'gray'" size="small">
                {{ feedbackEvents.length ? '待人工确认' : '等待中' }}
              </a-tag>
              <span class="ke-card-time">{{ stageTime.feedback }}</span>
            </header>
            <div class="ke-card-body">
              <div class="ke-pane">
                <div class="ke-pane-title">
                  <a-tag color="purple" size="small">AI 生成内容</a-tag>
                  <a-select
                    v-model="feedbackSignal"
                    size="small"
                    class="ke-select"
                    @change="loadFeedback"
                  >
                    <a-option value="">全部信号</a-option>
                    <a-option v-for="(t, k) in signalLabels" :key="k" :value="k">{{ t }}</a-option>
                  </a-select>
                </div>
                <ul class="ke-list">
                  <li v-for="f in feedbackEvents.slice(0, 6)" :key="f.id">
                    <a-tag :color="signalTagColor(f.signal)" size="small">{{ signalText(f.signal) }}</a-tag>
                    <span class="ke-list-text">{{ feedbackSummary(f) }}</span>
                    <em class="ke-list-meta">{{ f.actor?.username || f.actor_type }}</em>
                  </li>
                </ul>
                <p v-if="!feedbackEvents.length" class="ke-empty">暂无反馈信号</p>
                <div class="ke-pane-foot">
                  <span>共 {{ feedbackEvents.length }} 条信号</span>
                  <a-button size="mini" type="text" @click="currentStage = 1">查看全部</a-button>
                </div>
              </div>
              <div class="ke-pane ke-pane-human">
                <div class="ke-pane-title"><span class="ke-pane-label">人工确认</span></div>
                <p class="ke-human-tip">请确认反馈归集结果，确认后进入评测运行阶段</p>
                <div class="ke-human-stat">
                  <span class="ke-stat-num">{{ feedbackEvents.length }}</span>
                  <span class="ke-stat-label">条待归集信号</span>
                </div>
                <a-button size="small" type="primary" long @click="confirmStage(1)">
                  <template #icon><icon-check /></template>
                  确认归集结果
                </a-button>
                <span class="ke-human-note">待确认</span>
              </div>
            </div>
            <footer class="ke-card-foot">
              <span class="ke-foot-label">流转控制</span>
              <a-button size="small" @click="currentStage = 1">返回修改</a-button>
              <a-button size="small" type="primary" @click="confirmStage(1)">确认通过，进入下一阶段</a-button>
            </footer>
          </section>

          <!-- 阶段 2：评测运行 -->
          <section class="ke-card" :class="{ 'is-current': currentStage === 2 }">
            <header class="ke-card-head">
              <span class="ke-card-no">2</span>
              <h3>评测运行阶段</h3>
              <a-tag :color="completedRuns.length ? 'blue' : 'gray'" size="small">
                {{ completedRuns.length ? '待人工采纳' : '等待中' }}
              </a-tag>
              <span class="ke-card-time">{{ stageTime.run }}</span>
            </header>
            <div class="ke-card-body">
              <div class="ke-pane">
                <div class="ke-pane-title">
                  <a-tag color="purple" size="small">AI 生成内容</a-tag>
                  <span class="ke-pane-sub">{{ runs.length }} 次评测运行</span>
                </div>
                <ul class="ke-list ke-list-clickable">
                  <li
                    v-for="r in runs.slice(0, 4)"
                    :key="r.id"
                    :class="{ 'is-active': selectedRunId === r.id }"
                    @click="selectRun(r)"
                  >
                    <span class="ke-list-text">{{ r.suite_name || selectedSuite?.name || '评测集' }}</span>
                    <a-tag :color="statusTagColor(r.status)" size="small">{{ statusText(r.status) }}</a-tag>
                    <span class="ke-score-chips">
                      <a-tag :color="scoreTagColor(r.l0_score)" size="small">L0 {{ fmt(r.l0_score) }}</a-tag>
                      <a-tag :color="scoreTagColor(r.l1_score)" size="small">L1 {{ fmt(r.l1_score) }}</a-tag>
                      <a-tag :color="scoreTagColor(r.l2_score)" size="small">L2 {{ fmt(r.l2_score) }}</a-tag>
                      <a-tag :color="scoreTagColor(r.l3_score)" size="small">L3 {{ fmt(r.l3_score) }}</a-tag>
                    </span>
                  </li>
                </ul>
                <p v-if="!runs.length" class="ke-empty">暂无评测运行，请在右侧发起</p>
                <div class="ke-pane-foot">
                  <span>成本 ${{ totalCost }}</span>
                  <a-button size="mini" type="text" @click="selectRun(runs[0])">查看执行详情</a-button>
                </div>
              </div>
              <div class="ke-pane ke-pane-human">
                <div class="ke-pane-title"><span class="ke-pane-label">人工采纳</span></div>
                <p class="ke-human-tip">选择一个评测集，发起评测运行</p>
                <a-select
                  v-model="selectedSuiteId"
                  size="small"
                  class="ke-select-block"
                  placeholder="选择评测集"
                  @change="loadRuns"
                >
                  <a-option v-for="s in suites" :key="s.id" :value="s.id">{{ s.name }}</a-option>
                </a-select>
                <div class="ke-human-stat">
                  <span class="ke-stat-num">{{ completedRuns.length }}/{{ runs.length }}</span>
                  <span class="ke-stat-label">已完成运行</span>
                </div>
                <a-button size="small" type="primary" long :disabled="!selectedSuiteId" @click="createRun">
                  <template #icon><icon-play-arrow /></template>
                  运行评测
                </a-button>
              </div>
            </div>
            <footer class="ke-card-foot">
              <span class="ke-foot-label">流转控制</span>
              <a-button size="small" @click="currentStage = 1">返回修改</a-button>
              <a-button
                size="small"
                type="primary"
                :disabled="!completedRuns.length"
                @click="confirmStage(2)"
              >
                确认采纳，进入下一阶段
              </a-button>
            </footer>
          </section>

          <!-- 阶段 3：失败样本分析 -->
          <section class="ke-card" :class="{ 'is-current': currentStage === 3 }">
            <header class="ke-card-head">
              <span class="ke-card-no">3</span>
              <h3>失败样本分析阶段</h3>
              <a-tag :color="failedResults.length ? 'blue' : 'gray'" size="small">
                {{ failedResults.length ? '待人工采纳' : '等待中' }}
              </a-tag>
              <span class="ke-card-time">{{ stageTime.failure }}</span>
            </header>
            <div class="ke-card-body">
              <div class="ke-pane">
                <div class="ke-pane-title">
                  <a-tag color="purple" size="small">AI 分析内容</a-tag>
                  <span class="ke-pane-sub">{{ failedResults.length }} 个失败样本</span>
                </div>
                <div class="ke-progress">
                  <a-progress
                    :percent="failureRate"
                    size="small"
                    status="danger"
                    :show-text="false"
                  />
                  <div class="ke-progress-meta">
                    <span>失败率 {{ failureRate }}%</span>
                    <span>样本 {{ results.length }}</span>
                    <span>失败 {{ failedResults.length }}</span>
                  </div>
                </div>
                <ul class="ke-list">
                  <li v-for="r in failedResults.slice(0, 4)" :key="r.id">
                    <a-tag color="red" size="small">失败</a-tag>
                    <span class="ke-list-text">{{ (r.error_log || r.id).slice(0, 56) }}</span>
                    <em class="ke-list-meta">L0 {{ fmt(r.l0_score) }}</em>
                  </li>
                </ul>
                <p v-if="!failedResults.length" class="ke-empty">暂无失败样本</p>
              </div>
              <div class="ke-pane ke-pane-human">
                <div class="ke-pane-title"><span class="ke-pane-label">人工采纳</span></div>
                <p class="ke-human-tip">请选择需要提取为知识候选的失败样本</p>
                <div class="ke-human-stat">
                  <span class="ke-stat-num">{{ failedResults.length }}</span>
                  <span class="ke-stat-label">个候选失败样本</span>
                </div>
                <a-button
                  size="small"
                  type="primary"
                  long
                  :disabled="!selectedRunId"
                  @click="openCandidateModal"
                >
                  <template #icon><icon-plus /></template>
                  生成知识候选
                </a-button>
              </div>
            </div>
            <footer class="ke-card-foot">
              <span class="ke-foot-label">流转控制</span>
              <a-button size="small" @click="currentStage = 2">返回修改</a-button>
              <a-button
                size="small"
                type="primary"
                :disabled="!failedResults.length"
                @click="confirmStage(3)"
              >
                确认采纳，进入下一阶段
              </a-button>
            </footer>
          </section>

          <!-- 阶段 4：知识候选审核 -->
          <section class="ke-card" :class="{ 'is-current': currentStage === 4 }">
            <header class="ke-card-head">
              <span class="ke-card-no">4</span>
              <h3>知识候选审核阶段</h3>
              <a-tag :color="pendingCandidates.length ? 'orange' : 'green'" size="small">
                {{ pendingCandidates.length ? '待人工复核' : '已完成' }}
              </a-tag>
              <span class="ke-card-time">{{ stageTime.review }}</span>
            </header>
            <div class="ke-card-body">
              <div class="ke-pane">
                <div class="ke-pane-title">
                  <a-tag color="purple" size="small">AI 生成内容</a-tag>
                  <a-select
                    v-model="candidateState"
                    size="small"
                    class="ke-select"
                    @change="loadCandidates"
                  >
                    <a-option value="">全部状态</a-option>
                    <a-option value="pending">待处理</a-option>
                    <a-option value="awaiting_approval">待审批</a-option>
                    <a-option value="accepted">已通过</a-option>
                    <a-option value="rejected">已驳回</a-option>
                  </a-select>
                </div>
                <ul class="ke-list ke-candidate-list">
                  <li v-for="c in candidates.slice(0, 5)" :key="c.id">
                    <div class="ke-candidate-main">
                      <span class="ke-list-text">{{ candidateTitle(c) }}</span>
                      <span class="ke-candidate-meta">
                        <span>{{ c.kind }}</span>
                        <span>来源 {{ c.origin }}</span>
                        <span>置信度 {{ (c.confidence * 100).toFixed(0) }}%</span>
                      </span>
                    </div>
                    <a-tag :color="candidateTagColor(c.state)" size="small">
                      {{ candidateStateText(c.state) }}
                    </a-tag>
                  </li>
                </ul>
                <p v-if="!candidates.length" class="ke-empty">暂无知识候选</p>
              </div>
              <div class="ke-pane ke-pane-human">
                <div class="ke-pane-title"><span class="ke-pane-label">人工复核</span></div>
                <p class="ke-human-tip">请审核待审批的知识候选，通过后进入知识库</p>
                <div class="ke-human-stat">
                  <span class="ke-stat-num">{{ pendingCandidates.length }}</span>
                  <span class="ke-stat-label">个待审批候选</span>
                </div>
                <div class="ke-human-actions">
                  <a-button
                    size="small"
                    type="primary"
                    :disabled="!currentCandidate"
                    @click="approve(currentCandidate)"
                  >
                    <template #icon><icon-check /></template>
                    通过
                  </a-button>
                  <a-button
                    size="small"
                    status="danger"
                    :disabled="!currentCandidate"
                    @click="reject(currentCandidate)"
                  >
                    <template #icon><icon-close /></template>
                    驳回
                  </a-button>
                </div>
                <span class="ke-human-note">
                  {{ currentCandidate ? `当前：${candidateTitle(currentCandidate)}` : '暂无待审批候选' }}
                </span>
              </div>
            </div>
            <footer class="ke-card-foot">
              <span class="ke-foot-label">流转控制</span>
              <a-button size="small" @click="currentStage = 3">返回修改</a-button>
              <a-button
                size="small"
                type="primary"
                :disabled="!!pendingCandidates.length"
                @click="confirmStage(4)"
              >
                审核完成，结束流程
              </a-button>
            </footer>
          </section>
        </main>

        <!-- 右栏：人 + AI 专家团队 -->
        <aside class="ke-team">
          <div class="ke-team-head">
            <span>人 + AI 专家团队</span>
            <a-button size="mini" type="text" @click="refreshAll">
              <template #icon><icon-refresh /></template>
              刷新
            </a-button>
          </div>

          <section class="ke-team-block">
            <h4>人工角色<span class="ke-perm">决策权限</span></h4>
            <div v-for="p in humanRoles" :key="p.name" class="ke-person">
              <span class="ke-avatar" :class="`is-${p.tone}`">{{ p.initial }}</span>
              <div class="ke-person-main">
                <div class="ke-person-name">
                  {{ p.name }}
                  <a-tag color="gold" size="small">决策</a-tag>
                </div>
                <div class="ke-person-desc">{{ p.desc }}</div>
              </div>
            </div>
          </section>

          <section class="ke-team-block">
            <h4>AI 专家团队<span class="ke-perm">执行权限</span></h4>
            <div v-for="a in agents" :key="a.name" class="ke-agent">
              <span class="ke-agent-icon">{{ a.icon }}</span>
              <div class="ke-agent-main">
                <div class="ke-agent-name">{{ a.name }}</div>
                <div class="ke-agent-desc">{{ a.desc }}</div>
                <div class="ke-agent-state">
                  <i :class="`dot-${a.state}`"></i>{{ a.stateText }}
                </div>
              </div>
              <a-tooltip content="人工监督" mini>
                <a-switch v-model="a.supervised" size="small" />
              </a-tooltip>
            </div>
          </section>

          <section class="ke-legend">
            <span><i class="dot-running"></i>执行中</span>
            <span><i class="dot-waiting"></i>等待中</span>
            <span><i class="dot-error"></i>异常</span>
            <span><i class="dot-done"></i>已完成</span>
          </section>
        </aside>
      </div>
    </template>

    <!-- 新建评测集 -->
    <a-modal
      v-model:visible="showSuiteModal"
      title="新建评测集"
      ok-text="创建"
      @ok="confirmCreateSuite"
      @cancel="showSuiteModal = false"
    >
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

    <!-- 生成知识候选 -->
    <a-modal
      v-model:visible="showCandidateModal"
      title="从失败样本生成知识候选"
      ok-text="生成"
      @ok="confirmCreateCandidates"
      @cancel="showCandidateModal = false"
    >
      <a-form :model="candidateForm" layout="vertical">
        <a-form-item label="失败阈值">
          <a-slider v-model="candidateForm.threshold" :min="0" :max="1" :step="0.05" />
        </a-form-item>
        <a-form-item label="最小失败样本数">
          <a-input-number v-model="candidateForm.minFailureCount" :min="1" :max="100" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 任务概览 -->
    <a-modal v-model:visible="showOverview" title="反馈与测评 · 任务概览" :footer="false" width="620px">
      <div class="ke-overview">
        <div v-for="(s, i) in stageNav" :key="s.key" class="ke-overview-row">
          <span class="ke-overview-no">{{ i + 1 }}</span>
          <div class="ke-overview-main">
            <div class="ke-overview-name">{{ s.name }}</div>
            <div class="ke-overview-desc">{{ stageDesc[i] }}</div>
          </div>
          <a-tag :color="s.tagColor" size="small">{{ s.badge }}</a-tag>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  IconLoop, IconStorage, IconApps, IconRight, IconPlus, IconPlayArrow,
  IconRefresh, IconCheck, IconClose,
} from '@arco-design/web-vue/es/icon';
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
const projectName = computed(() => projectStore.currentProject?.name || '未选择项目');

const currentStage = ref(1);
const showOverview = ref(false);

/* ---------------------------------- 数据 ---------------------------------- */
const suites = ref<EvaluationSuite[]>([]);
const suitesLoading = ref(false);
const showSuiteModal = ref(false);
const suiteForm = ref({ name: '', description: '', task_type: 'code_review' });
const selectedSuiteId = ref<string | undefined>();
const selectedSuite = computed(() => suites.value.find((s) => s.id === selectedSuiteId.value) || null);

const runs = ref<EvaluationRun[]>([]);
const completedRuns = computed(() => runs.value.filter((r) => r.status === 'completed'));
const totalCost = computed(() => runs.value.reduce((sum, r) => sum + (r.cost_usd || 0), 0).toFixed(2));

const results = ref<EvaluationResult[]>([]);
const selectedRunId = ref<string>('');
const selectedRun = computed(() => runs.value.find((r) => r.id === selectedRunId.value) || null);
const failedResults = computed(() => results.value.filter((r) => r.status !== 'passed'));
const failureRate = computed(() =>
  results.value.length ? Math.round((failedResults.value.length / results.value.length) * 100) : 0
);

const feedbackEvents = ref<FeedbackEvent[]>([]);
const feedbackSignal = ref('');

const candidates = ref<KnowledgeCandidate[]>([]);
const candidateState = ref('');
const pendingCandidates = computed(() =>
  candidates.value.filter((c) => c.state === 'awaiting_approval' || c.state === 'pending')
);
const currentCandidate = computed(() => pendingCandidates.value[0] || null);

const showCandidateModal = ref(false);
const candidateForm = ref({ threshold: 0.5, minFailureCount: 1 });

/* -------------------------------- 阶段与团队 ------------------------------- */
const stageNav = computed(() => [
  {
    key: 'feedback',
    name: '反馈采集阶段',
    badge: feedbackEvents.value.length ? '待人工确认' : '等待中',
    tagColor: feedbackEvents.value.length ? 'orange' : 'gray',
  },
  {
    key: 'run',
    name: '评测运行阶段',
    badge: completedRuns.value.length ? '待人工采纳' : '等待中',
    tagColor: completedRuns.value.length ? 'blue' : 'gray',
  },
  {
    key: 'failure',
    name: '失败样本分析阶段',
    badge: failedResults.value.length ? '待人工采纳' : '等待中',
    tagColor: failedResults.value.length ? 'blue' : 'gray',
  },
  {
    key: 'review',
    name: '知识候选审核阶段',
    badge: pendingCandidates.value.length ? '待人工复核' : '已完成',
    tagColor: pendingCandidates.value.length ? 'orange' : 'green',
  },
]);

const stageDesc = [
  '归集采纳、驳回、编辑与测试结果反馈，形成可追溯的证据链。',
  '在评测集上回放策略，产出 L0–L3 分层指标与成本数据。',
  '聚类失败样本、定位能力缺口，生成知识候选。',
  '人工审核候选，通过后进入知识版本并参与后续检索。',
];

const stageTime = computed(() => ({
  feedback: fmtTime(feedbackEvents.value[0]?.created_at),
  run: fmtTime(selectedRun.value?.created_at || runs.value[0]?.created_at),
  failure: fmtTime(selectedRun.value?.finished_at || selectedRun.value?.started_at),
  review: fmtTime(candidates.value[0]?.created_at),
}));

const humanRoles = [
  { name: '测试负责人', initial: '测', tone: 'blue', desc: '掌握评测准入与阶段流转决策' },
  { name: '知识管理员', initial: '知', tone: 'purple', desc: '掌握知识候选入库审核决策' },
];

const agents = ref([
  { name: '反馈归集 Agent', icon: '📥', desc: '采集采纳 / 驳回 / 编辑 / 测试结果信号', state: 'running', stateText: '执行中', supervised: true },
  { name: '评测执行 Agent', icon: '📊', desc: '回放评测集并产出 L0–L3 指标', state: 'running', stateText: '执行中', supervised: true },
  { name: '失败归因 Agent', icon: '🔍', desc: '聚类失败样本、定位能力缺口', state: 'waiting', stateText: '等待中', supervised: true },
  { name: '候选蒸馏 Agent', icon: '🧪', desc: '从失败模式沉淀知识候选', state: 'waiting', stateText: '等待中', supervised: true },
]);

/* --------------------------------- 加载逻辑 -------------------------------- */
async function loadSuites() {
  if (!projectStore.currentProjectId) return;
  suitesLoading.value = true;
  try {
    suites.value = (await listEvaluationSuites(projectStore.currentProjectId)) || [];
  } catch (e) {
    Message.error('加载评测集失败');
  } finally {
    suitesLoading.value = false;
  }
}

async function loadRuns() {
  try {
    runs.value = (await listEvaluationRuns(selectedSuiteId.value || undefined)) || [];
  } catch (e) {
    Message.error('加载评测运行失败');
  }
}

async function loadResults() {
  if (!selectedRunId.value) {
    results.value = [];
    return;
  }
  try {
    results.value = (await listEvaluationResults(selectedRunId.value)) || [];
  } catch (e) {
    Message.error('加载评测结果失败');
  }
}

async function loadFeedback() {
  try {
    feedbackEvents.value = (await listFeedbackEvents({
      project: projectStore.currentProjectId || undefined,
      signal: feedbackSignal.value || undefined,
    })) || [];
  } catch (e) {
    Message.error('加载反馈事件失败');
  }
}

async function loadCandidates() {
  if (!projectStore.currentProjectId) return;
  try {
    candidates.value = (await listKnowledgeCandidates({
      project: projectStore.currentProjectId,
      state: candidateState.value || undefined,
    })) || [];
  } catch (e) {
    Message.error('加载知识候选失败');
  }
}

async function refreshAll() {
  await Promise.all([loadSuites(), loadRuns(), loadResults(), loadFeedback(), loadCandidates()]);
  Message.success('状态已刷新');
}

async function selectSuite(suite: EvaluationSuite) {
  selectedSuiteId.value = suite.id;
  await loadRuns();
  if (runs.value.length) await selectRun(runs.value[0]);
}

async function selectRun(run?: EvaluationRun) {
  if (!run) return;
  selectedRunId.value = run.id;
  await loadResults();
  if (run.status === 'completed') currentStage.value = 3;
}

/* --------------------------------- 操作 ---------------------------------- */
async function confirmCreateSuite() {
  if (!suiteForm.value.name) {
    Message.warning('请填写评测集名称');
    return;
  }
  try {
    await createEvaluationSuite({ ...suiteForm.value, project_id: projectStore.currentProjectId } as Partial<EvaluationSuite>);
    Message.success('评测集创建成功');
    showSuiteModal.value = false;
    suiteForm.value = { name: '', description: '', task_type: 'code_review' };
    await loadSuites();
  } catch (e) {
    Message.error('创建评测集失败');
  }
}

async function createRun() {
  if (!selectedSuiteId.value) return;
  try {
    await createEvaluationRun({ suite: selectedSuiteId.value, policy_version: 'default', model_name: 'default' });
    Message.success('评测运行已启动');
    await loadRuns();
    currentStage.value = 2;
  } catch (e) {
    Message.error('启动评测运行失败');
  }
}

function openCandidateModal() {
  if (!selectedRunId.value) {
    Message.warning('请先选择一个已完成的评测运行');
    return;
  }
  showCandidateModal.value = true;
}

async function confirmCreateCandidates() {
  try {
    const res = await generateCandidatesFromRun(selectedRunId.value, {
      thresholds: { l0: candidateForm.value.threshold, l1: candidateForm.value.threshold },
      min_failure_count: candidateForm.value.minFailureCount,
    });
    Message.success(`已生成 ${res.created_count} 个知识候选`);
    showCandidateModal.value = false;
    await loadCandidates();
    currentStage.value = 4;
  } catch (e) {
    Message.error('生成知识候选失败');
  }
}

async function approve(item: KnowledgeCandidate | null) {
  if (!item) return;
  try {
    await updateCandidateState(item.id, 'accepted', '工作台人工审核通过');
    Message.success('已通过');
    await loadCandidates();
  } catch (e) {
    Message.error('审批失败');
  }
}

async function reject(item: KnowledgeCandidate | null) {
  if (!item) return;
  try {
    await updateCandidateState(item.id, 'rejected', '工作台人工驳回');
    Message.success('已驳回');
    await loadCandidates();
  } catch (e) {
    Message.error('驳回失败');
  }
}

function confirmStage(stage: number) {
  if (stage >= 4) {
    Message.success('流程已完成');
    return;
  }
  currentStage.value = stage + 1;
  Message.success(`已进入「${stageNav.value[stage].name}」`);
}

/* --------------------------------- 展示工具 -------------------------------- */
const signalLabels: Record<string, string> = {
  accepted: '采纳', rejected: '驳回', edited: '编辑', test_passed: '测试通过', test_failed: '测试失败',
  defect_confirmed: '缺陷确认', false_positive: '误报', missed: '漏报', merged: '已合并', reverted: '已回退',
};

function signalText(s: string) {
  return signalLabels[s] || s;
}

function signalTagColor(s: string) {
  if (s === 'accepted' || s === 'test_passed') return 'green';
  if (s === 'rejected' || s === 'test_failed' || s === 'defect_confirmed') return 'red';
  if (s === 'edited') return 'blue';
  if (s === 'false_positive' || s === 'missed') return 'orange';
  return 'gray';
}

function feedbackSummary(f: FeedbackEvent) {
  if (f.comment) return f.comment;
  const detail = f.detail || {};
  if (detail.edit) return `编辑 diff：${JSON.stringify(detail.edit).slice(0, 60)}`;
  const text = JSON.stringify(detail);
  return text && text !== '{}' ? text.slice(0, 60) : '无附加说明';
}

function statusText(s: string) {
  return ({ pending: '待执行', running: '运行中', completed: '已完成', failed: '失败' } as Record<string, string>)[s] || s;
}

function statusTagColor(s: string) {
  return ({ completed: 'green', running: 'blue', failed: 'red', pending: 'orange' } as Record<string, string>)[s] || 'gray';
}

function scoreTagColor(v?: number) {
  if (v === undefined || v === null) return 'gray';
  if (v >= 0.8) return 'green';
  if (v >= 0.5) return 'orange';
  return 'red';
}

function candidateTagColor(s: string) {
  return ({
    awaiting_approval: 'blue', pending: 'orange', accepted: 'green',
    rejected: 'red', conflicted: 'red', merged: 'purple',
  } as Record<string, string>)[s] || 'gray';
}

function candidateStateText(s: string) {
  return ({
    pending: '待处理', conflicted: '冲突', evaluating: '评测中', awaiting_approval: '待审批',
    accepted: '已通过', rejected: '已驳回', merged: '已合并',
  } as Record<string, string>)[s] || s;
}

function candidateTitle(item: KnowledgeCandidate): string {
  const p = item.payload || {};
  return String(p.title || p.name || p.statement || `${item.kind}-${item.id.slice(0, 8)}`);
}

function fmt(v?: number) {
  return v === undefined || v === null ? '-' : v.toFixed(2);
}

function fmtTime(v?: string) {
  return v ? new Date(v).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '--:--';
}

// 项目上下文可能在组件挂载后才恢复，因此监听项目变化而不是只在 mounted 时加载一次
watch(
  () => projectStore.currentProjectId,
  async (projectId) => {
    if (!projectId) return;
    await loadSuites();
    if (suites.value.length) await selectSuite(suites.value[0]);
    await Promise.all([loadFeedback(), loadCandidates()]);
  },
  { immediate: true }
);
</script>

<style scoped>
/* 配色统一使用平台主题变量（--theme-* 与 Arco --color-*），
   亮色 / 暗色主题下均自动适配，不写死颜色。 */
.ke-page {
  height: 100%;
  box-sizing: border-box;
  padding: 16px;
  background-color: transparent;
  color: var(--theme-page-text, var(--color-text-1));
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}

.ke-no-project {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--theme-card-bg, var(--color-bg-2));
  border: 1px solid var(--theme-card-border, var(--color-border-2));
  border-radius: 8px;
}

/* ------------------------------- 页头 ------------------------------- */
.ke-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex: 0 0 auto;
}
.ke-header-title h2 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-header-desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--theme-text-secondary, var(--color-text-3));
}
.ke-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

/* ------------------------------ 阶段导航 ------------------------------ */
.ke-stagebar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-radius: 8px;
  background: var(--theme-card-bg, var(--color-bg-2));
  border: 1px solid var(--theme-card-border, var(--color-border-2));
  box-shadow: var(--theme-card-shadow, none);
  overflow-x: auto;
}
.ke-stagechip {
  flex: 1 1 0;
  min-width: 176px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  background: var(--theme-surface-soft, var(--color-fill-1));
  border: 1px solid transparent;
  color: var(--theme-text-secondary, var(--color-text-2));
  transition: background-color 0.18s ease, border-color 0.18s ease;
}
.ke-stagechip:hover {
  background: var(--color-fill-2);
}
.ke-stagechip.is-current {
  background: rgba(var(--theme-accent-rgb), 0.08);
  border-color: var(--theme-accent);
}
.ke-chip-no {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  color: var(--theme-text-secondary, var(--color-text-2));
  background: var(--color-fill-2);
}
.ke-stagechip.is-current .ke-chip-no {
  background: var(--theme-accent);
  color: #fff;
}
.ke-chip-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
  white-space: nowrap;
}
.ke-stagechip .arco-tag {
  margin-left: auto;
}
.ke-chip-arrow {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-4));
}

/* ------------------------------ 三栏主体 ------------------------------ */
.ke-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 224px minmax(0, 1fr) 272px;
  gap: 12px;
}

.ke-rail,
.ke-team,
.ke-flow {
  min-height: 0;
  border-radius: 8px;
  background: var(--theme-card-bg, var(--color-bg-2));
  border: 1px solid var(--theme-card-border, var(--color-border-2));
  box-shadow: var(--theme-card-shadow, none);
}

/* 左栏 */
.ke-rail {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.ke-rail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--theme-border, var(--color-border-2));
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-rail-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ke-suite {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  background: var(--theme-surface-soft, var(--color-fill-1));
  border: 1px solid transparent;
  color: var(--theme-page-text, var(--color-text-1));
  transition: background-color 0.18s ease, border-color 0.18s ease;
}
.ke-suite:hover {
  background: var(--color-fill-2);
}
.ke-suite.is-active {
  background: rgba(var(--theme-accent-rgb), 0.08);
  border-color: var(--theme-accent);
}
.ke-suite-name {
  font-size: 13px;
  font-weight: 600;
}
.ke-suite-meta {
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-rail-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-top: 1px solid var(--theme-border, var(--color-border-2));
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgb(var(--green-6));
}

/* 中栏 */
.ke-flow {
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ke-card {
  flex: 0 0 auto;
  border-radius: 8px;
  background: var(--theme-card-bg, var(--color-bg-2));
  border: 1px solid var(--theme-border, var(--color-border-2));
  overflow: hidden;
  transition: border-color 0.2s ease;
}
.ke-card.is-current {
  border-color: var(--theme-accent);
}
.ke-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--theme-border, var(--color-border-2));
  background: var(--theme-surface-soft, var(--color-fill-1));
}
.ke-card-no {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-card.is-current .ke-card-no {
  background: var(--theme-accent);
}
.ke-card-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-card-time {
  margin-left: auto;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-4));
}

.ke-card-body {
  display: grid;
  grid-template-columns: minmax(0, 1.62fr) minmax(0, 1fr);
}
.ke-pane {
  padding: 12px 14px;
  min-width: 0;
}
.ke-pane + .ke-pane {
  border-left: 1px solid var(--theme-border, var(--color-border-2));
  background: var(--theme-surface-soft, var(--color-fill-1));
}
.ke-pane-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
  min-height: 24px;
}
.ke-pane-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-pane-sub {
  margin-left: auto;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-select {
  width: 116px;
  margin-left: auto;
}
.ke-select-block {
  width: 100%;
  margin-bottom: 10px;
}

/* 列表 */
.ke-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ke-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px;
  border-radius: 6px;
  background: var(--theme-card-bg, var(--color-bg-2));
  border: 1px solid var(--theme-border, var(--color-border-2));
}
.ke-list-clickable li {
  cursor: pointer;
  transition: border-color 0.18s ease;
}
.ke-list-clickable li:hover {
  border-color: var(--theme-accent);
}
.ke-list-clickable li.is-active {
  border-color: var(--theme-accent);
  background: rgba(var(--theme-accent-rgb), 0.06);
}
.ke-list-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--theme-page-text, var(--color-text-1));
}
.ke-list-meta {
  flex: 0 0 auto;
  font-style: normal;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-score-chips {
  margin-left: auto;
  display: flex;
  gap: 4px;
  flex: 0 0 auto;
}
.ke-pane-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-empty {
  margin: 6px 0;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-4));
}

/* 失败率进度 */
.ke-progress {
  margin-bottom: 9px;
}
.ke-progress :deep(.arco-progress-line) {
  margin: 0;
}
.ke-progress-meta {
  display: flex;
  gap: 14px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}

/* 知识候选 */
.ke-candidate-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.ke-candidate-meta {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}

/* 人工区 */
.ke-pane-human {
  display: flex;
  flex-direction: column;
}
.ke-human-tip {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--theme-text-secondary, var(--color-text-3));
}
.ke-human-stat {
  display: flex;
  align-items: baseline;
  gap: 7px;
  margin-bottom: 10px;
}
.ke-stat-num {
  font-size: 22px;
  font-weight: 700;
  color: var(--theme-accent);
}
.ke-stat-label {
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-human-actions {
  display: flex;
  gap: 8px;
}
.ke-human-note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-4));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 卡片底部流转控制 */
.ke-card-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-top: 1px solid var(--theme-border, var(--color-border-2));
  background: var(--theme-surface-soft, var(--color-fill-1));
}
.ke-foot-label {
  margin-right: auto;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}

/* ------------------------------ 右栏团队 ------------------------------ */
.ke-team {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  overflow-y: auto;
}
.ke-team-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 9px;
  border-bottom: 1px solid var(--theme-border, var(--color-border-2));
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-team-block h4 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-perm {
  font-size: 12px;
  font-weight: 400;
  color: var(--theme-text-tertiary, var(--color-text-4));
}
.ke-person,
.ke-agent {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px;
  margin-bottom: 7px;
  border-radius: 6px;
  background: var(--theme-surface-soft, var(--color-fill-1));
  border: 1px solid var(--theme-border, var(--color-border-2));
}
.ke-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
}
.ke-avatar.is-blue {
  background: rgb(var(--arcoblue-6));
}
.ke-avatar.is-purple {
  background: rgb(var(--purple-6));
}
.ke-person-main {
  min-width: 0;
}
.ke-person-name {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-person-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}

.ke-agent {
  align-items: flex-start;
}
.ke-agent-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  font-size: 14px;
  background: var(--color-fill-2);
  border: 1px solid var(--theme-border, var(--color-border-2));
}
.ke-agent-main {
  flex: 1;
  min-width: 0;
}
.ke-agent-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-agent-desc {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--theme-text-tertiary, var(--color-text-3));
}
.ke-agent-state {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--theme-text-secondary, var(--color-text-3));
}
.ke-agent-state i,
.ke-legend i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex: 0 0 auto;
}
.ke-agent .arco-switch {
  flex: 0 0 auto;
  margin-top: 4px;
}
.dot-running {
  background: rgb(var(--green-6));
}
.dot-waiting {
  background: rgb(var(--orange-6));
}
.dot-error {
  background: rgb(var(--red-6));
}
.dot-done {
  background: rgb(var(--gray-6));
}

.ke-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--theme-border, var(--color-border-2));
  font-size: 12px;
  color: var(--theme-text-tertiary, var(--color-text-3));
}

/* 概览弹窗 */
.ke-overview {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ke-overview-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--theme-surface-soft, var(--color-fill-1));
  border: 1px solid var(--theme-border, var(--color-border-2));
}
.ke-overview-no {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--theme-accent);
}
.ke-overview-main {
  flex: 1;
  min-width: 0;
}
.ke-overview-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--theme-text, var(--color-text-1));
}
.ke-overview-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--theme-text-secondary, var(--color-text-3));
}

@media (max-width: 1440px) {
  .ke-body {
    grid-template-columns: 200px minmax(0, 1fr) 244px;
  }
}
@media (max-width: 1200px) {
  .ke-body {
    grid-template-columns: minmax(0, 1fr);
    overflow-y: auto;
  }
  .ke-rail,
  .ke-team,
  .ke-flow {
    min-height: auto;
  }
  .ke-flow {
    overflow-y: visible;
  }
}
</style>
