<template>
  <a-modal :visible="visible" title="人工标记并保存内部金标" width="820px" :ok-loading="saving" :ok-button-props="{ disabled: loading || !context?.findings.length }" :mask-closable="false" @cancel="close" @ok="save">
    <a-alert>保存独立的代码变更、审查报告和人工结论快照。服务升级、原任务重跑或删除不会覆盖该样本。初标后仍需独立复核；不会自动进入已确认金标或用于优化。</a-alert>
    <a-spin :loading="loading" style="width:100%">
      <a-form layout="vertical" style="margin-top:16px">
        <a-form-item label="内部数据集名称" required>
          <a-input v-model="datasetName" :max-length="255" />
          <template #extra>可沿用已有名称；首次建立数据集需要当前项目的负责人权限。保存还需修改审查任务、添加金标用例权限。</template>
        </a-form-item>
        <a-empty v-if="!loading && !context?.findings.length" description="当前审查没有可逐项标记的风险" />
        <a-card v-for="(finding, index) in context?.findings || []" :key="finding.index" size="small" style="margin-bottom:12px">
          <b>{{ index + 1 }}. {{ finding.title }}</b>
          <p>{{ finding.file }}</p>
          <pre style="white-space:pre-wrap;max-height:160px;overflow:auto">{{ finding.evidence }}</pre>
          <a-form-item label="人工结论" required>
            <a-radio-group v-model="marks[index].label">
              <a-radio value="confirmed">确认问题</a-radio>
              <a-radio value="false_positive">误报</a-radio>
              <a-radio value="uncertain">待复核</a-radio>
            </a-radio-group>
          </a-form-item>
          <a-textarea v-model="marks[index].comment" :max-length="4000" placeholder="判断依据（标记误报时必填）" />
        </a-card>
      </a-form>
    </a-spin>
  </a-modal>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getInternalGoldContext, saveInternalGold, type InternalGoldContext, type GoldMark, type InternalGoldResult } from './service';
const props = defineProps<{ visible: boolean; taskId: string }>();
const emit = defineEmits<{ (event: 'update:visible', value: boolean): void; (event: 'saved', value: InternalGoldResult): void }>();
const loading = ref(false), saving = ref(false), datasetName = ref('内部代码审查金标集');
const context = ref<InternalGoldContext | null>(null);
const marks = ref<Array<{index: number; label: GoldMark['label'] | ''; comment: string}>>([]);
let requestVersion = 0;
const close = () => emit('update:visible', false);
watch(() => [props.visible, props.taskId] as const, async ([visible, taskId]) => {
  const version = ++requestVersion;
  context.value = null; marks.value = [];
  if (!visible || !taskId) return;
  loading.value = true;
  try {
    const data = await getInternalGoldContext(taskId);
    if (version !== requestVersion) return;
    context.value = data;
    datasetName.value = data.dataset_names[0] || '内部代码审查金标集';
    marks.value = data.findings.map(finding => ({ index: finding.index, label: '', comment: '' }));
  } catch (error: unknown) { Message.error(error instanceof Error ? error.message : '读取快照失败'); }
  finally { if (version === requestVersion) loading.value = false; }
});
async function save() {
  if (!context.value || !datasetName.value.trim()) { Message.warning('请填写数据集名称'); return; }
  const annotations: GoldMark[] = [];
  for (const mark of marks.value) {
    if (!mark.label) { Message.warning('请逐项完成人工标记'); return; }
    if (mark.label === 'false_positive' && !mark.comment.trim()) { Message.warning('误报项需填写判断依据'); return; }
    annotations.push({index: mark.index, label: mark.label, comment: mark.comment});
  }
  saving.value = true;
  try {
    const result = await saveInternalGold(props.taskId, {snapshot_hash: context.value.snapshot_hash, dataset_name: datasetName.value.trim(), annotations});
    Message.success(result.reused ? '相同快照已保存，已返回已有样本' : result.message);
    emit('saved', result); close();
  } catch (error: unknown) { Message.error(error instanceof Error ? error.message : '保存内部金标失败'); }
  finally { saving.value = false; }
}
</script>
