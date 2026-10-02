<template>
  <div class="workbench">
    <!-- 未选 Skill -->
    <div v-if="!skill" class="workbench__state">
      <icon-apps style="font-size: 40px" />
      <p>请从左侧目录选择一个 Skill</p>
    </div>

    <template v-else>
      <header class="workbench__head">
        <div class="workbench__title">
          <h3>{{ skill.name }}</h3>
          <a-tag v-if="skill.stage" size="small" color="arcoblue">{{ stageLabel(skill.stage) }}</a-tag>
          <a-tag v-else size="small">未声明阶段</a-tag>
          <a-tag v-if="!skill.is_active" size="small" color="gray">已停用</a-tag>
        </div>
        <p class="workbench__desc">{{ skill.description || '无描述' }}</p>
        <a-space size="small">
          <a-button size="mini" :loading="loadingVersions" @click="emit('refresh')">刷新版本</a-button>
          <a-button
            size="mini"
            :loading="downloading"
            :disabled="!canDownload"
            :title="canDownload ? '' : downloadHint"
            @click="emit('download')"
          >
            下载脱敏包
          </a-button>
          <a-button
            size="mini"
            type="primary"
            :loading="validating"
            :disabled="!canValidate"
            :title="canValidate ? '' : validateHint"
            @click="emit('validate')"
          >
            提交静态校验
          </a-button>
        </a-space>
      </header>

      <!-- 下载凭据：下到的是哪一版、哈希是多少，必须留在页面上 -->
      <div v-if="lastExport" class="workbench__export">
        <span class="workbench__export-title">最近导出</span>
        <span>v{{ lastExport.version }}</span>
        <span class="mono">{{ lastExport.filename }}</span>
        <span v-if="lastExport.redactedFiles">脱敏 {{ lastExport.redactedFiles }} 个文件</span>
        <span>包哈希 <code class="mono">{{ shortHash(lastExport.packageSha256) }}</code></span>
        <span>导出哈希 <code class="mono">{{ shortHash(lastExport.exportSha256) }}</code></span>
      </div>

      <div v-if="!canValidate" class="workbench__blocked">{{ validateHint }}</div>

      <div class="workbench__body">
        <!-- 版本轨道 -->
        <section class="panel">
          <div class="panel__title">
            版本轨道
            <span class="panel__hint">选一个版本作为「候选」，再选一个作为「基线」做对比</span>
          </div>

          <div v-if="loadingVersions && !versions.length" class="panel__state"><a-spin dot /> 读取中…</div>
          <div v-else-if="versionsError" class="panel__state panel__state--error">
            {{ versionsError }}
            <a-button size="mini" @click="emit('refresh')">重试</a-button>
          </div>
          <div v-else-if="!versions.length" class="panel__state">
            该 Skill 还没有版本包，请先上传候选
          </div>
          <ul v-else class="timeline">
            <li
              v-for="item in versions"
              :key="item.id"
              class="timeline__row"
              :class="{ 'timeline__row--active': item.id === selectedVersionId }"
            >
              <span class="timeline__dot" :class="`timeline__dot--${releaseStateColor(item.state)}`" />
              <button type="button" class="timeline__main" @click="emit('selectVersion', item.id)">
                <span class="timeline__version">v{{ item.version }}</span>
                <a-tag size="small" :color="releaseStateColor(item.state)">
                  {{ stateLabel(item.state) }}
                </a-tag>
                <span class="timeline__meta">{{ sourceTypeLabel(item.source_type) }}</span>
                <span class="timeline__meta">{{ formatTime(item.created_at) }}</span>
              </button>
              <a-tooltip content="设为对比基线">
                <button
                  type="button"
                  class="timeline__base"
                  :class="{ 'timeline__base--on': item.id === baseVersionId }"
                  @click.stop="emit('update:baseVersionId', baseVersionId === item.id ? '' : item.id)"
                >
                  {{ item.id === baseVersionId ? '基线' : '设为基线' }}
                </button>
              </a-tooltip>
            </li>
          </ul>
        </section>

        <!-- 版本详情 -->
        <section class="panel">
          <div class="panel__title">
            版本详情
            <span v-if="versionDetail" class="panel__hint">
              候选 {{ versionDetail.version }}
              <template v-if="baseVersionId">· 基线 {{ baseVersionLabel }}</template>
            </span>
          </div>

          <div v-if="loadingDetail" class="panel__state"><a-spin dot /> 读取版本详情…</div>
          <div v-else-if="detailError" class="panel__state panel__state--error">{{ detailError }}</div>
          <div v-else-if="!versionDetail" class="panel__state">选择上方任一版本查看详情</div>
          <template v-else>
            <a-descriptions :column="2" size="small" bordered class="detail">
              <a-descriptions-item label="版本">{{ versionDetail.version }}</a-descriptions-item>
              <a-descriptions-item label="状态">
                <a-tag size="small" :color="releaseStateColor(versionDetail.state)">
                  {{ stateLabel(versionDetail.state) }}
                </a-tag>
              </a-descriptions-item>
              <a-descriptions-item label="阶段">{{ stageLabel(versionDetail.stage) }}</a-descriptions-item>
              <a-descriptions-item label="来源">{{ sourceTypeLabel(versionDetail.source_type) }}</a-descriptions-item>
              <a-descriptions-item label="入口">{{ versionDetail.entrypoint || '—' }}</a-descriptions-item>
              <a-descriptions-item label="创建人">{{ versionDetail.created_by_name || '—' }}</a-descriptions-item>
              <a-descriptions-item label="包哈希" :span="2">
                <code class="mono">{{ versionDetail.package_sha256 || '—' }}</code>
              </a-descriptions-item>
              <a-descriptions-item label="上一版本" :span="2">
                {{ previousVersionText }}
              </a-descriptions-item>
            </a-descriptions>

            <div v-if="manifestText" class="detail__sub">
              <div class="detail__sub-title">manifest</div>
              <pre class="mono pre">{{ manifestText }}</pre>
            </div>

            <div v-if="validationIssues.length" class="detail__sub">
              <div class="detail__sub-title">包校验报告</div>
              <ul class="issues">
                <li v-for="(issue, index) in validationIssues" :key="index" :class="`issues--${issue.severity}`">
                  <span class="issues__code">{{ issue.code }}</span>
                  <span class="issues__msg">{{ issue.message }}</span>
                  <span v-if="issue.path" class="issues__path mono">{{ issue.path }}</span>
                </li>
              </ul>
            </div>
          </template>
        </section>

        <!-- 文件差异 -->
        <section class="panel">
          <div class="panel__title">
            文件差异
            <span class="panel__hint">{{ diffSummary }}</span>
          </div>

          <div v-if="loadingDiff" class="panel__state"><a-spin dot /> 计算差异…</div>
          <div v-else-if="diffError" class="panel__state panel__state--error">{{ diffError }}</div>
          <div v-else-if="!versionDiff" class="panel__state">
            {{
              versionDetail
                ? '该版本没有可比对的基线（首个版本）。可在上方版本轨道点「设为基线」再对比。'
                : '先选择版本'
            }}
          </div>
          <template v-else>
            <div class="diff__counts">
              <span class="diff__count diff__count--add">新增 {{ versionDiff.files.added.length }}</span>
              <span class="diff__count diff__count--del">删除 {{ versionDiff.files.removed.length }}</span>
              <span class="diff__count diff__count--mod">修改 {{ versionDiff.files.modified.length }}</span>
              <span class="diff__count">未变 {{ versionDiff.files.unchanged_count }}</span>
            </div>

            <div v-if="!hasDiffFiles" class="panel__state">与基线内容一致，没有文件差异</div>
            <template v-else>
              <div class="diff__files">
                <span v-for="path in versionDiff.files.added" :key="`a-${path}`" class="diff__file diff__file--add mono">
                  + {{ path }}
                </span>
                <span v-for="path in versionDiff.files.removed" :key="`r-${path}`" class="diff__file diff__file--del mono">
                  − {{ path }}
                </span>
                <span v-for="item in versionDiff.files.modified" :key="`m-${item.path}`" class="diff__file diff__file--mod mono">
                  ~ {{ item.path }}
                </span>
              </div>

              <a-collapse v-if="textDiffs.length" :bordered="false" class="diff__text">
                <a-collapse-item v-for="item in textDiffs" :key="item.path" :header="item.path">
                  <pre v-if="item.unified" class="mono pre pre--diff">{{ item.unified }}</pre>
                  <div v-else class="panel__state">{{ item.skipped || '无文本差异' }}</div>
                </a-collapse-item>
              </a-collapse>
            </template>
          </template>
        </section>

        <!-- 能力绑定 -->
        <section class="panel">
          <div class="panel__title">
            能力绑定
            <span class="panel__hint">这一版被哪些单次能力 / 全链路测试使用</span>
          </div>

          <div v-if="loadingBindings" class="panel__state"><a-spin dot /> 读取绑定关系…</div>
          <div v-else-if="bindingsError" class="panel__state panel__state--error">{{ bindingsError }}</div>
          <div v-else-if="!bindings" class="panel__state">该版本尚未关联能力定义</div>
          <div v-else-if="!bindings.definitions.length" class="panel__state">
            没有能力定义引用该版本或其阶段（激活后不影响任何链路）
          </div>
          <ul v-else class="bindings">
            <li v-for="item in bindings.definitions" :key="item.id" class="bindings__row">
              <div class="bindings__main">
                <span class="bindings__name">{{ item.name }}</span>
                <a-tag size="small" :color="item.binding === 'active_release' ? 'green' : 'orange'">
                  {{ item.binding === 'active_release' ? '正在使用' : '激活后影响' }}
                </a-tag>
                <a-tag v-if="item.evaluation_mode === 'workflow'" size="small">全链路测试</a-tag>
                <a-tag v-else size="small">单次能力</a-tag>
              </div>
              <div class="bindings__stages">
                <span v-if="item.position" class="bindings__pos">第 {{ item.position }} 段</span>
                <span class="mono">{{ item.stages.join(' → ') }}</span>
              </div>
            </li>
          </ul>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { IconApps } from '@arco-design/web-vue/es/icon'

