<template>
  <div class="console" :class="{ 'console--compact': compact }">
    <!-- 页面抬头只回答三个问题：这里是什么、当前有多少能力、下一步能做什么。 -->
    <header class="console__bar">
      <div class="console__bar-left">
        <div class="console__brand-mark">E</div>
        <div class="console__heading">
          <div class="console__heading-line">
            <h2 class="console__title">Skill 进化工坊</h2>
            <a-tag size="small" :color="isMember ? 'arcoblue' : 'red'">{{ roleLabel }}</a-tag>
          </div>
          <p>让 Skill 进化：候选版本、评测门禁与发布治理</p>
        </div>
      </div>

      <a-space size="small">
        <a-button v-if="compact" size="small" @click="catalogDrawer = true">
          <template #icon><icon-apps /></template>
          能力目录
        </a-button>
        <a-button size="small" :disabled="!releaseId" @click="governanceDrawer = true">
          <template #icon><icon-settings /></template>
          版本治理
          <span v-if="missingCount" class="console__button-count">{{ missingCount }}</span>
        </a-button>
        <a-button
          size="small"
          :disabled="!canReview"
          :title="canReview ? '' : '仅项目成员可导入'"
          @click="importVisible = true"
        >
          导入
        </a-button>
        <a-button
          size="small"
          type="primary"
          :disabled="!canReview"
          :title="canReview ? '' : '仅项目成员可上传'"
          @click="uploadVisible = true"
        >
          上传候选
        </a-button>
      </a-space>
    </header>

    <section v-if="isMember" class="console__overview" aria-label="Skill 进化工坊 概览">
      <div class="console__metric">
        <span class="console__metric-value">{{ catalogStats.total }}</span>
        <span class="console__metric-label">全部 Skill</span>
      </div>
      <div class="console__metric console__metric--success">
        <span class="console__metric-value">{{ catalogStats.active }}</span>
        <span class="console__metric-label">生产生效</span>
      </div>
      <div class="console__metric console__metric--warning">
        <span class="console__metric-value">{{ catalogStats.candidates }}</span>
        <span class="console__metric-label">候选版本</span>
      </div>
      <div class="console__metric" :class="{ 'console__metric--danger': catalogStats.risks }">
        <span class="console__metric-value">{{ catalogStats.risks }}</span>
        <span class="console__metric-label">隔离版本</span>
      </div>
      <div class="console__overview-tip">
        <span class="console__overview-dot" />
        先从左侧选择 Skill，再查看版本；评测、审批和激活统一在“版本治理”中处理。
      </div>
    </section>

    <!-- 角色加载 / 越权 / 读取失败：三种情况分别说清楚，不用同一个灰底盖住 -->
    <div v-if="accessLoading && !isMember" class="console__blocked">
      <a-spin dot /> 正在确认你在本项目的角色…
    </div>
    <div v-else-if="accessError" class="console__blocked console__blocked--error">
      <icon-exclamation-circle />
      <span>{{ accessError }}</span>
      <a-button size="mini" @click="reloadAccess">重试</a-button>
    </div>
    <div v-else-if="!isMember" class="console__blocked">
      <icon-lock />
      <span>
        该控制台只对项目成员开放（测试负责人、测试执行人员）。当前账号不是本项目成员，
        如需参与 Skill 的评测与发布，请先由项目负责人把你加入项目。
      </span>
    </div>

    <div v-else class="console__grid">
      <aside v-if="!compact" class="console__col console__col--catalog">
        <SkillCatalogPanel
          :entries="filteredEntries"
          :loading="catalogLoading"
          :error="catalogError"
          :selected-skill-id="selectedSkillId"
          :keyword="keyword"
          :stage-filter="stageFilter"
          @select="selectSkill"
          @refresh="loadCatalog"
          @update:keyword="(value: string) => (keyword = value)"
          @update:stage-filter="(value: string) => (stageFilter = value)"
        />
      </aside>

      <main class="console__col console__col--main">
        <SkillVersionWorkbench
          :skill="selectedSkill"
          :versions="selectedVersions"
          :version-detail="versionDetail"
          :version-diff="versionDiff"
          :bindings="bindings"
          :selected-version-id="selectedVersionId"
          :base-version-id="baseVersionId"
          :loading-versions="loadingVersions"
          :loading-detail="loadingDetail"
          :loading-diff="loadingDiff"
          :loading-bindings="loadingBindings"
          :versions-error="versionsErrorText"
          :detail-error="detailError"
          :diff-error="diffError"
          :bindings-error="bindingsError"
          :can-validate="canValidate"
          :validate-hint="validateHint"
          :can-download="canDownload"
          :download-hint="downloadHint"
          :downloading="downloading"
          :validating="validating"
          :last-export="lastExport"
          @select-version="selectVersion"
          @update:base-version-id="onBaseVersionChange"
          @refresh="refreshVersions"
          @validate="validateSelected"
          @download="downloadSelected"
        />
      </main>

    </div>

    <!-- 小屏收敛：目录进左侧抽屉，治理进底部抽屉（design.md §8） -->
    <a-drawer
      v-model:visible="catalogDrawer"
      placement="left"
      :width="320"
      :footer="false"
      unmount-on-close
    >
      <template #title>能力目录</template>
      <SkillCatalogPanel
        :entries="filteredEntries"
        :loading="catalogLoading"
        :error="catalogError"
        :selected-skill-id="selectedSkillId"
        :keyword="keyword"
        :stage-filter="stageFilter"
        @select="selectSkill"
        @refresh="loadCatalog"
        @update:keyword="(value: string) => (keyword = value)"
        @update:stage-filter="(value: string) => (stageFilter = value)"
      />
    </a-drawer>

    <a-drawer
      v-model:visible="governanceDrawer"
      :placement="compact ? 'bottom' : 'right'"
      :height="compact ? 640 : undefined"
      :width="compact ? undefined : 420"
      :footer="false"
      unmount-on-close
    >
      <template #title>版本治理与发布门禁</template>
      <SkillGovernancePanel
        :release-id="releaseId"
        :approval-view="approvalView"
        :audit-logs="auditLogs"
        :actions="actions"
        :role-label="roleLabel"
        :loading-approval="loadingApproval"
        :loading-audit="loadingAudit"
        :approval-error="approvalError"
        :audit-error="auditError"
        @action="openAction"
        @refresh="loadVersionContext"
      />
    </a-drawer>

    <SkillUploadPreflightDrawer
      v-model:visible="uploadVisible"
      :project-id="projectId"
      @created="onCandidateCreated"
    />

    <SkillImportDrawer
      v-model:visible="importVisible"
      :project-id="projectId"
      @imported="loadCatalog"
    />

    <!-- 治理动作的原因弹窗：驳回/隔离/回滚必须留下原因，否则审计只有"有人点了按钮" -->
    <a-modal
      :visible="reasonModal.visible"
      :title="reasonModal.title"
      :ok-text="reasonModal.okText"
      :ok-loading="reasonModal.submitting"
      :mask-closable="false"
      unmount-on-close
      @cancel="closeReason"
      @before-ok="submitReason"
    >
      <a-textarea
        v-model="reasonModal.reason"
        :rows="3"
        :max-length="500"
        show-word-limit
        :placeholder="reasonModal.placeholder"
      />
      <div v-if="reasonModal.required" class="console__modal-note">
        该操作会写入审计与审批历史，必须填写原因。
      </div>
      <div v-if="reasonModal.error" class="console__modal-error">{{ reasonModal.error }}</div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconApps,
  IconExclamationCircle,
  IconLock,
  IconSettings,
} from '@arco-design/web-vue/es/icon'

