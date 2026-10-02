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
  StageOutputView,
  WorkflowStageCatalog,
  WorkflowStatusView,
  CaseReviewEvolutionCandidate,
  CaseReviewEvolutionPreflight,
  CaseReviewEvolutionResult,
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

/** 「查看结果」：按 project + workflow_id + stage 三元定位读取产出正文与门禁证据。 */
export async function getStageOutput(projectId: number, workflowId: string, stage: string): Promise<StageOutputView> {
  return get<StageOutputView>('/operations/workflow-stage-output/', { project: projectId, workflow_id: workflowId, stage });
}

/**
 * 启动四阶段流水线：后端会**一次性锁定四个阶段的 Skill 版本**。
 *
 * `pins` 是「阶段 → Skill ID」：向导里人逐个阶段显式选包时传进来。
 * 不传则沿用后端按 manifest 声明的默认解析（保持老调用方的行为不变）。
 *
 * 为什么允许人选到"声明的是别的阶段"的包：主链路刚换过阶段名，现存包声明的还是旧阶段，
 * 按声明硬筛会让向导一个候选都给不出来。人选它比包里写了什么更强，但后端会把
 * `pinned` / `declared_stage` / `stage_mismatch` 写进流程锁留痕——见 `mismatched_stages`。
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
  humanScore: number | null,
  threshold?: number,
): Promise<CaseReviewEvolutionPreflight> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('review_id', reviewId);
  form.append('file', file);
  if (humanScore !== null && humanScore !== undefined) form.append('human_score', String(humanScore));
  if (threshold !== undefined) form.append('threshold', String(threshold));
  return upload<CaseReviewEvolutionPreflight>('/case-review-evolution/preflight/', form);
}

/**
 * 发起自进化：从这份已确认报告派生 Skill 候选版本。
 *
 * 返回的候选一律是 `draft`，**不会**自动激活——它还要走 Skill Hub 的评测与负责人
 * 审批。`active_untouched` 是"派生过程没碰活跃包"的断言，界面上要如实显示。
 */
export async function evolveCaseReview(
  projectId: number,
  reviewId: string,
  file: File,
  humanScore: number,
  options?: { threshold?: number; changeReason?: string },
): Promise<CaseReviewEvolutionResult> {
  const form = new FormData();
  form.append('project', String(projectId));
  form.append('review_id', reviewId);
  form.append('file', file);
  form.append('human_score', String(humanScore));
  if (options?.threshold !== undefined) form.append('threshold', String(options.threshold));
  if (options?.changeReason) form.append('change_reason', options.changeReason);
  return upload<CaseReviewEvolutionResult>('/case-review-evolution/evolve/', form);
}

/**
 * 下载导出后的 Skill 包。
 *
 * 必须走 blob 而不是 `window.open(url)`：下载端点要鉴权，新开标签不会带
 * `Authorization` 头，只会拿到 401 页面（还常常表现为"下载了一个坏 zip"）。
 * 文件名优先用服务端 `Content-Disposition` 给的那个——它才是导出时的正式命名。
 */
export async function downloadSkillPackage(downloadUrl: string): Promise<string> {
  const authStore = useAuthStore();
  const token = authStore.getAccessToken;
  const res = await axios.get(downloadUrl, {
    responseType: 'blob',
    headers: { Authorization: token ? `Bearer ${token}` : '' },
  });
  const disposition = String(res.headers?.['content-disposition'] || '');
  const matched = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const filename = matched ? decodeURIComponent(matched[1]) : 'skill-package.zip';
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
