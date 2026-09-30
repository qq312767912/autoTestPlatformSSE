import axios from 'axios';
import { useAuthStore } from '@/store/authStore';
import type {
  EvaluationSuite,
  EvaluationRun,
  EvaluationResult,
  FeedbackEvent,
  KnowledgeCandidate,
  CreateFeedbackRequest,
  CreateCandidateFromRunRequest,
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
