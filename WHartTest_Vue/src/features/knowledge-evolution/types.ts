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

/**
 * 阶段的"执行派发"留痕。
 *
 * 有它、且该阶段还没产出，界面就显示「执行中」——与「待测评」区分开：
 * 前者是该等（产出由外部 agent / 测试执行推进），后者是该动手（产出已有，等算账）。
 */
export interface WorkflowStageExecutionView {
  requested_by: string;
  requested_at: string;
  channel: 'agent' | 'platform' | string;
  module_key: string;
}

/** 人工评分留痕。`status` 只看 passed/failed 分不出"机器评的"还是"人评的"。 */
export interface WorkflowManualScoreView {
  value: number;
  normalized: number;
  threshold: number;
  by: string;
  at: string;
  note: string;
}

export interface WorkflowStageGateView {
  stage: string;
  /**
   * `unscored` = 自动评测拿不到分（信号缺失），与 `failed`（结论为负）严格区分；
   * `confirmed` = 人工确认放行，没评分也能推进；`running` 只出现在未产出的派发阶段。
   */
  status: 'pending' | 'unscored' | 'passed' | 'failed' | 'confirmed' | 'overridden' | 'ready' | 'blocked' | 'running';
  output_id?: string | null;
  task_id: string;
  gate_id?: string | null;
  scores: Record<string, number>;
  /** 门禁阈值（0–1）。评分弹窗的"百分制阈值"由它换算，不在前端写死 0.7。 */
  threshold?: number;
  reason: string;
  decided_by: string;
  skill_name: string;
  skill_version: string;
  self_evolution: boolean;
  /** 与后端 `confirm` 的准入集合同源，避免按钮显示出来却被接口 400 拒掉。 */
  confirmable?: boolean;
  /** 与后端 `score` 的准入集合同源。 */
  scorable?: boolean;
  passed?: boolean;
  human_decided?: boolean;
  manual_score?: WorkflowManualScoreView | null;
  execution?: WorkflowStageExecutionView | null;
}

