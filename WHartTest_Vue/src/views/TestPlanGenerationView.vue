<template>
  <div class="plan-page">
    <a-page-header title="测试方案分析" subtitle="基于已确认的需求与技术文档产出可追溯的测试方案" />

    <!-- 受控模式横幅（T03 §4.3）：流程、阶段、锁定的 Skill 与上游产出必须一眼可见。
         这些值全部来自**服务端解析**的执行上下文；URL 里只有一个
         execution_context_id，篡改它只会得到"上下文失效"，换不出别的 Skill 版本。 -->
    <a-alert v-if="controlled" type="info" class="context-alert">
      <template #title>已关联质量飞轮流程 · {{ executionContext!.stage_label }}</template>
      <div class="context-lines">
        <span>流程 <code>{{ executionContext!.workflow_id }}</code></span>
        <span>
          Skill 版本
          <code>{{ executionContext!.skill.skill_name || '未锁定 Skill' }}{{ executionContext!.skill.version ? ' ' + executionContext!.skill.version : '' }}</code>
          （由流程锁定，本页不可更换）
        </span>
        <span v-if="executionContext!.parent_output_ids.length">
          上游产出 <code>{{ executionContext!.parent_output_ids.join('、') }}</code>
        </span>
        <span>执行尝试 <code>{{ executionContext!.attempt_id }}</code></span>
      </div>
    </a-alert>
    <a-alert v-else-if="workflowId" type="info" class="context-alert">
      当前由质量飞轮流程进入，产出将关联流程 {{ workflowId }}。
    </a-alert>

    <!-- 上下文失效不是"静默降级成旁路"，而是一次明确的失败：受控运行的产出必须
         归属到流程与锁定版本，悄悄跑完只会制造一条没有版本溯源的产出。 -->
    <a-alert v-if="contextError" type="error" class="context-alert">
      <template #title>执行上下文已失效，本页暂不可执行</template>
      <div class="context-lines">
        <span>{{ contextError }}</span>
        <a-button size="mini" type="primary" @click="backToFlywheel">回到质量飞轮重新派发</a-button>
      </div>
    </a-alert>

    <a-grid :cols="24" :col-gap="16" :row-gap="16">
      <a-grid-item :span="14">
        <a-card title="生成配置" :bordered="false">
          <a-form :model="form" layout="vertical">
            <a-form-item label="当前项目">
              <a-input :model-value="projectStore.currentProject?.name || '未选择项目'" disabled />
            </a-form-item>

            <a-form-item label="Skill 与版本" required>
              <a-select
                v-model="form.skillVersionId"
                :loading="loadingSkills"
                :disabled="controlled"
                allow-search
                placeholder="选择适用于方案生成的 Skill 版本"
              >
                <a-option v-for="skill in planSkills" :key="skill.skill_version_id" :value="skill.skill_version_id">
                  <span>{{ skill.skill_name }}</span>
                  <span class="option-meta">{{ skill.version }} · {{ shortHash(skill.package_sha256) }}</span>
                </a-option>
              </a-select>
              <template #extra>
                <template v-if="controlled">
                  本页由质量飞轮流程进入，Skill 版本已在发起流程时锁定，不可更换。
                </template>
                <template v-else>
                  仅展示 Skill Hub 中声明为“测试方案生成”且可运行的版本；已自动选中默认版本。
                </template>
              </template>
            </a-form-item>

            <a-form-item label="需求 / 技术文档" required>
              <a-select
                v-model="form.documentIds"
                multiple
                allow-search
                allow-clear
                :loading="loadingDocuments"
                :max-tag-count="3"
                placeholder="选择已拆解的文档"
                @change="handleDocumentChange"
              >
                <a-optgroup v-for="group in documentGroups" :key="group.category" :label="group.label">
                  <a-option v-for="doc in group.documents" :key="doc.id" :value="doc.id" :disabled="doc.modules_count === 0">
                    {{ doc.title }}{{ doc.modules_count === 0 ? '（待拆解）' : '' }}
                  </a-option>
                </a-optgroup>
              </a-select>
            </a-form-item>

            <a-form-item label="文档模块" required>
              <div v-if="moduleGroups.length" class="module-groups">
                <div v-for="group in moduleGroups" :key="group.documentId" class="module-row">
                  <span class="module-title" :title="group.documentTitle">{{ group.documentTitle }}</span>
                  <a-select
                    :model-value="moduleIdsFor(group.documentId)"
                    multiple
                    allow-search
                    allow-clear
                    :loading="loadingModules"
                    placeholder="选择本文档模块"
                    @change="value => updateDocumentModules(group.documentId, value)"
                  >
                    <a-option v-for="module in group.modules" :key="module.id" :value="module.id">{{ module.title }}</a-option>
                  </a-select>
                </div>
              </div>
              <a-empty v-else description="请先选择已拆解的文档" />
            </a-form-item>

            <a-form-item label="知识库">
              <a-select v-model="form.knowledgeBaseIds" multiple allow-clear :loading="loadingKnowledge" placeholder="可选">
                <a-option v-for="kb in knowledgeBases" :key="kb.id" :value="kb.id">{{ kb.name }}</a-option>
              </a-select>
              <template #extra>
                方案生成使用平台固定质量要求；具体 Skill 由上方所选版本或质量飞轮锁定版本决定。
              </template>
            </a-form-item>

            <KnowledgeDocumentScopeSelector
              v-if="form.knowledgeBaseIds.length"
              :knowledge-base-ids="form.knowledgeBaseIds"
              :knowledge-bases="knowledgeBases"
              :scope-mode="form.knowledgeDocumentScope"
              :document-ids="form.knowledgeDocumentIds"
              @update:scope-mode="form.knowledgeDocumentScope = $event"
              @update:document-ids="form.knowledgeDocumentIds = $event"
            />

            <a-form-item label="补充要求">
              <a-textarea v-model="form.extraRequirements" :max-length="2000" show-word-limit :auto-size="{ minRows: 3, maxRows: 8 }" placeholder="例如：重点覆盖权限、兼容性与回归范围" />
            </a-form-item>

            <div class="actions">
              <a-button type="primary" size="large" :loading="isStreaming" @click="generatePlan">生成测试方案</a-button>
              <a-button v-if="sessionId" size="large" @click="openConversation">
                {{ isStreaming ? '查看生成过程' : '在 LLM 对话中查看' }}
              </a-button>
            </div>
          </a-form>
        </a-card>
      </a-grid-item>

      <a-grid-item :span="10">
        <a-card title="生成结果" :bordered="false" class="result-card">
          <template #extra>
            <span class="result-card-actions">
              <a-link v-if="sessionId" class="result-card-link" @click="refreshArtifacts">刷新产物</a-link>
              <a-link v-if="sessionId" class="result-card-link" @click="openConversation">LLM 对话</a-link>
            </span>
          </template>

          <div class="result-body">
            <div class="result-status">
              <a-spin v-if="isStreaming || restoring" :size="14" />
              <a-tag :color="status.tone" size="small">{{ status.text }}</a-tag>
              <span v-if="isStreaming" class="result-step">{{ stepLabel || 'Skill 正在执行' }}</span>
              <span v-else-if="allArtifacts.length" class="result-step">共 {{ allArtifacts.length }} 个文件</span>
            </div>

            <div v-if="isStreaming" class="progress-panel">
              <div v-if="recentToolLogs.length" class="progress-log">
                <div v-for="(log, index) in recentToolLogs" :key="`${log.name}-${index}`" class="progress-log-item">
                  <span class="progress-log-name">{{ log.name }}</span>
                  <span class="progress-log-summary">{{ log.summary }}</span>
                </div>
              </div>
              <div v-else class="progress-hint">正在调用所选 Skill 生成测试方案，产物文件将在完成后列出…</div>
            </div>

            <a-alert v-if="streamError" type="error" :title="streamError" class="result-alert" />

            <template v-if="!isStreaming && !restoring">
              <div v-if="primaryArtifacts.length" class="artifact-section">
                <div class="artifact-title">本次产出文件</div>
                <div class="artifact-list">
                  <div v-for="file in primaryArtifacts" :key="file.url" class="artifact-item">
                    <IconFile class="artifact-icon" />
                    <div class="artifact-main">
                      <div class="artifact-name" :title="file.name">{{ file.name }}</div>
                      <div class="artifact-meta">
                        <span v-if="fileExtLabel(file)">{{ fileExtLabel(file) }}</span>
                        <span v-if="formatSize(file.size)">{{ formatSize(file.size) }}</span>
                      </div>
                    </div>
                    <div class="artifact-actions">
                      <a :href="file.url" target="_blank" rel="noopener noreferrer" class="artifact-action">打开</a>
                      <a :href="file.url" :download="file.name" class="artifact-action artifact-action-primary">下载</a>
                    </div>
                  </div>
                </div>

                <a-collapse v-if="historicalArtifacts.length" class="other-collapse" :bordered="false">
                  <a-collapse-item key="history" :header="`历史版本（${historicalArtifacts.length} 个）`">
                    <div class="artifact-history-hint">历史产物按执行批次保留，重新生成不会覆盖原文件。</div>
                    <div class="artifact-list">
                      <div v-for="file in historicalArtifacts" :key="file.url" class="artifact-item artifact-item-quiet">
                        <IconFile class="artifact-icon artifact-icon-quiet" />
                        <div class="artifact-main">
                          <div class="artifact-name" :title="file.name">{{ file.name }}</div>
                          <div class="artifact-meta">
                            <span>历史产物</span>
                            <span v-if="fileExtLabel(file)">{{ fileExtLabel(file) }}</span>
                            <span v-if="formatSize(file.size)">{{ formatSize(file.size) }}</span>
                          </div>
                        </div>
                        <div class="artifact-actions">
                          <a :href="file.url" target="_blank" rel="noopener noreferrer" class="artifact-action">打开</a>
                          <a :href="file.url" :download="file.name" class="artifact-action">下载</a>
                        </div>
                      </div>
                    </div>
                  </a-collapse-item>
                </a-collapse>

                <a-collapse v-if="intermediateArtifacts.length" class="other-collapse" :bordered="false">
                  <a-collapse-item key="others" :header="`其他文件（${intermediateArtifacts.length} 个，含中间过程产物）`">
                    <div class="artifact-list">
                      <div v-for="file in intermediateArtifacts" :key="file.url" class="artifact-item artifact-item-quiet">
                        <IconFile class="artifact-icon artifact-icon-quiet" />
                        <div class="artifact-main">
                          <div class="artifact-name" :title="file.name">{{ file.name }}</div>
                          <div class="artifact-meta">
                            <span v-if="fileExtLabel(file)">{{ fileExtLabel(file) }}</span>
                            <span v-if="formatSize(file.size)">{{ formatSize(file.size) }}</span>
                          </div>
                        </div>
                        <div class="artifact-actions">
                          <a :href="file.url" :download="file.name" class="artifact-action">下载</a>
                        </div>
                      </div>
                    </div>
                  </a-collapse-item>
                </a-collapse>
              </div>

              <a-empty v-else-if="streamError" description="本次生成未产出文件" />

              <a-empty v-else-if="sessionId">
                <template #description>
                  <div class="empty-hint">
                    <p>本次会话暂未解析到可下载的产物文件。</p>
                    <p>不同 Skill 的产物形态不一致；若刚在 LLM 对话里完成生成，可点右上角「刷新产物」。</p>
                  </div>
                </template>
              </a-empty>

              <a-empty v-else description="完成左侧配置后生成测试方案" />
            </template>

            <a-collapse v-if="historicalOutputs.length" class="raw-collapse" :bordered="false">
              <a-collapse-item key="historical" :header="`历史产出（${historicalOutputs.length} 个批次）`">
                <div class="artifact-history-hint">历史批次只读保留，重新生成不会覆盖；展开可查看当时的产出正文和登记文件。</div>
                <div v-for="item in historicalOutputs" :key="item.id" class="historical-output-item">
                  <div class="historical-output-head">
                    <span>{{ formatHistoryDate(item.created_at) }} · {{ item.prompt_version || '默认提示词' }}</span>
                    <span>{{ historyFiles(item).length }} 个文件</span>
                  </div>
                  <div class="artifact-list">
                    <div v-for="file in historyFiles(item)" :key="`${item.id}-${file.key || file.name}`" class="artifact-item artifact-item-quiet">
                      <IconFile class="artifact-icon artifact-icon-quiet" />
                      <div class="artifact-main"><div class="artifact-name">{{ file.name }}</div><div class="artifact-meta">历史批次 · {{ formatSize(file.size) }}</div></div>
                      <a-button size="mini" type="text" @click="downloadHistoricalArtifact(item, file)">下载</a-button>
                    </div>
                  </div>
                  <pre class="result-content historical-output-content">{{ item.content }}</pre>
                </div>
              </a-collapse-item>
            </a-collapse>

            <a-collapse v-if="sessionId && streamContent && !isStreaming && !restoring" class="raw-collapse" :bordered="false">
              <a-collapse-item key="raw" header="查看 Skill 原始输出">
                <pre class="result-content">{{ streamContent }}</pre>
              </a-collapse-item>
            </a-collapse>
          </div>
        </a-card>
      </a-grid-item>
    </a-grid>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Message, Notification } from '@arco-design/web-vue';
