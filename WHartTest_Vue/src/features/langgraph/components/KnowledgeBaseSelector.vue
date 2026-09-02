<template>
  <div class="knowledge-base-selector">
    <div class="kb-select-container">
      <a-select
        :model-value="selectedKnowledgeBaseIds"
        @update:model-value="handleSelectKnowledgeBase"
        :placeholder="text.selectKnowledgeBase"
        :loading="loading"
        :disabled="!knowledgeBases.length"
        size="small"
        multiple
        allow-clear
        :max-tag-count="2"
        :trigger-props="selectTriggerProps"
        class="kb-select"
      >
        <a-option
          v-for="kb in knowledgeBases"
          :key="kb.id"
          :value="kb.id"
          :label="kb.name"
        >
          <div class="kb-option">
            <span class="kb-option-icon"><icon-storage /></span>
            <span class="kb-name">{{ kb.name }}</span>
            <span class="kb-stats">{{ text.kbStats(kb.document_count, kb.chunk_count) }}</span>
          </div>
        </a-option>
      </a-select>

      <a-tooltip :content="text.advancedSettings">
        <a-button
          type="text"
          size="small"
          @click="showAdvancedSettings = !showAdvancedSettings"
          class="settings-button"
          :class="{ 'is-active': showAdvancedSettings }"
        >
          <template #icon>
            <icon-settings />
          </template>
        </a-button>
      </a-tooltip>
    </div>

    <!-- 高级设置面板 -->
    <div v-if="showAdvancedSettings" class="advanced-settings">
      <div class="setting-item">
        <label>{{ text.retrievalMode }}</label>
        <a-radio-group :model-value="retrievalMode" type="button" size="small" @change="handleModeChange">
          <a-radio value="fast">{{ text.fastQa }}</a-radio>
          <a-radio value="standard">{{ text.standardQa }}</a-radio>
          <a-radio value="test_case">{{ text.testCaseGeneration }}</a-radio>
          <a-radio value="custom">{{ text.custom }}</a-radio>
        </a-radio-group>
      </div>
      <span class="mode-description">{{ modeDescription }}</span>

      <template v-if="retrievalMode === 'custom'">
        <div class="custom-settings">
          <div class="setting-item">
            <label>{{ text.similarityThreshold }}</label>
            <a-input-number
              :model-value="similarityThreshold"
              :min="0.1"
              :max="1"
              :step="0.05"
              :precision="2"
              size="small"
              @update:model-value="handleSimilarityChange"
            />
          </div>
          <div class="setting-item">
            <label>{{ text.maxContextChunks }}</label>
            <a-input-number
              :model-value="topK"
              :min="1"
              :max="20"
              :step="1"
              size="small"
              @update:model-value="handleTopKChange"
            />
          </div>
          <a-checkbox :model-value="coveragePriority" @change="handleCoverageChange">
            {{ text.coveragePriority }}
          </a-checkbox>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import {
  Message,
  Select as ASelect,
  Option as AOption,
  Button as AButton,
  Tooltip as ATooltip,
  RadioGroup as ARadioGroup,
  Radio as ARadio,
  InputNumber as AInputNumber,
  Checkbox as ACheckbox
} from '@arco-design/web-vue';
import { KnowledgeService } from '@/features/knowledge/services/knowledgeService';
import { IconSettings, IconStorage } from '@arco-design/web-vue/es/icon';
import type { KnowledgeBase } from '@/features/knowledge/types/knowledge';
import { useAppI18n } from '@/composables/useAppI18n';
import { toArray } from '@/features/api-testing/services/responseHelpers';

interface Props {
  projectId?: number | null;
  useKnowledgeBase: boolean;
  selectedKnowledgeBaseIds: string[];
  similarityThreshold: number;
  topK: number;
  coveragePriority: boolean;
}

const props = defineProps<Props>();
const { isEnglish } = useAppI18n();

