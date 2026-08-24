<template>
  <a-card class="image-analysis-card">
    <template #title>
      <div class="panel-title">
        <div>
          <span>需求图片识别与调整</span>
          <a-tag :color="statusColor" size="small">{{ statusText }}</a-tag>
        </div>
        <a-space>
          <a-button size="small" :disabled="!hasModules" :loading="preparing" @click="prepareImages">提取图片</a-button>
          <a-button size="small" type="primary" :disabled="!localImages.length || status === 'processing'" :loading="analyzing || status === 'processing'" @click="analyzeImages()">
            OCR + AI识别
          </a-button>
          <a-button size="small" status="success" :disabled="!localImages.length" :loading="confirming" @click="confirmImages">
            确认图片结果
          </a-button>
        </a-space>
      </div>
    </template>

    <a-alert :type="hasModules ? 'info' : 'warning'" :closable="false" class="panel-hint">
      {{ hasModules
        ? '图片在模块拆分后处理。AI会建议归属模块和变更内容，但只有人工确认后才能进入测试方案和用例上下文。'
        : '请先完成模块拆分。拆分后可在这里提取需求文档图片，并识别截图中的页面变更。' }}
    </a-alert>

    <a-alert v-if="status === 'processing'" type="info" :closable="false" class="panel-hint">
      后台正在处理：已完成 {{ progress.completed }}/{{ progress.total }}，处理中 {{ progress.processing }}，等待 {{ progress.pending }}。可以关闭弹窗，任务会继续执行。
    </a-alert>

    <a-empty v-if="!localImages.length" :description="hasModules ? '暂无已提取图片，请点击“提取图片”' : '等待模块拆分完成'" />

    <div v-else class="image-list">
      <a-card v-for="image in localImages" :key="image.id" class="image-item" :bordered="true">
        <div class="image-layout">
          <div class="image-preview-wrap">
            <a-image :src="image.image_url" width="280" fit="contain" class="image-preview" />
            <div class="image-meta">
              <a-tag>{{ image.image_id }}</a-tag>
              <a-tag :color="reviewColor(image.review_status)">{{ reviewText(image.review_status) }}</a-tag>
              <span v-if="image.confidence !== null">置信度 {{ Math.round(image.confidence * 100) }}%</span>
            </div>
            <a-switch v-model="image.is_enabled" @change="saveImage(image)">
              <template #checked>采用</template>
              <template #unchecked>忽略</template>
            </a-switch>
          </div>

          <a-form :model="image" layout="vertical" class="image-form" :disabled="!image.is_enabled">
            <div class="two-columns">
              <a-form-item label="所属需求模块">
                <a-select v-model="image.module" allow-clear placeholder="请选择模块">
                  <a-option v-for="module in modules" :key="module.id" :value="module.id">
                    {{ module.order }}. {{ module.title }}
                  </a-option>
                </a-select>
              </a-form-item>
              <a-form-item label="页面名称">
                <a-input v-model="image.page_title" placeholder="例：科技评价导入" />
              </a-form-item>
              <a-form-item label="变更类型">
                <a-select v-model="image.change_type">
                  <a-option value="add">新增</a-option>
                  <a-option value="change">修改</a-option>
                  <a-option value="remove">删除</a-option>
                  <a-option value="unknown">无法判断</a-option>
                </a-select>
              </a-form-item>
              <a-form-item label="图片附近文字">
                <a-textarea v-model="image.nearby_text" :auto-size="{ minRows: 2, maxRows: 5 }" />
              </a-form-item>
            </div>
            <a-form-item label="OCR文字">
              <a-textarea v-model="image.ocr_text" :auto-size="{ minRows: 3, maxRows: 8 }" />
            </a-form-item>
            <div class="ai-result-block">
              <div class="ai-result-title">AI 结构化识别结果</div>
              <a-form-item label="内容摘要">
                <a-textarea :model-value="analysisSummary(image)" readonly :auto-size="{ minRows: 2, maxRows: 5 }" />
              </a-form-item>
              <div class="two-columns">
                <a-form-item label="识别字段/控件">
                  <a-textarea :model-value="analysisList(image, 'detected_fields') || detectedElements(image)" readonly :auto-size="{ minRows: 3, maxRows: 8 }" />
                </a-form-item>
                <a-form-item label="可证明的业务规则">
                  <a-textarea :model-value="analysisList(image, 'business_rules')" readonly :auto-size="{ minRows: 3, maxRows: 8 }" />
                </a-form-item>
              </div>
              <a-form-item label="变更标注与证据">
                <a-textarea :model-value="annotationText(image)" readonly :auto-size="{ minRows: 2, maxRows: 6 }" />
              </a-form-item>
            </div>
            <a-form-item label="表格 Markdown">
              <a-textarea
                v-model="image.table_markdown"
                :auto-size="{ minRows: 4, maxRows: 14 }"
                placeholder="表格图片识别后会在这里生成可编辑的 Markdown 表格"
              />
            </a-form-item>
            <a-form-item label="变更说明">
              <a-textarea v-model="image.change_description" :auto-size="{ minRows: 2, maxRows: 6 }" placeholder="请只保留图片和文档能证明的变更" />
            </a-form-item>
            <a-form-item label="建议测试点（每行一条）">
              <a-textarea v-model="testPointTexts[image.id]" :auto-size="{ minRows: 3, maxRows: 8 }" />
            </a-form-item>
            <a-form-item label="用户备注">
              <a-input v-model="image.user_notes" />
            </a-form-item>
            <a-alert v-if="image.analysis_error" type="warning" class="analysis-warning">
              视觉分析未完整成功，已保留OCR结果：{{ image.analysis_error }}
            </a-alert>
            <div class="form-actions">
              <a-button :disabled="status === 'processing'" @click="analyzeImages([image.id])">重新识别此图</a-button>
              <a-button type="primary" :loading="savingId === image.id" @click="saveImage(image)">保存调整</a-button>
            </div>
          </a-form>
        </div>
      </a-card>
    </div>
  </a-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { RequirementDocumentService } from '../services/requirementService';