import { IconFile } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { RequirementDocumentService } from '@/features/requirements/services/requirementService';
import type { DocumentCategory, DocumentModule, RequirementDocument } from '@/features/requirements/types';
import { KnowledgeService } from '@/features/knowledge/services/knowledgeService';
import type { KnowledgeBase } from '@/features/knowledge/types/knowledge';
import KnowledgeDocumentScopeSelector from '@/features/knowledge/components/KnowledgeDocumentScopeSelector.vue';
import { downloadGenerationOutputArtifact, getExecutionContext, getWorkflowStageCatalog, listGenerationOutputs } from '@/features/knowledge-evolution/service';
import type { ExecutionContextView, WorkflowCatalogSkill, GenerationOutput } from '@/features/knowledge-evolution/types';
import { activeStreams, getChatHistory, sendChatMessageStream } from '@/features/langgraph/services/chatService';
import type { ChatHistoryMessage, ChatRequest } from '@/features/langgraph/types/chat';
import { parseToolResultDisplayPayload } from '@/features/langgraph/utils/toolResultParser';
import type { ToolFileAttachment } from '@/features/langgraph/utils/toolResultParser';
import { toArray } from '@/features/api-testing/services/responseHelpers';

type SelectedModule = DocumentModule & { documentId: string; documentTitle: string; documentCategory: DocumentCategory };

