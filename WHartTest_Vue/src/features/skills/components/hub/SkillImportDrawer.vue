<template>
  <a-drawer
    :visible="visible"
    :width="520"
    :footer="false"
    unmount-on-close
    @update:visible="(value: boolean) => emit('update:visible', value)"
  >
    <template #title>从 Git / Skill 商店导入</template>

    <div class="import">
      <a-alert type="normal" class="import__note">
        两种导入都会为包内容建立<strong>草稿候选版本</strong>，不会自动生效；
        仍需静态校验 → 门禁评测 → 负责人审批。
      </a-alert>

      <a-tabs v-model:active-key="tab" size="small">
        <a-form-item label="所属分类（展示阶段）" required>
          <!-- allow-create：找不到合适的阶段时直接敲一个名称新建（后端归一化成 custom:<名称>） -->
          <a-select v-model="category" allow-search allow-create placeholder="选择分类，或直接输入以新增">
            <a-option v-for="opt in categoryOptions" :key="opt.value" :value="opt.value">{{ stageOptionText(opt) }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="Skill 名称（平台生成）">
          <!-- 留空即沿用包内 name；多 Skill 包置只读——一个名字对应不了多条 Skill。 -->
          <a-input
            v-model="name"
            :max-length="40"
            :disabled="multiSkill"
            placeholder="生成后请人工确认；留空则沿用包内名称"
          />
          <template v-if="nameHint" #extra>
            <span class="import__hint">{{ nameHint }}</span>
          </template>
        </a-form-item>
        <a-form-item label="功能简介（平台生成）" required>
          <a-textarea v-model="description" :max-length="60" show-word-limit />
        </a-form-item>
        <a-tab-pane key="git" title="Git 仓库">
          <a-form :model="gitForm" layout="vertical">
            <a-form-item label="仓库地址（HTTPS）" required>
              <a-input v-model="gitForm.git_url" placeholder="https://example.com/team/skills.git" />
            </a-form-item>
            <a-form-item label="分支">
              <a-input v-model="gitForm.branch" placeholder="main" />
            </a-form-item>
          </a-form>
        </a-tab-pane>

        <a-tab-pane key="store" title="Skill 商店（zip URL）">
          <a-form :model="storeForm" layout="vertical">
            <a-form-item label="zip 包地址（HTTPS）" required>
              <a-input v-model="storeForm.zip_url" placeholder="https://example.com/skills/foo-1.0.0.zip" />
            </a-form-item>
            <a-form-item label="SHA256 校验和（可选）">
              <a-input v-model="storeForm.sha256" placeholder="64 位小写 16 进制" />
            </a-form-item>
          </a-form>
        </a-tab-pane>
      </a-tabs>

      <a-alert v-if="error" type="error" class="import__alert">{{ error }}</a-alert>

      <div v-if="imported.length" class="import__result">
        <div class="import__result-title">已导入 {{ imported.length }} 个 Skill（草稿候选）</div>
        <ul>
          <li v-for="item in imported" :key="item.id">
            <span class="import__name">{{ item.name }}</span>
            <span class="import__state">草稿</span>
          </li>
        </ul>
      </div>
    </div>

    <div class="import__footer">
      <a-space>
        <a-button @click="emit('update:visible', false)">关闭</a-button>
        <a-button type="primary" :loading="loading" @click="submit">开始导入</a-button>
      </a-space>
    </div>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'

import { SkillService } from '../../services/skillService'
import { toErrorMessage } from '../../services/skillHubService'
import type { Skill } from '../../types'
import { stageOptionText } from '../../utils/stages'

const props = defineProps<{
  visible: boolean
  projectId: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'imported'): void
}>()

const tab = ref('git')
const loading = ref(false)
const error = ref('')
const imported = ref<Skill[]>([])
const gitForm = ref({ git_url: '', branch: 'main' })
const storeForm = ref({ zip_url: '', sha256: '' })
const category = ref('')
const description = ref('')
/** 展示名称：平台生成、人工确认；留空则沿用包内 name。 */
const name = ref('')
/** 包内识别到的 Skill 数量：>1 时名称输入置只读。 */
const skillCount = ref(0)
const suggested = ref(false)
const categoryOptions = ref<Array<{ value: string; label: string; custom?: boolean }>>([])

const multiSkill = computed(() => skillCount.value > 1)
const nameHint = computed(() =>
  multiSkill.value
    ? `该包含 ${skillCount.value} 个 Skill，名称按各自 SKILL.md 保留，此处不做统一改名。`
    : '',
)

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      SkillService.getSkills(props.projectId).then(({ meta }) => { categoryOptions.value = meta.stage_options || [] })
    }
    if (!visible) {
      error.value = ''
      imported.value = []
      gitForm.value = { git_url: '', branch: 'main' }
      storeForm.value = { zip_url: '', sha256: '' }
      category.value = ''
      description.value = ''
      name.value = ''
      skillCount.value = 0
      suggested.value = false
    }
  },
)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (!suggested.value) {
      const suggestion = tab.value === 'git'
        ? await SkillService.suggestMetadata(props.projectId, { git_url: gitForm.value.git_url, branch: gitForm.value.branch })
        : await SkillService.suggestMetadata(props.projectId, { name: storeForm.value.zip_url, content: storeForm.value.zip_url })
      category.value = suggestion.category
      description.value = suggestion.description
      name.value = suggestion.name || ''
      skillCount.value = suggestion.skill_count || 0
      suggested.value = true
      Message.info(nameHint.value ? '已生成分类与简介；该仓库含多个 Skill，名称按各包保留' : '已生成建议，请确认后再次点击导入')
      return
    }
    if (!category.value || !description.value.trim()) { error.value = '请确认所属分类与功能简介'; return }
    if (tab.value === 'git') {
      if (!gitForm.value.git_url) {
        error.value = '请填写仓库地址'
        return
      }
      imported.value = await SkillService.importFromGit(
        props.projectId,
        gitForm.value.git_url,
        category.value,
        description.value.trim(),
        gitForm.value.branch || 'main',
        undefined,
        name.value.trim(),
      )
    } else {
      if (!storeForm.value.zip_url) {
        error.value = '请填写 zip 包地址'
        return
      }
      imported.value = await SkillService.importFromZipUrl(
        props.projectId,
        storeForm.value.zip_url,
        category.value,
        description.value.trim(),
        storeForm.value.sha256 || undefined,
        undefined,
        name.value.trim(),
      )
    }
    Message.success(`导入完成，共 ${imported.value.length} 个 Skill`)
    emit('imported')
  } catch (err) {
    error.value = toErrorMessage(err, '导入失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.import {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.import__note {
  font-size: 12px;
}

.import__hint {
  font-size: 12px;
  color: var(--color-text-3);
}

.import__alert {
  margin-top: 4px;
}

.import__result {
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--color-fill-1);
}

.import__result-title {
  font-size: 12px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 6px;
}

.import__result ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.import__result li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 12px;
}

.import__name {
  color: #1f2937;
}

.import__state {
  color: #ff7d00;
  font-size: 11px;
}

.import__footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
}
</style>