import { SkillService } from '../services/skillService'
import { SkillHubService, toErrorMessage } from '../services/skillHubService'
import { useSkillHubAccess } from '../composables/useSkillHubAccess'
import SkillCatalogPanel from '../components/hub/SkillCatalogPanel.vue'
import SkillGovernancePanel from '../components/hub/SkillGovernancePanel.vue'
import SkillImportDrawer from '../components/hub/SkillImportDrawer.vue'
import SkillUploadPreflightDrawer from '../components/hub/SkillUploadPreflightDrawer.vue'
import SkillVersionWorkbench from '../components/hub/SkillVersionWorkbench.vue'
import type { SkillListItem } from '../types'
import type {
  AuditLogEntry,
  GovernanceAction,
  ReleaseApprovalView,
  ReleaseBindings,
  SkillCatalogEntry,
  SkillExportReceipt,
  SkillVersionDetail,
  SkillVersionSummary,
  VersionDiff,
} from '../types/hub'
import { RELEASE_STATE_LABEL, canReachReleaseState, type ReleaseState } from '../types/hub'

const props = defineProps<{ projectId: number }>()

// --------------------------------------------------------------------- 角色

const projectIdRef = computed(() => props.projectId)
const {
  roleLabel,
  loading: accessLoading,
  error: accessError,
  isMember,
  canReview,
  canGovern,
  reload: reloadAccess,
} = useSkillHubAccess(projectIdRef)