import type {
  ReleaseBindings,
  SkillCatalogEntry,
  SkillExportReceipt,
  SkillVersionDetail,
  SkillVersionSummary,
  VersionDiff,
} from '../../types/hub'
import { RELEASE_STATE_LABEL, releaseStateColor, type ReleaseState } from '../../types/hub'
import { sourceTypeLabel, stageLabel } from '../../utils/stages'

const props = defineProps<{
  skill: SkillCatalogEntry | null
  versions: SkillVersionSummary[]
  versionDetail: SkillVersionDetail | null
  versionDiff: VersionDiff | null
  bindings: ReleaseBindings | null
  selectedVersionId: string
  baseVersionId: string
  loadingVersions: boolean
  loadingDetail: boolean
  loadingDiff: boolean
  loadingBindings: boolean
  versionsError: string
  detailError: string
  diffError: string
  bindingsError: string
  canValidate: boolean
  validateHint: string
  validating: boolean
  canDownload: boolean
  downloadHint: string
  downloading: boolean
  lastExport: SkillExportReceipt | null
}>()

const emit = defineEmits<{
  (e: 'selectVersion', versionId: string): void
  (e: 'update:baseVersionId', versionId: string): void
  (e: 'refresh'): void
  (e: 'validate'): void
  (e: 'download'): void
}>()

