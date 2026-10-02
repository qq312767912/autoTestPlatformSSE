// 知识数据飞轮 - 反馈与测评相关类型

export interface RetrievalTrace {
  id: string;
  project: number;
  knowledge_base?: number | null;
  user?: number | null;
  task_type: string;
  task_id: string;
  query: string;
  rewritten_query?: string;
  policy_version?: string;
  status: string;
  channels?: Record<string, unknown>;
  candidates?: unknown[];
  citations?: unknown[];
  timings?: Record<string, unknown>;
  token_usage: number;
  error_code?: string;
  output_ids?: string[];
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
  project: number;
  output?: string | null;
  trace?: string | null;
  signal: 'accepted' | 'rejected' | 'edited' | 'test_passed' | 'test_failed' | 'defect_confirmed' | 'false_positive' | 'missed' | 'merged' | 'reverted';
  value?: number;
  reason_code?: string;
  comment?: string;
  detail: Record<string, unknown>;
  actor?: { id: number; username: string } | null;
  actor_type: 'user' | 'system' | 'integration';
  occurred_at?: string;
  created_at: string;
}

export interface EvaluationSuite {
  id: string;
  project: number;
  name: string;
  suite_type: 'seed' | 'gold' | 'regression' | 'fresh' | 'challenge';
  task_type: string;
  description: string;
  split_ratio: Record<string, number>;
  case_count: number;
  is_active: boolean;
  created_at: string;
}

export interface EvaluationCase {
  id: string;
  suite: string;
  case_number: number;
  task_type: string;
  split: 'gold' | 'regression' | 'fresh' | 'challenge';
  golden_labels: Record<string, unknown>;
  annotator?: string;
}

export interface EvaluationRun {
  id: string;
  suite: string;
  suite_name?: string;
  name: string;
  config: Record<string, unknown>;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  metrics_summary: Record<string, unknown>;
  cost_summary: Record<string, unknown>;
  result_summary?: { total: number; completed: number; failed: number };
  policy_version: string;
  model_name: string;
  l0_score?: number | null;
  l1_score?: number | null;
  l2_score?: number | null;
  l3_score?: number | null;
  cost_usd?: number | null;
  started_at?: string;
  finished_at?: string;
  created_at: string;
}

export interface EvaluationResult {
  id: string;
  run: string;
  case: string;
  case_number: number;
  split: 'gold' | 'regression' | 'fresh' | 'challenge';
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  predicted_payload?: Record<string, unknown>;
  latency_ms?: number;
  token_usage?: number;
  estimated_cost_usd?: number;
  l0_score: number | null;
  l1_score: number | null;
  l2_score: number | null;
  l3_score: number | null;
  raw_scores?: Record<string, unknown>;
  error_message?: string;
  created_at?: string;
}

export interface KnowledgeCandidate {
  id: string;
  project: number;
  source_snapshot?: string | null;
  kind: 'concept' | 'rule' | 'relation' | 'summary' | 'knowledge_atom' | 'experience';
  origin: 'extraction' | 'distillation' | 'manual' | 'evaluation_failure';
  payload: Record<string, unknown>;
  level?: string;
  confidence: number;
  evidence?: unknown[];
  review_reason?: string;
  state: 'pending' | 'conflicted' | 'evaluating' | 'awaiting_approval' | 'accepted' | 'rejected' | 'merged';
  created_at: string;
  updated_at?: string;
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

export interface CapabilityDefinition {
  id: string;
  project: number;
  kind: 'knowledge' | 'retrieval_policy' | 'prompt' | 'skill' | 'agent';
  name: string;
  description: string;
  evaluation_mode: 'single' | 'workflow';
  stages: string[];
  default_suite?: string | null;
  gate_rules: Record<string, unknown>;
  active_release?: { id: string; name: string; version: string; state: string } | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CapabilityRelease {
  id: string;
  project?: number;
  kind: CapabilityDefinition['kind'];
  name: string;
  version: string;
  state: string;
  gate_report?: Record<string, unknown>;
  created_at: string;
}

export interface GoldDataset {
  id: string;
  project: number;
  name: string;
  task_type: string;
  description: string;
  status: 'active' | 'archived';
  version_count?: number;
  created_at: string;
}

export interface ExecutionSpan {
  id: string;
  trace: string;
  parent_span?: string | null;
  workflow_id: string;
  stage: string;
  step_type: string;
  sequence: number;
  tool_name?: string;
  status: 'running' | 'completed' | 'failed' | 'skipped';
  latency_ms: number;
  token_usage: number;
  error_type?: string;
  metadata?: Record<string, unknown>;
}

export interface FailureAttribution {
  id: string;
  project: number;
  output?: string | null;
  span?: string | null;
  workflow_id: string;
  category: string;
  source: 'rule' | 'llm' | 'human';
  confidence: number;
  hypothesis: string;
  evidence: unknown[];
  counterevidence: unknown[];
  state: 'proposed' | 'confirmed' | 'rejected';
  created_at: string;
}

export interface OptimizationProposal {
  id: string;
  project: number;
  proposal_type: string;
  title: string;
  summary: string;
  expected_benefit: string;
  risk_notes: string;
  rollback_plan: string;
  state: string;
  created_at: string;
}

export interface RunEvolutionRequest {
  name?: string;
  baseline_release_id?: string;
  candidate_config?: Record<string, unknown>;
  version?: string;
}

export interface RunEvolutionResponse {
  definition_id: string;
  run_id: string;
  suite_id: string;
  case_count: number;
  created_release_id: string | null;
  gate_passed: boolean | null;
}

export interface ProjectQualityPerson {
  id: number;
  username: string;
  display_name: string;
  email: string;
  project_role: 'owner' | 'admin' | 'member' | string;
}

export interface WorkflowStageGateView {
  stage: string;
  status: 'pending' | 'passed' | 'failed' | 'overridden' | 'ready' | 'blocked';
  output_id?: string | null;
  task_id: string;
  gate_id?: string | null;
  scores: Record<string, number>;
  reason: string;
  decided_by: string;
  skill_name: string;
  skill_version: string;
  self_evolution: boolean;
}

export interface ProjectWorkflowView {
  workflow_id: string;
  stages: WorkflowStageGateView[];
}

export interface SingleCapabilitySummary {
  stage: string;
  outputs: number;
  feedback: number;
  failed: number;
  latest_at?: string | null;
}

export interface ProjectQualityCockpit {
  people: { leads: ProjectQualityPerson[]; executors: ProjectQualityPerson[] };
  gold_by_type: Record<string, Array<{ id: string; name: string; status: string; versions: number; cases: number }>>;
  single_capabilities: SingleCapabilitySummary[];
  workflows: ProjectWorkflowView[];
  stage_order: string[];
}