// --------------------------------------------------------------------- 状态

interface VersionBucket {
  versions: SkillVersionSummary[]
  /** 版本概览拉取失败的原因；为空表示这一项确实是"没有版本"。 */
  error: string
}

const skills = ref<SkillListItem[]>([])
const versionsBySkill = ref<Record<number, VersionBucket>>({})

const catalogLoading = ref(false)
const catalogError = ref('')
const keyword = ref('')
const stageFilter = ref('')

const selectedSkillId = ref<number | null>(null)
const selectedVersionId = ref('')
const baseVersionId = ref('')

const versionDetail = ref<SkillVersionDetail | null>(null)
const versionDiff = ref<VersionDiff | null>(null)
const bindings = ref<ReleaseBindings | null>(null)
const approvalView = ref<ReleaseApprovalView | null>(null)
const auditLogs = ref<AuditLogEntry[]>([])

const loadingVersions = ref(false)
const loadingDetail = ref(false)
const loadingDiff = ref(false)
const loadingBindings = ref(false)
const loadingApproval = ref(false)
const loadingAudit = ref(false)
const validating = ref(false)
const downloading = ref(false)

const versionsError = ref('')
const detailError = ref('')
const diffError = ref('')
const bindingsError = ref('')
const approvalError = ref('')
const auditError = ref('')

const lastExport = ref<SkillExportReceipt | null>(null)

const uploadVisible = ref(false)
const importVisible = ref(false)
const catalogDrawer = ref(false)
const governanceDrawer = ref(false)

// ------------------------------------------------------------- 响应式收敛

const compact = ref(false)
let mediaQuery: MediaQueryList | null = null

function onMediaChange(event: MediaQueryListEvent) {
  compact.value = event.matches
}

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 1180px)')
  compact.value = mediaQuery.matches
  mediaQuery.addEventListener('change', onMediaChange)
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', onMediaChange)
})

function closeDrawers() {
  catalogDrawer.value = false
  governanceDrawer.value = false
}

// ------------------------------------------------------------- 目录派生数据

const entries = computed<SkillCatalogEntry[]>(() => {
  return skills.value.map((skill) => {
    const bucket = versionsBySkill.value[skill.id]
    const versions = bucket?.versions ?? []
    const activeVersion = versions.find((item) => item.state === 'active') ?? null
    const candidateStates = ['draft', 'validating', 'shadow', 'awaiting_approval']
    return {
      id: skill.id,
      name: skill.name,
      description: skill.description,
      is_active: skill.is_active,
      creator_name: skill.creator_name,
      created_at: skill.created_at,
      // 阶段以"正在生效的那一版"为准；没有生效版本时退到最新一版。
      stage: String(activeVersion?.stage || versions[0]?.stage || ''),
      activeVersion,
      versions,
      candidateCount: versions.filter((item) => candidateStates.includes(item.state)).length,
      quarantinedCount: versions.filter((item) => item.state === 'quarantined').length,
      latestAt: versions[0]?.created_at ?? skill.created_at,
      versionsError: bucket?.error ?? '',
    }
  })
})

const filteredEntries = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return entries.value.filter((entry) => {
    if (stageFilter.value && entry.stage !== stageFilter.value) return false
    if (!kw) return true
    return `${entry.name} ${entry.description}`.toLowerCase().includes(kw)
  })
})

const catalogStats = computed(() => ({
  total: entries.value.length,
  active: entries.value.filter((item) => Boolean(item.activeVersion)).length,
  candidates: entries.value.reduce((sum, item) => sum + item.candidateCount, 0),
  risks: entries.value.reduce((sum, item) => sum + item.quarantinedCount, 0),
}))

const selectedSkill = computed(
  () => entries.value.find((entry) => entry.id === selectedSkillId.value) ?? null,
)

const selectedVersions = computed<SkillVersionSummary[]>(() => {
  const id = selectedSkillId.value
  if (id == null) return []
  return versionsBySkill.value[id]?.versions ?? []
})

