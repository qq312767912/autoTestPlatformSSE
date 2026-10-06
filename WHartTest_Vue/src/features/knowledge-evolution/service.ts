import axios from 'axios';
import { useAuthStore } from '@/store/authStore';
import type {
  EvaluationSuite,
  EvaluationRun,
  EvaluationResult,
  FeedbackEvent,
  KnowledgeCandidate,
  RetrievalTrace,
  CapabilityDefinition,
  CapabilityRelease,
  CreateFeedbackRequest,
  CreateCandidateFromRunRequest,
  RunEvolutionRequest,
  RunEvolutionResponse,
  GoldDataset,
  ExecutionSpan,
  FailureAttribution,
  OptimizationProposal,
  GenerationOutput,
  ProjectQualityCockpit,
  StartWorkflowResult,
  StageExecutionPlan,
  ExecutionContextView,
  StageOutputView,
  WorkflowStageCatalog,
  WorkflowStatusView,
  CaseReviewEvolutionCandidate,
  CaseReviewEvolutionPreflight,
  CaseReviewEvolutionResult,
  CaseReviewReportFeedbackResult,
  OptimizationDecisionResult,
  OptimizationProposalResult,
  OutputLineageView,
  StageFeedbackResult,
  StageExecutionAttemptView,
  StageAttemptTraceView,
  StageReviewStatusView,
  StageReviewUploadResult,
  StageAttachmentView,
  StageAttachmentCatalog,
  StageDiffView,
  SkillContentPlanView,
  AssetCandidateEvent,
  AssetCandidateStats,
  GoldCase,
  GoldAnnotation,
  GoldDatasetVersion,
  TestAssetTaxonomy,
  AnnotationConflict,
  HistoryImportBatch,
  HistoryReplay,
} from './types';

const BASE = '/api/knowledge-evolution';

function getHeaders() {
  const authStore = useAuthStore();
  const token = authStore.getAccessToken;
  return {
    Authorization: token ? `Bearer ${token}` : '',
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };
}

export async function listRetrievalTraces(params?: {
  project?: number;
  task_type?: string;
  status?: string;
}): Promise<RetrievalTrace[]> {
  const data = await get<RetrievalTrace[] | { results: RetrievalTrace[] }>('/retrieval-traces/', params);
  return Array.isArray(data) ? data : (data?.results ?? []);
}

async function request<T>(method: string, path: string, data?: unknown, params?: Record<string, unknown>): Promise<T> {
  const res = await axios({
    method,
    url: `${BASE}${path}`,
    params,
    data,
    headers: getHeaders(),
  });
  return (res.data?.data ?? res.data) as T;
}

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  return request<T>('get', path, undefined, params);
}

async function post<T>(path: string, data?: unknown): Promise<T> {
  return request<T>('post', path, data);
}

async function patch<T>(path: string, data?: unknown): Promise<T> {
  return request<T>('patch', path, data);
}

const rows = <T>(data: T[] | { results: T[] }): T[] => Array.isArray(data) ? data : (data?.results ?? []);

export async function listGoldDatasetVersions(dataset?: string): Promise<GoldDatasetVersion[]> {
  return rows(await get<GoldDatasetVersion[] | {results:GoldDatasetVersion[]}>('/gold-dataset-versions/', dataset ? {dataset} : undefined));
}
export async function freezeGoldDatasetVersion(id: string): Promise<GoldDatasetVersion> {
  return post<GoldDatasetVersion>(`/gold-dataset-versions/${id}/freeze/`);
}
export async function listGoldCases(params: {version?:string;state?:string}): Promise<GoldCase[]> {
  return rows(await get<GoldCase[] | {results:GoldCase[]}>('/gold-cases/', params));
}
export async function listAnnotationConflicts(params?: {case?:string;state?:string}): Promise<AnnotationConflict[]> {
  return rows(await get<AnnotationConflict[] | {results:AnnotationConflict[]}>('/annotation-conflicts/', params));
}
export async function resolveAnnotationConflict(id: string, payload: Record<string, unknown>): Promise<GoldAnnotation> {
  return post<GoldAnnotation>(`/annotation-conflicts/${id}/resolve/`, payload);
}
export async function listTestAssetTaxonomies(project: number): Promise<TestAssetTaxonomy[]> {
  return rows(await get<TestAssetTaxonomy[] | {results:TestAssetTaxonomy[]}>('/test-asset-taxonomies/', {project}));
}
export async function createTestAssetTaxonomy(payload: Partial<TestAssetTaxonomy>): Promise<TestAssetTaxonomy> {
  return post<TestAssetTaxonomy>('/test-asset-taxonomies/', payload);
}
export async function submitTestAssetTaxonomy(id:string): Promise<TestAssetTaxonomy> { return post<TestAssetTaxonomy>(`/test-asset-taxonomies/${id}/submit/`); }
export async function publishTestAssetTaxonomy(id:string): Promise<TestAssetTaxonomy> { return post<TestAssetTaxonomy>(`/test-asset-taxonomies/${id}/publish/`); }
export async function listHistoryImports(project:number): Promise<HistoryImportBatch[]> {
  return rows(await get<HistoryImportBatch[] | {results:HistoryImportBatch[]}>('/history-imports/', {project}));
}
export async function preflightHistoryImport(project:number, manifest:Record<string,unknown>): Promise<Record<string,unknown>> { return post('/history-imports/preflight/', {project,manifest}); }
export async function confirmHistoryImport(project:number, confirmation_token:string): Promise<HistoryImportBatch> { return post('/history-imports/confirm/', {project,confirmation_token}); }
export async function listHistoryReplays(project:number): Promise<HistoryReplay[]> {
  return rows(await get<HistoryReplay[] | {results:HistoryReplay[]}>('/history-replays/', {project}));
}
export async function startHistoryReplay(payload:Record<string,unknown>): Promise<HistoryReplay> { return post('/history-replays/start/', payload); }
export async function decideHistoryDifference(replayId:string,difference:string,decision:string,note=''): Promise<unknown> { return post(`/history-replays/${replayId}/decide-difference/`,{difference,decision,note}); }
export async function getFlywheelSetting(project:number): Promise<{project:number;enabled:boolean;rollout_note:string}|null> {
  const values=rows(await get<Array<{project:number;enabled:boolean;rollout_note:string}>|{results:Array<{project:number;enabled:boolean;rollout_note:string}>}>('/flywheel-settings/', {project}));
  return values[0]||null;
}
export async function setFlywheelSetting(project:number,enabled:boolean,rollout_note=''): Promise<{project:number;enabled:boolean;rollout_note:string}> {
  return post('/flywheel-settings/set/',{project,enabled,rollout_note});
}

