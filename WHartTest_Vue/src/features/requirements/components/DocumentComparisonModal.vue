<template>
  <a-modal :visible="visible" title="一本化文档版本对比" :width="1100" :footer="false"
    :mask-closable="false" :closable="!busy" @cancel="emit('close')">
    <a-space direction="vertical" fill size="large">
      <a-alert>上传旧版与新版 DOCX，逐块核对新增和删除，确认后提取所选变更。全程不调用 AI。</a-alert>
      <a-form :model="{ title }" layout="vertical">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="旧版文档（基线）">
              <input aria-label="旧版文档" type="file" accept=".docx" :disabled="busy" @change="chooseFile($event, 'old')" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="新版文档">
              <input aria-label="新版文档" type="file" accept=".docx" :disabled="busy" @change="chooseFile($event, 'new')" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="本次迭代名称">
          <a-input v-model="title" :max-length="200" placeholder="例如：V3.2.5 登录流程调整" :disabled="busy" />
        </a-form-item>
        <a-space>
          <a-button type="primary" :loading="comparing" :disabled="busy || !oldFile || !newFile" @click="compare">开始对比</a-button>
          <a-button v-if="!session && savedId" :disabled="busy" @click="restore">恢复上次对比</a-button>
          <span v-if="comparing">正在解析两份文档并生成差异，请稍候…</span>
        </a-space>
      </a-form>
      <template v-if="session">
        <div>{{ session.old_name }} → {{ session.new_name }}</div>
        <a-alert type="warning">
          按正文段落和表格对比，数字为内容块序号，并非 Word 页码。两份文档均按接受修订后的视图处理，手工删除线视为删除。
        </a-alert>
        <a-alert v-for="warning in session.result.warnings" :key="warning" type="warning">{{ warning }}</a-alert>
        <a-space>
          <a-tag color="green">＋ {{ session.result.added }} 块</a-tag>
          <a-tag color="red">− {{ session.result.deleted }} 块</a-tag>
          <span>已选 {{ selected.length }} / {{ session.result.hunks.length }} 个变更块</span>
          <a-button size="small" :disabled="busy" @click="selectAll">全选</a-button>
          <a-button size="small" :disabled="busy" @click="clearSelection">清空</a-button>
        </a-space>
        <a-empty v-if="!session.result.hunks.length" description="两份文档的有效正文没有差异，无需提取。" />
        <div v-else class="diff-list">
          <section v-for="hunk in session.result.hunks" :key="hunk.id" class="diff-hunk">
            <div class="diff-hunk-header">
              <a-checkbox :model-value="selected.includes(hunk.id)" :disabled="busy" @change="toggle(hunk.id)">
                变更块 {{ Number(hunk.id.slice(1)) + 1 }}
              </a-checkbox>
              <span>{{ hunk.rows.find(row => row.kind !== 'context')?.path || '正文' }}</span>
            </div>
            <div v-for="(row, index) in hunk.rows" :key="index" :class="['diff-row', `diff-${row.kind}`]">
              <span class="diff-number">{{ row.old_line ?? '' }}</span>
              <span class="diff-number">{{ row.new_line ?? '' }}</span>
              <span class="diff-sign">{{ row.kind === 'add' ? '+' : row.kind === 'delete' ? '−' : ' ' }}</span>
              <div class="diff-text">
                <small v-if="row.block_kind === 'table'">[整表变更，导出时保留新版表格]</small>
                <span>{{ row.text }}</span>
              </div>
            </div>
          </section>
        </div>
        <a-alert>
          下载包包含“迭代需求.docx”和“变更说明.md”。前者仅含所选新增或修改后的完整段落、表格及来源章节；后者保留删除记录。
          重新上传普通文档时请选择 DOCX。若只选删除项，DOCX 不含有效需求，需根据变更说明核验取消范围。
        </a-alert>
        <a-checkbox v-model="confirmed" :disabled="busy || !selected.length">我已核对所选变更，确认这是本次迭代范围</a-checkbox>
        <a-space>
          <a-button type="primary" :loading="extracting" :disabled="busy || !confirmed || !selected.length || !title.trim()" @click="extract">
            确认并提取所选 Diff
          </a-button>
          <span v-if="session.confirmed_at">上次确认：{{ new Date(session.confirmed_at).toLocaleString() }}</span>
        </a-space>
      </template>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
    </a-space>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { compareFiles, extractComparison, loadComparison, type ComparisonSession } from '../services/comparisonService';