const projectStore = useProjectStore();
const route = useRoute();
const router = useRouter();

// ---------------------------------------------------------------- 受控执行上下文（T03）
//
// 飞轮派发后跳到本页，URL 上只有 execution_context_id。流程 / 阶段 / 锁定的 Skill
// 版本 / 上游产出全部由服务端按这个 id 解析——**这是本页唯一可信的取值来源**。
// 绝不能改成"URL 上有 workflow_id 就用 URL 上的"：那是用户可改的字符串，
// 一旦被当成版本依据，页面显示的版本和流程实际锁定的版本就可以不是同一个。
const executionContextId = computed(() => String(route.query.execution_context_id || ''));
const executionContext = ref<ExecutionContextView | null>(null);
const loadingContext = ref(false);
const contextError = ref('');
/** 受控模式：由飞轮派发进入，Skill 版本锁定，页面不得更换。 */
const controlled = computed(() => Boolean(executionContext.value));

const workflowId = computed(() => executionContext.value?.workflow_id || String(route.query.workflow_id || ''));
const parentOutputIds = computed(() => {
  // 受控模式下上游产出来自服务端解析结果；Query 参数只作为旁路模式的历史兼容。
  if (executionContext.value) return executionContext.value.parent_output_ids;
  const raw = route.query.parent_output_ids;
  const values = Array.isArray(raw) ? raw : String(raw || '').split(',');
  return values.map(value => String(value || '').trim()).filter(Boolean);
});

const loadingSkills = ref(false);
const loadingDocuments = ref(false);
const loadingModules = ref(false);
const loadingKnowledge = ref(false);
const generating = ref(false);
const sessionId = ref('');
// 从历史会话回填的产物。页面只靠内存里的 streaming 状态时会「一离开就空白」，
// 所以用页面级会话键 + 历史接口把已生成的产物重新捞回来。
const restoring = ref(false);
const historyArtifacts = ref<ToolFileAttachment[]>([]);
const historyContent = ref('');
const historicalOutputs = ref<GenerationOutput[]>([]);
const documents = ref<RequirementDocument[]>([]);
const modules = ref<SelectedModule[]>([]);
const knowledgeBases = ref<KnowledgeBase[]>([]);
const planSkills = ref<WorkflowCatalogSkill[]>([]);

