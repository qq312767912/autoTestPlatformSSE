<template>
  <div class="doc-anonymization-page">
    <div class="page-header">
      <h2>
        <icon-safe />
        {{ tl('文档脱敏管理') }}
      </h2>
      <p class="page-desc">{{ tl('上传文档，配置脱敏规则，执行脱敏并下载脱敏后的文件。') }}</p>
    </div>

    <a-tabs v-model:active-key="activeTab" type="rounded">
      <!-- ========== Tab 1: 文档列表 ========== -->
      <a-tab-pane key="docs" :title="tl('文档列表')">
        <div class="tab-toolbar">
          <a-button type="primary" @click="triggerFileInput" :loading="uploading">
                <template #icon><icon-upload /></template>
                {{ tl('上传文档') }}
              </a-button>
              <input
                ref="fileInputRef"
                type="file"
                multiple
                accept=".txt,.md,.docx"
                style="display: none"
                @change="handleFileSelected"
              />
          <a-select
            v-model="docFilter.status"
            :placeholder="tl('脱敏状态')"
            style="width: 140px"
            allow-clear
            @change="loadDocs"
          >
            <a-option value="pending">{{ tl('待脱敏') }}</a-option>
            <a-option value="anonymized">{{ tl('已脱敏') }}</a-option>
            <a-option value="failed">{{ tl('脱敏失败') }}</a-option>
          </a-select>
          <a-input
            v-model="docFilter.search"
            :placeholder="tl('搜索文件名')"
            style="width: 200px"
            allow-clear
            @keyup.enter="loadDocs"
          >
            <template #prefix><icon-search /></template>
          </a-input>
          <a-button type="primary" @click="loadDocs">
            <template #icon><icon-search /></template>
            {{ tl('查询') }}
          </a-button>
        </div>

        <a-table
          :data="docs"
          :loading="docsLoading"
          :pagination="docsPagination"
          row-key="id"
          @page-change="handleDocPageChange"
        >
          <template #columns>
            <a-table-column :title="tl('文件名')" data-index="original_filename" :width="220">
              <template #cell="{ record }">
                <span class="doc-title">{{ record.original_filename }}</span>
              </template>
            </a-table-column>
            <a-table-column :title="tl('类型')" :width="70">
              <template #cell="{ record }">
                <a-tag size="small">{{ record.file_type?.replace('.', '').toUpperCase() }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column :title="tl('大小')" :width="80">
              <template #cell="{ record }">
                {{ formatFileSize(record.file_size) }}
              </template>
            </a-table-column>
            <a-table-column :title="tl('脱敏状态')" :width="100">
              <template #cell="{ record }">
                <a-tag v-if="record.status === 'anonymized'" color="green" size="small">
                  <icon-check /> {{ tl('已脱敏') }}
                </a-tag>
                <a-tag v-else-if="record.status === 'pending'" color="orange" size="small">
                  <icon-clock-circle /> {{ tl('待脱敏') }}
                </a-tag>
                <a-tag v-else-if="record.status === 'anonymizing'" color="blue" size="small">
                  <icon-loading /> {{ tl('脱敏中') }}
                </a-tag>
                <a-tag v-else color="red" size="small">
                  <icon-close /> {{ tl('脱敏失败') }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column :title="tl('脱敏时间')" :width="160">
              <template #cell="{ record }">
                {{ record.anonymized_at ? formatTime(record.anonymized_at) : '-' }}
              </template>
            </a-table-column>
            <a-table-column :title="tl('上传者')" :width="90" data-index="uploaded_by_name" />
            <a-table-column :title="tl('操作')" :width="320" fixed="right">
              <template #cell="{ record }">
                <a-space wrap>
                  <a-button size="small" type="outline" @click="handleConfigureRules(record)">
                    {{ tl('配置规则') }}
                  </a-button>
                  <a-button
                    size="small"
                    type="primary"
                    status="warning"
                    :loading="record._executing"
                    @click="handleExecute(record)"
                  >
                    {{ tl('执行脱敏') }}
                  </a-button>
                  <a-button
                    size="small"
                    :disabled="record.status !== 'anonymized'"
                    @click="handleViewReport(record)"
                  >
                    {{ tl('报告') }}
                  </a-button>
                  <a-button
                    size="small"
                    type="outline"
                    status="success"
                    :disabled="record.status !== 'anonymized'"
                    @click="handleDownload(record)"
                  >
                    <template #icon><icon-download /></template>
                  </a-button>
                  <a-popconfirm :content="tl('确定删除此文档？')" @ok="handleDelete(record)">
                    <a-button size="small" type="outline" status="danger">
                      {{ tl('删除') }}
                    </a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- ========== Tab 2: 脱敏规则 ========== -->
      <a-tab-pane key="rules" :title="tl('脱敏规则')">
        <div class="tab-toolbar">
          <a-button type="primary" @click="handleOpenRuleModal(null)">
            <template #icon><icon-plus /></template>
            {{ tl('新增规则') }}
          </a-button>
          <a-button type="outline" @click="handleInitDefaultRules" :loading="rulesLoading">
            <template #icon><icon-sync /></template>
            {{ tl('初始化默认规则') }}
          </a-button>
        </div>
        <a-table :data="presetRules" :loading="rulesLoading" row-key="id" :pagination="false">
          <template #columns>
            <a-table-column :title="tl('规则名称')" data-index="name" :width="140" />
            <a-table-column :title="tl('类型标识')" data-index="entity_type" :width="130">
              <template #cell="{ record }">
                <a-tag size="small" color="blue">{{ record.entity_type }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column :title="tl('显示名称')" data-index="entity_label" :width="100" />
            <a-table-column :title="tl('正则表达式')" data-index="regex" :width="200">
              <template #cell="{ record }">
                <code class="regex-code">{{ record.regex }}</code>
              </template>
            </a-table-column>
            <a-table-column :title="tl('置信度')" data-index="score" :width="80" />
            <a-table-column :title="tl('说明')" data-index="description" />
            <a-table-column :title="tl('状态')" :width="80">
              <template #cell="{ record }">
                <a-tag :color="record.is_active ? 'green' : 'gray'" size="small">
                  {{ record.is_active ? tl('已启用') : tl('已禁用') }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column :title="tl('操作')" :width="140" fixed="right">
              <template #cell="{ record }">
                <a-space>
                  <a-button size="small" type="text" @click="handleOpenRuleModal(record)">
                    {{ tl('编辑') }}
                  </a-button>
                  <a-popconfirm :content="tl('确定删除此规则？')" @ok="handleDeleteRule(record)">
                    <a-button size="small" type="text" status="danger">
                      {{ tl('删除') }}
                    </a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-tab-pane>

      <!-- ========== Tab 3: 脱敏模板 ========== -->
      <a-tab-pane key="templates" :title="tl('脱敏模板')">
        <div class="tab-toolbar">
          <a-button type="primary" @click="handleOpenTemplateModal(null)">
            <template #icon><icon-plus /></template>
            {{ tl('新增模板') }}
          </a-button>
          <a-button type="outline" @click="handleSeedDefaultTemplates" :loading="templatesLoading">
            <template #icon><icon-sync /></template>
            {{ tl('初始化脱敏模板') }}
          </a-button>
        </div>
        <a-table :data="templates" :loading="templatesLoading" row-key="id" :pagination="false">
          <template #columns>
            <a-table-column :title="tl('模板名称')" data-index="name" :width="160" />
            <a-table-column :title="tl('模板说明')" data-index="description" />
            <a-table-column :title="tl('预设类型')" :width="240">
              <template #cell="{ record }">
                <a-tag v-for="t in (record.enabled_preset_types || [])" :key="t" size="small" color="blue" style="margin: 2px">
                  {{ t }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column :title="tl('关键词数')" :width="90">
              <template #cell="{ record }">
                {{ (record.custom_keywords || []).length }}
              </template>
            </a-table-column>
            <a-table-column :title="tl('创建者')" data-index="created_by_name" :width="90" />
            <a-table-column :title="tl('更新时间')" :width="160">
              <template #cell="{ record }">{{ formatTime(record.updated_at) }}</template>
            </a-table-column>
            <a-table-column :title="tl('操作')" :width="140" fixed="right">
              <template #cell="{ record }">
                <a-space>
                  <a-button size="small" type="text" @click="handleOpenTemplateModal(record)">
                    {{ tl('编辑') }}
                  </a-button>
                  <a-popconfirm :content="tl('确定删除此模板？')" @ok="handleDeleteTemplate(record)">
                    <a-button size="small" type="text" status="danger">
                      {{ tl('删除') }}
                    </a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <!-- ========== 规则配置弹窗 ========== -->
    <a-modal
      v-model:visible="ruleModalVisible"
      :width="680"
      :ok-text="tl('保存')"
      @ok="handleSaveRules"
      :ok-loading="ruleSaving"
    >
      <template #title>
        <div class="modal-title-ellipsis" :title="ruleModalDoc?.original_filename">
          {{ tl('配置脱敏规则') }}
          <span v-if="ruleModalDoc" class="modal-title-filename">- {{ ruleModalDoc.original_filename }}</span>
        </div>
      </template>
      <div style="margin-top: 16px">
        <!-- 应用模板（顶部，支持多选） -->
        <div style="margin-bottom: 16px" v-if="templateList.length > 0">
          <h4 style="margin-bottom: 8px">{{ tl('应用模板') }}</h4>
          <div style="display: flex; gap: 8px; align-items: flex-start">
            <a-select
              v-model="selectedTemplateIds"
              :placeholder="tl('选择模板以快速填充规则')"
              allow-clear
              multiple
              style="flex: 1"
            >
              <a-option v-for="tpl in templateList" :key="tpl.id" :value="tpl.id">
                {{ tpl.name }}
              </a-option>
            </a-select>
            <a-button
              type="primary"
              size="small"
              :disabled="selectedTemplateIds.length === 0"
              @click="handleApplySelectedTemplates"
            >
              {{ tl('应用选中模板') }}
            </a-button>
          </div>
          <!-- 模板规则预览 -->
          <div v-if="selectedTemplateIds.length > 0" class="template-preview-panel">
            <div class="template-preview-title">{{ tl('模板匹配规则预览') }}</div>
            <div v-if="templatePreview.presetTypes.length > 0" style="margin-bottom: 8px">
              <span class="preview-label">{{ tl('预设敏感类型') }}：</span>
              <a-tag v-for="pt in templatePreview.presetTypes" :key="pt" size="small" color="blue" style="margin: 2px 4px 2px 0">
                {{ getPresetLabel(pt) }}
              </a-tag>
            </div>
            <div v-if="templatePreview.keywords.length > 0">
              <span class="preview-label">{{ tl('自定义关键词') }}：</span>
              <span v-for="(kw, idx) in templatePreview.keywords" :key="idx" class="preview-kw-item">
                <a-tag size="small" color="orangered" style="margin: 2px 4px 2px 0">
                  {{ kw.keyword }}<template v-if="kw.replacement"> → {{ kw.replacement }}</template>
                </a-tag>
              </span>
            </div>
            <div v-if="templatePreview.presetTypes.length === 0 && templatePreview.keywords.length === 0" class="preview-empty">
              {{ tl('所选模板暂无匹配规则') }}
            </div>
          </div>
        </div>

        <!-- 关键词匹配（放在上面） -->
        <h4 style="margin-bottom: 8px">{{ tl('自定义关键词匹配') }}</h4>
        <p class="hint-text">{{ tl('添加需要精确匹配的关键词，并指定替换词。留空则使用 <关键词> 作为默认替换词。') }}</p>

        <div class="keyword-list">
          <div v-for="(kw, idx) in keywordsList" :key="idx" class="keyword-row">
            <a-input
              v-model="kw.keyword"
              :placeholder="tl('关键词')"
              style="flex: 1"
              allow-clear
            />
            <a-input
              v-model="kw.replacement"
              :placeholder="tl('替换词（留空默认 <关键词>）')"
              style="flex: 1"
              allow-clear
            />
            <a-button type="text" status="danger" size="small" @click="removeKeyword(idx)">
              <template #icon><icon-delete /></template>
            </a-button>
          </div>
        </div>

        <a-button type="outline" size="small" @click="addKeyword" style="margin-bottom: 4px">
          <template #icon><icon-plus /></template>
          {{ tl('添加关键词') }}
        </a-button>

        <a-divider />

        <!-- 预设敏感信息类型（放在下面） -->
        <h4 style="margin-bottom: 12px">{{ tl('预设敏感信息类型') }}</h4>
        <div class="preset-checks">
          <a-checkbox-group v-model="ruleForm.enabled_preset_types" direction="vertical">
            <a-checkbox v-for="rule in allPresetRules" :key="rule.entity_type" :value="rule.entity_type">
              {{ rule.entity_label }}
              <span class="rule-desc">({{ rule.description || rule.entity_type }})</span>
            </a-checkbox>
          </a-checkbox-group>
        </div>
        <div style="margin-bottom: 8px">
          <a-space>
            <a-button size="small" type="text" @click="selectAllPresets">{{ tl('全选') }}</a-button>
            <a-button size="small" type="text" @click="ruleForm.enabled_preset_types = []">{{ tl('清空') }}</a-button>
          </a-space>
        </div>
      </div>
    </a-modal>

    <!-- ========== 脱敏报告弹窗 ========== -->
    <a-modal
      v-model:visible="reportModalVisible"
      :title="tl('脱敏报告')"
      :width="700"
      :footer="false"
    >
      <div v-if="currentReport" class="report-content">
        <a-alert type="info" :closable="false" style="margin-bottom: 16px">
          {{ tl('共检测到') }} <strong>{{ currentReport.total_count }}</strong> {{ tl('处敏感信息') }}
        </a-alert>

        <h4>{{ tl('按类型统计') }}</h4>
        <a-table :data="currentReport.details" :pagination="false" size="small" row-key="entity_type" style="margin-bottom: 16px">
          <template #columns>
            <a-table-column :title="tl('类型')" data-index="entity_label" :width="120" />
            <a-table-column :title="tl('数量')" data-index="count" :width="80" />
            <a-table-column :title="tl('示例')">
              <template #cell="{ record }">
                <a-tag v-for="(ex, i) in record.examples" :key="i" size="small" color="orangered" style="margin: 2px">
                  {{ ex }}
                </a-tag>
              </template>
            </a-table-column>
          </template>
        </a-table>

        <a-collapse>
          <a-collapse-item :header="tl('详细列表（前100条）')">
            <a-table :data="currentReport.all_entities" :pagination="{ pageSize: 10 }" size="small" row-key="start">
              <template #columns>
                <a-table-column :title="tl('类型')" data-index="entity_label" :width="100" />
                <a-table-column :title="tl('内容')" data-index="text_snippet">
                  <template #cell="{ record }">
                    <span class="pii-text">{{ record.text_snippet }}</span>
                  </template>
                </a-table-column>
                <a-table-column :title="tl('位置')" :width="80">
                  <template #cell="{ record }">{{ record.start }}-{{ record.end }}</template>
                </a-table-column>
              </template>
            </a-table>
          </a-collapse-item>
        </a-collapse>
      </div>
    </a-modal>

    <!-- ========== 规则编辑弹窗 ========== -->
    <a-modal
      v-model:visible="ruleEditModalVisible"
      :title="ruleEditForm.id ? tl('编辑规则') : tl('新增规则')"
      :width="560"
      :ok-text="tl('保存')"
      @ok="handleSaveRule"
      :ok-loading="ruleEditSaving"
    >
      <a-form :model="ruleEditForm" layout="vertical" style="margin-top: 16px">
        <a-form-item :label="tl('规则名称')" required>
          <a-input v-model="ruleEditForm.name" :placeholder="tl('如：chinese_phone')" />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item :label="tl('实体类型')" required>
              <a-input v-model="ruleEditForm.entity_type" :placeholder="tl('如：PHONE_NUMBER')" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item :label="tl('显示标签')" required>
              <a-input v-model="ruleEditForm.entity_label" :placeholder="tl('如：手机号')" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item :label="tl('正则表达式')" required>
          <a-textarea v-model="ruleEditForm.regex" :placeholder="tl('输入正则表达式')" :auto-size="{ minRows: 2, maxRows: 4 }" />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="8">
            <a-form-item :label="tl('置信度')">
              <a-input-number v-model="ruleEditForm.score" :min="0" :max="1" :step="0.05" :precision="2" style="width: 100%" />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item :label="tl('是否启用')">
              <a-switch v-model="ruleEditForm.is_active" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item :label="tl('规则说明')">
          <a-input v-model="ruleEditForm.description" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- ========== 模板编辑弹窗 ========== -->
    <a-modal
      v-model:visible="templateModalVisible"
      :title="templateEditForm.id ? tl('编辑模板') : tl('新增模板')"
      :width="680"
      :ok-text="tl('保存')"
      @ok="handleSaveTemplate"
      :ok-loading="templateSaving"
    >
      <div style="margin-top: 16px">
        <a-form :model="templateEditForm" layout="vertical">
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item :label="tl('模板名称')" required>
                <a-input v-model="templateEditForm.name" :placeholder="tl('输入模板名称')" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item :label="tl('模板说明')">
                <a-input v-model="templateEditForm.description" :placeholder="tl('输入模板说明')" />
              </a-form-item>
            </a-col>
          </a-row>
        </a-form>

        <!-- 关键词列表 -->
        <h4 style="margin-bottom: 8px">{{ tl('自定义关键词匹配') }}</h4>
        <div class="keyword-list">
          <div v-for="(kw, idx) in templateKeywordsList" :key="idx" class="keyword-row">
            <a-input v-model="kw.keyword" :placeholder="tl('关键词')" style="flex: 1" allow-clear />
            <a-input v-model="kw.replacement" :placeholder="tl('替换词（留空默认 <关键词>）')" style="flex: 1" allow-clear />
            <a-button type="text" status="danger" size="small" @click="templateKeywordsList.splice(idx, 1)">
              <template #icon><icon-delete /></template>
            </a-button>
          </div>
        </div>
        <a-button type="outline" size="small" @click="templateKeywordsList.push({ keyword: '', replacement: '' })" style="margin-bottom: 12px">
          <template #icon><icon-plus /></template>
          {{ tl('添加关键词') }}
        </a-button>

        <a-divider />

        <!-- 预设敏感信息类型 -->
        <h4 style="margin-bottom: 12px">{{ tl('预设敏感信息类型') }}</h4>
        <div class="preset-checks">
          <a-checkbox-group v-model="templateEditForm.enabled_preset_types" direction="vertical">
            <a-checkbox v-for="rule in allPresetRules" :key="rule.entity_type" :value="rule.entity_type">
              {{ rule.entity_label }}
              <span class="rule-desc">({{ rule.description || rule.entity_type }})</span>
            </a-checkbox>
          </a-checkbox-group>
        </div>
        <div style="margin-bottom: 8px">
          <a-space>
            <a-button size="small" type="text" @click="templateEditForm.enabled_preset_types = allPresetRules.map(r => r.entity_type)">{{ tl('全选') }}</a-button>
            <a-button size="small" type="text" @click="templateEditForm.enabled_preset_types = []">{{ tl('清空') }}</a-button>
          </a-space>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue';
import { useAppI18n } from '@/composables/useAppI18n';
import { Message } from '@arco-design/web-vue';
import {
  IconSearch,
  IconSafe,
  IconCheck,
  IconClose,
  IconClockCircle,
  IconLoading,
  IconUpload,
  IconDownload,
  IconSync,
  IconPlus,
  IconDelete,
} from '@arco-design/web-vue/es/icon';
import {
  uploadDocuments,
    getAnonymizedDocuments,
  updateDocumentRules,
    executeAnonymization,
    downloadAnonymizedFile,
    deleteAnonymizedDocument,
    resetAnonymization,
    getPresetRules,
    seedDefaultRules,
    createRule,
    updateRule,
    deleteRule,
    getTemplates,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    applyTemplate,
    applyMultipleTemplates,
    seedDefaultTemplates,
    type AnonymizedDocumentItem,
    type AnonymizationReport,
    type PresetRuleItem,
    type KeywordItem,
    type AnonymizationTemplate,
} from '@/services/documentAnonymizationService';
import dayjs from 'dayjs';

const { tl } = useAppI18n();

// ============ 文档列表 ============
const docs = ref<(AnonymizedDocumentItem & { _executing?: boolean })[]>([]);
const docsLoading = ref(false);
const docsPagination = reactive({ current: 1, total: 0, pageSize: 20 });
const docFilter = reactive({ status: '', search: '' });

async function loadDocs() {
  docsLoading.value = true;
  try {
    const res = await getAnonymizedDocuments({
      page: docsPagination.current,
      page_size: docsPagination.pageSize,
      status: docFilter.status || undefined,
      search: docFilter.search || undefined,
    });
    const data = res.data as any;
    if (data?.results) {
      docs.value = data.results.map((d: any) => ({ ...d, _executing: false }));
      docsPagination.total = data.count || 0;
    } else if (Array.isArray(data)) {
      docs.value = data.map((d: any) => ({ ...d, _executing: false }));
      docsPagination.total = data.length;
    }
  } catch (e) {
    console.error('加载文档列表失败', e);
  } finally {
    docsLoading.value = false;
  }
}

function handleDocPageChange(page: number) {
  docsPagination.current = page;
  loadDocs();
}



// ============ 上传 ============
const fileInputRef = ref<HTMLInputElement | null>(null);
const uploading = ref(false);

function triggerFileInput() {
  fileInputRef.value?.click();
}

async function handleFileSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  const fileList = input.files;
  if (!fileList || fileList.length === 0) return;
  const files = Array.from(fileList);
  uploading.value = true;
  try {
    const res = await uploadDocuments(files);
    // request 函数不会抛异常，错误时返回 { success: false, error: '...' }
    if (res.success === false) {
      Message.error(res.error || res.message || tl('上传失败'));
      return;
    }
    const data = res.data as any;
    if (data?.created_count > 0) {
      Message.success(tl('上传成功，已添加 ') + data.created_count + tl(' 个文档'));
    } else if (!data?.created_count) {
      Message.warning(tl('未成功添加任何文档'));
    }
    if (data?.errors && data.errors.length > 0) {
      const errorMessages = data.errors.map((e: any) => e.error || '').join(', ');
      Message.warning(errorMessages);
    }
    await loadDocs();
  } catch (e: any) {
    Message.error(e?.message || e?.error || tl('上传失败'));
  } finally {
    uploading.value = false;
    // 清空 input 以便再次选择相同文件
    if (fileInputRef.value) fileInputRef.value.value = '';
  }
}

// ============ 执行脱敏 ============
async function handleExecute(record: AnonymizedDocumentItem & { _executing?: boolean }) {
  record._executing = true;
  try {
    const res = await executeAnonymization(record.id);
    const data = res.data as any;
    if (data?.status === 'anonymized' || data?.id) {
      Object.assign(record, data);
      Message.success(tl('脱敏完成'));
    } else {
      Message.error(data?.error || tl('脱敏失败'));
    }
  } catch (e: any) {
    Message.error(e?.response?.data?.error || tl('脱敏请求失败'));
  } finally {
    record._executing = false;
  }
} // ============ 下载报告 ============
const reportModalVisible = ref(false);
const currentReport = ref<AnonymizationReport | null>(null);

function handleViewReport(record: AnonymizedDocumentItem) {
  currentReport.value = record.anonymization_report;
  reportModalVisible.value = true;
};

// ============ 下载 ============
async function handleDownload(record: AnonymizedDocumentItem) {
  try {
    const result = await downloadAnonymizedFile(record.id);
    if (!result.blob || result.blob.size === 0) {
      Message.error(tl('下载的文件为空'));
      return;
    }
    const url = URL.createObjectURL(result.blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = result.fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e: any) {
    console.error('下载脱敏文件失败:', e);
    const detail = e?.error || e?.message || e?.response?.data?.error || '';
    Message.error(tl('下载失败') + (detail ? ': ' + detail : ''));
  }
}

// ============ 删除 ============
async function handleDelete(record: AnonymizedDocumentItem) {
  try {
    await deleteAnonymizedDocument(record.id);
    Message.success(tl('删除成功'));
    await loadDocs();
  } catch (e: any) {
    Message.error(tl('删除失败'));
  }
}

// ============ 规则配置弹窗 ============
const ruleModalVisible = ref(false);
const ruleModalDoc = ref<AnonymizedDocumentItem | null>(null);
const ruleForm = reactive({ enabled_preset_types: [] as string[] });
const keywordsList = ref<KeywordItem[]>([]);
const ruleSaving = ref(false);
const allPresetRules = ref<PresetRuleItem[]>([]);

function addKeyword() {
  keywordsList.value.push({ keyword: '', replacement: '' });
}

function removeKeyword(idx: number) {
  keywordsList.value.splice(idx, 1);
}

async function handleConfigureRules(record: AnonymizedDocumentItem) {
  ruleModalDoc.value = record;
  ruleForm.enabled_preset_types = [...(record.enabled_preset_types || [])];
  // 兼容旧格式 (string[]) 和新格式 ({keyword, replacement}[])
  const rawKeywords = record.custom_keywords || [];
  keywordsList.value = rawKeywords.map((item: any) => {
    if (typeof item === 'string') return { keyword: item, replacement: '' };
    return { keyword: item.keyword || '', replacement: item.replacement || '' };
  });
  // 重置模板选择
  selectedTemplateIds.value = [];
  // 加载所有可用预设规则和模板
  await loadAllPresetRules();
  await loadTemplateList();
  ruleModalVisible.value = true;
}

async function loadAllPresetRules() {
  try {
    const res = await getPresetRules();
    const data = res.data as any;
    if (data?.results) {
      allPresetRules.value = data.results.filter((r: any) => r.is_active);
    } else if (Array.isArray(data)) {
      allPresetRules.value = data.filter((r: any) => r.is_active);
    }
  } catch (e) {
    console.error('加载预设规则失败', e);
  }
}

function selectAllPresets() {
  ruleForm.enabled_preset_types = allPresetRules.value.map(r => r.entity_type);
}

async function handleSaveRules() {
  if (!ruleModalDoc.value) return;
  ruleSaving.value = true;
  try {
    // 过滤空关键词并转换为新格式
    const keywords: KeywordItem[] = keywordsList.value
      .filter(kw => kw.keyword.trim())
      .map(kw => ({
        keyword: kw.keyword.trim(),
        replacement: kw.replacement.trim(),
      }));
    await updateDocumentRules(ruleModalDoc.value.id, {
      enabled_preset_types: ruleForm.enabled_preset_types,
      custom_keywords: keywords,
    });
    Message.success(tl('规则已保存'));
    ruleModalVisible.value = false;
    await loadDocs();
  } catch (e: any) {
    Message.error(tl('保存失败'));
  } finally {
    ruleSaving.value = false;
  }
}

// ============ 预设规则 Tab ============
const presetRules = ref<PresetRuleItem[]>([]);
const rulesLoading = ref(false);

async function loadPresetRules() {
  rulesLoading.value = true;
  try {
    const res = await getPresetRules();
    const data = res.data as any;
    if (data?.results) {
      presetRules.value = data.results;
    } else if (Array.isArray(data)) {
      presetRules.value = data;
    }
  } catch (e) {
    console.error('加载预设规则失败', e);
  } finally {
    rulesLoading.value = false;
  }
}

async function handleInitDefaultRules() {
  rulesLoading.value = true;
  try {
    const res = await seedDefaultRules();
    const data = res.data as any;
    Message.success(data?.message || tl('默认规则初始化完成'));
    await loadPresetRules();
  } catch (e: any) {
    Message.error(tl('初始化失败'));
  } finally {
    rulesLoading.value = false;
  }
}

// ============ 规则 CRUD ============
const ruleEditModalVisible = ref(false);
const ruleEditSaving = ref(false);
const ruleEditForm = reactive({
  id: null as number | null,
  name: '',
  entity_type: '',
  entity_label: '',
  regex: '',
  score: 0.8,
  is_active: true,
  description: '',
});

function handleOpenRuleModal(record: PresetRuleItem | null) {
  if (record) {
    Object.assign(ruleEditForm, {
      id: record.id,
      name: record.name,
      entity_type: record.entity_type,
      entity_label: record.entity_label,
      regex: record.regex,
      score: record.score,
      is_active: record.is_active,
      description: record.description,
    });
  } else {
    Object.assign(ruleEditForm, {
      id: null,
      name: '',
      entity_type: '',
      entity_label: '',
      regex: '',
      score: 0.8,
      is_active: true,
      description: '',
    });
  }
  ruleEditModalVisible.value = true;
}

async function handleSaveRule() {
  if (!ruleEditForm.name || !ruleEditForm.entity_type || !ruleEditForm.entity_label || !ruleEditForm.regex) {
    Message.warning(tl('请填写必填项'));
    return;
  }
  ruleEditSaving.value = true;
  try {
    const payload = {
      name: ruleEditForm.name,
      entity_type: ruleEditForm.entity_type,
      entity_label: ruleEditForm.entity_label,
      regex: ruleEditForm.regex,
      score: ruleEditForm.score,
      is_active: ruleEditForm.is_active,
      description: ruleEditForm.description,
    };
    if (ruleEditForm.id) {
      await updateRule(ruleEditForm.id, payload);
      Message.success(tl('保存成功'));
    } else {
      await createRule(payload);
      Message.success(tl('新增规则成功'));
    }
    ruleEditModalVisible.value = false;
    await loadPresetRules();
  } catch (e: any) {
    Message.error(e?.error || e?.message || tl('保存失败'));
  } finally {
    ruleEditSaving.value = false;
  }
}

async function handleDeleteRule(record: PresetRuleItem) {
  try {
    await deleteRule(record.id);
    Message.success(tl('删除成功'));
    await loadPresetRules();
  } catch (e: any) {
    Message.error(tl('删除失败'));
  }
}

// ============ 模板 CRUD ============
const templates = ref<AnonymizationTemplate[]>([]);
const templatesLoading = ref(false);
const templateModalVisible = ref(false);
const templateSaving = ref(false);
const templateEditForm = reactive({
  id: null as number | null,
  name: '',
  description: '',
  enabled_preset_types: [] as string[],
});
const templateKeywordsList = ref<KeywordItem[]>([]);

async function loadTemplates() {
  templatesLoading.value = true;
  try {
    const res = await getTemplates();
    const data = res.data as any;
    if (data?.results) {
      templates.value = data.results;
    } else if (Array.isArray(data)) {
      templates.value = data;
    }
    // 同时更新模板下拉列表
    templateList.value = templates.value;
  } catch (e) {
    console.error('加载模板列表失败', e);
  } finally {
    templatesLoading.value = false;
  }
}

function handleOpenTemplateModal(record: AnonymizationTemplate | null) {
  if (record) {
    Object.assign(templateEditForm, {
      id: record.id,
      name: record.name,
      description: record.description,
      enabled_preset_types: [...(record.enabled_preset_types || [])],
    });
    templateKeywordsList.value = (record.custom_keywords || []).map((item: any) => ({
      keyword: item.keyword || '',
      replacement: item.replacement || '',
    }));
  } else {
    Object.assign(templateEditForm, {
      id: null,
      name: '',
      description: '',
      enabled_preset_types: [],
    });
    templateKeywordsList.value = [];
  }
  // 加载预设规则供勾选
  loadAllPresetRules();
  templateModalVisible.value = true;
}

async function handleSaveTemplate() {
  if (!templateEditForm.name) {
    Message.warning(tl('请输入模板名称'));
    return;
  }
  templateSaving.value = true;
  try {
    const keywords: KeywordItem[] = templateKeywordsList.value
      .filter(kw => kw.keyword.trim())
      .map(kw => ({ keyword: kw.keyword.trim(), replacement: kw.replacement.trim() }));
    const payload = {
      name: templateEditForm.name,
      description: templateEditForm.description,
      enabled_preset_types: templateEditForm.enabled_preset_types,
      custom_keywords: keywords,
    };
    if (templateEditForm.id) {
      await updateTemplate(templateEditForm.id, payload);
      Message.success(tl('保存成功'));
    } else {
      await createTemplate(payload);
      Message.success(tl('新增模板成功'));
    }
    templateModalVisible.value = false;
    await loadTemplates();
  } catch (e: any) {
    Message.error(e?.error || e?.message || tl('保存失败'));
  } finally {
    templateSaving.value = false;
  }
}

async function handleDeleteTemplate(record: AnonymizationTemplate) {
  try {
    await deleteTemplate(record.id);
    Message.success(tl('删除成功'));
    await loadTemplates();
  } catch (e: any) {
    Message.error(tl('删除失败'));
  }
}

async function handleSeedDefaultTemplates() {
  templatesLoading.value = true;
  try {
    const res = await seedDefaultTemplates();
    const data = res.data as any;
    Message.success(data?.message || tl('初始化脱敏模板完成'));
    await loadTemplates();
  } catch (e: any) {
    Message.error(e?.error || e?.message || tl('初始化失败'));
  } finally {
    templatesLoading.value = false;
  }
}

// ============ 应用模板到文档配置弹窗（支持多选） ============
const templateList = ref<AnonymizationTemplate[]>([]);
const selectedTemplateIds = ref<number[]>([]);

// 计算当前选中模板的合并规则预览
const templatePreview = computed(() => {
  const presetTypes = new Set<string>();
  const keywords: KeywordItem[] = [];
  const keywordSet = new Set<string>();

  for (const id of selectedTemplateIds.value) {
    const tpl = templateList.value.find(t => t.id === id);
    if (!tpl) continue;
    (tpl.enabled_preset_types || []).forEach(pt => presetTypes.add(pt));
    (tpl.custom_keywords || []).forEach((kw: any) => {
      const kwKey = typeof kw === 'string' ? kw : (kw.keyword || '');
      if (kwKey && !keywordSet.has(kwKey)) {
        keywordSet.add(kwKey);
        keywords.push({
          keyword: kwKey,
          replacement: typeof kw === 'string' ? '' : (kw.replacement || ''),
        });
      }
    });
  }
  return { presetTypes: Array.from(presetTypes), keywords };
});

// 获取预设类型的显示标签
function getPresetLabel(entityType: string): string {
  const rule = allPresetRules.value.find(r => r.entity_type === entityType);
  return rule ? rule.entity_label : entityType;
}

// 点击"应用选中模板"按钮
function handleApplySelectedTemplates() {
  const ids = selectedTemplateIds.value;
  if (!ids || ids.length === 0) return;
  ruleForm.enabled_preset_types = [...templatePreview.value.presetTypes];
  keywordsList.value = templatePreview.value.keywords.map(kw => ({ ...kw }));
  Message.success(tl('已应用') + ' ' + ids.length + ' ' + tl('个模板的规则'));
}

async function loadTemplateList() {
  try {
    const res = await getTemplates();
    const data = res.data as any;
    templateList.value = data?.results || (Array.isArray(data) ? data : []);
  } catch (e) {
    console.error('加载模板列表失败', e);
  }
}

// ============ 工具函数 ============
function formatTime(ts: string) {
  return dayjs(ts).format('YYYY-MM-DD HH:mm');
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

// ============ 初始化 ============
const activeTab = ref('docs');

onMounted(() => {
  loadDocs();
  loadPresetRules();
  loadTemplates();
});
</script>

<style scoped>
.doc-anonymization-page {
  padding: 20px 24px;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px 0;
  font-size: 20px;
}
.page-desc {
  color: var(--color-text-3);
  margin: 0;
  font-size: 14px;
}
.tab-toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.doc-title {
  font-weight: 500;
}
.pii-text {
  color: var(--color-danger-6);
  font-weight: 500;
  background: var(--color-danger-light-1);
  padding: 1px 4px;
  border-radius: 2px;
}
.preset-checks {
  margin-bottom: 8px;
}
.preset-checks :deep(.arco-checkbox) {
  margin-bottom: 6px;
}
.keyword-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}
.keyword-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.rule-desc {
  color: var(--color-text-3);
  font-size: 12px;
}
.hint-text {
  color: var(--color-text-3);
  font-size: 13px;
  margin-bottom: 8px;
}
.regex-code {
  font-size: 12px;
  color: var(--color-text-2);
  background: var(--color-fill-2);
  padding: 2px 6px;
  border-radius: 3px;
  word-break: break-all;
}
.modal-title-ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 600px;
}
.modal-title-filename {
  color: var(--color-text-3);
  font-weight: normal;
}
.report-content h4 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
}
.template-preview-panel {
  margin-top: 10px;
  padding: 10px 12px;
  background: var(--color-fill-1);
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
}
.template-preview-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-1);
  margin-bottom: 8px;
}
.preview-label {
  font-size: 12px;
  color: var(--color-text-3);
  margin-right: 4px;
}
.preview-empty {
  font-size: 12px;
  color: var(--color-text-4);
}
</style>
