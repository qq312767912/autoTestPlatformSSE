<template>
  <section v-if="knowledgeBaseIds.length" class="document-scope">
    <div class="document-scope__head">
      <div>
        <strong>检索范围</strong>
        <span>默认检索所选知识库的全部文档</span>
      </div>
      <a-radio-group :model-value="scopeMode" type="button" size="small" @change="setScopeMode">
        <a-radio value="all">全部文档</a-radio>
        <a-radio value="selected">指定文档</a-radio>
      </a-radio-group>
    </div>
    <template v-if="scopeMode === 'selected'">
      <a-select
        :model-value="documentIds"
        multiple allow-clear allow-search
        :loading="loading"
        :max-tag-count="2"
        placeholder="搜索并选择文档（最多 20 篇）"
        @update:model-value="updateDocuments"
      >
        <a-optgroup v-for="group in documentGroups" :key="group.id" :label="group.name">
          <a-option v-for="document in group.documents" :key="document.id" :value="document.id" :label="document.title">
            <span>{{ document.title }}</span>
            <small>{{ document.chunk_count }} 分块</small>
          </a-option>
        </a-optgroup>
      </a-select>
      <p class="document-scope__hint">已选择 {{ documentIds.length }}/20 篇；检索结果只来自这些文档。</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { KnowledgeService } from '@/features/knowledge/services/knowledgeService';
import type { Document, KnowledgeBase } from '@/features/knowledge/types/knowledge';
import { toArray } from '@/features/api-testing/services/responseHelpers';

const props = defineProps<{
  knowledgeBaseIds: string[];
  knowledgeBases: KnowledgeBase[];
  scopeMode: 'all' | 'selected';
  documentIds: string[];
}>();
const emit = defineEmits<{
  'update:scope-mode': [value: 'all' | 'selected'];
  'update:document-ids': [value: string[]];
}>();

const loading = ref(false);
const documents = ref<Document[]>([]);
const documentGroups = computed(() => props.knowledgeBaseIds.map(id => ({
  id,
  name: props.knowledgeBases.find(kb => kb.id === id)?.name || '知识库',
  documents: documents.value.filter(document => document.knowledge_base === id),
})).filter(group => group.documents.length));

const loadDocuments = async () => {
  if (props.scopeMode !== 'selected' || !props.knowledgeBaseIds.length) return;
  loading.value = true;
  try {
    const responses = await Promise.all(props.knowledgeBaseIds.map(knowledge_base =>
      KnowledgeService.getDocuments({ knowledge_base, status: 'completed' })
    ));
    documents.value = responses.flatMap(response => toArray<Document>((response as any)?.results ?? response));
    const validIds = new Set(documents.value.map(document => document.id));
    const validSelection = props.documentIds.filter(id => validIds.has(id));
    if (validSelection.length !== props.documentIds.length) emit('update:document-ids', validSelection);
  } catch (error) {
    console.error('加载知识库文档失败:', error);
    Message.error('加载知识库文档失败');
    documents.value = [];
  } finally {
    loading.value = false;
  }
};

const setScopeMode = (value: string | number | boolean) => {
  const mode = value === 'selected' ? 'selected' : 'all';
  emit('update:scope-mode', mode);
  if (mode === 'all') emit('update:document-ids', []);
};

const updateDocuments = (value: string[]) => {
  const ids = Array.isArray(value) ? value : [];
  if (ids.length > 20) {
    Message.warning('一次最多指定 20 篇文档');
    emit('update:document-ids', ids.slice(0, 20));
    return;
  }
  emit('update:document-ids', ids);
};

watch(() => [props.scopeMode, ...props.knowledgeBaseIds], loadDocuments, { immediate: true });
</script>

<style scoped>
.document-scope { display: grid; gap: 8px; min-width: 360px; padding-top: 10px; border-top: 1px solid #e8edf3; }
.document-scope__head { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.document-scope__head strong { margin-right: 8px; color: #344054; font-size: 13px; }
.document-scope__head span, .document-scope__hint { color: #8692a3; font-size: 12px; }
.document-scope__hint { margin: 0; }
small { margin-left: 8px; color: #98a2b3; }
</style>
