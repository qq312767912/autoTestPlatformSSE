<template>
  <a-modal :visible="visible" title="从 ONES 导入" :footer="false" :width="720" @cancel="close">
    <a-alert>选择页面后导入当前知识库，同时下载支持的文档附件。图片识别尚未启用。</a-alert>
    <a-form layout="vertical" :model="credentials">
      <a-form-item label="ONES 邮箱"><a-input v-model="credentials.email" autocomplete="off" /></a-form-item>
      <a-form-item label="密码"><a-input-password v-model="credentials.password" autocomplete="new-password" /></a-form-item>
    </a-form>
    <a-button :loading="loading" @click="loadSpaces">登录并加载空间</a-button>
    <a-select v-if="spaces.length" v-model="space" placeholder="选择空间" allow-search style="margin-top:16px" @change="loadPages">
      <a-option v-for="item in spaces" :key="item.uuid" :value="item.uuid">{{ item.title }}</a-option>
    </a-select>
    <a-tree v-if="pages.length" v-model:checked-keys="selected" v-model:expanded-keys="expanded" :auto-expand-parent="false" :data="tree" checkable style="max-height:360px;overflow:auto;margin-top:16px" />
    <a-button v-if="pages.length" :loading="loading" :disabled="!selected.length" @click="previewAttachments">加载选中页面附件</a-button>
    <a-checkbox v-if="allAttachmentIds.length" :model-value="allAttachmentsSelected" :indeterminate="someAttachmentsSelected" @change="toggleAllAttachments">全选附件（{{ allAttachmentIds.length }}）</a-checkbox>
    <div v-for="page in previews" :key="page.uuid">
      <h4>{{ page.title }}（{{ page.attachments.length }} 个附件）</h4>
      <a-checkbox-group v-model="attachmentIds" direction="vertical">
        <a-checkbox v-for="file in page.attachments" :key="file.uuid" :value="file.uuid">{{ file.name || file.title || file.uuid }}</a-checkbox>
      </a-checkbox-group>
    </div>
    <a-button v-if="previews.length" type="primary" :loading="loading" :disabled="!selected.length" style="margin-top:16px" @click="importPages">导入页面正文及选中附件（{{ attachmentIds.length }}）</a-button>
    <p v-if="progress">{{ progress }}</p>
    <p v-for="failure in failures" :key="failure" style="color:var(--color-danger-6)">{{ failure }}</p>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { request } from '@/utils/request';
const props = defineProps<{ visible: boolean; knowledgeBaseId: string }>();
const emit = defineEmits<{ close: []; imported: [] }>();
const credentials = reactive({ email: '', password: '' });
const spaces = ref<any[]>([]);
const pages = ref<any[]>([]);
const selected = ref<Array<string | number>>([]);
const expanded = ref<Array<string | number>>([]);
const space = ref('');
const loading = ref(false);
const progress = ref('');
const failures = ref<string[]>([]);
const previews = ref<any[]>([]);
const attachmentIds = ref<Array<string | number>>([]);
const allAttachmentIds = computed<string[]>(() => [...new Set<string>(
  previews.value.flatMap(page => page.attachments.map((file: { uuid: string }) => file.uuid))
)]);
const allAttachmentsSelected = computed(() => allAttachmentIds.value.length > 0 && allAttachmentIds.value.every(id => attachmentIds.value.includes(id)));
const someAttachmentsSelected = computed(() => !allAttachmentsSelected.value && allAttachmentIds.value.some(id => attachmentIds.value.includes(id)));
function toggleAllAttachments(checked: boolean | (string | number | boolean)[]) {
  attachmentIds.value = checked === true ? [...allAttachmentIds.value] : [];
}
watch(selected, () => { previews.value = []; attachmentIds.value = []; }, { deep: true });
const previewAttachments = () => run(async () => {
  previews.value = [];
  attachmentIds.value = [];
  for (const id of selected.value) {
    const result = await call({ space_uuid: space.value, page_uuids: [id], preview: true });
    previews.value.push(...result.results);
  }
});
const tree = computed(() => {
  const nodes = new Map(pages.value.map(p => [p.uuid, { key: p.uuid, title: p.title, children: [] as any[] }]));
  const roots: any[] = [];
  for (const p of pages.value) {
    const node = nodes.get(p.uuid)!;
    const parent = nodes.get(p.parent_uuid);
    if (parent && parent !== node) parent.children.push(node); else roots.push(node);
  }
  return roots;
});
async function call(extra = {}) {
  const response = await request<any>({ url: `/knowledge/knowledge-bases/${props.knowledgeBaseId}/ones/`, method: 'POST', data: { ...credentials, ...extra } });
  if (!response.success) throw new Error(response.error || 'ONES 请求失败');
  return response.data;
}
async function run(action: () => Promise<void>) {
  loading.value = true;
  try { await action(); } catch (e: any) { Message.error(e.message); } finally { loading.value = false; }
}
const loadSpaces = () => run(async () => { pages.value = []; selected.value = []; spaces.value = (await call()).spaces; });
const loadPages = () => run(async () => {
  selected.value = [];
  previews.value = [];
  attachmentIds.value = [];
  expanded.value = [];
  pages.value = (await call({ space_uuid: space.value })).pages;
  expanded.value = tree.value.map(node => node.key);
});
const importPages = () => run(async () => {
  const ids = [...selected.value];
  failures.value = [];
  let done = 0;
  for (const id of ids) {
    const result = await call({ space_uuid: space.value, page_uuids: [id], attachment_uuids: attachmentIds.value });
    for (const row of result.results) {
      if (row.status === 'failed') failures.value.push(`${pages.value.find(p => p.uuid === id)?.title}: ${row.error}`);
      for (const warning of row.warnings || []) failures.value.push(warning);
    }
    progress.value = `已处理 ${++done}/${ids.length}，失败 ${failures.value.length}。文档索引状态可在知识库查看。`;
  }
  emit('imported');
});
function close() { if (loading.value) return; credentials.password = ''; emit('close'); }
</script>