const form = reactive({
  skillVersionId: '',
  documentIds: [] as string[],
  moduleIds: [] as string[],
  knowledgeBaseIds: [] as string[],
  knowledgeDocumentScope: 'all' as 'all' | 'selected',
  knowledgeDocumentIds: [] as string[],
  extraRequirements: '',
});

const streamState = computed(() => sessionId.value ? activeStreams.value[sessionId.value] : undefined);
const streamContent = computed(() => streamState.value?.content || historyContent.value);
const streamError = computed(() => streamState.value?.error || '');

const isStreaming = computed(() => {
  if (generating.value) return true;
  const state = streamState.value;
  return Boolean(state && !state.isComplete && !state.error);
});

function formatHistoryDate(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未知时间';
}

function historyFiles(item: GenerationOutput): Array<{ name: string; key?: string; size?: number }> {
  const raw = item.metadata?.artifacts;
  return Array.isArray(raw) ? raw.filter((entry): entry is { name: string; key?: string; size?: number } => Boolean(entry && typeof entry === 'object' && typeof (entry as Record<string, unknown>).name === 'string')) : [];
}

async function downloadHistoricalArtifact(item: GenerationOutput, file: { name: string; key?: string }) {
  try {
    await downloadGenerationOutputArtifact(item.id, file.name, file.key || '');
    Message.success(`已下载 ${file.name}`);
  } catch {
    Message.error('历史文件下载失败，文件可能已被清理');
  }
}

async function loadHistoricalOutputs() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  try {
    historicalOutputs.value = (await listGenerationOutputs(Number(projectId), 'test_plan_generation'))
      .filter(item => item.id !== executionContext.value?.attempt_id)
      .slice(0, 10);
  } catch {
    historicalOutputs.value = [];
  }
}

const stepLabel = computed(() => {
  const state = streamState.value;
  if (!state?.maxSteps) return '';
  return `步骤 ${state.currentStep || 0}/${state.maxSteps}`;
});

const status = computed<{ text: string; tone: string }>(() => {
  if (streamError.value) return { text: '生成失败', tone: 'red' };
  if (isStreaming.value) return { text: '生成中', tone: 'arcoblue' };
  if (restoring.value) return { text: '读取产物', tone: 'arcoblue' };
  if (sessionId.value) return { text: '已生成', tone: 'green' };
  return { text: '待生成', tone: 'gray' };
});

// Skill 执行过程中的工具调用摘要（只保留最近几条，避免结果面板再次变成“一大坨”）
const recentToolLogs = computed(() => {
  const messages = streamState.value?.messages || [];
  return messages
    .filter(message => message.type === 'tool')
    .map(message => ({
      name: message.toolName || '工具调用',
      summary: (message.content || '').replace(/\s+/g, ' ').trim().slice(0, 72),
    }))
    .slice(-5);
});

const EXT_PRIORITY: Record<string, number> = { xlsx: 0, xls: 0, docx: 1, doc: 1, pdf: 2, md: 3, json: 4 };

const fileExt = (name: string) => {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index + 1).toLowerCase() : '';
};

const fileExtLabel = (file: ToolFileAttachment) => {
  const extension = fileExt(file.name);
  return extension ? extension.toUpperCase() : (file.mimeType || '');
};

