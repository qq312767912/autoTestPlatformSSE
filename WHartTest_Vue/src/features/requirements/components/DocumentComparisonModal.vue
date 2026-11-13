<template>
  <a-modal :visible="visible" title="文档版本对比与提取" width="94vw" :footer="false" :align-center="false" :top="24"
    :body-style="{ maxHeight: 'calc(100vh - 140px)', overflowY: 'auto' }"
    :mask-closable="false" :closable="!busy" @cancel="emit('close')">
    <a-space direction="vertical" fill size="large">
      <p class="intro">上传修改前、修改后的两份 Word，核对差异后提取本次迭代内容。</p>
      <a-form :model="{ title }" layout="vertical">
        <div class="upload-grid">
          <div class="upload-panel">
            <label>旧版文档 · 修改前</label>
            <a-upload class="document-upload" :file-list="[]" :auto-upload="false" :show-file-list="false"
              :multiple="false" :disabled="busy" accept=".docx" draggable :on-before-upload="validateDocument"
              @change="(_files, file) => chooseUpload(file, 'old')">
              <template #upload-button>
                <div class="upload-target">
                  <a-button type="primary" :disabled="busy">{{ oldFile || session ? '重新选择旧版文档' : '上传旧版文档' }}</a-button>
                  <span>或将旧版 DOCX 文件拖到这里</span>
                </div>
              </template>
            </a-upload>
            <span class="file-name" :title="oldFile?.name || session?.old_name">{{ oldFile?.name || session?.old_name || '请选择 DOCX 文件' }}</span>
          </div>
          <div class="upload-panel">
            <label>新版文档 · 修改后</label>
            <a-upload class="document-upload" :file-list="[]" :auto-upload="false" :show-file-list="false"
              :multiple="false" :disabled="busy" accept=".docx" draggable :on-before-upload="validateDocument"
              @change="(_files, file) => chooseUpload(file, 'new')">
              <template #upload-button>
                <div class="upload-target">
                  <a-button type="primary" :disabled="busy">{{ newFile || session ? '重新选择新版文档' : '上传新版文档' }}</a-button>
                  <span>或将新版 DOCX 文件拖到这里</span>
                </div>
              </template>
            </a-upload>
            <span class="file-name" :title="newFile?.name || session?.new_name">{{ newFile?.name || session?.new_name || '请选择 DOCX 文件' }}</span>
          </div>
        </div>
        <label class="iteration-label">本次迭代名称（必填）</label>
        <div class="compare-actions">
          <a-input ref="titleInput" v-model="title" :max-length="200" placeholder="本次迭代名称，例如 V3.2.5 登录流程调整" :disabled="busy" aria-label="本次迭代名称" />
          <a-button type="primary" :loading="comparing" :disabled="busy || !oldFile || !newFile" @click="compare">开始对比</a-button>
          <a-button v-if="!session && savedId" :disabled="busy" @click="restore">恢复上次对比</a-button>
        </div>
        <p v-if="comparing" class="muted">正在解析两份文档并生成差异，请稍候…</p>
      </a-form>
      <template v-if="session">
        <details class="comparison-rules">
          <summary>对比与提取规则</summary>
          <p>全程不调用 AI。按接受修订后的正文对比，手工删除线视为删除；域按文档已保存的显示结果处理。行号为内容块序号，不是 Word 页码。</p>
          <p v-for="warning in session.result.warnings" :key="warning">{{ warning }}</p>
          <p>下载包包含“迭代需求.docx”和“变更说明.md”。DOCX 按修订汇总报告格式生成：完整保留改动小节，新增绿色、删除灰色删除线；删除内容会在需求解析时识别为取消/修改需求，变更说明保留原文证据。可将 DOCX 重新上传为普通需求文档。</p>
        </details>
        <details v-if="session.result.issues?.length" class="comparison-issues" open>
          <summary>有 {{ session.result.issues.length }} 处内容需要核对</summary>
          <ul><li v-for="(issue, i) in session.result.issues" :key="i">{{ issue.side === 'old' ? '旧版' : '新版' }} · {{ issue.path || issue.block_id }}：{{ issue.message }}</li></ul>
        </details>
        <div class="diff-toolbar">
          <a-space wrap>
            <a-tag color="green">＋ {{ session.result.added }} 块</a-tag>
            <a-tag color="red">− {{ session.result.deleted }} 块</a-tag>
            <span>已选 {{ selected.length }} / {{ session.result.hunks.length }} 个变更块</span>
            <a-button size="small" :disabled="busy" @click="selectAll">全选</a-button>
            <a-button size="small" :disabled="busy" @click="clearSelection">清空</a-button>
          </a-space>
          <a-radio-group v-model="displayMode" type="button" size="small" aria-label="Diff 展示方式">
            <a-radio value="split">左右对比</a-radio><a-radio value="unified">统一视图</a-radio>
          </a-radio-group>
        </div>
        <a-empty v-if="!session.result.hunks.length" description="两份文档的有效正文没有差异，无需提取。" />
        <div v-else class="diff-list">
          <div v-if="displayMode === 'split'" class="diff-file-header"><span>旧版 · {{ session.old_name }}</span><span>新版 · {{ session.new_name }}</span></div>
          <section v-for="hunk in session.result.hunks" :key="hunk.id" class="diff-hunk">
            <div class="diff-hunk-header">
              <a-checkbox :model-value="selected.includes(hunk.id)" :disabled="busy" @change="toggle(hunk.id)">变更块 {{ Number(hunk.id.slice(1)) + 1 }}</a-checkbox>
              <span class="section-path">{{ hunk.rows.find(row => row.kind !== 'context')?.path || '正文' }}</span>
            </div>
            <template v-if="displayMode === 'split'">
              <div v-for="(pair, index) in hunk.pairs || []" :key="index" class="diff-pair">
                <ComparisonDiffCell :row="pair.old" side="old" :session-id="session.id" />
                <ComparisonDiffCell :row="pair.new" side="new" :session-id="session.id" />
              </div>
            </template>
            <template v-else>
              <ComparisonDiffCell v-for="(row, index) in hunk.rows" :key="index" :row="row" :side="row.kind === 'add' ? 'new' : 'old'" :session-id="session.id" />
            </template>
          </section>
        </div>
        <div class="extract-footer">
          <a-checkbox v-model="confirmed" :disabled="busy || !selected.length">我已核对所选变更，确认这是本次迭代范围</a-checkbox>
          <a-button type="primary" :loading="extracting" :title="extractHint" :disabled="busy || !confirmed || !selected.length" @click="extract">确认并提取所选 Diff</a-button>
          <span v-if="extractHint" class="extract-hint" role="status">{{ extractHint }}</span>
          <span v-if="session.confirmed_at" class="muted">上次确认：{{ new Date(session.confirmed_at).toLocaleString() }}</span>
        </div>
        <a-alert v-if="downloadUrl" type="success">
          提取已完成。若浏览器没有自动下载，请
          <a :href="downloadUrl" :download="downloadName">点击下载提取结果（ZIP）</a>。
        </a-alert>
      </template>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
    </a-space>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue';