const props = defineProps<{ visible: boolean; projectId: string }>();
const emit = defineEmits<{ close: [] }>();
const oldFile = ref<File>();
const newFile = ref<File>();
const title = ref('');
const session = ref<ComparisonSession>();
const selected = ref<string[]>([]);
const confirmed = ref(false);
const comparing = ref(false);
const extracting = ref(false);
const error = ref('');
const savedId = ref('');
const busy = computed(() => comparing.value || extracting.value);
watch(title, () => { confirmed.value = false; });
const storageKey = computed(() => `requirement-comparison:${props.projectId}`);
watch(() => props.projectId, () => {
  session.value = undefined;
  selected.value = [];
  confirmed.value = false;
  oldFile.value = undefined;
  newFile.value = undefined;
  error.value = '';
}, { immediate: true });
watch(() => props.visible, (visible) => {
  if (visible) savedId.value = sessionStorage.getItem(storageKey.value) || '';
});
function chooseFile(event: Event, side: 'old' | 'new') {
  const file = (event.target as HTMLInputElement).files?.[0];
  session.value = undefined;
  selected.value = [];
  confirmed.value = false;
  error.value = '';
  if (side === 'old') oldFile.value = undefined;
  else newFile.value = undefined;
  if (file && !file.name.toLowerCase().endsWith('.docx')) {
    error.value = '版本对比仅支持 .docx 文件。';
    return;
  }
  if (side === 'old') oldFile.value = file;
  else newFile.value = file;
}
function toggle(id: string) {
  selected.value = selected.value.includes(id) ? selected.value.filter(v => v !== id) : [...selected.value, id];
  confirmed.value = false;
}
function selectAll() { selected.value = session.value?.result.hunks.map(h => h.id) ?? []; confirmed.value = false; }
function clearSelection() { selected.value = []; confirmed.value = false; }
async function compare() {
  if (!oldFile.value || !newFile.value || !props.projectId) return;
  comparing.value = true;
  error.value = '';
  session.value = undefined;
  confirmed.value = false;
  const project = props.projectId;
  try {
    const result = await compareFiles(project, oldFile.value, newFile.value);
    if (project !== props.projectId) return;
    session.value = result;
    selectAll();
    savedId.value = result.id;
    sessionStorage.setItem(storageKey.value, result.id);
  } catch (e) { error.value = e instanceof Error ? e.message : '文档对比失败'; }
  finally { comparing.value = false; }
}
async function restore() {
  comparing.value = true;
  error.value = '';
  const project = props.projectId;
  try {
    const result = await loadComparison(savedId.value);
    if (project !== props.projectId) return;
    session.value = result;
    title.value = result.iteration_title;
    selected.value = result.confirmed_at ? result.confirmed_hunks : result.result.hunks.map(h => h.id);
    confirmed.value = false;
  } catch (e) { error.value = e instanceof Error ? e.message : '恢复失败'; }
  finally { comparing.value = false; }
}
async function extract() {
  if (!session.value || !confirmed.value) return;
  extracting.value = true;
  error.value = '';
  try {
    const blob = await extractComparison(session.value.id, title.value.trim(), selected.value);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${title.value.trim().replace(/[\\/:*?"<>|]/g, '_')}-diff.zip`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    session.value.confirmed_at = new Date().toISOString();
    Message.success('已提取，请解压后将迭代需求 DOCX 上传为普通文档。');
  } catch (e) {
    if (e && typeof e === 'object' && 'response' in e) {
      const response = (e as { response?: { data?: unknown } }).response;
      if (response?.data instanceof Blob) {
        try { error.value = JSON.stringify(JSON.parse(await response.data.text())); }
        catch { error.value = '提取失败，请重试。'; }
      } else error.value = '提取失败，请检查所选内容或访问权限。';
    } else error.value = e instanceof Error ? e.message : '提取失败';
  } finally { extracting.value = false; }
}
</script>

<style scoped>
.diff-list { max-height: 52vh; overflow: auto; border: 1px solid var(--color-border-2); }
.diff-hunk + .diff-hunk { margin-top: 16px; }
.diff-hunk-header { display: flex; gap: 16px; padding: 10px 12px; background: var(--color-fill-2); }
.diff-row { display: grid; grid-template-columns: 46px 46px 24px minmax(0, 1fr); font-family: ui-monospace, monospace; font-size: 13px; }
.diff-number { text-align: right; padding: 5px; color: var(--color-text-3); border-right: 1px solid var(--color-border-1); user-select: none; }
.diff-sign { padding: 5px; }
.diff-text { white-space: pre-wrap; overflow-wrap: anywhere; padding: 5px 12px; }
.diff-text small { display: block; color: var(--color-text-3); }
.diff-add { background: rgba(35, 165, 90, 0.13); }
.diff-delete { background: rgba(225, 65, 65, 0.13); }
</style>