const formatSize = (size?: number) => {
  if (typeof size !== 'number' || !Number.isFinite(size)) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

// Skill 执行会把过程文件一并落盘（skill 自身定义、平台探查稿、脱敏副本、插图等），
// 这些不是交付物，统一收进“其他文件”，避免结果面板又变成一长串文件。
const INTERMEDIATE_PATTERNS: RegExp[] = [
  /^SKILL\.md$/i,
  /^README\.md$/i,
  /^(TestPlan|RequirementAnalyzer|PageExplorer)\.md$/i,
  /^(input|output)\.json$/i,
  /_anonymized/i,
  /^已脱敏[_\-. ]/, // 输入文档的脱敏副本
  /^[\w-]+(\.[\w-]+)+\.md$/i, // 形如 roadshow.sseinfo.com.md 的站点探查稿
  /\.(jpe?g|png|gif|webp|svg|bmp|ico)$/i,
  // Skill 自带的模板/样例脚手架（如「测试方案_template.xlsx」）只是填表用的壳，
  // 名字里往往也带「测试方案」，会被交付物关键词误命中，必须先在这里拦掉。
  /[_\-. ](template|sample|example)([_\-.]|$)/i,
  /(?:模板|样例|示例)(?:[_\-.]|$)/,
];

const PLAN_WORKBOOK_PATTERN = /(?:^|\/).+_测试方案_V[^/]+\.xlsx$/i;
const PLAN_GUIDE_PATTERN = /(?:^|\/).+_测试方案说明_V[^/]+\.md$/i;
const REQUIREMENTS_PATTERN = /(?:^|\/)requirements_parsed\.json$/i;
const DELIVERABLE_HINT = /(测试方案|test[_\s-]?plan|requirements_parsed)/i;

// 产物文件取工具结果里的 file 附件（后端 execute_skill_script 已把落盘文件转成可下载 URL）；
// 来源有两处：本次页面内 streaming 的消息、以及从历史会话回填的消息。同名文件只保留最后一次生成的版本。
const allArtifacts = computed<ToolFileAttachment[]>(() => {
  const byUrl = new Map<string, ToolFileAttachment>();
  for (const file of historyArtifacts.value) {
    if (file?.url) byUrl.set(file.url, file);
  }
  for (const message of streamState.value?.messages || []) {
    const attachments = message.fileAttachments?.length
      ? message.fileAttachments
      : parseToolResultDisplayPayload(message.content).fileAttachments;
    for (const file of attachments) {
      if (file?.url) byUrl.set(file.url, file);
    }
  }
  return [...byUrl.values()].sort((a, b) => {
    const rankA = EXT_PRIORITY[fileExt(a.name)] ?? 9;
    const rankB = EXT_PRIORITY[fileExt(b.name)] ?? 9;
    return rankA - rankB;
  });
});

const isIntermediate = (file: ToolFileAttachment) => INTERMEDIATE_PATTERNS.some(pattern => pattern.test(file.name));

const isPlanDeliverable = (file: ToolFileAttachment) => (
  PLAN_WORKBOOK_PATTERN.test(file.name)
  || PLAN_GUIDE_PATTERN.test(file.name)
  || REQUIREMENTS_PATTERN.test(file.name)
);

// 新协议严格展示三件人工交付物；存量批次仍用关键词兜底，避免历史文件失踪。
const primaryArtifacts = computed(() => {
  const candidates = allArtifacts.value.filter(file => !isIntermediate(file));
  const contracted = candidates.filter(isPlanDeliverable);
  if (contracted.length) return latestByName(contracted);
  const hinted = candidates.filter(file => DELIVERABLE_HINT.test(file.name));
  return hinted.length ? latestByName(hinted) : latestByName(candidates);
});

const latestByName = (files: ToolFileAttachment[]) => {
  const latest = new Map<string, ToolFileAttachment>();
  for (const file of files) latest.set(file.name, file);
  return [...latest.values()];
};

const historicalArtifacts = computed(() => {
  const primaryUrls = new Set(primaryArtifacts.value.map(file => file.url));
  return allArtifacts.value.filter(file => (
    !primaryUrls.has(file.url) && !isIntermediate(file) && isPlanDeliverable(file)
  ));
});

const intermediateArtifacts = computed(() => {
  const hiddenHistoryUrls = new Set(historicalArtifacts.value.map(file => file.url));
  const primaryUrls = new Set(primaryArtifacts.value.map(file => file.url));
  return allArtifacts.value.filter(file => !primaryUrls.has(file.url) && !hiddenHistoryUrls.has(file.url));
});

const categoryLabels: Record<DocumentCategory, string> = {
  business_requirement: '业务需求文档',
  requirement_specification: '需求规格说明书',
  technical_design: '技术设计文档',
};
const documentGroups = computed(() => (Object.keys(categoryLabels) as DocumentCategory[])
  .map(category => ({ category, label: categoryLabels[category], documents: documents.value.filter(doc => doc.document_category === category) }))
  .filter(group => group.documents.length));
const moduleGroups = computed(() => form.documentIds.map(documentId => ({
  documentId,
  documentTitle: documents.value.find(doc => doc.id === documentId)?.title || documentId,
  modules: modules.value.filter(module => module.documentId === documentId),
})));

const shortHash = (value: string) => value ? `sha ${value.slice(0, 10)}` : '无哈希';
const moduleIdsFor = (documentId: string) => {
  const ids = new Set(modules.value.filter(module => module.documentId === documentId).map(module => module.id));
  return form.moduleIds.filter(id => ids.has(id));
};
const updateDocumentModules = (documentId: string, value: unknown) => {
  const ids = new Set(modules.value.filter(module => module.documentId === documentId).map(module => module.id));
  const retained = form.moduleIds.filter(id => !ids.has(id));
  const selected = Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
  form.moduleIds = [...retained, ...selected];
};

async function loadSkills() {
  if (!projectStore.currentProjectId) return;
  loadingSkills.value = true;
  try {
    const catalog = await getWorkflowStageCatalog(projectStore.currentProjectId);
    planSkills.value = catalog.skills.filter(skill => skill.runnable && skill.declared_stage === 'test_plan_generation');
    const stage = catalog.stages.find(item => item.stage === 'test_plan_generation');
    // 受控模式下版本由流程锁定，**不能**用"当前默认版本"覆盖它：
    // 那会让页面显示 A、流程记录的是 B，事后谁都不认账。
    // 受控模式的取值统一在 resolveExecutionContext 里落下。
    if (!executionContextId.value) {
      form.skillVersionId = stage?.default?.skill_version_id || planSkills.value[0]?.skill_version_id || '';
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '加载 Skill 列表失败');
  } finally {
    loadingSkills.value = false;
  }
}

async function loadDocuments() {
  if (!projectStore.currentProjectId) return;
  loadingDocuments.value = true;
  try {
    const response = await RequirementDocumentService.getDocumentList({ project: String(projectStore.currentProjectId), page_size: 1000 });
    documents.value = response.status === 'success' ? toArray<RequirementDocument>(response.data && 'results' in response.data ? response.data.results : response.data) : [];
  } finally {
    loadingDocuments.value = false;
  }
}

async function handleDocumentChange(value: unknown) {
  const ids = Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
  form.documentIds = ids;
  form.moduleIds = [];
  modules.value = [];
  if (!ids.length) return;
  loadingModules.value = true;
  try {
    const details = await Promise.all(ids.map(id => RequirementDocumentService.getDocumentDetail(id)));
    modules.value = details.flatMap((response, index) => {
      const doc = documents.value.find(item => item.id === ids[index]);
      if (response.status !== 'success' || !response.data || !doc) return [];
      return (response.data.modules || []).map(module => ({ ...module, documentId: doc.id, documentTitle: doc.title, documentCategory: doc.document_category }));
    });
  } finally {
    loadingModules.value = false;
  }
}

async function loadAuxiliaryData() {
  loadingKnowledge.value = true;
  try {
    const kbResponse = await KnowledgeService.getKnowledgeBases({});
    knowledgeBases.value = toArray<KnowledgeBase>('results' in kbResponse ? kbResponse.results : kbResponse);
  } catch {
    Message.warning('知识库配置加载失败，不影响使用已加载的文档生成');
  } finally {
    loadingKnowledge.value = false;
  }
}

function buildMessage(selectedModules: SelectedModule[]) {
  const context = selectedModules.map((module, index) => `---\n[文档来源]\n${categoryLabels[module.documentCategory]}：《${module.documentTitle}》（ID: ${module.documentId}）\n\n[模块 ${index + 1}] ${module.title}\n${module.content}\n${module.confirmed_image_context ? `\n[人工确认的图片识别上下文]\n${module.confirmed_image_context}` : ''}\n---`).join('\n\n');
  return `请基于以下已确认文档模块生成可执行、可追溯的测试方案。平台会在运行时注入本轮锁定的 Skill，请遵循该 Skill 的具体操作要求。

除完整方案外，必须产出测试点清单。每条测试点至少包含：稳定 ID、所属模块、标题或测试目标、场景类型、优先级、关联需求 ID、预期结果；无法从材料确认的内容放入“不确定项”，禁止臆造。测试点不得只写在概述中，必须同时进入主产物和 stage_result.json 的 items。

人工交付物必须同时生成：{项目名}_测试方案_V{版本}.xlsx、{项目名}_测试方案说明_V{版本}.md、requirements_parsed.json。版本缺失时使用 1.0。Excel 固定包含测试点明细、目标与范围、策略、环境与数据、进入与退出准则、风险与待确认项、需求功能点与覆盖工作表。

另外生成符合 stage-result/v1 的 stage_result.json，仅用于质量飞轮，不计入三件人工交付物。最终回复不要粘贴方案正文，只列出文件名与一句话说明。

${context}

[补充要求]
${form.extraRequirements || '无'}`;
}

// 本页专属的会话键。不能只靠全局的 langgraph_session_id —— 那个键被 LLM 对话页共用，
// 拿回来可能是用例生成等其它用途的会话，会把无关产物显示到本页。
function planSessionKey() {
  return `wharttest_plan_session:${projectStore.currentProjectId || ''}`;
}

// 历史里的工具消息同样带 file 附件，解析方式与 LLM 对话页完全一致
function collectHistory(history: ChatHistoryMessage[]) {
  const byUrl = new Map<string, ToolFileAttachment>();
  const texts: string[] = [];
  for (const item of history) {
    if (item.type === 'tool') {
      const payload = parseToolResultDisplayPayload(item.content, item.tool_input);
      for (const file of payload.fileAttachments) {
        if (file?.url) byUrl.set(file.url, file);
      }
    } else if (item.type === 'ai' && item.content) {
      texts.push(item.content);
    }
  }
  return { files: [...byUrl.values()], text: texts.join('\n\n') };
}

function applyHistory(history: ChatHistoryMessage[]) {
  const { files, text } = collectHistory(history);
  historyArtifacts.value = files;
  historyContent.value = text;
}

// 进入页面时把上一次生成会话的产物捞回来（生成在 LLM 对话里继续跑完的情况也覆盖）。
// 优先用本页记录的会话；没有记录时退回全局会话，但必须能解析出方案交付物才认领，
// 否则会把用例生成等无关会话的产物显示到本页。
async function loadSessionArtifacts() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  const owned = localStorage.getItem(planSessionKey()) || '';
  const fallback = localStorage.getItem('langgraph_session_id') || '';
  const candidates: Array<{ id: string; strict: boolean }> = [];
  if (owned) candidates.push({ id: owned, strict: false });
  if (fallback && fallback !== owned) candidates.push({ id: fallback, strict: true });

  restoring.value = true;
  try {
    for (const candidate of candidates) {
      const response = await getChatHistory(candidate.id, projectId, 500, 0);
      if (response.status !== 'success' || !response.data) continue;
      const { files, text } = collectHistory(response.data.history || []);
      if (candidate.strict && !files.some(file => DELIVERABLE_HINT.test(file.name))) continue;
      sessionId.value = response.data.session_id || candidate.id;
      historyArtifacts.value = files;
      historyContent.value = text;
      localStorage.setItem(planSessionKey(), sessionId.value);
      return;
    }
    if (owned) localStorage.removeItem(planSessionKey());
  } catch {
    // 历史拉取失败不影响继续发起新的生成
  } finally {
    restoring.value = false;
  }
}