import type { DocumentImageAnalysis, DocumentModule } from '../types';

const props = defineProps<{
  documentId: string;
  modules: DocumentModule[];
  images: DocumentImageAnalysis[];
  status: string;
}>();
const emit = defineEmits<{ (event: 'refresh'): void }>();

const localImages = ref<DocumentImageAnalysis[]>([]);
const testPointTexts = reactive<Record<string, string>>({});
const preparing = ref(false);
const analyzing = ref(false);
const confirming = ref(false);
const savingId = ref('');
const hasModules = computed(() => props.modules.length > 0);
let pollTimer: ReturnType<typeof setInterval> | undefined;

const progress = computed(() => ({
  total: localImages.value.filter(image => image.is_enabled).length,
  completed: localImages.value.filter(image => ['analyzed', 'confirmed', 'ignored', 'error'].includes(image.review_status)).length,
  processing: localImages.value.filter(image => image.review_status === 'processing').length,
  pending: localImages.value.filter(image => image.review_status === 'pending').length,
}));

const stopPolling = () => {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = undefined;
};

watch(() => props.status, (status) => {
  stopPolling();
  if (status === 'processing') {
    pollTimer = setInterval(() => emit('refresh'), 2500);
  }
}, { immediate: true });

onBeforeUnmount(stopPolling);

watch(() => props.images, (images) => {
  localImages.value = JSON.parse(JSON.stringify(images || []));
  localImages.value.forEach((image) => {
    testPointTexts[image.id] = (image.suggested_test_points || []).join('\n');
  });
}, { immediate: true, deep: true });

