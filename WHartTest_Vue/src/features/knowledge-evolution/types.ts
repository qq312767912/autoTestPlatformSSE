// 知识数据飞轮 - 反馈与测评相关类型

export interface RetrievalTrace {
  id: string;
  project_id: number;
  task_type: string;
  task_id: string;
  query: string;
  query_vector_id?: string;
  policy_version?: string;
  status: string;
  channels?: Record<string, unknown>;
  candidates?: unknown[];
  citations?: unknown[];
  timings?: Record<string, unknown>;
  token_usage: number;
  created_at: string;
}

export interface GenerationOutput {
  id: string;
  project_id: number;
  trace_id?: string;
  task_type: string;
  task_id: string;
  prompt_version: string;
  content: string;
  output_hash: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface FeedbackEvent {
  id: string;
  project_id: number;
  output?: string;
  trace?: string;
  signal: 'accepted' | 'rejected' | 'edited' | 'test_passed' | 'test_failed' | 'defect_confirmed' | 'false_positive' | 'missed' | 'merged' | 'reverted';
  value?: number;
  reason_code?: string;
  comment?: string;
  detail: Record<string, unknown>;
  actor_type: 'user' | 'system' | 'integration';
  actor?: { id: number; username: string };
  created_at: string;
}

export interface EvaluationSuite {
  id: string;
  project_id: number;
  name: string;
  description: string;
  task_type: string;
  split_ratio: Record<string, number>;
  case_count: number;
  is_active: boolean;
  created_at: string;
}

export interface EvaluationCase {
  id: string;
  suite_id: string;
  trace_id?: string;
  output_id?: string;
  split: 'gold' | 'regression' | 'fresh' | 'challenge';
  golden_labels: Record<string, unknown>;
  annotator?: string;
}

export interface EvaluationRun {
  id: string;
  suite_id: string;
  suite_name?: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  policy_version: string;
  model_name: string;
  l0_score?: number;
  l1_score?: number;
  l2_score?: number;
  l3_score?: number;
  metrics: Record<string, unknown>;
  cost_usd?: number;
  started_at?: string;
  finished_at?: string;
  created_at: string;
}

export interface EvaluationResult {
  id: string;
  run_id: string;
  case_id: string;
  status: 'passed' | 'failed' | 'error';
  l0_score: number;
  l1_score: number;
  l2_score: number;
  l3_score: number;
  output?: Record<string, unknown>;
  error_log?: string;
}

export interface KnowledgeCandidate {
  id: string;
  project_id: number;
  source_snapshot_id?: string;
  kind: 'concept' | 'rule' | 'relation' | 'summary' | 'knowledge_atom' | 'experience';
  origin: 'extraction' | 'distillation' | 'manual' | 'evaluation_failure';
  payload: Record<string, unknown>;
  level?: string;
  confidence: number;
  state: 'pending' | 'conflicted' | 'evaluating' | 'awaiting_approval' | 'accepted' | 'rejected' | 'merged';
  created_at: string;
}

export interface CreateFeedbackRequest {
  output?: string;
  trace?: string;
  signal: FeedbackEvent['signal'];
  detail?: Record<string, unknown>;
}

export interface CreateCandidateFromRunRequest {
  thresholds?: Record<string, number>;
  min_failure_count?: number;
}