// 手动刷新：在 LLM 对话里补充生成后，不必刷新整页就能拿到新产物
async function refreshArtifacts() {
  const projectId = projectStore.currentProjectId;
  if (!sessionId.value) return Message.info('本次还没有生成会话');
  if (!projectId) return Message.warning('请先选择项目');
  restoring.value = true;
  try {
    const response = await getChatHistory(sessionId.value, projectId, 500, 0);
    if (response.status !== 'success' || !response.data) {
      return Message.error('产物刷新失败，请稍后重试');
    }
    applyHistory(response.data.history || []);
    Message.success(allArtifacts.value.length ? `已刷新，共 ${allArtifacts.value.length} 个文件` : '已刷新，暂未解析到产物文件');
  } finally {
    restoring.value = false;
  }
}

// 与用例生成保持一致：把本次会话与检索配置写入本地存储，跳转 LLM 对话后可恢复上下文
function persistConversationContext(id: string) {
  localStorage.setItem(planSessionKey(), id);
  localStorage.setItem('langgraph_session_id', id);
  localStorage.setItem('langgraph_knowledge_settings', JSON.stringify({
    useKnowledgeBase: form.knowledgeBaseIds.length > 0,
    selectedKnowledgeBaseIds: form.knowledgeBaseIds,
    knowledgeDocumentScope: form.knowledgeDocumentScope,
    selectedKnowledgeDocumentIds: form.knowledgeDocumentIds,
    similarityThreshold: 0.3,
    topK: 5,
  }));
}