function stateLabel(state: string): string {
  return RELEASE_STATE_LABEL[state as ReleaseState] || state
}

function formatTime(value?: string): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

function shortHash(value?: string): string {
  if (!value) return '—'
  return value.slice(0, 16)
}

const baseVersionLabel = computed(() => {
  const found = props.versions.find((item) => item.id === props.baseVersionId)
  return found ? found.version : ''
})

const previousVersionText = computed(() => {
  const detail = props.versionDetail
  if (!detail) return '—'
  if (!detail.previous_version) return '首个版本（无上一版本）'
  const found = props.versions.find((item) => item.id === detail.previous_version)
  return found ? `v${found.version}` : String(detail.previous_version)
})

const manifestText = computed(() => {
  const manifest = props.versionDetail?.manifest
  if (!manifest || !Object.keys(manifest).length) return ''
  return JSON.stringify(manifest, null, 2)
})

const validationIssues = computed(() => {
  const report = props.versionDetail?.validation_report as
    | { errors?: { code: string; message: string; severity: string; path: string }[]; warnings?: { code: string; message: string; severity: string; path: string }[] }
    | undefined
  if (!report) return []
  return [...(report.errors ?? []), ...(report.warnings ?? [])]
})

const diffSummary = computed(
  () => props.versionDiff?.summary || (props.baseVersionId ? '' : '无可比对基线'),
)

const hasDiffFiles = computed(() => {
  const diff = props.versionDiff
  if (!diff) return false
  return diff.files.added.length + diff.files.removed.length + diff.files.modified.length > 0
})

const textDiffs = computed(() =>
  (props.versionDiff?.text_diffs ?? []).slice(0, 20),
)
</script>