const text = computed(() => (
  isEnglish.value
    ? {
        selectKnowledgeBase: 'Select knowledge base',
        kbStats: (documentCount: number, chunkCount: number) => `${documentCount} docs ${chunkCount} chunks`,
        advancedSettings: 'Advanced settings',
        retrievalMode: 'Retrieval mode:', fastQa: 'Quick Q&A', standardQa: 'Standard Q&A', testCaseGeneration: 'Test generation', custom: 'Custom',
        similarityThreshold: 'Min similarity:', maxContextChunks: 'Max context chunks:', coveragePriority: 'Coverage first',
        modeDescriptions: { fast: 'Faster response', standard: 'Balanced recall and speed', test_case: 'Coverage first; dynamically recalls requirement context', custom: 'Fine-tune retrieval parameters for the current task' },
        fetchKnowledgeBasesFailed: 'Failed to load knowledge bases',
      }
    : {
        selectKnowledgeBase: '选择知识库',
        kbStats: (documentCount: number, chunkCount: number) => `${documentCount}文档 ${chunkCount}分块`,
        advancedSettings: '高级设置',
        retrievalMode: '检索模式:', fastQa: '快速问答', standardQa: '标准问答', testCaseGeneration: '用例生成', custom: '自定义',
        similarityThreshold: '最低相似度:', maxContextChunks: '最多保留片段:', coveragePriority: '覆盖优先',
        modeDescriptions: { fast: '响应更快', standard: '兼顾召回效果和速度', test_case: '覆盖优先，动态召回完整需求上下文', custom: '根据当前任务精细调整检索参数' },
        fetchKnowledgeBasesFailed: '获取知识库列表失败',
      }
));

const emit = defineEmits<{
  'update:use-knowledge-base': [value: boolean];
  'update:selected-knowledge-base-ids': [value: string[]];
  'update:similarity-threshold': [value: number];
  'update:top-k': [value: number];
  'update:coverage-priority': [value: boolean];
}>();

// 响应式数据
const loading = ref(false);
const knowledgeBases = ref<KnowledgeBase[]>([]);
const showAdvancedSettings = ref(false);
const selectTriggerProps = { contentClass: 'kb-select-dropdown' };
const modeOverride = ref<string | null>(null);
const inferredMode = computed(() => {
  if (props.coveragePriority && props.topK === 20 && props.similarityThreshold === 0.2) return 'test_case';
  if (!props.coveragePriority && props.topK === 5 && props.similarityThreshold === 0.35) return 'fast';
  if (!props.coveragePriority && props.topK === 10 && props.similarityThreshold === 0.3) return 'standard';
  return 'custom';
});
const retrievalMode = computed(() => modeOverride.value || inferredMode.value);
const modeDescription = computed(() => text.value.modeDescriptions[retrievalMode.value]);

// 方法
const fetchKnowledgeBases = async () => {
  loading.value = true;
  try {
    // 知识库全局共享，不再按项目过滤
    const response = await KnowledgeService.getKnowledgeBases({
      is_active: true,
    });

    const kbList = toArray<KnowledgeBase>((response as any)?.results ?? response);

    knowledgeBases.value = kbList;

    // 移除已失效或已停用的选中项；初次开启时仍默认选中第一个。
    const availableIds = new Set(kbList.map(kb => kb.id));
    const validSelectedIds = props.selectedKnowledgeBaseIds.filter(id => availableIds.has(id));
    if (validSelectedIds.length !== props.selectedKnowledgeBaseIds.length) {
      emit('update:selected-knowledge-base-ids', validSelectedIds);
    } else if (!validSelectedIds.length && kbList.length > 0) {
      emit('update:selected-knowledge-base-ids', [kbList[0].id]);
    }
  } catch (error) {
    console.error('获取知识库列表失败:', error);
    Message.error(text.value.fetchKnowledgeBasesFailed);
    knowledgeBases.value = [];
  } finally {
    loading.value = false;
  }
};

const handleSelectKnowledgeBase = (value: string[]) => {
  emit('update:selected-knowledge-base-ids', value || []);
};