function notifyGenerationStarted(id: string) {
  const notification = Notification.info({
    title: '测试方案生成已开始',
    content: 'Skill 正在生成测试方案，完成后产出文件会列在“生成结果”面板中。',
    footer: () => h(
      'div',
      { style: 'text-align: right; margin-top: 12px;' },
      [
        h(
          'a',
          {
            href: 'javascript:;',
            onClick: () => {
              openConversation();
              notification?.close();
            },
          },
          '点此查看生成过程'
        ),
      ]
    ),
    duration: 10000,
    id: `plan-generate-${id}`,
  });
}

/**
 * 解析受控执行上下文（T03 / §4.3）。
 *
 * 解析失败**不降级成旁路模式**：受控运行的产出必须归属到流程与锁定版本，静默降级
 * 等于制造一条没有版本溯源的产出，事后既归因不了也回滚不了。所以失败时只暴露
 * 恢复入口（回飞轮重新派发），页面本身保持不可执行。
 *
 * 锁定版本还会被补进 `planSkills`：若该版本未被声明为"方案生成"（例如发起流程时
 * 由负责人显式指定），下拉里就没有它的选项，选择框会显示成空——页面看起来像
 * "没选版本"，而实际用的是锁定版本，又是一处两套说法。
 */
async function resolveExecutionContext() {
  const contextId = executionContextId.value;
  const projectId = projectStore.currentProjectId;
  if (!contextId || !projectId) return;
  loadingContext.value = true;
  contextError.value = '';
  try {
    const view = await getExecutionContext(projectId, contextId);
    executionContext.value = view;
    if (view.skill.skill_version_id) {
      if (!planSkills.value.some(skill => skill.skill_version_id === view.skill.skill_version_id)) {
        planSkills.value = [{
          skill_id: view.skill.skill_id,
          skill_name: view.skill.skill_name,
          description: '',
          declared_stage: view.stage,
          declared_stage_label: view.stage_label,
          runnable: true,
          skill_version_id: view.skill.skill_version_id,
          version: view.skill.version,
          release_state: '',
          package_sha256: view.skill.package_sha256,
          updated_at: '',
        }, ...planSkills.value];
      }
      form.skillVersionId = view.skill.skill_version_id;
    }
  } catch (error) {
    executionContext.value = null;
    contextError.value = error instanceof Error
      ? error.message
      : '执行上下文已过期或不再可信，请回到质量飞轮重新派发';
  } finally {
    loadingContext.value = false;
  }
}

/** 上下文失效后的恢复入口：回飞轮重新派发（只新建上下文，attempt 不变）。 */
function backToFlywheel() {
  router.push({ name: 'KnowledgeEvolution' });
}

async function generatePlan() {
  if (!projectStore.currentProjectId) return Message.warning('请先选择项目');
  // 受控模式下上下文没解析成功就不允许执行：宁可让用户回飞轮重派，
  // 也不要让这一轮跑到流程外面去——那样产出的版本溯源就断了。
  if (executionContextId.value && !executionContext.value) {
    return Message.error(contextError.value || '正在解析执行上下文，请稍候再试');
  }
  if (!form.skillVersionId) return Message.warning('请选择 Skill 版本');
  if (!form.documentIds.length) return Message.warning('请选择文档');
  const selectedModules = modules.value.filter(module => form.moduleIds.includes(module.id));
  if (!selectedModules.length || form.documentIds.some(id => !selectedModules.some(module => module.documentId === id))) {
    return Message.warning('请为每份文档至少选择一个模块');
  }
  if (form.knowledgeDocumentScope === 'selected' && form.knowledgeBaseIds.length && !form.knowledgeDocumentIds.length) {
    return Message.warning('请选择要检索的知识库文档');
  }
  generating.value = true;
  sessionId.value = '';
  historyArtifacts.value = [];
  historyContent.value = '';
  const request: ChatRequest = {
    message: buildMessage(selectedModules),
    project_id: String(projectStore.currentProjectId),
    module_key: 'test_plan_generation',
    skill_version_id: form.skillVersionId,
    use_knowledge_base: form.knowledgeBaseIds.length > 0,
    knowledge_base_ids: form.knowledgeBaseIds,
    knowledge_document_ids: form.knowledgeDocumentScope === 'selected' ? form.knowledgeDocumentIds : undefined,
    workflow_id: workflowId.value || undefined,
    parent_output_ids: parentOutputIds.value.length ? parentOutputIds.value : undefined,
    // 受控模式下必须带上 attempt_id：Agent 据此把这条执行尝试推进到 running
    // 并绑定 session_id，正式产出发布后再回写 attempt → output。
    // 没有它，飞轮只看到"已派发"，永远不知道这一轮跑起来没有。
    attempt_id: executionContext.value?.attempt_id || undefined,
  };
  try {
    await sendChatMessageStream(request, id => {
      sessionId.value = id;
      persistConversationContext(id);
      notifyGenerationStarted(id);
    }, undefined, message => Message.error(message));
  } finally {
    generating.value = false;
  }
}