<style scoped>
.workbench {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.workbench__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 100%;
  color: var(--color-text-3);
  font-size: 13px;
}

.workbench__head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border-1);
}

.workbench__title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.workbench__title h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
}

.workbench__desc {
  flex: 1;
  min-width: 120px;
  margin: 0;
  font-size: 12px;
  color: var(--color-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench__blocked {
  padding: 6px 16px;
  font-size: 12px;
  color: #ff7d00;
  background: rgba(255, 125, 0, 0.08);
  border-bottom: 1px solid var(--color-border-1);
}

.workbench__export {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 6px 16px;
  font-size: 11px;
  color: var(--color-text-3);
  background: rgba(0, 180, 42, 0.06);
  border-bottom: 1px solid var(--color-border-1);
}

.workbench__export-title {
  font-weight: 600;
  color: #1f2937;
}

.workbench__export code {
  color: #1f2937;
}

.workbench__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel {
  border: 1px solid var(--color-border-1);
  border-radius: 6px;
  background: var(--color-bg-2);
}

.panel__title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
  border-bottom: 1px solid var(--color-border-1);
}

.panel__hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-3);
}

.panel__state {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px 12px;
  font-size: 12px;
  color: var(--color-text-3);
}

.panel__state--error {
  color: rgb(var(--red-6));
}

.timeline {
  margin: 0;
  padding: 6px;
  list-style: none;
}

.timeline__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
}

.timeline__row--active {
  background: rgba(22, 119, 255, 0.06);
}

.timeline__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
  background: var(--color-text-4);
}

.timeline__dot--green { background: #00b42a; }
.timeline__dot--orange { background: #ff7d00; }
.timeline__dot--red { background: rgb(var(--red-6)); }
.timeline__dot--gray { background: var(--color-text-4); }

.timeline__main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  font: inherit;
  color: inherit;
  text-align: left;
}

.timeline__version {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.timeline__meta {
  font-size: 11px;
  color: var(--color-text-3);
}

.timeline__base {
  flex: none;
  padding: 1px 8px;
  font-size: 11px;
  border: 1px solid var(--color-border-2);
  border-radius: 3px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-3);
}

.timeline__base--on {
  border-color: #1677ff;
  color: #1677ff;
  background: rgba(22, 119, 255, 0.08);
}

.detail {
  padding: 12px;
}

.detail__sub {
  padding: 0 12px 12px;
}

.detail__sub-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-3);
  margin-bottom: 6px;
}

.mono {
  font-family: var(--font-mono, 'SFMono-Regular', Menlo, Consolas, monospace);
  font-size: 11px;
  word-break: break-all;
}

.pre {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--color-fill-1);
  color: #1f2937;
  white-space: pre-wrap;
}

.pre--diff {
  max-height: 320px;
}

.issues {
  margin: 0;
  padding: 0;
  list-style: none;
}

.issues li {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px dashed var(--color-border-1);
}

.issues--error .issues__msg { color: rgb(var(--red-6)); }
.issues--warning .issues__msg { color: #ff7d00; }

.issues__code {
  flex: none;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  color: var(--color-text-3);
}

.issues__path {
  flex: none;
  color: var(--color-text-4);
}

.diff__counts {
  display: flex;
  gap: 12px;
  padding: 10px 12px 4px;
  font-size: 12px;
}

.diff__count { color: var(--color-text-3); }
.diff__count--add { color: #00b42a; }
.diff__count--del { color: rgb(var(--red-6)); }
.diff__count--mod { color: #ff7d00; }

.diff__files {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 12px 10px;
  max-height: 200px;
  overflow-y: auto;
}

.diff__file {
  font-size: 11px;
  padding: 1px 4px;
  border-radius: 3px;
}

.diff__file--add { color: #00b42a; background: rgba(0, 180, 42, 0.07); }
.diff__file--del { color: rgb(var(--red-6)); background: rgba(245, 63, 63, 0.07); }
.diff__file--mod { color: #ff7d00; background: rgba(255, 125, 0, 0.07); }

.diff__text {
  padding: 0 6px 8px;
}

.bindings {
  margin: 0;
  padding: 6px;
  list-style: none;
}

.bindings__row {
  padding: 8px 10px;
  border-radius: 4px;
}

.bindings__row:hover {
  background: var(--color-fill-1);
}

.bindings__main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.bindings__name {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.bindings__stages {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--color-text-3);
  flex-wrap: wrap;
}

.bindings__pos {
  color: #1677ff;
}
</style>
