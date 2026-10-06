export interface Skill {
  id: number
  name: string
  /**
   * 展示名称：上传/导入时由平台生成、人工确认；为空时页面回退展示 `name`。
   *
   * 与 `name` 是两件事——`name` 是 Skill Hub 的跨项目归并键、也是版本包认逻辑
   * 身份的依据，**不可改**；改名只落在这一层。
   */
  display_name: string
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

/**
 * 最近一次 `stage-result/v1` 校验结论（T05）。
 *
 * 后端在产出落库时把摘要写进 `GenerationOutput.metadata`，这里读的就是那份摘要，
 * **不在前端重算** —— 重算需要当时的 Skill 包与产出文件，多半已经不在了。
 */
export interface StageResultRun {
  ok: boolean
  /** 由产出内容实测出的等级（不看声明）。 */
  level: string
  level_label: string
  /** Skill 自己声明的等级；空串表示未声明。 */
  declared_level: string
  /** 平台实际按哪一档对待 = 声明与实测中较低的一档。 */
  effective_level: string
  effective_level_label: string
  /** 声明高于实测。 */
  level_gap: boolean
  /**
   * `ok` / `structured_protocol_failure`。
   *
   * 与「业务生成失败」是两个标记，页面必须分开显示：前者是"跑成功了但信封不合规"，
   * 对应的补救动作是改 Skill 或改产出；后者才是重跑。
   */
  result_marker: string
  /** 字段级错误清单（`path` 是 JSON Pointer，如 `/items/0/id`）。 */
  issues: Array<{ path: string; message: string; code: string }>
  detail: string
  compatibility_level: string
  recorded_at: string
}

export interface SkillListItem {
  id: number
  name: string
  /**
   * 展示名称（上传/导入时人工确认过的那一个）；为空时卡片回退展示 `name`。
   *
   * `name` 仍是归并键：列表按它归并出跨项目「正本」，所以归并结果里的
   * `display_name` 取的是正本那条的值，可能为空。
   */
  display_name: string
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
  /**
   * Skill **声明**的产出协议等级（L0–L3），来自版本 manifest；未声明为空串。
   *
   * 与 `stage_result_last_run` 分开显示而不是合成一个字段：
   * "声称 L3"与"跑出来是 L3"恰恰是最需要被分开看见的差距。
   */
  stage_result_level: string
  stage_result_level_label: string
  /** 该等级能做什么（后端给的自然语言说明，避免前端再抄一份口径）。 */
  stage_result_capability: string
  /** **实测**结论：最近一次产出的校验摘要；从没跑过受控阶段时为 `null`。 */
  stage_result_last_run: StageResultRun | null
}

export interface SkillUploadResponse {
  code: number
  message: string
  data: Skill[] | null
}

/**
 * `suggest-metadata` 返回的导入建议（平台生成，全部要人工确认后才入库）。
 *
 * 名称建议只在"单 Skill 包"时才有值：一个输入框对应不了多条 Skill。
 */
export interface SkillMetadataSuggestion {
  /** 展示名称建议；多 Skill 包时为空串，前端据此把输入置为只读。 */
  name: string
  category: string
  category_label: string
  description: string
  /** 包内识别到的 Skill 数量。 */
  skill_count: number
  /** 包内各 Skill 的 frontmatter name，用于提示里列出。 */
  skill_names: string[]
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
  /**
   * 可选的「所属阶段」：先是规范任务类型，之后是**库里已在用的用户自定义阶段**
   * （`value` 形如 `custom:<名称>`，`custom` 为 true）。前端据此把两类在下拉里区分开，
   * 但**不自己做归一化**——能不能提交、怎么归一化一律以后端返回为准。
   */
  stage_options: Array<{ value: string; label: string; custom?: boolean }>
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
