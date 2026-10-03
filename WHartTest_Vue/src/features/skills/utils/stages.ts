// 业务阶段口径（前端展示用）。
//
// 唯一真值在后端 `knowledge_evolution/capability_registry.py`；这里只是同名的
// 展示副本，用于分组与中文标签。**不含任何判定逻辑**——凡是"能不能"的问题
// 一律以后端返回为准，避免出现第三套口径。

export const STAGE_LABELS: Record<string, string> = {
  case_review: '用例审查',
  code_review: '代码审查',
  test_plan_generation: '测试方案生成',
  testcase_generation: '测试用例生成',
  test_execution: '测试执行',
  report_generation: '报告生成',
  risk_identification: '风险识别',
  issue_tracking: '问题跟踪',
  knowledge_query: '知识问答',
  platform_base: '平台基础能力',
}

/**
 * 跨阶段的「平台基础能力」档。它不是业务能力阶段（后端 `PLATFORM_BASE_STAGE`），
 * 只是 Skill 的归属标签：平台自带的公共手段（测试管理工具、浏览器自动化、
 * 视觉识别、知识库检索等）被多个阶段共用，不专属某一阶段。
 *
 * 分组排序里排在主链路之后、未声明之前——它是"不绑单一阶段"的归属，不是缺数据。
 */
export const PLATFORM_BASE_STAGE = 'platform_base'

/** 四阶段主链路的顺序。 */
export const WORKFLOW_STAGES: string[] = [
  'test_plan_generation',
  'testcase_generation',
  'test_execution',
  'report_generation',
]

/** 阶段分组展示顺序：主链路 → 其余业务能力 → 平台基础能力 → 未声明。 */
export const STAGE_GROUP_ORDER: string[] = [
  ...WORKFLOW_STAGES,
  'case_review',
  'risk_identification',
  'issue_tracking',
  'code_review',
  PLATFORM_BASE_STAGE,
]

/** 无可声明阶段时的分组名。 */
export const UNSTAGED_KEY = '__unstaged__'

/**
 * 用户自定义阶段在库里的存储前缀（后端 `CUSTOM_STAGE_PREFIX`）。
 *
 * 用户在导入 Skill 时可以自己敲一档（如「性能测试」）。这类值不属规范任务类型，
 * 后端统一存成 `custom:<名称>`：既能与规范阶段隔开命名空间，也让"敲错的标识符"
 * 变成一个看得见的自定义档、而不是与"没填"长得一样的静默失败。
 *
 * 前端只做**展示**：判断是否是自定义（决定标签样式）、显示时剥掉前缀。
 * 能不能提交、怎么归一化，一律以后端返回为准。
 */
export const CUSTOM_STAGE_PREFIX = 'custom:'

export function isCustomStage(stage?: string | null): boolean {
  return String(stage || '').startsWith(CUSTOM_STAGE_PREFIX)
}

/** 自定义阶段只显示名称本身（剥掉命名空间前缀）；规范阶段查中文标签；其余原样。 */
export function stageLabel(stage?: string | null): string {
  if (!stage) return '未声明阶段'
  if (isCustomStage(stage)) return stage.slice(CUSTOM_STAGE_PREFIX.length) || stage
  return STAGE_LABELS[stage] || stage
}

/**
 * 阶段下拉项的文案：自定义档额外标出「（自定义）」。
 *
 * 规范阶段是平台任务类型（有门禁分区与评测模板），自定义档只是用户自建的归属标签；
 * 混在一个下拉里不加区分，会让人误以为后者也是平台阶段。
 */
export function stageOptionText(opt: { label: string; custom?: boolean }): string {
  return opt.custom ? `${opt.label}（自定义）` : opt.label
}

export function sourceTypeLabel(sourceType?: string | null): string {
  const map: Record<string, string> = {
    upload: '本地上传',
    git: 'Git 导入',
    store: 'Skill 商店',
    evolution: '自进化派生',
    migration: '存量迁移',
  }
  if (!sourceType) return '未知来源'
  return map[sourceType] || sourceType
}

/** 审计动作的中文名。未知动作原样显示，不吞掉信息。 */
export function auditActionLabel(action?: string | null): string {
  const map: Record<string, string> = {
    validate: '静态校验',
    download: '下载导出',
    quarantine: '隔离',
    approve: '审批激活',
    submit_approval: '提交审批',
    reject: '驳回',
    rollback: '回滚',
    create: '创建',
    promote: '晋级',
    upload: '上传',
  }
  if (!action) return '未知动作'
  return map[action] || action
}