const selectedVersion = computed(
  () => selectedVersions.value.find((item) => item.id === selectedVersionId.value) ?? null,
)

const releaseId = computed(() => selectedVersion.value?.release_id ?? '')

const versionsErrorText = computed(() => {
  if (versionsError.value) return versionsError.value
  const id = selectedSkillId.value
  if (id == null) return ''
  return versionsBySkill.value[id]?.error ?? ''
})

const missingCount = computed(() => approvalView.value?.missing_conditions.length ?? 0)

// ------------------------------------------------------------------ 数据加载

/** 统一收口"置 loading → 调用 → 归一错误"，避免每个接口各写一遍 try/catch。 */
async function runLoad(
  flag: Ref<boolean>,
  errorRef: Ref<string>,
  task: () => Promise<void>,
  fallback: string,
) {
  flag.value = true
  try {
    await task()
  } catch (err) {
    errorRef.value = toErrorMessage(err, fallback)
  } finally {
    flag.value = false
  }
}

async function loadCatalog() {
  const id = props.projectId
  if (!id) return
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const { items: list } = await SkillService.getSkills(id)
    skills.value = list

    // 版本概览逐个拉取：一个 Skill 拉失败不影响其它项，失败的那一项如实标错。
    const buckets: Record<number, VersionBucket> = {}
    await Promise.all(
      list.map(async (skill) => {
        try {
          buckets[skill.id] = {
            versions: await SkillHubService.listVersions(id, skill.id),
            error: '',
          }
        } catch (err) {
          buckets[skill.id] = {
            versions: [],
            error: toErrorMessage(err, '版本概览读取失败'),
          }
        }
      }),
    )
    versionsBySkill.value = buckets
  } catch (err) {
    catalogError.value = toErrorMessage(err, '能力目录读取失败')
    skills.value = []
    versionsBySkill.value = {}
    catalogLoading.value = false
    return
  }
  catalogLoading.value = false

  // 首次进入或刷新后，把选中项对齐到仍然存在的版本上。
  if (selectedSkillId.value == null) {
    const first = entries.value[0]
    if (first) await selectSkill(first.id)
    return
  }
  const list = selectedVersions.value
  if (!list.some((item) => item.id === selectedVersionId.value)) {
    selectedVersionId.value = list[0]?.id ?? ''
  }
  if (!selectedVersionId.value) {
    clearVersionContext()
    return
  }
  await loadVersionContext()
}

async function selectSkill(skillId: number) {
  if (selectedSkillId.value === skillId && selectedVersionId.value) return
  selectedSkillId.value = skillId
  selectedVersionId.value = ''
  baseVersionId.value = ''
  lastExport.value = null
  closeDrawers()

  const list = selectedVersions.value
  if (!list.length) {
    clearVersionContext()
    return
  }
  await selectVersion(list[0].id)
}

async function selectVersion(versionId: string) {
  selectedVersionId.value = versionId
  lastExport.value = null

  // 基线交给 loadVersionContext 按该版本的父版本决定；这里只负责把失效的基线清掉。
  const list = selectedVersions.value
  if (baseVersionId.value && !list.some((item) => item.id === baseVersionId.value)) {
    baseVersionId.value = ''
  }
  if (baseVersionId.value === versionId) baseVersionId.value = ''

  await loadVersionContext()
}

async function onBaseVersionChange(versionId: string) {
  baseVersionId.value = versionId
  const version = selectedVersion.value
  if (!version) return
  await loadDiff(version.id, versionId)
}

/**
 * 拉取版本差异。
 *
 * **没有基线就不发请求**：后端在没有 base 时会回落到 `previous_version`，而首个版本
 * 的 `previous_version` 是空的，接口会以 400「缺少对比基线」返回。那不是"差异为空"，
 * 而是"这一版没有可比的上一版"——把它当成空态讲清楚，比把一个红色 400 摆在用户面前诚实。
 */
async function loadDiff(versionId: string, baseId: string) {
  const skillId = selectedSkillId.value
  versionDiff.value = null
  diffError.value = ''
  if (!baseId || skillId == null) return
  await runLoad(
    loadingDiff,
    diffError,
    async () => {
      versionDiff.value = await SkillHubService.getVersionDiff(
        props.projectId,
        skillId,
        versionId,
        baseId,
      )
    },
    '版本差异计算失败',
  )
}