function openConversation() {
  // 跳转前再写一次，覆盖“生成中途离开”与“刷新后仅剩 sessionId”两种情况
  if (sessionId.value) {
    localStorage.setItem(planSessionKey(), sessionId.value);
    localStorage.setItem('langgraph_session_id', sessionId.value);
  }
  router.push({ name: 'LangGraphChat' });
}

onMounted(async () => {
  await loadAuxiliaryData();
  await loadHistoricalOutputs();
});

// projectStore 的当前项目在 onMounted 时可能尚未恢复，用 watch 保证恢复后自动加载，
// 否则 Skill 与文档下拉会一直为空（守卫静默跳过，不报错）。
watch(() => projectStore.currentProjectId, async (projectId) => {
  if (!projectId) return;
  form.skillVersionId = '';
  form.documentIds = [];
  form.moduleIds = [];
  modules.value = [];
  documents.value = [];
  // 切项目后旧的会话与产物都不再适用，先清空再按新项目回填
  sessionId.value = '';
  historyArtifacts.value = [];
  historyContent.value = '';
  await Promise.all([loadSkills(), loadDocuments(), loadSessionArtifacts(), loadHistoricalOutputs()]);
  // 上下文解析必须排在 Skill 加载**之后**：受控模式下它会用锁定版本覆盖选择器，
  // 顺序相反时 loadSkills 的"默认版本"会盖掉锁定版本，
  // 于是页面显示 A、流程记录的是 B —— 正是本设计要消灭的两套说法。
  await resolveExecutionContext();
}, { immediate: true });
</script>

<style scoped>
.plan-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 20px;
  overflow-x: hidden;
  overflow-y: auto;
  background: var(--color-fill-1);
  scrollbar-gutter: stable;
}
.context-alert { margin-bottom: 16px; }
/* 受控模式横幅的字段行：流程 / 锁定版本 / 上游产出 / attempt 各自成行。
   这些值是要被核对与抄录的，挤成一整句话会让人漏看其中一项。 */
.context-lines { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.context-lines code { padding: 1px 5px; border-radius: 4px; background: var(--color-fill-2); font-size: 12px; }
.module-groups { display: grid; gap: 12px; width: 100%; }
.module-row { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 12px; align-items: center; }
.module-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-2); }
.option-meta { float: right; color: var(--color-text-3); font-size: 12px; }
.actions { display: flex; gap: 12px; justify-content: flex-end; }

.result-card { min-height: 620px; }
.result-card-link { font-size: 13px; }
.result-card-actions { display: inline-flex; gap: 12px; align-items: center; }
.result-body { display: flex; flex-direction: column; gap: 12px; min-height: 520px; }
.result-status { display: flex; align-items: center; gap: 8px; }
.result-step { color: var(--color-text-3); font-size: 12px; }
.result-alert { margin: 0; }

.progress-panel { display: flex; flex-direction: column; gap: 8px; padding: 12px; background: var(--color-fill-2); border-radius: 6px; }
.progress-hint { text-align: left; color: var(--color-text-3); font-size: 13px; line-height: 1.7; }
.progress-log { display: flex; flex-direction: column; gap: 6px; }
.progress-log-item { display: flex; gap: 8px; align-items: baseline; font-size: 12px; line-height: 1.5; }
.progress-log-name { flex: 0 0 auto; padding: 0 6px; color: var(--color-text-2); background: var(--color-bg-2); border-radius: 4px; }
.progress-log-summary { min-width: 0; overflow: hidden; color: var(--color-text-3); text-overflow: ellipsis; white-space: nowrap; }

.artifact-section { display: flex; flex-direction: column; gap: 8px; }
/* 平台布局层带 text-align: center，纯文本块会跟着居中；这里显式拉回左对齐 */
.artifact-title { text-align: left; color: var(--color-text-2); font-size: 13px; font-weight: 500; }
.artifact-list { display: flex; flex-direction: column; gap: 8px; }
.artifact-item { display: flex; gap: 10px; align-items: center; padding: 10px 12px; background: var(--color-fill-2); border-radius: 6px; }
.artifact-icon { flex: 0 0 auto; color: rgb(var(--arcoblue-6)); font-size: 16px; }
.artifact-main { flex: 1 1 auto; min-width: 0; }
.artifact-name { text-align: left; overflow: hidden; color: var(--color-text-1); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.artifact-meta { display: flex; gap: 8px; color: var(--color-text-3); font-size: 12px; }
.artifact-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.artifact-action { color: rgb(var(--arcoblue-6)); font-size: 13px; text-decoration: none; }
.artifact-action:hover { text-decoration: underline; }
.artifact-action-primary { font-weight: 500; }

.other-collapse { margin-top: 4px; }
.other-collapse :deep(.arco-collapse-item-header) { padding-right: 0; padding-left: 0; color: var(--color-text-3); font-size: 13px; }
.artifact-item-quiet { padding: 8px 10px; background: transparent; border: 1px solid var(--color-border-2); }
.artifact-icon-quiet { color: var(--color-text-3); }
.artifact-history-hint { margin: 0 0 8px; color: var(--color-text-3); font-size: 12px; }

.empty-hint { color: var(--color-text-3); font-size: 13px; line-height: 1.7; }
.empty-hint p { margin: 0; }

.raw-collapse { margin-top: auto; border-top: 1px solid var(--color-border-2); }
.result-content { max-height: 320px; margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--color-fill-2); border-radius: 6px; font: inherit; font-size: 12px; line-height: 1.7; }
.historical-output-item { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.historical-output-head { display: flex; justify-content: space-between; color: var(--color-text-3); font-size: 12px; }
.historical-output-content { max-height: 220px; }
</style>
