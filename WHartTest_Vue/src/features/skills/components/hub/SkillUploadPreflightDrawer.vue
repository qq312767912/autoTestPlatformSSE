<template>
  <a-drawer
    :visible="visible"
    :width="560"
    :footer="false"
    unmount-on-close
    @update:visible="(value: boolean) => emit('update:visible', value)"
  >
    <template #title>上传候选（两阶段：先预检，再落候选）</template>

    <div class="upload">
      <a-steps :current="step" size="small" class="upload__steps">
        <a-step>选择并预检</a-step>
        <a-step>保存为候选版本</a-step>
      </a-steps>

      <div class="upload__tip">
        预检只把包解压到临时暂存区校验，**不写入正式版本库**；候选一律落在草稿态，
        需经静态校验 → 门禁评测 → 负责人审批才能生效。
      </div>

      <!-- 第一步：选文件 + 预检 -->
      <template v-if="step === 0">
        <input
          ref="fileInputRef"
          type="file"
          accept=".zip"
          style="display: none"
          @change="onFileChange"
        />
        <div class="upload__drop" @click="triggerFileInput">
          <icon-upload style="font-size: 28px" />
          <div>点击选择 Skill 包（.zip，≤20MB）</div>
        </div>

        <div v-if="file" class="upload__file">
          <icon-file />
          <span class="upload__file-name">{{ file.name }}</span>
          <span class="upload__file-size">{{ prettySize(file.size) }}</span>
          <a-button type="text" size="mini" @click="resetFile"><icon-close /></a-button>
        </div>

        <a-alert v-if="error" type="error" class="upload__alert">{{ error }}</a-alert>

        <!-- 预检报告 -->
        <div v-if="report" class="report" :class="report.ok ? 'report--ok' : 'report--bad'">
          <div class="report__head">
            <icon-check-circle v-if="report.ok" />
            <icon-exclamation-circle v-else />
            <span>{{ report.ok ? '预检通过，可以保存为候选版本' : '预检未通过，请修正后重新上传' }}</span>
          </div>
          <a-descriptions :column="1" size="small" bordered>
            <a-descriptions-item label="包名">{{ manifestName }}</a-descriptions-item>
            <a-descriptions-item label="声明阶段">{{ manifestStage }}</a-descriptions-item>
            <a-descriptions-item label="文件数">{{ report.files.length }}</a-descriptions-item>
            <a-descriptions-item label="包哈希">
              <code class="mono">{{ report.package_sha256 }}</code>
            </a-descriptions-item>
          </a-descriptions>

          <ul v-if="issues.length" class="report__issues">
            <li v-for="(issue, index) in issues" :key="index" :class="`report__issue--${issue.severity}`">
              <span class="report__code">{{ issue.code }}</span>
              <span>{{ issue.message }}</span>
              <span v-if="issue.path" class="mono report__path">{{ issue.path }}</span>
            </li>
          </ul>
        </div>
      </template>

      <!-- 第二步：变更说明 + 落候选 -->
      <template v-else>
        <div class="upload__file upload__file--static">
          <icon-file />
          <span class="upload__file-name">{{ file?.name }}</span>
          <span class="upload__file-size">预检通过</span>
        </div>

        <a-form :model="form" layout="vertical">
          <a-form-item label="变更原因（会展示给审批人）">
            <a-textarea v-model="form.change_reason" :rows="2" :max-length="500" show-word-limit />
          </a-form-item>
          <a-form-item label="预期收益">
            <a-textarea v-model="form.expected_benefit" :rows="2" :max-length="500" show-word-limit />
          </a-form-item>
          <a-form-item label="影响范围">
            <a-textarea v-model="form.impact_scope" :rows="2" :max-length="500" show-word-limit />
          </a-form-item>
        </a-form>

        <a-alert v-if="error" type="error" class="upload__alert">{{ error }}</a-alert>
      </template>
    </div>

    <div class="upload__footer">
      <a-space>
        <a-button v-if="step === 0" @click="emit('update:visible', false)">取消</a-button>
        <a-button v-else @click="step = 0">上一步</a-button>
        <a-button
          v-if="step === 0"
          type="primary"
          :loading="preflighting"
          :disabled="!file"
          @click="runPreflight"
        >
          开始预检
        </a-button>
        <a-button v-else type="primary" :loading="saving" @click="saveCandidate">
          保存为候选版本
        </a-button>
      </a-space>
    </div>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconCheckCircle,
  IconClose,
  IconExclamationCircle,
  IconFile,
  IconUpload,
} from '@arco-design/web-vue/es/icon'

import { SkillHubService, toErrorMessage } from '../../services/skillHubService'
import type { PreflightIssue, PreflightResult } from '../../types/hub'
import { stageLabel } from '../../utils/stages'

