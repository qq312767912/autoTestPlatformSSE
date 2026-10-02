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