const statusText = computed(() => ({
  not_started: '未开始', processing: '分析中', user_reviewing: '待用户确认', confirmed: '已确认', failed: '分析失败'
}[props.status] || props.status));
const statusColor = computed(() => ({ not_started: 'gray', processing: 'orange', user_reviewing: 'arcoblue', confirmed: 'green', failed: 'red' }[props.status] || 'gray'));
const reviewText = (status: string) => ({ pending: '待分析', processing: '处理中', analyzed: '待确认', confirmed: '已确认', ignored: '已忽略', error: '分析失败' }[status] || status);
const reviewColor = (status: string) => ({ pending: 'gray', processing: 'orange', analyzed: 'arcoblue', confirmed: 'green', ignored: 'gray', error: 'red' }[status] || 'gray');
const analysisSummary = (image: DocumentImageAnalysis) => String(image.analysis_result?.content_summary || '暂无AI摘要，请重新识别此图');
const analysisList = (image: DocumentImageAnalysis, key: string) => {
  const value = image.analysis_result?.[key];
  return Array.isArray(value) ? value.map(item => typeof item === 'string' ? item : JSON.stringify(item)).join('\n') : String(value || '');
};
const detectedElements = (image: DocumentImageAnalysis) => {
  const regions = Array.isArray(image.analysis_result?.regions) ? image.analysis_result.regions : [];
  return regions.flatMap((region: any) => (region.elements || []).map((item: any) => `${region.name || '页面'}：${item.name || item.visible_text || item.type}`)).join('\n');
};
const annotationText = (image: DocumentImageAnalysis) => {
  const annotations = Array.isArray(image.analysis_result?.annotations) ? image.analysis_result.annotations : [];
  return annotations.map((item: any) => `[${item.change_type || 'unknown'}] ${item.target || ''}：${item.evidence || ''}`).join('\n') || '未发现截图能够证明的新增、修改或删除标注';
};

const prepareImages = async () => {
  preparing.value = true;
  try {
    const response = await RequirementDocumentService.prepareImages(props.documentId);
    if (response.status !== 'success') throw new Error(response.message);
    Message.success(`已提取 ${response.data?.total || 0} 张图片`);
    emit('refresh');
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '图片提取失败');
  } finally { preparing.value = false; }
};

const analyzeImages = async (imageIds?: string[]) => {
  analyzing.value = true;
  try {
    const response = await RequirementDocumentService.analyzeImages(props.documentId, imageIds);
    if (response.status !== 'success') throw new Error(response.message);
    Message.success(`已提交后台任务，共 ${response.data?.queued || localImages.value.length} 张图片`);
    emit('refresh');
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '图片分析失败');
  } finally { analyzing.value = false; }
};

const saveImage = async (image: DocumentImageAnalysis) => {
  savingId.value = image.id;
  const payload = {
    module: image.module,
    nearby_text: image.nearby_text,
    ocr_text: image.ocr_text,
    page_title: image.page_title,
    change_type: image.change_type,
    change_description: image.change_description,
    table_markdown: image.table_markdown,
    suggested_test_points: (testPointTexts[image.id] || '').split('\n').map(item => item.trim()).filter(Boolean),
    user_notes: image.user_notes,
    is_enabled: image.is_enabled,
  };
  try {
    const response = await RequirementDocumentService.updateImageAnalysis(props.documentId, image.id, payload);
    if (response.status !== 'success') throw new Error(response.message);
    Message.success('图片调整已保存');
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '保存失败');
  } finally { savingId.value = ''; }
};

const confirmImages = async () => {
  confirming.value = true;
  try {
    for (const image of localImages.value) await saveImage(image);
    const response = await RequirementDocumentService.confirmImageAnalysis(props.documentId);
    if (response.status !== 'success') throw new Error(response.message);
    Message.success(`已确认 ${response.data?.confirmed || 0} 张需求图片`);
    emit('refresh');
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '确认失败');
  } finally { confirming.value = false; }
};
</script>

<style scoped>
.image-analysis-card { margin-top: 20px; }
.panel-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.panel-title > div:first-child { display: flex; align-items: center; gap: 10px; }
.panel-hint { margin-bottom: 16px; }
.image-list { display: grid; gap: 16px; }
.image-layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 20px; }
.image-preview-wrap { display: flex; flex-direction: column; gap: 12px; align-items: flex-start; }
.image-preview { max-height: 360px; background: #f7f8fa; border-radius: 6px; }
.image-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; color: var(--color-text-3); }
.image-form { min-width: 0; }
.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.analysis-warning { margin-bottom: 12px; }
.ai-result-block { margin: 8px 0 16px; padding: 14px; background: var(--color-fill-1); border-radius: 6px; }
.ai-result-title { margin-bottom: 12px; font-weight: 600; color: var(--color-text-1); }
.form-actions { display: flex; justify-content: flex-end; }
@media (max-width: 960px) {
  .panel-title, .image-layout { display: flex; flex-direction: column; align-items: stretch; }
  .two-columns { grid-template-columns: 1fr; }
}
</style>