function clearVersionContext() {
  versionDetail.value = null
  versionDiff.value = null
  bindings.value = null
  approvalView.value = null
  auditLogs.value = []
  detailError.value = ''
  diffError.value = ''
  bindingsError.value = ''
  approvalError.value = ''
  auditError.value = ''
}

async function loadVersionContext() {
  const version = selectedVersion.value
  const skillId = selectedSkillId.value
  if (!version || skillId == null) {
    clearVersionContext()
    return
  }
  clearVersionContext()
  const rid = version.release_id || ''

  await Promise.all([
    runLoad(
      loadingDetail,
      detailError,
      async () => {
        versionDetail.value = await SkillHubService.getVersion(props.projectId, skillId, version.id)
      },
      '版本详情读取失败',
    ),
    runLoad(
      loadingBindings,
      bindingsError,
      async () => {
        bindings.value = rid ? await SkillHubService.getBindings(rid) : null
      },
      '能力绑定读取失败',
    ),
    runLoad(
      loadingApproval,
      approvalError,
      async () => {
        approvalView.value = rid ? await SkillHubService.getApprovalView(rid) : null
      },
      '治理状态读取失败',
    ),
    loadAudit(version.id, rid),
  ])

  // 基线：用户显式选过就用它，否则用详情里的父版本（权威的派生来源，
  // 比"取列表里下一个"可靠——单版本 Skill 就没有下一个）。
  if (!baseVersionId.value && versionDetail.value?.previous_version) {
    baseVersionId.value = String(versionDetail.value.previous_version)
  }
  await loadDiff(version.id, baseVersionId.value)
}

async function refreshVersions() {
  const skillId = selectedSkillId.value
  if (skillId == null || !props.projectId) return
  loadingVersions.value = true
  versionsError.value = ''
  try {
    const versions = await SkillHubService.listVersions(props.projectId, skillId)
    versionsBySkill.value = { ...versionsBySkill.value, [skillId]: { versions, error: '' } }
    if (!versions.some((item) => item.id === selectedVersionId.value)) {
      selectedVersionId.value = versions[0]?.id ?? ''
    }
  } catch (err) {
    versionsError.value = toErrorMessage(err, '版本列表读取失败')
  } finally {
    loadingVersions.value = false
  }
  if (selectedVersionId.value) await loadVersionContext()
  else clearVersionContext()
}

/**
 * 审计时间线。
 *
 * 后端按 `entity_type` 记的是 Django 类名，且过滤集里没有 `entity_id`：
 * 版本级动作（静态校验/下载/隔离）记在 `SkillVersion` 下，
 * 发布级动作（提交审批/审批/驳回/回滚）记在 `CapabilityRelease` 下。
 * 所以这里取两类各一次，再按 `entity_id` 收敛到当前版本与当前发布单元。
 */
async function loadAudit(versionId: string, rid: string) {
  loadingAudit.value = true
  auditError.value = ''
  try {
    const [versionLogs, releaseLogs] = await Promise.all([
      SkillHubService.listAuditLogs({
        project: props.projectId,
        entity_type: 'SkillVersion',
        page_size: 200,
      }),
      rid
        ? SkillHubService.listAuditLogs({
            project: props.projectId,
            entity_type: 'CapabilityRelease',
            page_size: 200,
          })
        : Promise.resolve([] as AuditLogEntry[]),
    ])

    const seen = new Set<string>()
    const merged = [...versionLogs, ...releaseLogs].filter((entry) => {
      const forVersion = String(entry.entity_id) === String(versionId)
      const forRelease = rid ? String(entry.entity_id) === String(rid) : false
      if (!forVersion && !forRelease) return false
      if (seen.has(entry.id)) return false
      seen.add(entry.id)
      return true
    })
    merged.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    auditLogs.value = merged.slice(0, 50)
  } catch (err) {
    auditLogs.value = []
    auditError.value = toErrorMessage(err, '审计日志读取失败')
  } finally {
    loadingAudit.value = false
  }
}

// ------------------------------------------------------------------ 按钮门控

function stateText(state: ReleaseState | string): string {
  return RELEASE_STATE_LABEL[state as ReleaseState] || state
}

const canValidate = computed(() => {
  const version = selectedVersion.value
  if (!version || !canReview.value) return false
  return version.state === 'draft' || version.state === 'validating'
})