export interface ProjectWorkflowView {
  workflow_id: string;
  /** 第一个还没放行的阶段：步骤条据此高亮"当前该看哪一步"。 */
  current_stage?: string;
  stages: WorkflowStageGateView[];
  /** 本条流程自己的阶段序列。存量流程是新流程之前发起的，序列不同，不能套全局。 */
  stage_order?: string[];
  /** `current` = 新主链路，`legacy` = 历史主链路。左栏据此给流程分组/标注。 */
  stage_template?: 'current' | 'legacy';
  locked_version_count?: number;
  passed_count?: number;
  completed?: boolean;
  /** 发起时间（最早一次锁定/产出）与最近动静。 */
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * 发起流程向导第一步的数据源（`workflow-stage-catalog/`）。
 *
 * `default` 由后端按 manifest 声明解析，前端**不自己挑一个"看起来最像"的包**——
 * 两处各写一套"哪个包管哪个阶段"的判断，改一处就会出现"默认项和你锁的项不是一个"。
 */
export interface WorkflowStageCatalogStage {
  stage: string;
  label: string;
  default: WorkflowCatalogSkill | null;
  declared_skill_ids: string[];
  runnable_count: number;
}

export interface WorkflowCatalogSkill {
  skill_id: string;
  skill_name: string;
  description: string;
  /** 包自己声明的阶段。与所选阶段不同即为跨声明选用，会写进流程锁的留痕。 */
  declared_stage: string;
  declared_stage_label: string;
  /** 包启用 + 有活跃版本 + 发布单元未否决。为 false 时选它锁不上，向导要拦。 */
  runnable: boolean;
  skill_version_id: string;
  version: string;
  release_state: string;
  package_sha256: string;
  updated_at: string;
}

export interface WorkflowStageCatalog {
  stage_order: string[];
  all_stage_order: string[];
  stages: WorkflowStageCatalogStage[];
  skills: WorkflowCatalogSkill[];
}

/**
 * 「执行本阶段」的返回：前置门禁校验通过后下发的执行参数。
 *
 * 它**不等于"已经在跑"**。平台只有测试执行有真正的执行器（且要先选用例套件），
 * 另外三个阶段由 agent 经 agent-loop 产出后按协议回写。所以界面必须把
 * `channel` / `entry` / `hint` 如实呈现给使用者，而不是给一个跑没跑都不知道的按钮。
 */
export interface StageExecutionPlan {
  workflow_id: string;
  stage: string;
  stage_label: string;
  channel: 'agent' | 'platform' | string;
  module_key: string;
  entry: string;
  hint: string;
  parent_output_ids: string[];
  managed: boolean;
  skill_name: string;
  skill_version: string;
  package_sha256: string;
  replaces_output: boolean;
  requested_by: string;
  requested_at: string;
}

/** 「查看结果」的返回：产出正文（可能截断）+ 门禁证据。 */
export interface StageOutputView {
  stage: string;
  stage_label: string;
  workflow_id: string;
  output_id: string;
  task_id: string;
  task_type: string;
  created_at: string;
  content: string;
  content_length: number;
  truncated: boolean;
  skill_name: string;
  skill_version: string;
  package_sha256: string;
  gate: {
    status: string;
    scores: Record<string, number>;
    threshold: number;
    reason: string;
    decided_by: string;
    decided_at: string;
    manual_score: WorkflowManualScoreView | null;
    report_contract: Record<string, unknown> | null;
    evaluation: Record<string, unknown> | null;
  } | null;
}

/**
 * 单个阶段的 Skill 版本锁定信息。
 *
 * `managed: false` 表示项目未登记该阶段可用的 Skill 版本——此时该阶段
 * 按平台默认行为执行、**没有版本溯源**。界面必须如实标出这一点，
 * 而不是用"当前活跃版本"糊过去。
 */
export interface WorkflowBindingView {
  stage: string;
  managed: boolean;
  skill_id: string;
  skill_name: string;
  skill_version_id: string;
  version: string;
  package_sha256: string;
  lock_id: string;
  /** 人是否在向导里显式点了这个包（而非沿用后端默认解析）。 */
  pinned?: boolean;
  /** 包自己声明的阶段；与所在阶段不同即为跨声明选用。 */
  declared_stage?: string;
  stage_mismatch?: boolean;
  detail: string;
}

export interface StartWorkflowResult {
  workflow_id: string;
  stage_order: string[];
  bindings: Record<string, WorkflowBindingView>;
  locked_stages: string[];
  unmanaged_stages: string[];
  /**
   * 跨声明选用（人选了包的 manifest 里写的是别的阶段）的阶段列表。
   *
   * 这**不是错误**：主链路刚换过阶段，现存包声明的还是旧阶段名，硬拦会让人选不出东西。
   * 但必须显示出来——否则事后没人知道"这条链路用的是不是原本为它准备的包"。
   */
  mismatched_stages?: string[];
}

export interface WorkflowStageStatusView {
  stage: string;
  label: string;
  state: string;
  entered: boolean;
  can_enter: boolean;
  execution?: WorkflowStageExecutionView | null;
  output: { id: string; task_id: string; created_at: string } | null;
  gate: {
    id: string;
    status: string;
    scores: Record<string, number>;
    threshold: number;
    reason: string;
    decided_by: string;
    decided_at: string;
    overridden: boolean;
    confirmable: boolean;
    scorable: boolean;
    passed: boolean;
    human_decided: boolean;
    manual_score: WorkflowManualScoreView | null;
    report_contract: Record<string, unknown> | null;
    evaluation: Record<string, unknown> | null;
  } | null;
  version: WorkflowBindingView | null;
}

/** `workflow-status` 的完整返回：四阶段门禁 + 锁定版本的唯一真值。 */
export interface WorkflowStatusView {
  workflow_id: string;
  stage_order: string[];
  stages: WorkflowStageStatusView[];
  /** 第一个还没放行的阶段，步骤条据此决定"哪一步是当前步"。 */
  current_stage: string;
  locked_version_count: number;
  managed: boolean;
  report_contract: Record<string, unknown> | null;
  completed: boolean;
  blocked_at: string;
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