const handleModeChange = (value: string | number | boolean) => {
  const mode = String(value);
  modeOverride.value = mode;
  if (mode === 'custom') return;
  const config = {
    fast: { threshold: 0.35, topK: 5, coverage: false },
    standard: { threshold: 0.3, topK: 10, coverage: false },
    test_case: { threshold: 0.2, topK: 20, coverage: true },
  }[mode] || { threshold: 0.3, topK: 10, coverage: false };
  emit('update:similarity-threshold', config.threshold);
  emit('update:top-k', config.topK);
  emit('update:coverage-priority', config.coverage);
};

const handleSimilarityChange = (value: number | undefined) => {
  if (value !== undefined) emit('update:similarity-threshold', value);
};

const handleTopKChange = (value: number | undefined) => {
  if (value !== undefined) emit('update:top-k', value);
};

const handleCoverageChange = (value: boolean | (string | number | boolean)[]) => {
  emit('update:coverage-priority', value === true);
};

// 组件挂载时加载知识库列表
onMounted(() => {
  fetchKnowledgeBases();
});
</script>

<style scoped>
.knowledge-base-selector {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 16px;
}

.kb-select-container {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 460px;
}

.kb-select {
  width: 360px;
}

.kb-select :deep(.arco-select-view) {
  min-height: 38px;
  padding: 4px 34px 4px 8px;
  border-color: #d8dee8;
  border-radius: 9px;
  background: #fff;
  box-shadow: 0 1px 2px rgb(29 41 57 / 4%);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.kb-select :deep(.arco-select-view:hover) {
  border-color: #aebcd0;
}

.kb-select :deep(.arco-select-view-focus) {
  border-color: #168cff;
  box-shadow: 0 0 0 3px rgb(22 140 255 / 10%);
}

.kb-select :deep(.arco-tag) {
  height: 25px;
  padding: 0 8px;
  color: #304156;
  border: 1px solid #dce7f5;
  border-radius: 6px;
  background: #f2f7fd;
}

.kb-option {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-width: 300px;
}

.kb-option-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  color: #3388d8;
  border-radius: 7px;
  background: #edf6ff;
}

.kb-name {
  font-size: 13px;
  font-weight: 600;
  line-height: 20px;
  color: #27364a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-stats {
  font-size: 11px;
  line-height: 20px;
  color: #7a8798;
  white-space: nowrap;
}

.settings-button {
  width: 34px;
  height: 34px;
  color: #6f7d90;
  border: 1px solid transparent;
  border-radius: 9px;
  transition: all 160ms ease;
}

.settings-button:hover {
  color: #1677c8;
  border-color: #dce8f5;
  background: #f4f8fc;
}

.settings-button.is-active {
  color: #1677c8;
  border-color: #cfe2f6;
  background: #eaf5ff;
}

.advanced-settings {
  width: fit-content;
  padding: 10px 14px;
  background: #f8fafc;
  border-radius: 9px;
  border: 1px solid #e6ebf2;
  display: flex;
  gap: 20px;
  align-items: center;
}

.mode-description {
  color: #7a8798;
  font-size: 12px;
}

.custom-settings {
  display: flex;
  align-items: center;
  gap: 18px;
  padding-left: 14px;
  border-left: 2px solid #dce9f7;
}

.custom-settings :deep(.arco-input-number) {
  width: 92px;
}

.setting-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.setting-item label {
  font-size: 12px;
  color: #4e5969;
  white-space: nowrap;
}

.value-display {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: #5f6b7a;
  min-width: 30px;
}

:global(.kb-select-dropdown) {
  margin-top: 5px;
  padding: 6px;
  border: 1px solid #e3e9f1;
  border-radius: 11px;
  box-shadow: 0 12px 32px rgb(37 55 78 / 14%), 0 2px 8px rgb(37 55 78 / 7%);
}

:global(.kb-select-dropdown .arco-select-option) {
  min-height: 42px;
  margin: 2px 0;
  padding: 7px 10px;
  border-radius: 7px;
  background: transparent;
}

:global(.kb-select-dropdown .arco-select-option:hover) {
  background: #f5f8fc;
}

:global(.kb-select-dropdown .arco-select-option-active),
:global(.kb-select-dropdown .arco-select-option-selected) {
  color: inherit;
  background: #edf6ff;
}

:global(.kb-select-dropdown .arco-select-option-checkbox) {
  margin-right: 9px;
}
</style>