export async function listEvaluationSuites(projectId: number): Promise<EvaluationSuite[]> {
  return get<EvaluationSuite[]>('/evaluation-suites/', { project: projectId });
}

export async function createEvaluationSuite(data: Partial<EvaluationSuite>): Promise<EvaluationSuite> {
  return post<EvaluationSuite>('/evaluation-suites/', data);
}

export async function listEvaluationRuns(suiteId?: string): Promise<EvaluationRun[]> {
  return get<EvaluationRun[]>('/evaluation-runs/', suiteId ? { suite: suiteId } : undefined);
}

export async function createEvaluationRun(data: {
  suite: string;
  name?: string;
  policy_version?: string;
  model_name?: string;
}): Promise<EvaluationRun> {
  // 后端的 policy_version / model_name 是只读的 SerializerMethodField，
  // 真值来自 run.config，必须放进 config 才不会被丢弃。
  return post<EvaluationRun>('/evaluation-runs/', {
    suite: data.suite,
    name: data.name || '',
    config: {
      policy_version: data.policy_version || 'default-policy@v3',
      model_version: data.model_name || 'qwen3-coder-plus',
    },
  });
}

export async function listEvaluationResults(runId?: string): Promise<EvaluationResult[]> {
  return get<EvaluationResult[]>('/evaluation-results/', runId ? { run: runId } : undefined);
}

export async function generateCandidatesFromRun(
  runId: string,
  data: CreateCandidateFromRunRequest
): Promise<{ created_count: number; candidate_ids: string[] }> {
  return post<{ created_count: number; candidate_ids: string[] }>(`/evaluation-runs/${runId}/generate-review-candidates/`, data);
}

