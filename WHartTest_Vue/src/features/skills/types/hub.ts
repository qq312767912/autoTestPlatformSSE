// Skill Hub 生产控制台的数据契约（T18 / 对应需求 R10、R11）。
//
// 这些类型刻意贴着后端**真实返回**的形状写，而不是重构成前端更顺手的形状：
// 控制台上要展示的正是"状态机停在哪一格、门禁哪一项没过、缺失条件是什么"，
// 任何一层重新命名或合并都会让"页面结论"与"后端判据"有机会分叉。

/** 项目内业务角色。owner/admin = 测试负责人，member = 测试执行人员。 */
export type ProjectBusinessRole = 'lead' | 'executor' | 'none'

/** 发布单元状态机（唯一真值：knowledge_evolution/capabilities.ALLOWED_RELEASE_TRANSITIONS）。 */
export type ReleaseState =
  | 'draft'
  | 'validating'
  | 'shadow'
  | 'awaiting_approval'
  | 'active'
  | 'retired'
  | 'rolled_back'
  | 'rejected'
  | 'quarantined'

export const RELEASE_STATE_LABEL: Record<ReleaseState, string> = {
  draft: '草稿',
  validating: '校验中',
  shadow: '影子验证',
  awaiting_approval: '待审批',
  active: '生产生效',
  retired: '已退役',
  rolled_back: '已回滚',
  rejected: '未通过',
  quarantined: '已隔离',
}

/** 状态色只用于门禁与风险：生效绿、待审批橙、异常红、其余中性。 */
export function releaseStateColor(state: ReleaseState | string): string {
  switch (state) {
    case 'active':
      return 'green'
    case 'awaiting_approval':
    case 'validating':
    case 'shadow':
      return 'orange'
    case 'rejected':
    case 'quarantined':
      return 'red'
    default:
      return 'gray'
  }
}

/**
 * 状态机合法边的**展示副本**。
 *
 * 唯一真值是后端 `knowledge_evolution/capabilities.ALLOWED_RELEASE_TRANSITIONS`；
 * 这里抄一份只为了把"按钮为什么是灰的"讲清楚。**真正的拦截在后端**——
 * 任何绕过页面直接打接口的调用都会被 `assert_can_transition` 拒掉。
 * 因此这里的表可以保守（少给几个可点状态），但不能激进（放出后端会拒的动作）。
 */
const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  draft: ['validating', 'quarantined'],
  validating: ['shadow', 'rejected', 'quarantined'],
  shadow: ['awaiting_approval', 'active', 'rejected', 'quarantined'],
  awaiting_approval: ['active', 'shadow', 'rejected', 'quarantined'],
  active: ['retired', 'rolled_back', 'quarantined'],
  retired: ['active', 'quarantined'],
  rejected: ['quarantined'],
  rolled_back: [],
  quarantined: [],
}

/** 当前状态能否走到目标状态（用于按钮禁用与提示文案）。 */
export function canReachReleaseState(from: ReleaseState | string, to: ReleaseState): boolean {
  return (ALLOWED_TRANSITIONS[from] ?? []).includes(to)
}

export interface PreflightIssue {
  code: string
  message: string
  severity: 'error' | 'warning'
  path: string
}

/** 上传预检报告（skills/validation.ValidationReport.as_dict）。 */
export interface PreflightReport {
  ok: boolean
  manifest: Record<string, unknown>
  files: string[]
  package_sha256: string
  errors: PreflightIssue[]
  warnings: PreflightIssue[]
}

/** 预检结果：报告 + 短期令牌（两阶段上传的第二步要用它换候选版本）。 */
export interface PreflightResult extends PreflightReport {
  token: string
  token_id: string
  expires_at: string
}

export interface VersionRef {
  id?: string
  version?: string
  package_sha256?: string
  state?: ReleaseState
}

export interface SkillVersionSummary {
  id: string
  version: string
  state: ReleaseState
  /** 关联的发布单元主键；未关联时为 null（此时 state 恒为 draft）。 */
  release_id: string | null
  /**
   * manifest 中声明的业务阶段。
   *
   * 版本列表接口目前返回的是完整版本序列化器，因此这个字段在列表里就有；
   * 若后端日后收紧为轻量列表序列化器，这里会退化成空串，目录统一落到
   * 「未声明阶段」分组——只是分组变粗，不会显示错的阶段。
   */
  stage?: string
  package_sha256: string
  source_type: string
  created_at: string
}

export interface SkillVersionDetail extends SkillVersionSummary {
  skill: number
  skill_name: string
  manifest: Record<string, unknown>
  validation_report: PreflightReport | Record<string, never>
  source_metadata: Record<string, unknown>
  previous_version: string | null
  /** manifest 声明的业务阶段；空串表示未声明。 */
  stage: string
  entrypoint: string
  created_by: number | null
  created_by_name: string | null
  updated_at: string
}

export interface VersionDiffEntry {
  path: string
  base_sha256: string
  candidate_sha256: string
  base_size: number
  candidate_size: number
}

