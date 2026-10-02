<template>
  <div class="skill-manager">
    <!-- 头部操作栏：标题由页面抬头统一给，这里只放动作，避免同一句话出现两次 -->
    <div class="header-bar">
      <a-space>
        <a-button type="primary" status="success" @click="showStoreModal = true">
          <template #icon><icon-storage /></template>
          {{ text.skillStore }}
        </a-button>
        <a-button @click="showGitImportModal = true">
          <template #icon><icon-github /></template>
          {{ text.importFromGit }}
        </a-button>
        <a-button type="primary" @click="showUploadModal = true">
          <template #icon><icon-upload /></template>
          {{ text.uploadSkill }}
        </a-button>
      </a-space>
    </div>

    <!-- 类型筛选：来源 + 能力阶段。纯前端过滤，列表本来就是这个项目的全量。 -->
    <div class="filter-bar">
      <span class="filter-label">{{ text.filterLabel }}</span>
      <a-select
        v-model="sourceFilter"
        class="filter-select"
        size="small"
        allow-clear
        :placeholder="text.filterSourceAll"
      >
        <a-option v-for="opt in sourceOptions" :key="`src-${opt.value}`" :value="opt.value">
          {{ opt.label }}（{{ opt.count }}）
        </a-option>
      </a-select>
      <a-select
        v-model="stageFilter"
        class="filter-select"
        size="small"
        allow-clear
        :placeholder="text.filterStageAll"
      >
        <a-option v-for="opt in stageOptions" :key="`stage-${opt.value}`" :value="opt.value">
          {{ opt.label }}（{{ opt.count }}）
        </a-option>
      </a-select>
      <span class="filter-summary">{{ text.filterSummary(filteredSkills.length, skills.length) }}</span>
      <a-button v-if="hasFilter" type="text" size="mini" @click="clearFilters">
        {{ text.clearFilters }}
      </a-button>
    </div>

    <!-- 存量 Skill 的 manifest 没有声明阶段，筛选器只剩"未声明"一档是真实情况，先说清楚。 -->
    <p v-if="stageFilterHint" class="filter-hint">{{ text.stageUndeclaredHint }}</p>

    <!-- Skills 列表 -->
    <a-spin :loading="loading">
      <div v-if="skills.length === 0" class="empty-state">
        <icon-apps style="font-size: 48px; color: #c0c4cc" />
        <p>{{ text.emptyState }}</p>
      </div>

      <div v-else-if="filteredSkills.length === 0" class="empty-state">
        <icon-search style="font-size: 48px; color: #c0c4cc" />
        <p>{{ text.filterEmpty }}</p>
      </div>

      <div v-else class="skill-list">
        <div
          v-for="skill in filteredSkills"
          :key="skill.id"
          class="skill-card"
          :class="{ inactive: !skill.is_active }"
        >
          <div class="skill-header">
            <div class="skill-name">{{ skill.name }}</div>
            <a-switch
              v-model="skill.is_active"
              size="small"
              @change="(val) => handleToggle(skill, val as boolean)"
            />
          </div>
          <div class="skill-tags">
            <a-tag v-if="skill.source_type_label" size="small" :color="sourceTagColor(skill.source_type)">
              {{ skill.source_type_label }}
            </a-tag>
            <a-tag v-if="skill.stage_label" size="small" color="arcoblue">{{ skill.stage_label }}</a-tag>
            <a-tag v-else size="small" color="gray">{{ text.stageUndeclared }}</a-tag>
            <a-tag v-if="skill.version" size="small">{{ skill.version }}</a-tag>
          </div>
          <!-- 功能简介：卡片里最多两行，超出省略；悬浮看完整简介。 -->
          <div class="skill-summary">
            <span class="skill-summary__label">{{ text.summaryLabel }}</span>
            <a-tooltip :content="skill.description" position="top" :disabled="!isSummaryClipped(skill.description)">
              <div class="skill-description">{{ skill.description }}</div>
            </a-tooltip>
          </div>
          <div class="skill-footer">
            <span class="skill-meta">
              <icon-user /> {{ skill.creator_name }}
            </span>
            <span class="skill-meta">
              <icon-calendar /> {{ formatDate(skill.created_at) }}
            </span>
            <div class="skill-actions">
              <a-button type="text" size="mini" @click="handleViewContent(skill)">
                <icon-eye />
              </a-button>
              <a-popconfirm
                :content="text.deleteConfirm"
                @ok="handleDelete(skill)"
              >
                <a-button type="text" size="mini" status="danger">
                  <icon-delete />
                </a-button>
              </a-popconfirm>
            </div>
          </div>
        </div>
      </div>
    </a-spin>

    <!-- 上传弹窗 -->
    <a-modal
      v-model:visible="showUploadModal"
      :title="text.uploadSkill"
      :width="500"
      @ok="handleUpload"
      :ok-text="text.confirm"
      :cancel-text="text.cancel"
      :confirm-loading="uploading"
    >
      <div class="upload-container">
        <input
          ref="fileInputRef"
          type="file"
          accept=".zip"
          style="display: none"
          @change="handleFileChange"
        />
        <div class="upload-area" @click="triggerFileInput">
          <icon-upload style="font-size: 32px" />
          <div class="upload-text">
            <div>{{ text.selectZipFile }}</div>
            <div class="upload-tip">{{ text.uploadTip }}</div>
          </div>
        </div>
        <div v-if="selectedFile" class="selected-file">
          <icon-file />
          <span>{{ selectedFile.name }}</span>
          <a-button type="text" size="mini" @click="selectedFile = null">
            <icon-close />
          </a-button>
        </div>
      </div>
    </a-modal>

    <!-- 内容查看弹窗 -->
    <a-modal
      v-model:visible="showContentModal"
      :title="currentSkillContent?.name || text.skillContent"
      :width="700"
      :footer="false"
    >
      <div v-if="currentSkillContent" class="skill-content-view">
        <div class="content-description">{{ currentSkillContent.description }}</div>
        <a-divider />
        <pre class="content-body">{{ currentSkillContent.content }}</pre>
      </div>
    </a-modal>

    <!-- Git 导入弹窗 -->
    <a-modal
      v-model:visible="showGitImportModal"
      :title="text.importSkillFromGit"
      :width="500"
      @ok="handleGitImport"
      :ok-text="text.confirm"
      :cancel-text="text.cancel"
      :confirm-loading="importing"
    >
      <a-form :model="{ gitUrl, gitBranch }" layout="vertical">
        <a-form-item :label="text.gitRepoUrl" required>
          <a-input
            v-model="gitUrl"
            :placeholder="text.gitRepoUrlPlaceholder"
          />
          <template #extra>
            <span class="form-tip">{{ text.gitRepoTip }}</span>
          </template>
        </a-form-item>
        <a-form-item :label="text.branch">
          <a-input
            v-model="gitBranch"
            :placeholder="text.branchPlaceholder"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- Skill 商店弹窗 -->
    <SkillStoreModal
      v-model:visible="showStoreModal"
      :project-id="props.projectId"
      :installed-skills="skills"
      @skills-changed="fetchSkills"
    />

    <SkillApiKeyConfirmModal
      :visible="showApiKeyConfirm"
      :skill-names="pendingApiKeySkillNames"
      @confirmed="onApiKeyConfirmed"
      @update:visible="onApiKeyModalVisible"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import { SkillService } from '../services/skillService'