export async function listFeedbackEvents(params?: {
  project?: number;
  output?: string;
  trace?: string;
  signal?: string;
}): Promise<FeedbackEvent[]> {
  const data = await get<FeedbackEvent[] | { results: FeedbackEvent[] }>('/feedback/', params);
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function createFeedbackEvent(data: CreateFeedbackRequest): Promise<FeedbackEvent> {
  return post<FeedbackEvent>('/feedback/', data);
}

export async function listKnowledgeCandidates(params?: {
  project?: number;
  state?: string;
}): Promise<KnowledgeCandidate[]> {
  const data = await get<KnowledgeCandidate[] | { results: KnowledgeCandidate[] }>('/knowledge-candidates/', params);
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function updateCandidateState(id: string, state: string, reviewReason?: string): Promise<KnowledgeCandidate> {
  return patch<KnowledgeCandidate>(`/knowledge-candidates/${id}/`, { state, review_reason: reviewReason });
}

export async function listCapabilityDefinitions(projectId: number): Promise<CapabilityDefinition[]> {
  const data = await get<CapabilityDefinition[] | { results: CapabilityDefinition[] }>('/capability-definitions/', { project: projectId });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function createCapabilityDefinition(data: Partial<CapabilityDefinition>): Promise<CapabilityDefinition> {
  return post<CapabilityDefinition>('/capability-definitions/', data);
}

export async function updateCapabilityDefinition(id: string, data: Partial<CapabilityDefinition>): Promise<CapabilityDefinition> {
  return patch<CapabilityDefinition>(`/capability-definitions/${id}/`, data);
}

export async function runCapabilityEvolution(
  id: string,
  data: RunEvolutionRequest
): Promise<RunEvolutionResponse> {
  return post<RunEvolutionResponse>(`/capability-definitions/${id}/run-evolution/`, data);
}

export async function activateCapabilityRelease(definitionId: string, releaseId: string): Promise<CapabilityDefinition> {
  return post<CapabilityDefinition>(`/capability-definitions/${definitionId}/activate-release/`, { release_id: releaseId });
}

export async function listCapabilityReleases(params?: { project?: number; kind?: string; state?: string }): Promise<CapabilityRelease[]> {
  const data = await get<CapabilityRelease[] | { results: CapabilityRelease[] }>('/capability-releases/', params);
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function listGoldDatasets(projectId: number): Promise<GoldDataset[]> {
  const data = await get<GoldDataset[] | { results: GoldDataset[] }>('/gold-datasets/', { project: projectId });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function listAssetCandidateEvents(projectId: number, status?: string): Promise<AssetCandidateEvent[]> {
  const data = await get<AssetCandidateEvent[] | { results: AssetCandidateEvent[] }>('/asset-candidates/', {
    project: projectId,
    ...(status ? { status } : {}),
  });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function getAssetCandidateStats(projectId: number): Promise<AssetCandidateStats> {
  const raw = await get<{ by_status?: Record<string, number> }>('/asset-candidates/stats/', { project: projectId });
  const counts = raw.by_status ?? {};
  const value = (key: string) => Number(counts[key] ?? 0);
  return {
    total: Object.values(counts).reduce((sum, count) => sum + Number(count || 0), 0),
    pending: value('pending'),
    processing: value('processing'),
    needs_review: value('needs_review'),
    completed: value('completed'),
    failed: value('failed'),
    dead_letter: value('dead_letter'),
  };
}

export async function retryAssetCandidate(eventId: string): Promise<AssetCandidateEvent> {
  return post<AssetCandidateEvent>(`/asset-candidates/${eventId}/retry/`);
}

export async function retryFailedAssetCandidates(projectId: number): Promise<{ project_id: number; retried: number }> {
  return post<{ project_id: number; retried: number }>('/asset-candidates/retry/', {
    project: projectId,
    statuses: ['failed', 'dead_letter'],
  });
}

export async function getGoldCase(id: string): Promise<GoldCase> {
  return get<GoldCase>(`/gold-cases/${id}/`);
}

export async function annotateGoldCase(id: string, data: {
  round: 'primary' | 'review';
  answer: Record<string, unknown>;
  evidence: unknown[];
  conclusion: 'accepted' | 'rejected' | 'needs_changes';
  comment: string;
  tags: string[];
  split: string;
  category: string;
}): Promise<GoldAnnotation> {
  return post<GoldAnnotation>(`/gold-cases/${id}/annotate/`, data);
}

export async function listExecutionSpans(traceId: string): Promise<ExecutionSpan[]> {
  const data = await get<ExecutionSpan[] | { results: ExecutionSpan[] }>('/execution-spans/', { trace: traceId });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function listFailureAttributions(projectId: number): Promise<FailureAttribution[]> {
  const data = await get<FailureAttribution[] | { results: FailureAttribution[] }>('/failure-attributions/', { project: projectId });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function listOptimizationProposals(projectId: number): Promise<OptimizationProposal[]> {
  const data = await get<OptimizationProposal[] | { results: OptimizationProposal[] }>('/optimization-proposals/', { project: projectId });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function getGenerationOutputsByTask(projectId: number, taskType: string, taskId: string): Promise<GenerationOutput[]> {
  const data = await get<GenerationOutput[] | { results: GenerationOutput[] }>('/generation-outputs/', {
    project: projectId,
    task_type: taskType,
    task_id: taskId,
  });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function listFailureAttributionsForOutput(outputId: string): Promise<FailureAttribution[]> {
  const data = await get<FailureAttribution[] | { results: FailureAttribution[] }>('/failure-attributions/', { output: outputId });
  return Array.isArray(data) ? data : (data?.results ?? []);
}

export async function submitFeedbackOutcome(payload: {
  output_id?: string;
  trace_id?: string;
  signal: CreateFeedbackRequest['signal'];
  reason_code?: string;
  comment?: string;
  detail?: Record<string, unknown>;
}): Promise<FeedbackEvent> {
  return post<FeedbackEvent>('/feedback/outcomes/', payload);
}

export async function getProjectQualityCockpit(projectId: number): Promise<ProjectQualityCockpit> {
  return get<ProjectQualityCockpit>('/operations/cockpit/', { project: projectId });
}

export async function evaluateWorkflowStage(projectId: number, workflowId: string, stage: string): Promise<unknown> {
  return post('/operations/evaluate-workflow-stage/', { project: projectId, workflow_id: workflowId, stage });
}

export async function overrideWorkflowStage(projectId: number, workflowId: string, stage: string, reason: string): Promise<unknown> {
  return post('/operations/override-workflow-stage/', { project: projectId, workflow_id: workflowId, stage, reason });
}

/**
 * 人工确认放行：**不要求先有评分**，确认后该阶段即可进入下一阶段。
 *
 * 与 `overrideWorkflowStage` 的区别是语义强度，不是接口：确认处理"尚无结论"
 * （待测评 / 无评分），放行处理"已有负面结论"（评测失败，必须填原因）。
 * `reason` 可空——但留痕照写，事后能分清"有人确认过"和"没人管"。
 */
export async function confirmWorkflowStage(projectId: number, workflowId: string, stage: string, reason = ''): Promise<unknown> {
  return post('/operations/confirm-workflow-stage/', { project: projectId, workflow_id: workflowId, stage, reason });
}

/**
 * 人工评分（**百分制**输入，后端按 0-1 存储）。
 *
 * >= 阈值判通过，< 阈值判未通过——人工评分同样会有"不达标"，不会因为是人打的
 * 就一律放行；不打分直接放行是 `confirmWorkflowStage` 的事。
 * 刻度对外百分制、对内 0-1，是为了让 `threshold`（默认 0.7）只有一种解释。
 */
export async function scoreWorkflowStage(projectId: number, workflowId: string, stage: string, score: number, reason = ''): Promise<unknown> {
  return post('/operations/score-workflow-stage/', { project: projectId, workflow_id: workflowId, stage, score, reason });
}

/**
 * 「执行本阶段」：前置门禁校验 + 执行参数下发。
 *
 * 它**不会**替你把阶段跑起来——平台只有测试执行有真正的执行器，另外三个阶段的
 * 产出由 agent 经 agent-loop 提交。返回里的 `channel` / `entry` / `hint` /
 * `module_key` / `parent_output_ids` 就是"这一阶段该由谁、带什么参数去跑"，
 * 界面必须如实呈现，不能做成一个跑没跑都不知道的按钮。
 */
export async function executeWorkflowStage(projectId: number, workflowId: string, stage: string): Promise<StageExecutionPlan> {
  return post<StageExecutionPlan>('/operations/execute-workflow-stage/', { project: projectId, workflow_id: workflowId, stage });
}

/** 从工作台移除指定旧批次；服务端保留执行与产出审计记录。 */
export async function deleteWorkflowBatch(projectId: number, workflowId: string): Promise<{ workflow_id: string; deleted: boolean }> {
  return post('/operations/delete-workflow-batch/', { project: projectId, workflow_id: workflowId });
}

/**
 * 解析执行上下文（T02 / R3）：业务页面唯一的可信取值入口。
 *
 * 页面只携带 `execution_context_id`，流程、阶段、锁定的 SkillVersion 和上游产出
 * 都由服务端按 id + 当前项目解析。**不要**改成从 URL 直接读 workflow_id /
 * skill_version_id：那些是可篡改的字符串，一旦被当成版本依据，
 * 受控运行就能用上流程没有锁定的包而流程记录仍显示"用的是锁定版本"。
 *
 * 解析失败（过期 / 跨项目 / 版本漂移）会抛错，页面据此给出"回飞轮重新派发"的入口，
 * 而不是静默降级成旁路模式——那等于把受控运行悄悄变成了不受控。
 */
export async function getExecutionContext(projectId: number, contextId: string): Promise<ExecutionContextView> {
  return get<ExecutionContextView>(`/execution-contexts/${contextId}/`, { project: projectId });
}

/** 「查看结果」：按 project + workflow_id + stage 三元定位读取产出正文与门禁证据。 */
export async function getStageOutput(projectId: number, workflowId: string, stage: string): Promise<StageOutputView> {
  return get<StageOutputView>('/operations/workflow-stage-output/', { project: projectId, workflow_id: workflowId, stage });
}

/**
 * 启动四阶段流水线：后端会**一次性锁定四个阶段的 Skill 版本**。
 *
 * `pins` 是「阶段 → SkillVersion ID」：向导里人逐个阶段显式选具体版本时传进来。
 * 不传则沿用后端按 manifest 声明的默认解析（保持老调用方的行为不变）。
 *
 * 发起向导按 `declared_stage` 严格分组，只允许从当前阶段的下拉列表选择具体版本；
 * 同一 Skill 的多个版本分别作为候选，最终选中的 SkillVersion ID 会被锁进本次流程。
 *
 * 返回值里的 `unmanaged_stages` 必须展示给发起人——它列出项目尚未登记
 * 可用 Skill 版本、因而没有版本溯源的阶段。等跑到报告阶段才暴露这个问题，
 * 前面阶段的算力与人工就白费了。
 */
export async function startWorkflow(projectId: number, workflowId: string, pins?: Record<string, string>): Promise<StartWorkflowResult> {
  return post<StartWorkflowResult>('/operations/start-workflow/', {
    project: projectId,
    workflow_id: workflowId,
    ...(pins && Object.keys(pins).length ? { pins } : {}),
  });
}

/** 四类入口统一的「创建或选择流程上下文」结果。 */
export interface FlywheelRunContext {
  run_id: string;
  project_id: number;
  workflow_id: string;
  entry_type: string;
  intent: string;
  status: string;
  /** 本次是否新建了流程（false = 汇入了已有链）。 */
  created: boolean;
  /** workflow_id 是否由后端派生（true = 用户没填、由入口自动生成）。 */
  derived: boolean;
}

/**
 * 创建或选择流程上下文（T06）。
 *
 * 页面**不再要求用户手工填/复制 workflow_id**：不传 `workflow_id` 时后端按
 * `entry_type + source_id` 确定性派生（人工入口给可读的唯一标识）。派生规则只在
 * 后端一处，否则同一份需求从需求页和飞轮页各发起一次就会开出两条链。
 */
export async function openFlywheelRun(payload: {
  project: number;
  entry_type: 'requirement' | 'chat' | 'test_management' | 'flywheel' | 'history_replay';
  source_id?: string;
  workflow_id?: string;
  requirement_document_ids?: string[];
  intent?: string;
  metadata?: Record<string, unknown>;
}): Promise<FlywheelRunContext> {
  return post<FlywheelRunContext>('/flywheel-runs/open/', payload);
}

/**
 * 发起向导第一步的数据源：按阶段给出候选包与默认包。
 *
 * 前端**不自己算默认包**。两处各写一套"哪个包管哪个阶段"的判断，
 * 改一处就会出现"页面显示的默认项和你实际锁定的项不是一个"。
 */
export async function getWorkflowStageCatalog(projectId: number): Promise<WorkflowStageCatalog> {
  return get<WorkflowStageCatalog>('/operations/workflow-stage-catalog/', { project: projectId });
}

/**
 * 单条流程的四阶段状态**唯一真值入口**。
 *
 * 刻意不读"当前活跃版本"：链路中途有人激活新版本时，读活跃版本会让历史
 * 流水线显示成用了新包，而它实际跑的是旧包。这里返回的是当时锁定的那一份。
 */
export async function getWorkflowStatus(projectId: number, workflowId: string): Promise<WorkflowStatusView> {
  return get<WorkflowStatusView>('/operations/workflow-status/', { project: projectId, workflow_id: workflowId });
}

/**
 * 端到端评测：产出「阶段独立评测 + 四阶段端到端评测」。
 *
 * 与 `evaluateWorkflowStage` **不是同一件事**：那个改门禁状态（是放行凭据），
 * 这个只产证据（结论落门禁 detail，不改状态）。界面必须分开呈现。
 */
export async function evaluateWorkflow(projectId: number, workflowId: string, stage: string): Promise<unknown> {
  return post('/operations/evaluate-workflow/', { project: projectId, workflow_id: workflowId, stage });
}

/** 按产出的 `protocol.parent_output_ids` 建联合链路图，幂等 upsert。 */
export async function buildWorkflowGraph(projectId: number, workflowId: string): Promise<{ workflow_id: string; node_count: number; edge_count: number }> {
  return post<{ workflow_id: string; node_count: number; edge_count: number }>('/operations/build-workflow-graph/', { project: projectId, workflow_id: workflowId });
}

// ---------------------------------------------------------------- 用例审查自进化（T23）

/**
 * 上传专用请求。**不能复用 `getHeaders()`**：它写死 `Content-Type: application/json`，
 * 会覆盖 axios 为 `FormData` 生成的 multipart boundary，后端拿到的 `request.FILES` 是空的，
 * 表现为"上传了但报没传文件"。
 */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const authStore = useAuthStore();
  const token = authStore.getAccessToken;
  const res = await axios({
    method: 'post',
    url: `${BASE}${path}`,
    data: form,
    headers: {
      Authorization: token ? `Bearer ${token}` : '',
      Accept: 'application/json',
    },
  });
  return (res.data?.data ?? res.data) as T;
}

/**
 * 独立能力面板里「用例审查」的自进化候选：该项目下已跑完的审查项目。
 *
 * 列表里的 `evolvable` / `blockers` 由后端按 `evolve` 的真实判据算，前端不另做一套
 * 判断——两处各写一份必然出现"页面说可以、接口报错"。
 */
export async function listCaseReviewEvolutionCandidates(projectId: number): Promise<{ threshold: number; items: CaseReviewEvolutionCandidate[] }> {
  const data = await get<{ threshold: number; items: CaseReviewEvolutionCandidate[] }>('/case-review-evolution/candidates/', { project: projectId });
  return { threshold: data?.threshold ?? 70, items: data?.items ?? [] };
}

/**
 * 上传已确认报告做预检：**不落库**，只返回解析结果与全部阻塞条件。
 *
 * 预检存在的意义是让"不能进化"在点下按钮之前就可见。直接调 `evolve` 一次只说
 * 一条原因，用户要来回试三次才知道真正卡在哪。
 */
export async function preflightCaseReviewEvolution(
  projectId: number,
  reviewId: string,
  file: File,
  threshold?: number,
): Promise<CaseReviewEvolutionPreflight> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('review_id', reviewId);
  form.append('file', file);
  if (threshold !== undefined) form.append('threshold', String(threshold));
  return upload<CaseReviewEvolutionPreflight>('/case-review-evolution/preflight/', form);
}

/**
 * 发起自进化：从这份已确认报告派生 Skill 候选版本。
 *
 * 返回的候选一律是 `draft`，**不会**自动顶掉正在使用的版本——已激活的 Skill 要
 * 在 Skill 进化工坊走完评测与负责人审批才会切包；未激活的 Skill 本来就在用最新的
 * 可运行版本，但派生候选仍需人工激活才会生效（激活是钉版手段，见 requirements §R13.1）。
 * `active_untouched` 是"派生过程没碰活跃包"的断言，界面上要如实显示。
 *
 * `attributionIds`：给人工确认过的候选优化点 id（三步向导的主路径）。
 * 给了就**只拿这些已确认的**当派生依据；不给则降级为"直接采信人工在报告里写下的
 * 结论"——未配置 LLM 的环境只有后者。用重复字段提交（后端 multipart/JSON/重复字段三种都认）。
 */
export async function evolveCaseReview(
  projectId: number,
  reviewId: string,
  file: File,
  options?: { threshold?: number; changeReason?: string; attributionIds?: string[] },
): Promise<CaseReviewEvolutionResult> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('review_id', reviewId);
  form.append('file', file);
  if (options?.threshold !== undefined) form.append('threshold', String(options.threshold));
  if (options?.changeReason) form.append('change_reason', options.changeReason);
  (options?.attributionIds || []).forEach(id => form.append('attribution_ids', id));
  return upload<CaseReviewEvolutionResult>('/case-review-evolution/evolve/', form);
}

/** 用例审查的反馈以已确认 Excel 为唯一入口，分数取最后一个 Sheet 的采纳率。 */
export async function submitCaseReviewReportFeedback(
  projectId: number,
  reviewId: string,
  file: File,
): Promise<CaseReviewReportFeedbackResult> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('review_id', reviewId);
  form.append('file', file);
  return upload<CaseReviewReportFeedbackResult>('/case-review-evolution/feedback/', form);
}

/**
 * 通用附件下载。
 *
 * 必须走 blob 而不是 `window.open(url)`：下载端点要鉴权，新开标签不会带
 * `Authorization` 头，只会拿到 401 页面（还常常表现为"下载了一个坏 zip"）。
 * 文件名优先用服务端 `Content-Disposition` 给的那个——它才是导出时的正式命名。
 */
async function downloadAttachment(
  url: string,
  params: Record<string, unknown> | undefined,
  fallbackName: string,
): Promise<string> {
  const authStore = useAuthStore();
  const token = authStore.getAccessToken;
  const res = await axios.get(url, {
    responseType: 'blob',
    params,
    headers: { Authorization: token ? `Bearer ${token}` : '' },
  });
  const disposition = String(res.headers?.['content-disposition'] || '');
  // 服务端优先给 `filename*=UTF-8''<编码名>`（中文名）。只给 `filename=` 时浏览器
  // 会按 latin-1 解，中文名变成乱码文件，所以两种写法都要能读出来。
  const matched = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const filename = matched ? decodeURIComponent(matched[1]) : fallbackName;
  const blobUrl = URL.createObjectURL(res.data as Blob);
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(blobUrl);
  return filename;
}

/** 下载导出后的 Skill 包。 */
export async function downloadSkillPackage(downloadUrl: string): Promise<string> {
  return downloadAttachment(downloadUrl, undefined, 'skill-package.zip');
}

// ---------------------------------------------------------------- 阶段报告出口 / 反馈入口（T03 / T04 / T05）

/**
 * 「下载报告」：下发某一阶段已产出的报告文件。
 *
 * 与「查看结果」是两个动作：查看结果回正文摘要（看内容），这里回附件（拿走文件）。
 * 有登记产物时下的是 Skill 的真实产物（保留该阶段的专业结构）；没有时是平台按正文
 * 渲染的文本兜底——响应头 `X-Artifact-Source` 会写明是哪一种。
 */
export async function downloadStageArtifact(
  projectId: number,
  workflowId: string,
  stage: string,
): Promise<string> {
  return downloadAttachment(
    `${BASE}/operations/workflow-stage-artifact/`,
    { project: projectId, workflow_id: workflowId, stage },
    `${stage}-report.md`,
  );
}

/**
 * 「上传反馈」：上传该阶段已人工确认的报告，记录采纳率。**不派生。**
 *
 * 采纳率只作为**版本间对比的评分维度**，不是上传门槛：低于参考线照常入库，
 * 响应里标 `below_reference` 供页面标黄。想改包走「AI 提候选 → 人工确认 → 派生」，
 * 那是一条会被明确点下确认的动作，不该被"记录一次评审"顺带触发。
 *
 * 报告末页必须有「采纳率」标签单元格；缺标签会被 400 拒绝并说明期望格式——
 * 那是"文件本身不对"，不是"质量不够"，两者混成一句话会让人去查错方向。
 */
export async function uploadStageFeedback(
  projectId: number,
  workflowId: string,
  stage: string,
  file: File,
  reference?: number,
): Promise<StageFeedbackResult> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('workflow_id', workflowId);
  form.append('stage', stage);
  form.append('file', file);
  if (reference !== undefined) form.append('reference', String(reference));
  return upload<StageFeedbackResult>('/operations/workflow-stage-feedback/', form);
}

export async function listStageAttempts(projectId: number, workflowId: string, stage: string): Promise<StageExecutionAttemptView[]> {
  return rows(await get<StageExecutionAttemptView[] | { results: StageExecutionAttemptView[] }>('/stage-attempts/', {
    project: projectId, workflow_id: workflowId, stage,
  }));
}

export async function getStageAttemptTrace(attemptId: string): Promise<StageAttemptTraceView> {
  return get<StageAttemptTraceView>(`/stage-attempts/${attemptId}/trace/`);
}

export async function getStageReviewStatus(projectId: number, workflowId: string, stage: string): Promise<StageReviewStatusView> {
  return get<StageReviewStatusView>('/operations/workflow-stage-review-status/', { project: projectId, workflow_id: workflowId, stage });
}

export async function downloadStageReviewReport(projectId: number, workflowId: string, stage: string): Promise<string> {
  return downloadAttachment(
    `${BASE}/operations/workflow-stage-review-report/`,
    { project: projectId, workflow_id: workflowId, stage },
    `${stage}-review.xlsx`,
  );
}

export async function uploadStageReview(
  projectId: number, workflowId: string, stage: string, file: File, submit: boolean,
): Promise<StageReviewUploadResult> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('workflow_id', workflowId);
  form.append('stage', stage);
  form.append('file', file);
  return upload<StageReviewUploadResult>(
    `/operations/workflow-stage-review-${submit ? 'submit' : 'draft'}/`, form,
  );
}

export async function getStageAttachmentCatalog(): Promise<StageAttachmentCatalog> {
  return get<StageAttachmentCatalog>('/operations/workflow-stage-attachment-catalog/');
}

export async function listStageAttachments(projectId: number, workflowId: string, stage: string): Promise<StageAttachmentView[]> {
  const data = await get<{ results: StageAttachmentView[] }>('/operations/workflow-stage-attachments/', {
    project: projectId, workflow_id: workflowId, stage,
  });
  return data.results ?? [];
}

export async function uploadStageAttachment(payload: {
  projectId: number; workflowId: string; stage: string; outputId: string;
  purpose: string; note: string; file: File;
}): Promise<StageAttachmentView & { created: boolean }> {
  const form = new FormData();
  form.append('project', String(payload.projectId));
  form.append('workflow_id', payload.workflowId);
  form.append('stage', payload.stage);
  form.append('output_id', payload.outputId);
  form.append('purpose', payload.purpose);
  form.append('note', payload.note);
  form.append('file', payload.file);
  return upload<StageAttachmentView & { created: boolean }>('/operations/workflow-stage-attachments/', form);
}

export async function getStageDiff(projectId: number, workflowId: string, stage: string): Promise<StageDiffView> {
  return get<StageDiffView>('/operations/workflow-stage-diff/', { project: projectId, workflow_id: workflowId, stage });
}

export async function runStageAttribution(projectId: number, workflowId: string, stage: string): Promise<{ created: number }> {
  return post<{ created: number }>('/operations/workflow-stage-attribution-run/', { project: projectId, workflow_id: workflowId, stage });
}

export async function decideStageAttribution(attributionId: string, action: 'confirm' | 'reject', note = ''): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>('/operations/workflow-stage-attribution-decide/', {
    attribution_id: attributionId, action, note,
  });
}

export async function rewriteStageAttribution(attributionId: string, category: string, hypothesis: string, note = ''): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>('/operations/workflow-stage-attribution-rewrite/', {
    attribution_id: attributionId, category, hypothesis, note,
  });
}

export async function generateSkillContentProposal(attributionIds: string[]): Promise<OptimizationProposal[]> {
  return post<OptimizationProposal[]>('/optimization-proposals/generate/', {
    attribution_ids: attributionIds, target_type: 'skill_content',
  });
}

export async function getSkillContentPlan(proposalId: string): Promise<SkillContentPlanView> {
  return get<SkillContentPlanView>(`/optimization-proposals/${proposalId}/skill-content-plan/`);
}

export async function materializeSkillContent(proposalId: string): Promise<SkillContentPlanView & Record<string, unknown>> {
  return post<SkillContentPlanView & Record<string, unknown>>(`/optimization-proposals/${proposalId}/skill-content-materialize/`, {});
}

export async function evaluateSkillContent(proposalId: string, payload: {
  gold_dataset_version: string; baseline_run: string; candidate_run: string;
}): Promise<{ experiment_id: string; status: string; gate_report: Record<string, unknown>; plan: SkillContentPlanView }> {
  return post(`/optimization-proposals/${proposalId}/skill-content-evaluate/`, payload);
}

export async function transitionSkillContent(
  proposalId: string, action: 'submit-approval' | 'activate' | 'reject' | 'rollback', reason = '',
): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>(`/optimization-proposals/${proposalId}/skill-content-${action}/`, { reason });
}

export async function getSkillContentRunningFlows(proposalId: string): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>(`/optimization-proposals/${proposalId}/skill-content-running-flows/`);
}

