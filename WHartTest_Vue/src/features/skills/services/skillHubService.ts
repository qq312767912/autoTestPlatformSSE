// Skill Hub 生产控制台的 API 服务（T18）。
//
// 两套后端入口，各自的返回包装**不一样**，这里显式分成两个 caller，不做启发式嗅探：
//
// - `skills` 域（`/api/projects/{id}/skills/...`）的视图返回 `{code, message, data}`，
//   平台统一包装层再包一层，所以业务数据在 `response.data.data.data`；
// - 知识飞轮域（`/api/knowledge-evolution/...`）的视图返回**裸对象**，
//   业务数据在 `response.data.data`。
//
// 早先的写法靠"看有没有 data 字段"来猜，遇到本身带 data 键的裸对象就会取错层。
import service, { request } from '@/utils/request'

import type {
  AuditLogEntry,
  CapabilityReleaseSummary,
  PreflightReport,
  PreflightResult,
  ReleaseApprovalView,
  ReleaseBindings,
  SkillVersionDetail,
  SkillVersionSummary,
  VersionDiff,
} from '../types/hub'

interface Wrapped {
  success: boolean
  data?: any
  total?: number
  error?: string
  message?: string
}

/** 与 `@/utils/request` 的 `RequestConfig` 结构对齐（该类型未导出，这里按形状声明）。 */
interface RequestArgs {
  url: string
  method: string
  data?: any
  params?: any
  [key: string]: any
}

/** skills 域：业务数据在第三层。 */
async function callSkills<T>(config: RequestArgs): Promise<T> {
  const response = (await request<any>(config)) as unknown as Wrapped
  if (!response || response.success !== true) {
    throw new Error(response?.error || '请求失败')
  }
  const api = response.data
  if (api == null) {
    throw new Error(response.error || '服务端未返回数据')
  }
  // 已是 `{code, message, data}` 形状时取下层的 data；否则原样返回，
  // 便于错误分支（如 400 带 data:null）也能被调用方看到。
  return (typeof api === 'object' && 'data' in api ? api.data : api) as T
}

/** 知识飞轮域：业务数据在第二层。 */
async function callEvo<T>(config: RequestArgs): Promise<T> {
  const response = (await request<any>(config)) as unknown as Wrapped
  if (!response || response.success !== true) {
    throw new Error(response?.error || '请求失败')
  }
  return response.data as T
}

/** 请求失败时把 error 归一成 Error，避免调用方到处判断类型。 */
export function toErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  if (typeof error === 'string' && error) {
    return error
  }
  return fallback
}

export interface CandidatePayload {
  token: string
  change_reason?: string
  expected_benefit?: string
  impact_scope?: string
  api_key?: string
}

export class SkillHubService {
  // ---------------------------------------------------------------- 上传预检

  /**
   * 第一步：上传包预检。
   *
   * **必须绕开 `request()`**：预检未通过时后端返回 400，而逐条校验报告就在 400 的
   * body 里（`{code:400, message:'包校验未通过', data: 报告}`）。`request()` 的错误
   * 分支只保留 `errors` 字段，会把 `data` 丢掉——那样页面就只剩一句"包校验未通过"，
   * 完全看不到哪一项没过，R11 要求的"先展示包校验结果"就落空了。
   *
   * 所以这里直接用 axios 实例 + `validateStatus: () => true`，自己判定两种状态码。
   */
  static async preflight(projectId: number, file: File): Promise<PreflightResult> {
    const formData = new FormData()
    formData.append('file', file)
    const response = await service({
      // baseURL 已经是 `/api`，这里不能再带前缀，否则拼成 `/api/api/...`。
      url: `/projects/${projectId}/skills/preflight/`,
      method: 'POST',
      data: formData,
      validateStatus: () => true,
      timeout: 300000,
    } as any)

    const body = response.data
    const payload = (body && typeof body === 'object' && 'data' in body ? body.data : body) as
      | PreflightResult
      | null
    if (!payload || typeof payload !== 'object' || !('package_sha256' in payload)) {
      throw new Error((body && body.message) || '预检失败')
    }
    // 预检失败时 ok=false，但报告本身是有价值的：交给调用方渲染。
    return payload
  }

  /** 第二步：用预检令牌创建候选版本（一律进入草稿，不自动生效）。 */
  static async createCandidate(
    projectId: number,
    payload: CandidatePayload,
  ): Promise<{ skill: { id: number; name: string }; version: SkillVersionDetail }> {
    return callSkills({
      url: `/projects/${projectId}/skills/candidates/`,
      method: 'POST',
      data: payload,
    })
  }

  // ---------------------------------------------------------------- 版本

  static async listVersions(projectId: number, skillId: number): Promise<SkillVersionSummary[]> {
    const data = await callSkills<SkillVersionSummary[] | null>({
      url: `/projects/${projectId}/skills/${skillId}/versions/`,
      method: 'GET',
    })
    return Array.isArray(data) ? data : []
  }

  static async getVersion(
    projectId: number,
    skillId: number,
    versionId: string,
  ): Promise<SkillVersionDetail> {
    return callSkills({
      url: `/projects/${projectId}/skills/${skillId}/versions/${versionId}/`,
      method: 'GET',
    })
  }