import SkillStoreModal from './SkillStoreModal.vue'
import SkillApiKeyConfirmModal from './SkillApiKeyConfirmModal.vue'
import type { SkillListItem } from '../types'
import { useAppI18n } from '@/composables/useAppI18n'
import { zipNameSuggestsInternalSkill } from '../utils/internalSkills'

const props = defineProps<{
  projectId: number
}>()
const { isEnglish } = useAppI18n()
const text = computed(() => (
  isEnglish.value
    ? {
        skillStore: 'Skill Store',
        importFromGit: 'Import from Git',
        uploadSkill: 'Upload Skill',
        emptyState: 'No Skills yet. Click the button above to upload',
        deleteConfirm: 'Delete this Skill?',
        confirm: 'Confirm',
        cancel: 'Cancel',
        selectZipFile: 'Click to select a zip file',
        uploadTip: 'The zip can contain one or more Skills',
        skillContent: 'Skill Content',
        importSkillFromGit: 'Import Skill from Git',
        gitRepoUrl: 'Git repository URL',
        gitRepoUrlPlaceholder: 'https://github.com/username/repo',
        gitRepoTip: 'Any publicly accessible Git repository is supported',
        branch: 'Branch',
        branchPlaceholder: 'main (default)',
        fetchSkillsFailed: 'Failed to fetch Skills',
        selectFile: 'Select a file',
        uploadSuccess: (count: number, names: string) => `Uploaded ${count} Skill(s) successfully: ${names}`,
        uploadFailed: 'Upload failed',
        enabled: 'Enabled',
        disabled: 'Disabled',
        operationFailed: 'Operation failed',
        deleteSuccess: 'Deleted successfully',
        deleteFailed: 'Delete failed',
        fetchContentFailed: 'Failed to fetch content',
        gitRepoRequired: 'Enter a Git repository URL',
        importSuccess: (count: number, names: string) => `Imported ${count} Skill(s) successfully: ${names}`,
        importFailed: 'Import failed',
      }
    : {
        skillStore: 'Skill 商店',
        summaryLabel: '功能简介',
        filterLabel: '类型筛选',
        filterSourceAll: '全部来源',
        filterStageAll: '全部阶段',
        filterSummary: (shown: number, total: number) => `显示 ${shown} / ${total}`,
        clearFilters: '清空',
        filterEmpty: '没有符合当前筛选条件的 Skill',
        stageUndeclared: '阶段未声明',
        stageUndeclaredHint: '本项目的 Skill 都还没有在 manifest 里声明能力阶段，阶段筛选目前只有「阶段未声明」一档；补齐需要在 Skill 进化工坊按阶段绑定。',
        importFromGit: '从 Git 导入',
        uploadSkill: '上传 Skill',
        emptyState: '暂无 Skills，点击上方按钮上传',
        deleteConfirm: '确定删除此 Skill 吗？',
        confirm: '确认',
        cancel: '取消',
        selectZipFile: '点击选择 zip 文件',
        uploadTip: '支持 zip 内包含一个或多个 Skill',
        skillContent: 'Skill 内容',
        importSkillFromGit: '从 Git 导入 Skill',
        gitRepoUrl: 'Git 仓库地址',
        gitRepoUrlPlaceholder: 'https://github.com/username/repo',
        gitRepoTip: '支持任何可公开访问的 Git 仓库',
        branch: '分支',
        branchPlaceholder: 'main（默认）',
        fetchSkillsFailed: '获取 Skills 失败',
        selectFile: '请选择文件',
        uploadSuccess: (count: number, names: string) => `成功上传 ${count} 个 Skill: ${names}`,
        uploadFailed: '上传失败',
        enabled: '已启用',
        disabled: '已禁用',
        operationFailed: '操作失败',
        deleteSuccess: '删除成功',
        deleteFailed: '删除失败',
        fetchContentFailed: '获取内容失败',
        gitRepoRequired: '请输入 Git 仓库地址',
        importSuccess: (count: number, names: string) => `成功导入 ${count} 个 Skill: ${names}`,
        importFailed: '导入失败',
      }
))