export async function listGenerationOutputs(projectId: number, taskType?: string): Promise<GenerationOutput[]> {
  return rows(await get<GenerationOutput[] | { results: GenerationOutput[] }>('/generation-outputs/', {
    project: projectId, ...(taskType ? { task_type: taskType } : {}),
  }));
}

export async function listWorkflowStageSubmissions(projectId: number): Promise<Array<Record<string, unknown>>> {
  const data = await get<{ results: Array<Record<string, unknown>> }>('/operations/workflow-stage-submissions/', { project: projectId });
  return data.results ?? [];
}

export async function preflightWorkflowStageSubmission(payload: {
  projectId: number; outputId: string; stage: string; workflowId: string;
}): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>('/operations/workflow-stage-submission-preflight/', {
    project: payload.projectId, output_id: payload.outputId, stage: payload.stage,
    target: 'existing', workflow_id: payload.workflowId,
  });
}

export async function submitWorkflowStageOutput(payload: {
  outputId: string; stage: string; workflowId: string; replaceOutputId?: string; confirmReplace?: boolean;
}): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>('/operations/workflow-stage-submit/', {
    output_id: payload.outputId, stage: payload.stage, target: 'existing', workflow_id: payload.workflowId,
    replace_output_id: payload.replaceOutputId || '', confirm_replace: payload.confirmReplace === true,
  });
}