const validateHint = computed(() => {
  const version = selectedVersion.value
  if (!version) return '请先在版本轨道里选一个版本'
  if (!canReview.value) return '仅项目成员可发起静态校验'
  if (version.state === 'draft' || version.state === 'validating') {
    return '静态校验通过后进入影子验证，才能执行门禁评测'
  }
  if (version.state === 'shadow' || version.state === 'awaiting_approval' || version.state === 'active') {
    return `v${version.version} 已通过静态校验（当前「${stateText(version.state)}」），无需重复提交`
  }
  if (version.state === 'rejected') {
    return '该版本静态校验未通过，需修正包内容后重新生成候选，不能原地重试'
  }
  return `当前状态「${stateText(version.state)}」不接受静态校验`
})

const canDownload = computed(() => Boolean(selectedVersion.value) && canReview.value)

const downloadHint = computed(() => {
  if (!selectedVersion.value) return '请先选择一个版本'
  if (!canReview.value) return '仅项目成员可下载'
  return '下载服务端生成的脱敏包（已隔离的版本会被拒绝导出）'
})

/**
 * 治理动作。
 *
 * "能不能点"取决于两处**后端给的**信号：`can_submit_approval` / `can_activate`
 * 与 `missing_conditions`；只有状态机合法边在本地抄了一份（`canReachReleaseState`），
 * 用于把"为什么点不动"讲清楚。前端不做任何"我认为可以"的判断。
 *
 * 这里刻意不提供"触发影子评测"：评测需要 baseline_run / candidate_run 两个评测轮次 ID，
 * 那是评测域的入口，控制台只负责展示评测结论（门禁 checks 与基线/候选指标）。
 */
const actions = computed<GovernanceAction[]>(() => {
  const view = approvalView.value
  if (!view) return []

  const label = view.state_label || stateText(view.state)
  const missingLabels = view.missing_conditions.map((item) => item.label)
  const missingText = missingLabels.length ? `还缺：${missingLabels.join('；')}` : ''

  return [
    {
      key: 'submit_approval',
      label: '提交审批',
      enabled: canReview.value && view.can_submit_approval,
      hint: !canReview.value
        ? '仅项目成员可提交审批'
        : view.can_submit_approval
          ? '提交后进入测试负责人审批队列'
          : missingText || `当前状态「${label}」不接受提交审批`,
    },
    {
      key: 'activate',
      label: '审批通过并激活',
      enabled: canGovern.value && view.can_activate,
      hint: !canGovern.value
        ? '仅测试负责人可审批激活'
        : // 先判状态机：已经生效/已隔离的版本即使门禁也报"还缺评测门禁"，
          // 那句话对着"生产生效"说会误导，所以状态走不到 active 时优先讲状态。
          !canReachReleaseState(view.state, 'active')
          ? `当前状态「${label}」不可直接激活`
          : view.evidence_stale
            ? '审批依据已变化：提交审批后被重新评测，需重新提交审批'
            : view.can_activate
              ? '激活后该版本立即进入生产生效'
              : missingText || `当前状态「${label}」不可直接激活`,
    },
    {
      key: 'reject',
      label: '驳回',
      danger: true,
      enabled: canGovern.value && canReachReleaseState(view.state, 'rejected'),
      hint: !canGovern.value
        ? '仅测试负责人可驳回'
        : canReachReleaseState(view.state, 'rejected')
          ? '驳回需写明原因，包内容修正后需重新生成候选'
          : `当前状态「${label}」不可驳回`,
    },
    {
      key: 'quarantine',
      label: '隔离',
      danger: true,
      enabled: canGovern.value && canReachReleaseState(view.state, 'quarantined'),
      hint: !canGovern.value
        ? '仅测试负责人可隔离'
        : canReachReleaseState(view.state, 'quarantined')
          ? '隔离后运行时立即停止加载该版本，并清除生效版本指针'
          : `当前状态「${label}」不能再隔离`,
    },
    {
      key: 'rollback',
      label: '回滚',
      danger: true,
      enabled: canGovern.value && canReachReleaseState(view.state, 'rolled_back'),
      hint: !canGovern.value
        ? '仅测试负责人可回滚'
        : canReachReleaseState(view.state, 'rolled_back')
          ? '回滚到上一个生效版本，回滚原因会写入审计'
          : '只有处于「生产生效」的版本可以回滚',
    },
  ]
})

