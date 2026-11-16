<template>
  <a-drawer :visible="visible" title="内部代码审查金标集" :width="900" @cancel="emit('update:visible', false)">
    <a-alert>这里保留独立的人工标记快照。初标样本尚未冻结，须经独立复核才能成为正式确认金标。</a-alert>
    <a-table :data="rows" :loading="loading" :pagination="{ current: page, pageSize: 20, total }" row-key="id" style="margin-top:16px" @page-change="changePage">
      <template #columns>
        <a-table-column title="样本名称" data-index="title" />
        <a-table-column title="数据集" data-index="dataset_name" />
        <a-table-column title="状态"><template #cell="{ record }">{{ stateLabel(record.state) }}</template></a-table-column>
        <a-table-column title="保存时间"><template #cell="{ record }">{{ new Date(record.created_at).toLocaleString('zh-CN') }}</template></a-table-column>
        <a-table-column title="操作"><template #cell="{ record }"><a-button type="text" @click="open(record.id)">查看快照</a-button></template></a-table-column>
      </template>
    </a-table>
    <a-modal v-model:visible="detailVisible" title="已保存的内部金标快照" width="800px" :footer="false">
      <a-spin :loading="detailLoading" style="width:100%">
        <template v-if="sample">
          <p>{{ sample.title }} · {{ stateLabel(sample.state) }}</p>
          <p>快照指纹：{{ sample.source_hash }}</p>
          <a-button type="primary" @click="download">导出完整快照</a-button>
          <h4>人工标记</h4>
          <p v-for="mark in sample.expected_output.annotations" :key="mark.index">第 {{ mark.index + 1 }} 项：{{ markLabel(mark.label) }}{{ mark.comment ? `；${mark.comment}` : '' }}</p>
          <h4>代码变更</h4><pre style="white-space:pre-wrap;max-height:260px;overflow:auto">{{ sample.input_snapshot.raw_diff || '原任务未记录代码变更文本' }}</pre>
        </template>
      </a-spin>
    </a-modal>
  </a-drawer>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { listInternalGold, getInternalGold, type InternalGoldSummary, type InternalGoldSample, type GoldMark } from './service';
const props = defineProps<{visible: boolean; projectId: number}>();
const emit = defineEmits<{(event: 'update:visible', value: boolean): void}>();
const rows = ref<InternalGoldSummary[]>([]), total = ref(0), page = ref(1), loading = ref(false);
const detailVisible = ref(false), detailLoading = ref(false), sample = ref<InternalGoldSample | null>(null);
let requestVersion = 0, detailVersion = 0;
const states: Record<string, string> = {candidate:'候选',labeling:'待独立复核',confirmed:'已确认',conflict:'待仲裁',rejected:'已拒绝'};
const stateLabel = (state: string) => states[state] || '待复核';
const markLabel = (label: GoldMark['label']) => ({confirmed:'确认问题',false_positive:'误报',uncertain:'待复核'}[label]);
async function load() {
  const version = ++requestVersion;
  if (!props.visible || !props.projectId) return;
  loading.value = true;
  try { const result = await listInternalGold(props.projectId, page.value); if (version === requestVersion) { rows.value = result.items; total.value = result.total; } }
  catch (error: unknown) { Message.error(error instanceof Error ? error.message : '读取内部金标失败'); }
  finally { if (version === requestVersion) loading.value = false; }
}
watch(() => [props.visible, props.projectId] as const, () => { page.value = 1; rows.value = []; detailVersion += 1; detailVisible.value = false; void load(); });
function changePage(value: number) { page.value = value; void load(); }
async function open(id: string) {
  const version = ++detailVersion;
  detailVisible.value = true; sample.value = null; detailLoading.value = true;
  try { const result = await getInternalGold(props.projectId, id); if (version === detailVersion) sample.value = result; }
  catch (error: unknown) { Message.error(error instanceof Error ? error.message : '读取快照失败'); }
  finally { if (version === detailVersion) detailLoading.value = false; }
}
function download() {
  if (!sample.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(sample.value, null, 2)], {type:'application/json;charset=utf-8'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `内部代码审查金标-${sample.value.id}.json`; anchor.click(); URL.revokeObjectURL(url);
}
</script>