export async function getRegistrationFailures(projectId: number): Promise<{
  open: number; failed: number; dead_letter: number; alert: boolean;
  items?: Array<{ id: string; output_id: string; workflow_id: string; stage: string; status: string; attempts: number; last_error: string }>;
}> {
  return get('/operations/registration-failures/', { project: projectId, detail: 1 });
}

export async function retryRegistrationFailures(projectId: number): Promise<{ retried: number }> {
  return post('/operations/registration-failures-retry/', { project: projectId });
}

// ---------------------------------------------------------------- AI 候选优化点 + 人工确认（T06 / T07）

/**
 * 第 ③ 步：AI 读「当前 Skill 包正文 + 本轮报告缺陷 + 历史归因 + 人工确认结论」，
 * 提出**候选**优化点。**只提候选，不派生。**
 *
 * 无激活 LLM 时返回 `degraded=true`（HTTP 200）而不是报错：未配置模型是一条合法的
 * 降级路径，人工在报告里写下的结论仍可直接作为派生依据。回 500 会让它变成"系统错误"。
 */
export async function proposeCaseReviewOptimizations(
  projectId: number,
  reviewId: string,
  file: File,
): Promise<OptimizationProposalResult> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('review_id', reviewId);
  form.append('file', file);
  return upload<OptimizationProposalResult>('/case-review-evolution/attributions/', form);
}

/**
 * 第 ③ 步的后半：人工逐条「采纳 / 改写 / 驳回」。
 *
 * 用 `attribution_id` 而不是候选列表下标：下标只在**某一次**响应里有意义，
 * 中途刷新或重新生成后同一个下标指向的是另一条结论——那会让"我明明驳回了这条"
 * 变成驳回另一条，且不留痕迹。
 */
export async function confirmCaseReviewOptimizations(
  projectId: number,
  reviewId: string,
  decisions: Array<{ attribution_id: string; action: 'accept' | 'edit' | 'reject'; hypothesis?: string; category?: string }>,
): Promise<OptimizationDecisionResult> {
  return post<OptimizationDecisionResult>('/case-review-evolution/attributions/confirm/', {
    project: projectId,
    review_id: reviewId,
    decisions,
  });
}

// ---------------------------------------------------------------- 内容来源（T09 / T10）

/** 「这条产出是怎么来的」：闭环七段（`stages`）+ 内容来源（`sources`）。 */
export async function getGenerationOutputLineage(outputId: string): Promise<OutputLineageView> {
  return get<OutputLineageView>(`/generation-outputs/${outputId}/lineage/`);
}
