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
}

/** 四阶段主链路的顺序。 */
export const WORKFLOW_STAGES: string[] = [
  'test_plan_generation',
  'testcase_generation',
  'test_execution',
  'report_generation',
]

/** 无可声明阶段时的分组名。 */
export const UNSTAGED_KEY = '__unstaged__'

export function stageLabel(stage?: string | null): string {
  if (!stage) return '未声明阶段'
  return STAGE_LABELS[stage] || stage
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