export interface VersionDiffTextEntry {
  path: string
  unified?: string
  skipped?: string
}

/** 版本差异（skills/versions.compute_diff）。 */
export interface VersionDiff {
  base: VersionRef
  candidate: VersionRef
  files: {
    added: string[]
    removed: string[]
    modified: VersionDiffEntry[]
    unchanged_count: number
  }
  text_diffs: VersionDiffTextEntry[]
  summary: string
}

export interface GateCheck {
  code: string
  label: string
  ok: boolean
  detail: string
  value?: unknown
  limit?: unknown
}

/** "还差什么"的逐条明细。这是唯一真值，前端不得自行重算。 */
export interface MissingCondition {
  code: string
  label: string
  detail: string
}

export interface GateSnapshotSummary {
  snapshot_id: string | null
  passed: boolean
  content_hash: string
  created_at?: string
  checks: GateCheck[]
  metrics: Record<string, unknown>
  partitions: Record<string, unknown>
  thresholds?: Record<string, unknown>
  missing_conditions: MissingCondition[]
}

export interface ReleaseDecisionView {
  decision: string
  reason: string
  actor: string
  created_at: string
}

export interface ReleaseObservationView {
  window_key: string
  status: string
  breaches: unknown[]
  metrics: Record<string, unknown>
  created_at: string
}

/** 控制台右栏的权威状态（capability-releases/{id}/approval-view/）。 */
export interface ReleaseApprovalView {
  release_id: string
  state: ReleaseState
  state_label: string
  version: string
  approval_snapshot_hash: string
  latest_snapshot_hash: string
  /** 审批后被重新评测 → 旧审批依据失效，必须重新提交审批。 */
  evidence_stale: boolean
  observation_state: string
  missing_conditions: MissingCondition[]
  can_submit_approval: boolean
  can_activate: boolean
  gate_report: Record<string, unknown>
  gate: GateSnapshotSummary
  decisions: ReleaseDecisionView[]
  observations: ReleaseObservationView[]
}

export interface CapabilityBindingDefinition {
  id: string
  name: string
  kind: string
  evaluation_mode: 'single' | 'workflow' | string
  is_active: boolean
  stages: string[]
  stage_labels: string[]
  /** 在链路中的位置（第几段）；非阶段命中时为 null。 */
  position: number | null
  /** active_release = 强绑定（正在用这一版）；stage = 候选绑定（激活后会影响它）。 */
  binding: 'active_release' | 'stage'
}

/** 能力绑定（capability-releases/{id}/bindings/）。 */
export interface ReleaseBindings {
  release_id: string
  kind: string
  name: string
  version: string
  stage: string
  stage_label: string
  workflow_stages: string[]
  definitions: CapabilityBindingDefinition[]
}

export interface AuditLogEntry {
  id: string
  project: number
  actor: number | null
  actor_type: string
  action: string
  entity_type: string
  entity_id: string
  from_state: string
  to_state: string
  before_version: string
  after_version: string
  reason: string
  detail: Record<string, unknown>
  created_at: string
}

/** 发布单元列表项（capability-releases/）；用于把 Skill 版本与治理状态对上。 */
export interface CapabilityReleaseSummary {
  id: string
  project: number
  kind: string
  name: string
  version: string
  state: ReleaseState
  config: Record<string, unknown>
  artifact_hash: string
  gate_report: Record<string, unknown>
  previous_release: string | null
  approved_at: string | null
  activated_at: string | null
  created_at: string
  updated_at: string
  decisions: ReleaseDecisionView[]
  observations: ReleaseObservationView[]
}

/** 左栏目录项：一个 Skill 及其版本概览。 */
export interface SkillCatalogEntry {
  id: number
  name: string
  description: string
  is_active: boolean
  creator_name: string | null
  created_at: string
  /** 由最新版本的 manifest 推导，用于按阶段分组。 */
  stage: string
  activeVersion: SkillVersionSummary | null
  versions: SkillVersionSummary[]
  candidateCount: number
  quarantinedCount: number
  latestAt: string
  /** 版本概览拉取失败时如实标记，不用空数组冒充"没有版本"。 */
  versionsError: string
}

/** 治理动作的前置条件（用于按钮禁用文案）。 */
export interface GovernanceAction {
  key: string
  label: string
  /** 该动作当前是否可执行。 */
  enabled: boolean
  /** 不可执行时展示缺什么；可执行时为提示语。 */
  hint: string
  danger?: boolean
}

/**
 * 一次下载的凭据。
 *
 * R11 要求"下载明确展示导出版本和哈希"：只给一个文件等于让用户凭信任收下，
 * 所以把版本号与两级哈希（包哈希 = 入库内容，导出哈希 = 脱敏后的字节）留在页面上。
 */
export interface SkillExportReceipt {
  version: string
  filename: string
  packageSha256: string
  exportSha256: string
  redactedFiles: number
}