import { Message } from '@arco-design/web-vue';
import type { FileItem } from '@arco-design/web-vue/es/upload/interfaces';
import ComparisonDiffCell from './ComparisonDiffCell.vue';
import { compareFiles, extractComparison, loadComparison, comparisonError, type ComparisonSession } from '../services/comparisonService';

const props = defineProps<{ visible: boolean; projectId: string }>();
const emit = defineEmits<{ close: [] }>();
const oldFile = ref<File>();
const newFile = ref<File>();
const title = ref('');
const titleInput = ref<{ focus: () => void }>();
const downloadUrl = ref('');
const downloadName = ref('');
const displayMode = ref('split');
const session = ref<ComparisonSession>();
const selected = ref<string[]>([]);
const confirmed = ref(false);
const comparing = ref(false);
const extracting = ref(false);
const error = ref('');
const savedId = ref('');
const busy = computed(() => comparing.value || extracting.value);
const extractHint = computed(() => {
  if (extracting.value) return '正在生成提取结果，请稍候…';
  if (comparing.value) return '正在对比文档，请稍候…';
  if (!selected.value.length) return '请先选择变更块，或点击全选。';
  if (!confirmed.value) return '请勾选“我已核对所选变更”后提取。';
  if (!title.value.trim()) return '请填写顶部的本次迭代名称；点击提取会定位到输入框。';
  return '';
});
function clearDownload() {
  if (downloadUrl.value) URL.revokeObjectURL(downloadUrl.value);
  downloadUrl.value = '';
  downloadName.value = '';
}
watch([title, selected, () => props.projectId], clearDownload);
onBeforeUnmount(clearDownload);
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
}, { immediate: true });
function validateDocument(file: File): boolean {
  if (!file.name.toLowerCase().endsWith('.docx')) {
    error.value = '版本对比仅支持 .docx 文件。';
    return false;
  }
  return true;
}
function chooseUpload(item: FileItem, side: 'old' | 'new') {
  const file = item.file;
  if (busy.value || !file || !validateDocument(file)) return;
  session.value = undefined;
  selected.value = [];
  confirmed.value = false;
  error.value = '';
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
  if (!session.value || !confirmed.value || !selected.value.length || busy.value) return;
  if (!title.value.trim()) {
    error.value = '请先填写本次迭代名称，再点击确认并提取。';
    titleInput.value?.focus();
    return;
  }
  extracting.value = true;
  error.value = '';
  try {
    const blob = await extractComparison(session.value.id, title.value.trim(), selected.value);
    clearDownload();
    downloadUrl.value = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl.value;
    link.download = `${title.value.trim().replace(/[\\/:*?"<>|]/g, '_')}-diff.zip`;
    downloadName.value = link.download;
    document.body.appendChild(link);
    link.click();
    link.remove();
    session.value.confirmed_at = new Date().toISOString();
    Message.success('已提取，请解压后将迭代需求 DOCX 上传为普通文档。');
  } catch (e) { error.value = e instanceof Error ? e.message : comparisonError(e);
  } finally { extracting.value = false; }
}
</script>