const props = defineProps<{
  visible: boolean
  projectId: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'created', payload: { skillId: number; versionId: string; version: string }): void
}>()

const fileInputRef = ref<HTMLInputElement | null>(null)
const file = ref<File | null>(null)
const report = ref<PreflightResult | null>(null)
const step = ref(0)
const preflighting = ref(false)
const saving = ref(false)
const error = ref('')
const form = ref({ change_reason: '', expected_benefit: '', impact_scope: '' })

// 关闭时清空：残留的上一次预检令牌会在 30 分钟后过期，留着只会误导用户。
watch(
  () => props.visible,
  (visible) => {
    if (!visible) reset()
  },
)

function reset() {
  file.value = null
  report.value = null
  step.value = 0
  error.value = ''
  form.value = { change_reason: '', expected_benefit: '', impact_scope: '' }
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function resetFile() {
  file.value = null
  report.value = null
  error.value = ''
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const picked = target.files?.[0]
  if (!picked) return
  if (!picked.name.toLowerCase().endsWith('.zip')) {
    error.value = '只支持 .zip 包'
    return
  }
  file.value = picked
  report.value = null
  error.value = ''
}

function prettySize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

const manifestName = computed(() => String(report.value?.manifest?.name || '—'))
const manifestStage = computed(() => stageLabel(String(report.value?.manifest?.stage || '')))

const issues = computed<PreflightIssue[]>(() => {
  if (!report.value) return []
  return [...report.value.errors, ...report.value.warnings]
})

async function runPreflight() {
  if (!file.value) return
  preflighting.value = true
  error.value = ''
  try {
    const result = await SkillHubService.preflight(props.projectId, file.value)
    report.value = result
    if (result.ok) {
      // 预检通过：进入第二步填写变更说明。令牌就在报告里，第二步直接用它。
      step.value = 1
    } else {
      error.value = '包校验未通过，请按下方问题逐条修正后重新上传'
    }
  } catch (err) {
    error.value = toErrorMessage(err, '预检失败')
  } finally {
    preflighting.value = false
  }
}

async function saveCandidate() {
  const token = report.value?.token
  if (!token) {
    error.value = '预检令牌缺失，请重新预检'
    step.value = 0
    return
  }
  saving.value = true
  error.value = ''
  try {
    const created = await SkillHubService.createCandidate(props.projectId, {
      token,
      change_reason: form.value.change_reason,
      expected_benefit: form.value.expected_benefit,
      impact_scope: form.value.impact_scope,
    })
    Message.success(`候选版本 ${created.version.version} 已创建（草稿）`)
    emit('created', {
      skillId: created.skill.id,
      versionId: created.version.id,
      version: created.version.version,
    })
    emit('update:visible', false)
  } catch (err) {
    error.value = toErrorMessage(err, '保存候选版本失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.upload {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.upload__steps {
  margin-bottom: 4px;
}

.upload__tip {
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--color-fill-1);
  font-size: 12px;
  color: var(--color-text-3);
  line-height: 1.6;
}

.upload__drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px;
  border: 1px dashed var(--color-border-2);
  border-radius: 6px;
  cursor: pointer;
  color: var(--color-text-3);
  font-size: 13px;
  transition: border-color 0.15s, background-color 0.15s;
}

.upload__drop:hover {
  border-color: #1677ff;
  background: rgba(22, 119, 255, 0.04);
}

.upload__file {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--color-fill-1);
  font-size: 12px;
}

.upload__file--static {
  background: rgba(0, 180, 42, 0.08);
}

.upload__file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #1f2937;
}

.upload__file-size {
  flex: none;
  color: var(--color-text-3);
}

.upload__alert {
  margin-top: 4px;
}

.report {
  border-radius: 6px;
  padding: 10px 12px;
  border: 1px solid var(--color-border-1);
}

.report--ok {
  border-color: rgba(0, 180, 42, 0.4);
  background: rgba(0, 180, 42, 0.05);
}

.report--bad {
  border-color: rgba(245, 63, 63, 0.4);
  background: rgba(245, 63, 63, 0.05);
}

.report__head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.report__issues {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}

.report__issues li {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px dashed var(--color-border-1);
}

.report__issue--error {
  color: rgb(var(--red-6));
}

.report__issue--warning {
  color: #ff7d00;
}

.report__code {
  flex: none;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  color: var(--color-text-3);
}

.report__path {
  flex: none;
  color: var(--color-text-3);
}

.mono {
  font-family: var(--font-mono, 'SFMono-Regular', Menlo, Consolas, monospace);
  font-size: 11px;
  word-break: break-all;
}

.upload__footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
}
</style>
