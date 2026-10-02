import { request } from '@/utils/request'
import type {
  Skill,
  SkillListItem,
  SkillUploadResponse,
  SkillGitImportResponse,
  SkillListResponse,
  SkillListMeta,
  SkillDetailResponse,
  SkillContentResponse,
  SkillStoreConfig
} from '../types'

export class SkillService {
  /**
   * 获取**公共** Skill 目录（Skill Hub）。
   *
   * ⚠️ 返回的列表**不按项目过滤**：Skill 是平台公共资源，任何项目看到的是同一份
   * 内容，同名副本已在后端归并成一条正本（`copies` 给出库里实际有几份）。
   * `projectId` 仍要传（后端要它做写侧的项目锚点与角色判定），但它不再决定
   * "能看到什么"。
   *
   * 同时返回 `meta`：调用者能否补填阶段、可绑定的阶段清单。调用方若用不到 meta，
   * 只取 `items` 即可。
   */
  static async getSkills(projectId: number): Promise<{ items: SkillListItem[]; meta: SkillListMeta }> {
    const response = await request<SkillListResponse>({
      url: `/projects/${projectId}/skills/`,
      method: 'GET'
    })

    const api = response.data as any
    if (response.success && api) {
      const data = api.data
      return {
        items: Array.isArray(data) ? data : [],
        meta: api.meta ?? { can_bind_stage: false, stage_options: [] }
      }
    }
    throw new Error(response.error || '获取 Skills 列表失败')
  }

  /**
   * 给 Skill 补填 / 撤销能力阶段（Skill Hub 上「阶段未声明」的补救入口）。
   *
   * 后端只改 `Skill.declared_stage`（版本包不可改写），阶段取值必须是
   * 能力注册表里登记过的业务能力阶段；传空串表示撤销声明。
   * 权限由后端判定：平台超管，或在任一项目里是测试负责人。
   */
  static async bindSkillStage(
    projectId: number,
    skillId: number,
    stage: string
  ): Promise<{ id: number; name: string; declared_stage: string }> {
    const response = await request<{ code: number; message: string; data: any }>({
      url: `/projects/${projectId}/skills/${skillId}/stage/`,
      method: 'POST',
      data: { stage }
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return api.data
    }
    throw new Error(response.error || '更新阶段声明失败')
  }

  /**
   * 获取 Skill 详情
   */
  static async getSkillDetail(projectId: number, skillId: number): Promise<Skill> {
    const response = await request<SkillDetailResponse>({
      url: `/projects/${projectId}/skills/${skillId}/`,
      method: 'GET'
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return api.data
    }
    throw new Error(response.error || '获取 Skill 详情失败')
  }

  /**
   * 上传 Skill zip 文件
   */
  static async uploadSkill(projectId: number, file: File, apiKey?: string): Promise<Skill[]> {
    const formData = new FormData()
    formData.append('file', file)
    if (apiKey) {
      formData.append('api_key', apiKey)
    }

    const response = await request<SkillUploadResponse>({
      url: `/projects/${projectId}/skills/upload/`,
      method: 'POST',
      data: formData
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return Array.isArray(api.data) ? api.data : [api.data]
    }
    throw new Error(api?.message || response.error || '上传 Skill 失败')
  }

  /**
   * 从 Git 仓库导入 Skill
   */
  static async importFromGit(
    projectId: number,
    gitUrl: string,
    branch?: string,
    apiKey?: string
  ): Promise<Skill[]> {
    const payload: { git_url: string; branch?: string; api_key?: string } = { git_url: gitUrl }
    if (branch) {
      payload.branch = branch
    }
    if (apiKey) {
      payload.api_key = apiKey
    }

    const response = await request<SkillGitImportResponse>({
      url: `/projects/${projectId}/skills/import-git/`,
      method: 'POST',
      data: payload
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return Array.isArray(api.data) ? api.data : [api.data]
    }
    throw new Error(api?.message || response.error || '从 Git 导入 Skill 失败')
  }

  /**
   * 从远程 zip URL 导入 Skill（用于 Skill 商店）
   */
  static async importFromZipUrl(
    projectId: number,
    zipUrl: string,
    sha256?: string,
    apiKey?: string
  ): Promise<Skill[]> {
    const payload: { zip_url: string; sha256?: string; api_key?: string } = { zip_url: zipUrl }
    if (sha256) {
      payload.sha256 = sha256
    }
    if (apiKey) {
      payload.api_key = apiKey
    }

    const response = await request<SkillGitImportResponse>({
      url: `/projects/${projectId}/skills/import-zip-url/`,
      method: 'POST',
      data: payload
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return Array.isArray(api.data) ? api.data : [api.data]
    }
    throw new Error(api?.message || response.error || '从 zip URL 导入 Skill 失败')
  }

  /**
   * 获取 Skill 商店配置（默认源等）
   */
  static async getStoreConfig(projectId: number): Promise<SkillStoreConfig> {
    const response = await request<{ code: number; message: string; data: SkillStoreConfig }>({
      url: `/projects/${projectId}/skills/store-config/`,
      method: 'GET'
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return api.data
    }
    throw new Error(api?.message || response.error || '获取 Skill 商店配置失败')
  }

  /**
   * 切换 Skill 启用状态
   */
  static async toggleSkill(projectId: number, skillId: number, isActive: boolean): Promise<Skill> {
    const response = await request<SkillDetailResponse>({
      url: `/projects/${projectId}/skills/${skillId}/`,
      method: 'PATCH',
      data: { is_active: isActive }
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return api.data
    }
    throw new Error(response.error || '更新 Skill 状态失败')
  }

  /**
   * 删除 Skill
   */
  static async deleteSkill(projectId: number, skillId: number): Promise<void> {
    const response = await request({
      url: `/projects/${projectId}/skills/${skillId}/`,
      method: 'DELETE'
    })

    if (!response.success) {
      throw new Error(response.error || '删除 Skill 失败')
    }
  }

  /**
   * 获取 Skill 的 SKILL.md 内容
   */
  static async getSkillContent(projectId: number, skillId: number): Promise<{ name: string; description: string; content: string }> {
    const response = await request<SkillContentResponse>({
      url: `/projects/${projectId}/skills/${skillId}/content/`,
      method: 'GET'
    })

    const api = response.data as any
    if (response.success && api?.data) {
      return api.data
    }
    throw new Error(response.error || '获取 Skill 内容失败')
  }
}