const loading = ref(false)
const uploading = ref(false)
const skills = ref<SkillListItem[]>([])
const showUploadModal = ref(false)
const showContentModal = ref(false)
const selectedFile = ref<File | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const currentSkillContent = ref<{ name: string; description: string; content: string } | null>(null)
const showGitImportModal = ref(false)
const showStoreModal = ref(false)
const gitUrl = ref('')
const gitBranch = ref('')
const importing = ref(false)
const showApiKeyConfirm = ref(false)
const pendingApiKeySkillNames = ref<string[]>([])
const pendingApiKeyAction = ref<'upload' | 'git' | null>(null)

// ---------------- 类型筛选（来源 / 能力阶段） ----------------
// 纯前端过滤：列表拿到的是本项目全量（量级十几条），不必为此加后端参数。
// 两个维度都来自展示版本的元数据：来源（source_type）100% 有值；阶段（manifest.stage）
// 存量包大多没声明，会落进「阶段未声明」一档——那是真实情况，不掩盖。
const sourceFilter = ref<string | undefined>(undefined)
const stageFilter = ref<string | undefined>(undefined)

/** 「阶段未声明」的哨兵值：筛选器要能单独看"没声明阶段的那批"才有用。 */
const UNDECLARED_STAGE = '__undeclared__'

const hasFilter = computed(() => Boolean(sourceFilter.value || stageFilter.value))

const sourceOptions = computed(() => {
  const counter = new Map<string, { count: number; label: string }>()
  for (const skill of skills.value) {
    if (!skill.source_type) continue
    const hit = counter.get(skill.source_type)
    if (hit) { hit.count += 1 } else {
      counter.set(skill.source_type, { count: 1, label: skill.source_type_label || skill.source_type })
    }
  }
  return [...counter.entries()]
    .map(([value, meta]) => ({ value, ...meta }))
    .sort((a, b) => b.count - a.count)
})

