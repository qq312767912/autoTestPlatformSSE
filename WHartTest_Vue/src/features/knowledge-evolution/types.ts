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
  scope_type?: 'general' | 'domain';
  scope_key?: string;
  governance?: Record<string, unknown>;
  taxonomy_version?: string | null;
  approver?: number | null;
  version_count?: number;
  created_at: string;
}

export interface AssetCandidateEvent {
  id: string;
  project: number;
  source_type: string;
  source_type_label: string;
  source_id: string;
  signal: string;
  status: 'pending' | 'processing' | 'needs_review' | 'completed' | 'failed' | 'dead_letter';
  status_label: string;
  attempts: number;
  max_attempts: number;
  last_error: string;
  payload: Record<string, unknown>;
  preflight: Record<string, unknown>;
  candidate?: string | null;
  processed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetCandidateStats {
  total: number;
  pending: number;
  processing: number;
  needs_review: number;
  completed: number;
  failed: number;
  dead_letter: number;
}

export interface GoldAnnotation {
  id: string;
  case: string;
  round: 'primary' | 'review' | 'arbitration';
  answer: Record<string, unknown>;
  evidence: unknown[];
  conclusion: 'accepted' | 'rejected' | 'needs_changes';
  comment: string;
  tags: string[];
  split: string;
  category: string;
  review_snapshot: Record<string, unknown>;
  annotator_name: string;
  created_at: string;
}

export interface GoldCase {
  id: string;
  version: string;
  task_type: string;
  title: string;
  input_snapshot: Record<string, unknown>;
  expected_output: Record<string, unknown>;
  evidence: unknown[];
  tags: string[];
  split: string;
  state: 'candidate' | 'labeling' | 'conflict' | 'confirmed' | 'rejected';
  privacy_level: 'internal' | 'restricted' | 'prohibited';
  allow_optimization: boolean;
  recommended_split: string;
  recommended_tags: string[];
  review_checklist: Record<string, unknown>;
  annotations: GoldAnnotation[];
  created_at: string;
}

export interface GoldDatasetVersion {
  id: string;
  dataset: string;
  version: string;
  state: 'draft' | 'labeling' | 'review' | 'frozen' | 'retired';
  content_hash: string;
  sample_stats: Record<string, number>;
  governance_snapshot: Record<string, unknown>;
  case_count: number;
  frozen_at?: string | null;
  created_at: string;
}

export interface TestAssetTaxonomy {
  id: string;
  project: number;
  scope_key: string;
  version: string;
  state: 'draft' | 'review' | 'published' | 'retired';
  categories: string[];
  critical_scenarios: string[];
  content_hash: string;
  approved_at?: string | null;
}

export interface AnnotationConflict {
  id: string;
  case: string;
  differing_fields: string[];
  state: 'open' | 'resolved';
  resolution: Record<string, unknown>;
  primary_annotation: GoldAnnotation;
  review_annotation: GoldAnnotation;
}

export interface HistoryImportBatch {
  id: string;
  project: number;
  name: string;
  status: string;
  manifest_hash: string;
  candidate_count: number;
  items: Array<{id:string;role:string;file:number;file_name:string;file_hash:string}>;
  created_at: string;
}

export interface HistoryReplayDifference {
  id: string; stage: string; case_key: string;
  category: 'missing'|'extra'|'conflict'|'equivalent'|'uncertain';
  critical: boolean; human_decision: string; decision_note: string;
  expected: Record<string, unknown>; actual: Record<string, unknown>;
}

export interface HistoryReplay {
  id: string; project: number; batch: string; status: string;
  config_hash: string; summary: Record<string, unknown>; gate_report: Record<string, unknown>;
  differences: HistoryReplayDifference[]; created_at: string;
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
  /**
   * 该阶段能下载什么（控制台卡片上的「下载报告」按钮读它）。
   * 与 `workflow-status` 的 `output.artifact` 同源，未产出时为 null。
   */
  artifact?: StageArtifactView | null;
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
  /** 包自己声明的阶段。发起向导据此把版本严格归入对应阶段的下拉列表。 */
  declared_stage: string;
  declared_stage_label: string;
  /** 包处于启用 且 版本可运行（未被隔离/驳回、且有包目录）。与"已激活"无关。 */
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
  /**
   * 下面四项（T02）是"派发之后到底发生了什么"的凭据。
   *
   * 在此之前，界面拿到的只是一段参数回执，用户要自己把 workflow_id / module_key
   * 抄到另一个页面去。现在派发同时产生一条执行尝试和一份**服务端保存**的上下文，
   * 前端只需要带着 `execution_context_id` 跳过去——流程锁定的 Skill 版本因此
   * 不再经过用户可改的 URL。
   */
  attempt_id: string;
  attempt_status: string;
  execution_context_id: string;
  execution_context_expires_at: string;
  /** 业务页面路由（含 context id）。为空表示该阶段还没有对应页面，界面不跳转。 */
  launch_url: string;
}

/**
 * 执行上下文的解析结果（T02 / R3）。
 *
 * `skill` 是**服务端认定**的锁定版本，业务页面必须只读展示它，不允许换成别的版本——
 * 这就是"篡改 URL 不能替换流程锁定的 SkillVersion"在界面上的落点。
 */
export interface ExecutionContextView {
  execution_context_id: string;
  project: number;
  workflow_id: string;
  stage: string;
  stage_label: string;
  entry_type: string;
  channel: string;
  module_key: string;
  attempt_id: string;
  attempt_status: string;
  parent_output_ids: string[];
  managed: boolean;
  skill: {
    skill_id: string;
    skill_name: string;
    skill_version_id: string;
    version: string;
    package_sha256: string;
  };
  payload: Record<string, unknown>;
  expires_at: string;
  expired: boolean;
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
  /**
   * 该阶段能下载什么（由后端判定文件是否存在、是登记产物还是回落模板）。
   * 前端**不自己猜**：猜出来的结果会表现为"有按钮点了 404"。
   */
  artifact?: StageArtifactView | null;
  /** 采纳率按版本横向列出 —— 「采纳率作为版本间对比评分维度」的落地。 */
  acceptance_history?: AcceptanceHistoryItem[];
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
  /** 未产出时为 null；有产出则带 `artifact` 描述「能下载什么」。 */
  output:
    | { id: string; task_id: string; created_at: string; artifact?: StageArtifactView | null }
    | null;
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
  /** 该能力是否走 Skill 自进化通道。由后端按注册表判定，前端不自己认阶段名。 */
  self_evolution?: boolean;
}

export interface ProjectQualityCockpit {
  people: { leads: ProjectQualityPerson[]; executors: ProjectQualityPerson[] };
  gold_by_type: Record<string, Array<{ id: string; name: string; status: string; versions: number; cases: number }>>;
  single_capabilities: SingleCapabilitySummary[];
  workflows: ProjectWorkflowView[];
  stage_order: string[];
}

// ---------------------------------------------------------------- 用例审查自进化（T23）
// 独立能力质量面板里「用例审查」的自进化通道：选一个跑完的审查项目，
// 回传人工确认过的报告，派生出新的 Skill 候选版本。

/** 一个已跑完、可（或不可）用于自进化的用例审查项目。 */
export interface CaseReviewEvolutionCandidate {
  review_id: string;
  source_name: string;
  review_mode: string;
  skill_name: string;
  status: string;
  completed_at: string;
  created_at: string;
  creator: string;
  output_id: string;
  trace_id: string;
  issues_count: number | null;
  skill_version: string;
  skill_version_id: string;
  package_sha256: string;
  skill_id: string;
  /** 审查时用的版本是否仍是活跃版本——派生基线必须是活跃版本，否则派生目标就是错的。 */
  is_active_version: boolean;
  evolvable: boolean;
  /** 不能进化的具体原因。页面必须逐条显示：说"不可用"而不说为什么，用户只能去猜。 */
  blockers: string[];
  derived_candidates: Array<{ version_id: string; version: string; state: string; created_at: string }>;
}

/** 报告里一类被人工确认的缺陷（按归因类别 + 报告问题类型聚合）。 */
export interface CaseReviewEvolutionDefect {
  category: string;
  issue_type: string;
  count: number;
  /** 会被原样写进 SKILL.md 受管护栏的要求句，界面展示它等于让人预览"包会被改成什么样"。 */
  hypothesis: string;
  samples: Array<Record<string, string>>;
}

/** 一份已确认报告的解析结果。 */
export interface CaseReviewEvolutionScan {
  total_rows: number;
  affirmative: number;
  negative: number;
  rewritten: number;
  unconfirmed: number;
  confirmed: number;
  defect_total: number;
  defects: CaseReviewEvolutionDefect[];
  warnings: string[];
  source_name: string;
  /** 报告最后一个 Sheet 里的采纳率（0–1）。 */
  acceptance_rate: number;
  /** 采纳率的百分制表示。 */
  acceptance_score: number;
  acceptance_sheet: string;
}

/** 上传报告后的预检结果：**不落库**，只回答"现在能不能发起"。 */
export interface CaseReviewEvolutionPreflight {
  review: CaseReviewEvolutionCandidate;
  threshold: number;
  human_score: number | null;
  scan: CaseReviewEvolutionScan;
  attribution_preview: Array<{ category: string; issue_type: string; count: number }>;
  blockers: string[];
  ready: boolean;
}

/** 派生成功后的结果：候选版本 + 可读 diff + 下载入口。 */
export interface CaseReviewEvolutionResult {
  review_id: string;
  source_name: string;
  skill_id: string;
  skill_name: string;
  baseline_version: string;
  baseline_package_sha256: string;
  human_score: number;
  threshold: number;
  feedback_id: string;
  scan: CaseReviewEvolutionScan;
  attribution_ids: string[];
  candidate: {
    version_id: string;
    version: string;
    state: string;
    package_sha256: string;
    change_reason: string;
    created_at: string;
  };
  diff: {
    summary?: string;
    files?: { added: string[]; removed: string[]; modified: Array<Record<string, unknown>>; unchanged_count: number };
    text_diffs?: Array<Record<string, unknown>>;
  };
  rollback_target: string;
  /** 派生过程是否一个字节都没碰活跃包。这是本功能能上生产的前提，页面上要明说。 */
  active_untouched: boolean;
  download_url: string;
}

/** 用例审查质量反馈：只记录报告采纳率，不派生 Skill。 */
export interface CaseReviewReportFeedbackResult {
  review_id: string;
  feedback_id: string;
  human_score: number;
  threshold: number;
  scan: CaseReviewEvolutionScan;
}

// ---------------------------------------------------------------- 阶段报告下载 / 反馈（T03 / T04 / T05）

/**
 * 「这一阶段能下载什么」的描述（不含文件字节）。
 *
 * `source` 必须如实展示：`registered` 是 Skill 产出时登记的真实产物（保留该阶段的
 * 专业结构），`fallback` 是平台按正文渲染的文本兜底。两者可信度不同，
 * 按钮上长得一样会让人以为"下到的就是正式报告"。
 */
export interface StageArtifactView {
  /** 恒为 true（回落模板一定会生成）；保留字段是为了将来"某阶段禁止下载"时前端不改结构。 */
  available: boolean;
  source: 'registered' | 'fallback' | string;
  name: string;
  /** 回落模板时不渲染正文取体积，故为 null；精确长度看 `content_length`。 */
  size: number | null;
  sha256: string;
}

/** 采纳率的历史版本对照项。采纳率是"人对某一版的评价"，只有排成版本序列才读得出趋势。 */
export interface AcceptanceHistoryItem {
  version: string;
  version_id: string;
  /** 百分制。 */
  score: number;
  at: string;
  reason_code: string;
}

/** 「上传反馈」的返回：只记反馈，不派生。 */
export interface StageFeedbackResult {
  stage: string;
  stage_label: string;
  workflow_id: string;
  output_id: string;
  feedback_id: string;
  /** 百分制。 */
  acceptance_score: number;
  /**
   * 低于参考线（**不是**拦截条件）。
   * skill 是一点点优化出来的，把参考线做成硬阻断等于要求每个中间版本一次跨过同一条线。
   */
  below_reference: boolean;
  acceptance_reference: number;
  report_name: string;
  acceptance_sheet: string;
  skill_version: string;
  /** false = 这份报告之前上传过（命中幂等键），只是又提交了一次。 */
  created: boolean;
}

// ---------------------------------------------------------------- AI 候选优化点（T06 / T07）

/** AI 提出的**候选**优化点。`state` 恒为 `proposed`，人工确认后才进派生。 */
export interface OptimizationCandidate {
  attribution_id: string;
  category: string;
  issue_type: string;
  hypothesis: string;
  /** ≤0.8：AI 假设不得伪装成 `confidence=1.0` 的人工结论。 */
  confidence: number;
  state: string;
  source: string;
}

/** 「生成候选优化点」的返回。无 LLM 时 `degraded=true`（HTTP 200，不是错误）。 */
export interface OptimizationProposalResult {
  degraded: boolean;
  /** 降级原因编码，目前只有 `llm_unavailable`。 */
  reason_code?: string;
  detail?: string;
  candidates: OptimizationCandidate[];
  review_id: string;
  output_id?: string;
  package?: {
    version: string;
    skill_name: string;
    package_sha256: string;
    files: Array<{ path: string; chars: number }>;
  };
  history_count?: number;
}

/** 人工确认结果。逐条返回：一条写错不该把其余已经做完的确认全丢掉。 */
export interface OptimizationDecisionResult {
  review_id: string;
  results: Array<{
    attribution_id: string;
    state?: string;
    confidence?: number;
    /** 该条失败时的原因；成功时为空。 */
    error?: string;
  }>;
  confirmed_count: number;
  confirmed_ids: string[];
}

// ---------------------------------------------------------------- 内容来源（T09 / T10）

/** 规范化后的引用条目：四阶段与知识问答同构，前端只渲染一种结构。 */
export interface LineageCitation {
  citation_id: string;
  source_type: 'graph' | 'document' | 'requirement' | 'test_case' | '' | string;
  source_id: string;
  node_id: string;
  document_id: string;
  chunk_index: number | null;
  rank: number;
  title: string;
}

/** 引用里出现的图谱节点。`resolved=false` 表示节点已被清理（图谱重建），但仍报出来。 */
export interface LineageGraphNode {
  node_id: string;
  kind: string;
  label: string;
  resolved: boolean;
}

/** 这条产出参考了这版 Skill 的哪些文件（清单 + 哈希，不落正文）。 */
export interface LineageSkillContent {
  skill_version_id: string;
  version?: string;
  package_sha256: string;
  files: Array<{ path: string; sha256: string; size: number }>;
  truncated: boolean;
}

export interface LineageSources {
  /** 检索通道实况：哪个通道跑了、命中几条、为什么被跳过。 */
  channels: Record<string, unknown>;
  citations: LineageCitation[];
  graph_nodes: LineageGraphNode[];
  skill_content: LineageSkillContent;
}

/** 产出血缘：闭环七段（`stages`）+ 内容来源（`sources`）分两段返回。 */
export interface OutputLineageView {
  output: {
    id: string;
    task_type: string;
    task_id: string;
    model_version: string;
    prompt_version: string;
    created_at: string;
    project_id: number;
  };
  skill: Record<string, unknown>;
  stages: Record<string, { ok: boolean; count: number } & Record<string, unknown>>;
  broken_at: string;
  closed_loop: boolean;
  sources: LineageSources;
}
