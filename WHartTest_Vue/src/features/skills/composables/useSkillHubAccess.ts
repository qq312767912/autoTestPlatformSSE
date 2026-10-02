// 控制台的角色门控（T18 验收：只显示测试负责人和测试执行人员）。
//
// 角色真值来自后端 `projects.roles`（owner/admin = 测试负责人，member = 测试执行人员）。
// 前端不自己发明第三套口径，只把同一个映射抄一遍用于"显示/禁用"，
// **真正的拦截仍在后端**——这里做的只是让按钮别亮成能点的样子。
//
// 取数走 skills 域的自服务端点 `projects/{id}/skills/hub-access/`，而**不是**
// `projects/{id}/members/`。原因（两者都不是"实现口味"，是实打实的可用性差异）：
//
// ① members 是成员管理接口，要求 `projects.view_projectmember` 这一全局模型权限位。
//    业务角色的真值本来在 `projects.roles`，与全局模型权限无关；一个持有 `member`
//    角色、本该能进控制台的项目成员，会因为缺这个权限位拿到 403，前端据此把他判成
//    "非本项目成员"——T18 的验收项就此落空。
// ② 为了知道自己是谁，没必要把整个项目的成员列表拉回来。hub-access 只回答"我是谁"。
//
// 403 是"不是本项目成员"的正常门控结果，不是读取故障：这里刻意不置 error，
// 让页面渲染"该控制台只对项目成员开放"的说明文案，而不是弹个报错让人点重试。
//
// 注意：项目上下文用 `watch(currentProjectId, { immediate: true })` 驱动，
// 不用 onMounted。项目恢复晚于组件挂载时，onMounted 会被静默跳过，
// 于是页面永远停在"非成员"。
import { computed, ref, watch, type Ref } from 'vue'

import { request } from '@/utils/request'

import type { ProjectBusinessRole } from '../types/hub'

/** hub-access 的业务数据体。 */
interface HubAccessPayload {
  project_id: number
  /** 'test_lead' / 'test_executor'；理论上非成员不会走到这里（403 已拦）。 */
  business_role: string | null
  is_test_lead: boolean
  is_test_executor: boolean
}

export interface SkillHubAccess {
  role: Ref<ProjectBusinessRole>
  roleLabel: Ref<string>
  loading: Ref<boolean>
  error: Ref<string>
  /** 是否为该项目成员（负责人或执行人员都算）。 */
  isMember: Ref<boolean>
  isLead: Ref<boolean>
  canReview: Ref<boolean>
  canGovern: Ref<boolean>
  reload: () => Promise<void>
}

export function useSkillHubAccess(projectId: Ref<number | null | undefined>): SkillHubAccess {
  const role = ref<ProjectBusinessRole>('none')
  const loading = ref(false)
  const error = ref('')

  async function load() {
    const id = projectId.value
    if (!id) {
      role.value = 'none'
      error.value = ''
      return
    }
    loading.value = true
    error.value = ''
    try {
      const response = (await request<any>({
        url: `/projects/${id}/skills/hub-access/`,
        method: 'GET',
      })) as unknown as { success: boolean; status?: number; data?: any; error?: string }

      if (!response || response.success !== true) {
        // 403 = 不是本项目成员。这是门控结果，不是故障，交给页面的说明文案去讲。
        if (response?.status === 403) {
          role.value = 'none'
          return
        }
        throw new Error(response?.error || '无法读取项目角色')
      }

      // skills 域视图返回 {code, message, data}，平台渲染层会再包一层，
      // 所以业务数据落在 response.data.data。
      const payload: HubAccessPayload | undefined = response.data?.data ?? response.data
      const businessRole = String(payload?.business_role || '')
      if (businessRole === 'test_lead') {
        role.value = 'lead'
      } else if (businessRole === 'test_executor') {
        role.value = 'executor'
      } else {
        role.value = 'none'
      }
    } catch (err) {
      role.value = 'none'
      error.value = err instanceof Error ? err.message : '无法读取项目角色'
    } finally {
      loading.value = false
    }
  }

  watch(projectId, load, { immediate: true })

  const isMember = computed(() => role.value !== 'none')
  const isLead = computed(() => role.value === 'lead')
  const roleLabel = computed(() => {
    switch (role.value) {
      case 'lead':
        return '测试负责人'
      case 'executor':
        return '测试执行人员'
      default:
        return '非本项目成员'
    }
  })

  return {
    role,
    roleLabel,
    loading,
    error,
    isMember,
    isLead,
    /** 上传、预检、校验、下载、查看版本与评测：项目成员皆可。 */
    canReview: isMember,
    /** 审批、激活、驳回、回滚、隔离：仅测试负责人。 */
    canGovern: isLead,
    reload: load,
  }
}