// ----------------------------------------------------------- 动作与原因弹窗

interface ActionMeta {
  title: string
  placeholder: string
  required: boolean
  okText: string
}

const ACTION_META: Record<string, ActionMeta> = {
  submit_approval: {
    title: '提交审批',
    placeholder: '可选：补充本次变更的背景，便于审批人判断',
    required: false,
    okText: '提交审批',
  },
  activate: {
    title: '审批通过并激活',
    placeholder: '可选：填写审批意见',
    required: false,
    okText: '确认激活',
  },
  reject: {
    title: '驳回候选',
    placeholder: '必填：说明驳回原因（会写入审计与审批历史）',
    required: true,
    okText: '确认驳回',
  },
  quarantine: {
    title: '隔离版本',
    placeholder: '必填：说明隔离原因（例如疑似密钥泄露、产出严重错误）',
    required: true,
    okText: '确认隔离',
  },
  rollback: {
    title: '回滚发布',
    placeholder: '必填：说明回滚原因（线上出现了什么问题）',
    required: true,
    okText: '确认回滚',
  },
}

const reasonModal = reactive({
  visible: false,
  key: '',
  title: '',
  placeholder: '',
  required: false,
  okText: '确认',
  reason: '',
  error: '',
  submitting: false,
})

function openAction(key: string) {
  const meta = ACTION_META[key]
  if (!meta) return
  reasonModal.visible = true
  reasonModal.key = key
  reasonModal.title = meta.title
  reasonModal.placeholder = meta.placeholder
  reasonModal.required = meta.required
  reasonModal.okText = meta.okText
  reasonModal.reason = ''
  reasonModal.error = ''
  reasonModal.submitting = false
}

function closeReason() {
  reasonModal.visible = false
  reasonModal.reason = ''
  reasonModal.error = ''
}

async function submitReason(): Promise<boolean> {
  const key = reasonModal.key
  const reason = reasonModal.reason.trim()
  if (reasonModal.required && !reason) {
    reasonModal.error = '该操作必须填写原因'
    return false
  }
  const view = approvalView.value
  const version = selectedVersion.value
  const skillId = selectedSkillId.value
  if (!view || !version || skillId == null) {
    reasonModal.error = '当前没有可操作的发布单元，请重新选择版本'
    return false
  }

  reasonModal.submitting = true
  reasonModal.error = ''
  try {
    switch (key) {
      case 'submit_approval':
        await SkillHubService.submitApproval(view.release_id, reason)
        Message.success(`v${version.version} 已提交审批`)
        break
      case 'activate':
        await SkillHubService.promote(view.release_id, reason)
        Message.success(`v${version.version} 已激活生效`)
        break
      case 'reject':
        await SkillHubService.reject(view.release_id, reason)
        Message.success(`v${version.version} 已驳回`)
        break
      case 'quarantine':
        // 走版本级隔离：除隔离发布单元外，还会清掉 Skill 的生效版本指针。
        await SkillHubService.quarantineVersion(props.projectId, skillId, version.id, reason)
        Message.success(`v${version.version} 已隔离`)
        break
      case 'rollback':
        await SkillHubService.rollback(view.release_id, reason)
        Message.success(`v${version.version} 已回滚`)
        break
      default:
        return false
    }
    closeReason()
    await refreshVersions()
    return true
  } catch (err) {
    reasonModal.error = toErrorMessage(err, '操作失败')
    return false
  } finally {
    reasonModal.submitting = false
  }
}

async function validateSelected() {
  const version = selectedVersion.value
  const skillId = selectedSkillId.value
  if (!version || skillId == null) return
  validating.value = true
  try {
    const result = await SkillHubService.validateVersion(
      props.projectId,
      skillId,
      version.id,
      '控制台发起静态校验',
    )
    Message.success(
      `v${result.version.version} 静态校验通过，当前状态：${stateText(result.version.state)}`,
    )
    await refreshVersions()
  } catch (err) {
    Message.error(toErrorMessage(err, '提交静态校验失败'))
  } finally {
    validating.value = false
  }
}