  /** 提交静态校验：草稿 -> 影子验证。返回版本快照与逐条校验报告。 */
  static async validateVersion(
    projectId: number,
    skillId: number,
    versionId: string,
    reason = '',
  ): Promise<{ version: SkillVersionDetail; report: PreflightReport }> {
    return callSkills({
      url: `/projects/${projectId}/skills/${skillId}/versions/${versionId}/validate/`,
      method: 'POST',
      data: { reason },
    })
  }

  static async getVersionDiff(
    projectId: number,
    skillId: number,
    versionId: string,
    baseId?: string,
  ): Promise<VersionDiff> {
    return callSkills({
      url: `/projects/${projectId}/skills/${skillId}/versions/${versionId}/diff/`,
      method: 'GET',
      params: baseId ? { base: baseId } : undefined,
    })
  }

  static async quarantineVersion(
    projectId: number,
    skillId: number,
    versionId: string,
    reason: string,
  ): Promise<SkillVersionDetail> {
    return callSkills({
      url: `/projects/${projectId}/skills/${skillId}/versions/${versionId}/quarantine/`,
      method: 'POST',
      data: { reason },
    })
  }

  /**
   * 下载脱敏包。
   *
   * blob 响应被请求拦截器原样放行（不套 success/data 包装），所以这里不能走
   * 统一 caller。导出哈希与版本号通过响应头带回，页面要当场展示给用户核对——
   * "下载到的到底是不是这一版"必须可见，不能只给一个文件。
   */
  static async downloadVersion(
    projectId: number,
    skillId: number,
    versionId: string,
  ): Promise<{ blob: Blob; filename: string; packageSha256: string; exportSha256: string; redactedFiles: number }> {
    const response = await service({
      // 同 preflight：axios 实例 baseURL 已是 `/api`，不能再加前缀。
      url: `/projects/${projectId}/skills/${skillId}/versions/${versionId}/download/`,
      method: 'GET',
      responseType: 'blob',
      timeout: 300000,
    } as any)

    const headers = (response.headers || {}) as Record<string, string>
    return {
      blob: response.data as Blob,
      filename: SkillHubService.parseFilename(headers['content-disposition']) || 'skill-version.zip',
      packageSha256: headers['x-package-sha256'] || '',
      exportSha256: headers['x-export-sha256'] || '',
      redactedFiles: Number(headers['x-skill-redacted-files'] || 0),
    }
  }

  static parseFilename(disposition?: string): string {
    if (!disposition) return ''
    // 优先取 RFC 5987 的 filename*，其次取普通 filename（两者都由后端设置）。
    const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
    if (star?.[1]) {
      try {
        return decodeURIComponent(star[1].trim())
      } catch {
        return star[1].trim()
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(disposition)
    return plain?.[1]?.trim() || ''
  }

  // ---------------------------------------------------------------- 发布治理

  static async listReleases(params: {
    project: number
    kind?: string
  }): Promise<CapabilityReleaseSummary[]> {
    const data = await callEvo<CapabilityReleaseSummary[] | { results: CapabilityReleaseSummary[] }>({
      url: '/knowledge-evolution/capability-releases/',
      method: 'GET',
      params,
    })
    return Array.isArray(data) ? data : (data?.results ?? [])
  }

  static async getApprovalView(releaseId: string): Promise<ReleaseApprovalView> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/approval-view/`,
      method: 'GET',
    })
  }

  static async getBindings(releaseId: string): Promise<ReleaseBindings> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/bindings/`,
      method: 'GET',
    })
  }

  static async submitApproval(releaseId: string, reason = ''): Promise<CapabilityReleaseSummary> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/submit-approval/`,
      method: 'POST',
      data: { reason },
    })
  }

  static async promote(releaseId: string, reason = ''): Promise<CapabilityReleaseSummary> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/promote/`,
      method: 'POST',
      data: { reason },
    })
  }

  static async reject(releaseId: string, reason: string): Promise<CapabilityReleaseSummary> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/reject/`,
      method: 'POST',
      data: { reason },
    })
  }

  static async rollback(releaseId: string, reason = ''): Promise<{ restored_release_id: string | null }> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/rollback/`,
      method: 'POST',
      data: { reason },
    })
  }

  static async quarantineRelease(releaseId: string, reason: string): Promise<CapabilityReleaseSummary> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/quarantine/`,
      method: 'POST',
      data: { reason },
    })
  }

  static async evaluateShadow(
    releaseId: string,
    baselineRun: string,
    candidateRun: string,
  ): Promise<Record<string, unknown>> {
    return callEvo({
      url: `/knowledge-evolution/capability-releases/${releaseId}/evaluate-shadow/`,
      method: 'POST',
      data: { baseline_run: baselineRun, candidate_run: candidateRun },
    })
  }

  // ---------------------------------------------------------------- 审计

  /**
   * 审计日志。
   *
   * 后端按 `entity_type` 记录的是 Django 类名：版本级动作记 `SkillVersion`，
   * 发布级动作记 `CapabilityRelease`；而过滤集里**没有** `entity_id`，
   * 所以调用方要按 `entity_id` 在本地二次过滤（见控制台 `loadAudit`）。
   */
  static async listAuditLogs(params: {
    project: number
    entity_type?: string
    action?: string
    page?: number
    page_size?: number
  }): Promise<AuditLogEntry[]> {
    const data = await callEvo<AuditLogEntry[] | { results: AuditLogEntry[] }>({
      url: '/knowledge-evolution/knowledge-audit-logs/',
      method: 'GET',
      params,
    })
    return Array.isArray(data) ? data : (data?.results ?? [])
  }
}