<style scoped>
.intro { margin: 0; color: var(--color-text-2); }
.upload-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.upload-panel { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 14px; border: 1px solid var(--color-border-2); border-radius: 6px; background: var(--color-fill-1); }
.upload-panel > label { width: 100%; color: var(--color-text-2); font-weight: 500; }
.document-upload { width: 100%; }
.upload-target { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 18px; border: 1px dashed var(--color-border-3); border-radius: 4px; background: var(--color-bg-2); cursor: pointer; }
.upload-target:hover { border-color: rgb(var(--primary-6)); }
.upload-target > span { color: var(--color-text-3); font-size: 12px; }
.file-name { min-width: 0; flex: 1; color: var(--color-text-3); overflow-wrap: anywhere; font-size: 12px; }
.iteration-label { display: block; margin-top: 16px; color: var(--color-text-2); font-size: 13px; }
.compare-actions { display: flex; gap: 12px; margin-top: 8px; }
.compare-actions > .arco-input-wrapper { max-width: 560px; }
.extract-hint { color: rgb(var(--orange-6)); font-size: 12px; }
.muted { color: var(--color-text-3); font-size: 12px; }
.comparison-rules, .comparison-issues { padding: 10px 14px; border: 1px solid var(--color-border-2); border-radius: 5px; font-size: 12px; color: var(--color-text-2); }
.comparison-rules summary, .comparison-issues summary { cursor: pointer; font-size: 13px; }
.comparison-issues { background: #fff7e8; color: #8c5700; }
.comparison-issues ul { max-height: 140px; overflow: auto; padding-left: 20px; }
.diff-toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; align-items: center; }
.diff-list { max-height: 54vh; overflow: auto; border: 1px solid var(--color-border-2); border-radius: 6px; }
.diff-file-header, .diff-pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.diff-file-header { position: sticky; top: 0; z-index: 1; background: var(--color-fill-2); font-size: 12px; font-weight: 500; }
.diff-file-header > span { padding: 9px 12px; overflow-wrap: anywhere; }
.diff-file-header > span + span, .diff-pair > :nth-child(2) { border-left: 1px solid var(--color-border-2); }
.diff-hunk + .diff-hunk { border-top: 14px solid var(--color-fill-1); }
.diff-hunk-header { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; padding: 9px 12px; background: #eef6ff; border-block: 1px solid var(--color-border-2); }
.section-path { color: var(--color-text-3); font-size: 12px; overflow-wrap: anywhere; }
.extract-footer { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; }
@media (max-width: 720px) { .upload-grid { grid-template-columns: 1fr; } .compare-actions { flex-wrap: wrap; } }
</style>