async function downloadSelected() {
  const version = selectedVersion.value
  const skillId = selectedSkillId.value
  if (!version || skillId == null) return
  downloading.value = true
  try {
    const result = await SkillHubService.downloadVersion(props.projectId, skillId, version.id)
    const url = window.URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)

    // 把"下到的是哪一版、哈希是多少"留在页面上（R11）。
    lastExport.value = {
      version: version.version,
      filename: result.filename,
      packageSha256: result.packageSha256,
      exportSha256: result.exportSha256,
      redactedFiles: result.redactedFiles,
    }
    Message.success(`已导出 v${version.version}：${result.filename}`)

    // 导出会写 download 审计，顺带把时间线刷新一下；版本可能因二次扫描被隔离。
    await loadAudit(version.id, releaseId.value)
  } catch (err) {
    Message.error(toErrorMessage(err, '下载失败'))
  } finally {
    downloading.value = false
  }
}

async function onCandidateCreated(payload: { skillId: number; versionId: string; version: string }) {
  selectedSkillId.value = payload.skillId
  selectedVersionId.value = payload.versionId
  baseVersionId.value = ''
  await loadCatalog()
  if (selectedVersionId.value !== payload.versionId) {
    selectedVersionId.value = payload.versionId
    await loadVersionContext()
  }
}

watch(projectIdRef, loadCatalog, { immediate: true })
</script>

<style scoped>
.console {
  display: flex;
  flex-direction: column;
  /* 父容器是 flex column，这里靠 flex:1 撑满，不用 height:100%（避免百分比高度链断裂）。 */
  flex: 1;
  min-height: 0;
  background: #f4f7fb;
}

.console__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 16px 20px 14px;
  background: var(--color-bg-2);
  border-bottom: 1px solid #e7edf5;
}

.console__bar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.console__brand-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 11px;
  color: #fff;
  font-size: 17px;
  font-weight: 700;
  background: linear-gradient(145deg, #1769e0, #2684ff);
  box-shadow: 0 7px 18px rgba(23, 105, 224, 0.2);
}

.console__heading {
  min-width: 0;
}

.console__heading-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.console__heading p {
  margin: 3px 0 0;
  color: #7a8799;
  font-size: 12px;
}

.console__title {
  margin: 0;
  font-size: 19px;
  font-weight: 650;
  letter-spacing: -0.02em;
  color: #1f2d3d;
}

.console__button-count {
  display: inline-grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 9px;
  color: #b54708;
  background: #fff1da;
  font-size: 11px;
  line-height: 1;
}

.console__overview {
  display: flex;
  align-items: center;
  gap: 0;
  min-height: 66px;
  padding: 10px 20px;
  background: #fff;
  border-bottom: 1px solid #e7edf5;
}

.console__metric {
  display: grid;
  grid-template-columns: auto auto;
  align-items: baseline;
  gap: 7px;
  min-width: 118px;
  padding: 2px 18px;
  border-right: 1px solid #edf1f6;
}

.console__metric:first-child {
  padding-left: 0;
}

.console__metric-value {
  color: #25364d;
  font-size: 22px;
  font-weight: 650;
  line-height: 1;
}

.console__metric-label {
  color: #7c899a;
  font-size: 12px;
}

.console__metric--success .console__metric-value { color: #17875d; }
.console__metric--warning .console__metric-value { color: #d46b08; }
.console__metric--danger .console__metric-value { color: #cf3c4f; }

.console__overview-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  max-width: 430px;
  color: #7a8799;
  font-size: 12px;
  line-height: 1.55;
}

.console__overview-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
  background: #2b78e4;
  box-shadow: 0 0 0 4px rgba(43, 120, 228, 0.1);
}

.console__blocked {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex: 1;
  padding: 48px 24px;
  font-size: 13px;
  color: var(--color-text-3);
  text-align: center;
  background: var(--color-bg-2);
}

.console__blocked--error {
  color: rgb(var(--red-6));
}

.console__grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 306px minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
  background: #f4f7fb;
}

.console--compact .console__grid {
  grid-template-columns: minmax(0, 1fr);
}

.console__col {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: var(--color-bg-2);
  border: 1px solid #e5ebf3;
  border-radius: 10px;
  box-shadow: 0 2px 10px rgba(30, 55, 90, 0.035);
}

@media (max-width: 900px) {
  .console__overview { overflow-x: auto; }
  .console__overview-tip { display: none; }
  .console__metric { min-width: 108px; }
}

.console__modal-note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-3);
}

.console__modal-error {
  margin-top: 6px;
  font-size: 12px;
  color: rgb(var(--red-6));
}
</style>