const stageOptions = computed(() => {
  const counter = new Map<string, { count: number; label: string }>()
  for (const skill of skills.value) {
    const key = skill.stage || UNDECLARED_STAGE
    const label = skill.stage
      ? (skill.stage_label || skill.stage)
      : text.value.stageUndeclared
    const hit = counter.get(key)
    if (hit) { hit.count += 1 } else { counter.set(key, { count: 1, label }) }
  }
  return [...counter.entries()]
    .map(([value, meta]) => ({ value, ...meta }))
    // 「未声明」永远排最后：它是"缺数据"的兜底，不该抢在真实阶段前面。
    .sort((a, b) => {
      const aLast = a.value === UNDECLARED_STAGE ? 1 : 0
      const bLast = b.value === UNDECLARED_STAGE ? 1 : 0
      return aLast - bLast || b.count - a.count
    })
})

const filteredSkills = computed(() => skills.value.filter((skill) => {
  if (sourceFilter.value && skill.source_type !== sourceFilter.value) return false
  if (stageFilter.value && (skill.stage || UNDECLARED_STAGE) !== stageFilter.value) return false
  return true
}))

/** 只有当"所有 Skill 都没声明阶段"时才提示，正常项目里不多一句废话。 */
const stageFilterHint = computed(
  () => skills.value.length > 0 && skills.value.every((skill) => !skill.stage),
)

const clearFilters = () => {
  sourceFilter.value = undefined
  stageFilter.value = undefined
}

/** 简介是否会被两行截断，决定要不要挂悬浮全文。按字符数粗判，省去逐卡量高。 */
const isSummaryClipped = (description: string) => (description || '').length > 34

/** 来源标签配色：四类来源一眼可辨，而不是全灰。 */
const sourceTagColor = (sourceType: string) => {
  switch (sourceType) {
    case 'upload': return 'arcoblue'
    case 'git': return 'purple'
    case 'store': return 'green'
    case 'evolution': return 'orangered'
    case 'migration': return 'gray'
    default: return 'gray'
  }
}

const fetchSkills = async () => {
  loading.value = true
  try {
    skills.value = await SkillService.getSkills(props.projectId)
  } catch (e: any) {
    Message.error(e.message || text.value.fetchSkillsFailed)
  } finally {
    loading.value = false
  }
}

const triggerFileInput = () => {
  fileInputRef.value?.click()
}

const handleFileChange = (e: Event) => {
  const target = e.target as HTMLInputElement
  if (target.files && target.files[0]) {
    selectedFile.value = target.files[0]
  }
}

const doUpload = async (apiKey?: string) => {
  if (!selectedFile.value) {
    Message.warning(text.value.selectFile)
    return
  }

  uploading.value = true
  try {
    const skills = await SkillService.uploadSkill(props.projectId, selectedFile.value, apiKey)
    const count = skills.length
    const names = skills.map(s => s.name).join(', ')
    Message.success(text.value.uploadSuccess(count, names))
    showUploadModal.value = false
    selectedFile.value = null
    await fetchSkills()
  } catch (e: any) {
    Message.error(e.message || text.value.uploadFailed)
  } finally {
    uploading.value = false
  }
}

const handleUpload = async () => {
  if (!selectedFile.value) {
    Message.warning(text.value.selectFile)
    return
  }
  // 内部 Skill 包（文件名粗判）安装前必须确认 API Key；后端会按 SKILL.md name 再校验
  if (zipNameSuggestsInternalSkill(selectedFile.value.name)) {
    pendingApiKeyAction.value = 'upload'
    pendingApiKeySkillNames.value = [selectedFile.value.name]
    showApiKeyConfirm.value = true
    return
  }
  await doUpload()
}

const handleToggle = async (skill: SkillListItem, isActive: boolean) => {
  try {
    await SkillService.toggleSkill(props.projectId, skill.id, isActive)
    Message.success(isActive ? text.value.enabled : text.value.disabled)
  } catch (e: any) {
    skill.is_active = !isActive
    Message.error(e.message || text.value.operationFailed)
  }
}

const handleDelete = async (skill: SkillListItem) => {
  try {
    await SkillService.deleteSkill(props.projectId, skill.id)
    Message.success(text.value.deleteSuccess)
    await fetchSkills()
  } catch (e: any) {
    Message.error(e.message || text.value.deleteFailed)
  }
}

const handleViewContent = async (skill: SkillListItem) => {
  try {
    currentSkillContent.value = await SkillService.getSkillContent(props.projectId, skill.id)
    showContentModal.value = true
  } catch (e: any) {
    Message.error(e.message || text.value.fetchContentFailed)
  }
}

const formatDate = (dateStr: string) => {
  return new Date(dateStr).toLocaleDateString(isEnglish.value ? 'en-US' : 'zh-CN')
}

