export interface Skill {
  id: number
  name: string
  description: string
  skill_content: string
  skill_path: string
  script_path: string | null
  is_active: boolean
  project: number
  project_name: string
  creator: number
  creator_name: string
  created_at: string
  updated_at: string
}

export interface SkillListItem {
  id: number
  name: string
  description: string
  is_active: boolean
  creator_name: string
  created_at: string
  /**
   * 展示版本的元数据（来源 / 声明阶段 / 版本号）。
   *
   * 取自"展示版本"（活跃版本优先，否则最新一版），**纯描述性**，
   * 不参与任何可用性判定——可用性判据只有后端一处（`SkillVersion.is_runnable`）。
   * Skill 一个版本都没有时统一为空串。
   */
  source_type: string
  source_type_label: string
  stage: string
  stage_label: string
  /**
   * 阶段是从哪来的：`manifest`（包自己声明）/ `declared`（管理员在 Skill Hub 补填）/
   * `''`（未声明）。前端靠它区分"包声明的"与"人补的"，避免把补填的东西
   * 说成是包自带的能力。
   */
  stage_source: string
  version: string
  /**
   * 同名副本数。Skill Hub 是公共目录，列表按名字归并成一条「正本」展示；
   * 库里其实可能有多份（存量迁移与商店安装会按项目各落一份）。
   * `1` 表示只有一条，`>1` 表示这条是归并展示的结果。
   */
  copies: number
  /** 同一 Skill 身份下的不可变版本数（原始版 + 后续迭代）。 */
  version_count: number
  /** 是否已有自进化派生版本。 */
  has_evolution: boolean
}

export interface SkillUploadResponse {
  code: number
  message: string
  data: Skill[] | null
}

export interface SkillGitImportResponse {
  code: number
  message: string
  data: Skill[] | null
}

/**
 * 列表信封里的**能力声明**（与具体哪条 Skill 无关）。
 *
 * `can_manage`：调用者能不能**治理公共目录的条目**（启停、删除、补填阶段、隔离版本）
 * —— 由后端按"平台超管或在任一项目里是测试负责人"判定。前端**不得**自己用
 * "我是当前项目的什么角色"去推：同名的正本可能落在别的项目名下，按 URL 项目
 * 判角色会判错，表现就是"该给的入口没给 / 给了却 403"。
 *
 * 这是 2026-10-02 那次 404 回归的教训：当时列表是公共的、写侧按 URL 项目过滤，
 * 结果卡片上的启停 / 查看内容 / 版本列表对归属别的项目的条目一律 404。
 * **凡是会 403/404 的按钮，前端都要先问后端能不能显示。**
 *
 * `can_bind_stage`：与 `can_manage` 是同一个门槛（补填阶段也属治理动作），
 * 保留独立字段是为了让调用点读起来是"这一件事能不能做"，而不是两个概念混用。
 */
export interface SkillListMeta {
  can_bind_stage: boolean
  can_manage: boolean
  stage_options: Array<{ value: string; label: string }>
}

export interface SkillListResponse {
  code: number
  message: string
  data: SkillListItem[]
  meta?: SkillListMeta
}

export interface SkillDetailResponse {
  code: number
  message: string
  data: Skill
}

export interface SkillContentResponse {
  code: number
  message: string
  data: {
    name: string
    description: string
    content: string
  }
}

export interface SkillStoreConfig {
  default_source: string
  default_source_name: string
  allow_custom_source: boolean
  max_zip_size: number
}

export interface SkillStoreSource {
  id: string
  name: string
  baseUrl: string
  isDefault?: boolean
}

export interface ManifestSkill {
  id: string
  name: string
  name_en?: string
  description: string
  description_en?: string
  version?: string
  author?: string
  tags?: string[]
  zip_path: string
  readme_path?: string
  sha256?: string
}

export interface SkillStoreManifest {
  version: string
  updated_at?: string
  skills: ManifestSkill[]
}

export type StoreItemStatus = 'idle' | 'installing' | 'uninstalling' | 'ok' | 'error'

export interface StoreItemState {
  item: ManifestSkill
  installed: boolean
  installedId: number | null
  selected: boolean
  status: StoreItemStatus
  error: string
}
