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
  version: string
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

export interface SkillListResponse {
  code: number
  message: string
  data: SkillListItem[]
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