const doGitImport = async (apiKey?: string) => {
  if (!gitUrl.value.trim()) {
    Message.warning(text.value.gitRepoRequired)
    return
  }

  importing.value = true
  try {
    const skills = await SkillService.importFromGit(
      props.projectId,
      gitUrl.value.trim(),
      gitBranch.value.trim() || undefined,
      apiKey
    )
    const count = skills.length
    const names = skills.map(s => s.name).join(', ')
    Message.success(text.value.importSuccess(count, names))
    showGitImportModal.value = false
    gitUrl.value = ''
    gitBranch.value = ''
    await fetchSkills()
  } catch (e: any) {
    Message.error(e.message || text.value.importFailed)
  } finally {
    importing.value = false
  }
}

const handleGitImport = async () => {
  if (!gitUrl.value.trim()) {
    Message.warning(text.value.gitRepoRequired)
    return
  }
  // Git 导入可能包含内部 Skill：统一先确认 Key，后端对非内部包忽略该字段
  pendingApiKeyAction.value = 'git'
  pendingApiKeySkillNames.value = isEnglish.value
    ? ['Git import (may include internal Skills)']
    : ['Git 导入（可能含内部 Skill）']
  showApiKeyConfirm.value = true
}

const onApiKeyConfirmed = async (payload: { apiKey: string; keyId: number; keyName: string }) => {
  const action = pendingApiKeyAction.value
  // 先开始执行再清 pending，避免取消清理与确认竞态
  if (action === 'upload') {
    pendingApiKeyAction.value = null
    pendingApiKeySkillNames.value = []
    await doUpload(payload.apiKey)
  } else if (action === 'git') {
    pendingApiKeyAction.value = null
    pendingApiKeySkillNames.value = []
    await doGitImport(payload.apiKey)
  } else {
    pendingApiKeyAction.value = null
    pendingApiKeySkillNames.value = []
  }
}

const onApiKeyModalVisible = (v: boolean) => {
  showApiKeyConfirm.value = v
  // 仅在用户取消/关闭且当前没有进行中的上传/导入时清理挂起状态
  if (!v && !uploading.value && !importing.value) {
    pendingApiKeyAction.value = null
    pendingApiKeySkillNames.value = []
  }
}

onMounted(() => {
  fetchSkills()
})
</script>

<style scoped>
.skill-manager {
  padding: 16px;
  overflow-x: hidden;
}

.header-bar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-fill-1);
}

.filter-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-2);
}

.filter-select {
  width: 168px;
}

.filter-summary {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-text-3);
}

.filter-hint {
  margin: 8px 0 0;
  padding: 6px 12px;
  border-left: 3px solid #ff9a2e;
  border-radius: 6px;
  background: var(--color-warning-light-1, #fff7e8);
  color: var(--color-text-2);
  font-size: 12px;
  line-height: 1.6;
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.skill-summary {
  margin-top: 12px;
  margin-bottom: 12px;
}

.skill-summary__label {
  display: block;
  margin-bottom: 2px;
  font-size: 11px;
  letter-spacing: 0.4px;
  color: var(--color-text-3);
}

.skill-summary .skill-description {
  margin-bottom: 0;
  cursor: default;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 0;
  color: #909399;
}

.skill-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 16px;
}

.skill-card {
  background: var(--color-bg-2);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 16px;
  transition: all 0.2s;
  overflow: hidden;
  min-width: 0;
}

.skill-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.skill-card.inactive {
  opacity: 0.6;
}

.skill-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  gap: 8px;
}

.skill-name {
  font-weight: 600;
  font-size: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.skill-description {
  color: var(--color-text-2);
  font-size: 13px;
  margin-bottom: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.skill-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  font-size: 12px;
  color: var(--color-text-3);
}

.skill-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.skill-actions {
  margin-left: auto;
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.upload-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.upload-area {
  border: 2px dashed var(--color-border);
  border-radius: 8px;
  padding: 32px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}

.upload-area:hover {
  border-color: var(--color-primary);
  background: var(--color-fill-1);
}

.upload-text {
  margin-top: 8px;
}

.upload-tip {
  color: var(--color-text-3);
  font-size: 12px;
  margin-top: 4px;
}

.selected-file {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--color-fill-2);
  border-radius: 4px;
}

.skill-content-view {
  max-height: 500px;
  overflow-y: auto;
}

.content-description {
  color: var(--color-text-2);
  font-size: 14px;
}

.content-body {
  background: var(--color-fill-2);
  padding: 16px;
  border-radius: 4px;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
}
.form-tip {
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
