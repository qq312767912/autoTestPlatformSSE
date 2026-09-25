<template>
  <div class="host-config-page">
    <header class="page-heading">
      <div>
        <h1>测试域名配置</h1>
        <p>统一管理内网被测系统的域名与 IP，发布后同步到宿主机和 UI 执行节点。</p>
      </div>
      <a-space>
        <a-button @click="openNodes"><template #icon><icon-computer /></template>节点状态</a-button>
        <a-button @click="openVersions"><template #icon><icon-history /></template>版本历史</a-button>
        <a-button v-if="canChange" type="primary" @click="openEditor()"><template #icon><icon-plus /></template>新增映射</a-button>
        <a-button v-if="canChange" @click="bulkVisible = true">批量导入</a-button>
        <a-button v-if="canPublish" type="primary" status="success" :disabled="!overview.pending_changes" @click="openPublish">
          <template #icon><icon-upload /></template>发布配置<span v-if="overview.pending_changes"> ({{ overview.pending_changes }})</span>
        </a-button>
      </a-space>
    </header>

    <section class="status-strip">
      <div class="status-cell"><span>已发布版本</span><strong>{{ overview.published_version ? `v${overview.published_version}` : '尚未发布' }}</strong></div>
      <div class="status-cell"><span>未发布变更</span><strong :class="{ warning: overview.pending_changes > 0 }">{{ overview.pending_changes }}</strong></div>
      <div class="status-cell"><span>已同步节点</span><strong class="success">{{ overview.node_counts.synced }}</strong></div>
      <div class="status-cell"><span>异常 / 离线</span><strong :class="{ danger: unhealthyNodes > 0 }">{{ unhealthyNodes }}</strong></div>
      <div class="status-cell status-cell-wide"><span>最后发布</span><strong>{{ overview.published_at ? formatDate(overview.published_at) : '-' }} {{ overview.published_by }}</strong></div>
    </section>

    <section class="table-panel">
      <div class="toolbar">
        <a-input-search v-model="filters.search" allow-clear placeholder="搜索系统名称、域名或 IP" style="width: 340px" @search="loadMappings" @clear="loadMappings" />
        <a-select v-model="filters.enabled" allow-clear placeholder="全部状态" style="width: 140px" @change="loadMappings">
          <a-option value="true">已启用</a-option><a-option value="false">已停用</a-option>
        </a-select>
        <a-button @click="refreshAll"><template #icon><icon-refresh /></template>刷新</a-button>
      </div>
      <a-table :data="mappings" :loading="loading" row-key="id" :pagination="{ pageSize: 15 }" :scroll="{ x: 1050 }">
        <template #columns>
          <a-table-column title="系统名称" data-index="system_name" :width="150" />
          <a-table-column title="域名" data-index="hostname" :width="240"><template #cell="{ record }"><code>{{ record.hostname }}</code></template></a-table-column>
          <a-table-column title="IPv4" data-index="ipv4" :width="150"><template #cell="{ record }"><code>{{ record.ipv4 }}</code></template></a-table-column>
          <a-table-column title="状态" :width="90"><template #cell="{ record }"><a-tag :color="record.enabled ? 'green' : 'gray'">{{ record.enabled ? '已启用' : '已停用' }}</a-tag></template></a-table-column>
          <a-table-column title="备注" data-index="remark" :ellipsis="true" :tooltip="true" />
          <a-table-column title="更新信息" :width="180"><template #cell="{ record }"><div>{{ record.updated_by_name || '-' }}</div><small>{{ formatDate(record.updated_at) }}</small></template></a-table-column>
          <a-table-column title="操作" fixed="right" :width="220">
            <template #cell="{ record }">
              <a-space>
                <a-link v-if="canDiagnose" @click="diagnose(record)">诊断</a-link>
                <a-link v-if="canChange" @click="openEditor(record)">编辑</a-link>
                <a-popconfirm v-if="canDelete" content="删除后需要重新发布才会从各节点移除，确认删除？" @ok="removeMapping(record.id)">
                  <a-link status="danger">删除</a-link>
                </a-popconfirm>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </section>

    <a-drawer :visible="editorVisible" :width="480" :title="editingId ? '编辑映射' : '新增映射'" @cancel="editorVisible = false" @ok="saveMapping" :ok-loading="saving">
      <a-form ref="formRef" :model="form" layout="vertical">
        <a-form-item field="system_name" label="系统名称" :rules="[{ required: true, message: '请输入系统名称' }]"><a-input v-model="form.system_name" :max-length="100" /></a-form-item>
        <a-form-item field="hostname" label="域名" :rules="hostnameRules"><a-input v-model="form.hostname" placeholder="trade.internal.example" /></a-form-item>
        <a-form-item field="ipv4" label="IPv4" :rules="ipv4Rules"><a-input v-model="form.ipv4" placeholder="10.10.20.30" /></a-form-item>
        <a-form-item field="enabled" label="状态"><a-switch v-model="form.enabled" checked-text="启用" unchecked-text="停用" /></a-form-item>
        <a-form-item field="remark" label="备注"><a-textarea v-model="form.remark" :max-length="500" show-word-limit :auto-size="{ minRows: 3, maxRows: 6 }" /></a-form-item>
      </a-form>
    </a-drawer>

    <a-drawer :visible="bulkVisible" :width="860" title="批量导入 hosts" @cancel="bulkVisible = false" :footer="false">
      <a-alert type="info">每行填写“IPv4 域名”，支持一行多个域名和 # 行尾注释。导入后仅保存为草稿，仍需发布配置。</a-alert>
      <a-form layout="vertical" style="margin-top:16px">
        <a-form-item label="系统名称"><a-input v-model="bulkSystemName" placeholder="例如：星企航测试环境" /></a-form-item>
        <a-form-item label="hosts 原文"><a-textarea v-model="bulkText" :auto-size="{ minRows: 10, maxRows: 18 }" placeholder="202.122.117.52 oc.test.sse.com.cn&#10;202.122.117.52 pujiang.test.sse.com.cn" /></a-form-item>
      </a-form>
      <a-space><a-button type="primary" :loading="bulkLoading" @click="previewBulk">解析预览</a-button><a-button v-if="bulkPreview.rows?.length" status="success" :loading="bulkLoading" :disabled="bulkPreview.errors?.length || bulkPreview.conflicts?.length" @click="commitBulk(false)">导入有效配置</a-button></a-space>
      <a-alert v-if="bulkPreview.conflicts?.length" type="warning" style="margin-top:14px">发现 {{ bulkPreview.conflicts.length }} 个 IP 冲突。确认后将覆盖草稿中的原 IP。 <a-link @click="commitBulk(true)">确认覆盖并导入</a-link></a-alert>
      <a-table v-if="bulkPreview.rows?.length || bulkPreview.errors?.length" :data="bulkPreview.rows || []" :pagination="false" :scroll="{ y: 360 }" style="margin-top:14px">
        <template #columns><a-table-column title="行" data-index="line" :width="60"/><a-table-column title="域名" data-index="hostname"/><a-table-column title="IPv4" data-index="ipv4" :width="150"/><a-table-column title="处理" :width="120"><template #cell="{record}">{{ record.action === 'create' ? '新增' : record.action === 'update' ? '覆盖' : '无变化' }}</template></a-table-column></template>
      </a-table>
      <a-alert v-for="error in bulkPreview.errors || []" :key="`${error.line}-${error.detail}`" type="error" style="margin-top:8px">第 {{ error.line }} 行：{{ error.detail }}（{{ error.text }}）</a-alert>
    </a-drawer>

    <a-drawer :visible="publishVisible" :width="680" title="发布配置" @cancel="publishVisible = false" @ok="confirmPublish" :ok-loading="publishing" ok-text="确认发布">
      <a-alert type="warning">发布后宿主机同步服务会将该版本应用到所有在线 UI 执行节点。</a-alert>
      <div class="diff-group" v-if="diff.added.length"><h3>新增 {{ diff.added.length }} 条</h3><div v-for="item in diff.added" :key="`a-${item.hostname}`" class="diff-row added">+ {{ item.hostname }} → {{ item.ipv4 }}</div></div>
      <div class="diff-group" v-if="diff.changed.length"><h3>修改 {{ diff.changed.length }} 条</h3><div v-for="item in diff.changed" :key="`c-${item.after.hostname}`" class="diff-row changed">{{ item.after.hostname }}：{{ item.before.ipv4 }} → {{ item.after.ipv4 }}</div></div>
      <div class="diff-group" v-if="diff.removed.length"><h3>移除 {{ diff.removed.length }} 条</h3><div v-for="item in diff.removed" :key="`r-${item.hostname}`" class="diff-row removed">- {{ item.hostname }} → {{ item.ipv4 }}</div></div>
    </a-drawer>

    <a-drawer :visible="nodesVisible" :width="760" title="节点同步状态" :footer="false" @cancel="nodesVisible = false">
      <a-alert v-if="!nodes.length" type="warning">尚未收到同步服务心跳，请检查 wharttest-host-sync.timer。</a-alert>
      <a-table :data="nodes" :pagination="false" row-key="node_id">
        <template #columns>
          <a-table-column title="节点" data-index="display_name" />
          <a-table-column title="类型" data-index="node_type" :width="110" />
          <a-table-column title="版本" :width="90"><template #cell="{ record }">{{ record.applied_version ? `v${record.applied_version}` : '-' }}</template></a-table-column>
          <a-table-column title="状态" :width="110"><template #cell="{ record }"><a-tag :color="nodeColor(record.effective_status)">{{ nodeLabel(record.effective_status) }}</a-tag></template></a-table-column>
          <a-table-column title="最后心跳" :width="170"><template #cell="{ record }">{{ formatDate(record.last_seen_at) }}</template></a-table-column>
          <a-table-column title="信息" data-index="message" />
        </template>
      </a-table>
    </a-drawer>

    <a-drawer :visible="versionsVisible" :width="760" title="版本历史" :footer="false" @cancel="versionsVisible = false">
      <a-table :data="versions" :pagination="{ pageSize: 10 }" row-key="id">
        <template #columns>
          <a-table-column title="版本" :width="90"><template #cell="{ record }"><strong>v{{ record.version }}</strong></template></a-table-column>
          <a-table-column title="状态" data-index="status" :width="110" />
          <a-table-column title="记录数" :width="100"><template #cell="{ record }">{{ record.snapshot.length }}</template></a-table-column>
          <a-table-column title="发布人" data-index="created_by_name" :width="120" />
          <a-table-column title="发布时间" :width="180"><template #cell="{ record }">{{ formatDate(record.published_at || record.created_at) }}</template></a-table-column>
          <a-table-column title="操作" :width="100"><template #cell="{ record }"><a-popconfirm v-if="canRollback && record.version !== overview.published_version" content="回滚会以该快照创建新版本，确认继续？" @ok="rollback(record.id)"><a-link>回滚</a-link></a-popconfirm></template></a-table-column>
        </template>
      </a-table>
    </a-drawer>

    <a-drawer :visible="diagnosisVisible" :width="620" title="域名诊断" :footer="false" @cancel="closeDiagnosis">
      <a-spin :loading="diagnosis?.status === 'pending' || diagnosis?.status === 'running'" style="width: 100%">
        <a-descriptions v-if="diagnosis" :column="1" bordered>
          <a-descriptions-item label="域名">{{ diagnosis.hostname }}</a-descriptions-item>
          <a-descriptions-item label="期望 IP">{{ diagnosis.ipv4 }}</a-descriptions-item>
          <a-descriptions-item label="状态"><a-tag :color="diagnosis.status === 'success' ? 'green' : diagnosis.status === 'failed' ? 'red' : 'blue'">{{ diagnosis.status }}</a-tag></a-descriptions-item>
          <a-descriptions-item label="耗时">{{ diagnosis.duration_ms }} ms</a-descriptions-item>
          <a-descriptions-item v-if="diagnosis.error_message" label="失败原因"><span class="danger-text">{{ diagnosis.error_message }}</span></a-descriptions-item>
        </a-descriptions>
        <pre v-if="diagnosis && Object.keys(diagnosis.result).length" class="diagnosis-result">{{ JSON.stringify(diagnosis.result, null, 2) }}</pre>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import type { FormInstance, FieldRule } from '@arco-design/web-vue';
import { Message } from '@arco-design/web-vue';
import { IconComputer, IconHistory, IconPlus, IconRefresh, IconUpload } from '@arco-design/web-vue/es/icon';
import { useAuthStore } from '@/store/authStore';
import {
  bulkImportMappings, createMapping, deleteMapping, getDiagnosis, getDiff, getOverview, listMappings,
  listNodes, listVersions, publishMappings, rollbackVersion, startDiagnosis, updateMapping,
} from '../service';
import type {
  MappingPayload, TestHostDiagnosis, TestHostDiff, TestHostMapping, TestHostNode,
  TestHostOverview, TestHostVersion,
} from '../types';

const authStore = useAuthStore();
const emptyCounts = () => ({ synced: 0, pending: 0, failed: 0, offline: 0 });
const mappings = ref<TestHostMapping[]>([]);
const nodes = ref<TestHostNode[]>([]);
const versions = ref<TestHostVersion[]>([]);
const overview = reactive<TestHostOverview>({ draft_revision: 0, published_version: null, published_checksum: '', published_at: null, published_by: '', pending_changes: 0, node_counts: emptyCounts() });
const diff = reactive<TestHostDiff>({ added: [], changed: [], removed: [], count: 0 });
const filters = reactive({ search: '', enabled: '' });
const loading = ref(false);
const saving = ref(false);
const publishing = ref(false);
const editorVisible = ref(false);
const publishVisible = ref(false);
const nodesVisible = ref(false);
const versionsVisible = ref(false);
const diagnosisVisible = ref(false);
const bulkVisible = ref(false);
const bulkLoading = ref(false);
const bulkText = ref('');
const bulkSystemName = ref('批量导入');
const bulkPreview = ref<any>({ rows: [], errors: [], conflicts: [] });
const editingId = ref<number | null>(null);
const diagnosis = ref<TestHostDiagnosis | null>(null);
const formRef = ref<FormInstance>();
const form = reactive<MappingPayload>({ system_name: '', hostname: '', ipv4: '', enabled: true, remark: '' });
let diagnosisTimer: number | null = null;

const canChange = computed(() => authStore.hasPermission('test_host_config.change_testhostmapping') || authStore.hasPermission('test_host_config.add_testhostmapping'));
const canDelete = computed(() => authStore.hasPermission('test_host_config.delete_testhostmapping'));
const canPublish = computed(() => authStore.hasPermission('test_host_config.publish_testhostconfig'));
const canRollback = computed(() => authStore.hasPermission('test_host_config.rollback_testhostconfig'));
const canDiagnose = computed(() => authStore.hasPermission('test_host_config.diagnose_testhostmapping'));
const unhealthyNodes = computed(() => overview.node_counts.failed + overview.node_counts.offline + overview.node_counts.pending);

const hostnameRules: FieldRule[] = [
  { required: true, message: '请输入域名' },
  { match: /^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$/, message: '请输入完整的精确域名' },
];
const ipv4Rules: FieldRule[] = [
  { required: true, message: '请输入 IPv4' },
  { match: /^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$/, message: '请输入有效的 IPv4' },
];

function formatDate(value: string | null) { return value ? new Date(value).toLocaleString() : '-'; }
function nodeColor(status: string) { return status === 'synced' ? 'green' : status === 'failed' ? 'red' : status === 'offline' ? 'gray' : 'orange'; }
function nodeLabel(status: string) { return ({ synced: '已同步', failed: '失败', offline: '离线', pending: '待同步' } as Record<string, string>)[status] || status; }
function showError(error: unknown) { Message.error(error instanceof Error ? error.message : '操作失败'); }

async function loadMappings() {
  loading.value = true;
  try { mappings.value = await listMappings({ search: filters.search || undefined, enabled: filters.enabled || undefined }); }
  catch (error) { showError(error); }
  finally { loading.value = false; }
}
async function loadOverview() { Object.assign(overview, await getOverview()); }
async function refreshAll() { try { await Promise.all([loadMappings(), loadOverview()]); } catch (error) { showError(error); } }

function openEditor(record?: TestHostMapping) {
  editingId.value = record?.id ?? null;
  Object.assign(form, record ? { system_name: record.system_name, hostname: record.hostname, ipv4: record.ipv4, enabled: record.enabled, remark: record.remark } : { system_name: '', hostname: '', ipv4: '', enabled: true, remark: '' });
  editorVisible.value = true;
}
async function saveMapping() {
  const errors = await formRef.value?.validate();
  if (errors) return;
  saving.value = true;
  try {
    if (editingId.value) await updateMapping(editingId.value, form); else await createMapping(form);
    Message.success('已保存到草稿'); editorVisible.value = false; await refreshAll();
  } catch (error) { showError(error); } finally { saving.value = false; }
}
async function removeMapping(id: number) { try { await deleteMapping(id); Message.success('已删除，发布后生效'); await refreshAll(); } catch (error) { showError(error); } }
async function previewBulk() {
  if (!bulkText.value.trim()) return Message.warning('请粘贴 hosts 配置');
  bulkLoading.value = true;
  try { bulkPreview.value = await bulkImportMappings({ text: bulkText.value, system_name: bulkSystemName.value, dry_run: true }); }
  catch (error) { showError(error); } finally { bulkLoading.value = false; }
}
async function commitBulk(overwrite: boolean) {
  bulkLoading.value = true;
  try {
    const result = await bulkImportMappings({ text: bulkText.value, system_name: bulkSystemName.value, dry_run: false, overwrite });
    Message.success(`已导入 ${result.created_or_updated} 条配置，等待发布`); bulkVisible.value = false; bulkPreview.value = { rows: [], errors: [], conflicts: [] }; await refreshAll();
  } catch (error) { showError(error); } finally { bulkLoading.value = false; }
}

async function openPublish() { try { Object.assign(diff, await getDiff()); publishVisible.value = true; } catch (error) { showError(error); } }
async function confirmPublish() {
  publishing.value = true;
  try { const version = await publishMappings(overview.draft_revision); Message.success(`已发布 v${version.version}，等待节点同步`); publishVisible.value = false; await refreshAll(); }
  catch (error) { showError(error); await loadOverview(); } finally { publishing.value = false; }
}
async function openNodes() { nodesVisible.value = true; try { nodes.value = await listNodes(); } catch (error) { showError(error); } }
async function openVersions() { versionsVisible.value = true; try { versions.value = await listVersions(); } catch (error) { showError(error); } }
async function rollback(id: number) { try { const version = await rollbackVersion(id); Message.success(`已创建回滚版本 v${version.version}`); await Promise.all([openVersions(), loadOverview()]); } catch (error) { showError(error); } }

async function diagnose(record: TestHostMapping) {
  try { diagnosis.value = await startDiagnosis(record.id); diagnosisVisible.value = true; scheduleDiagnosisPoll(); } catch (error) { showError(error); }
}
function scheduleDiagnosisPoll() {
  if (!diagnosis.value || !['pending', 'running'].includes(diagnosis.value.status)) return;
  diagnosisTimer = window.setTimeout(async () => {
    try { diagnosis.value = await getDiagnosis(diagnosis.value!.id); scheduleDiagnosisPoll(); } catch (error) { showError(error); }
  }, 1200);
}
function closeDiagnosis() { diagnosisVisible.value = false; if (diagnosisTimer !== null) window.clearTimeout(diagnosisTimer); diagnosisTimer = null; }

onMounted(refreshAll);
onBeforeUnmount(closeDiagnosis);
</script>

<style scoped>
.host-config-page { padding: 24px; color: var(--color-text-1); }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 18px; }
.page-heading h1 { margin: 0; font-size: 24px; line-height: 1.4; }
.page-heading p { margin: 6px 0 0; color: var(--color-text-3); }
.status-strip { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)) minmax(240px, 1.5fr); border: 1px solid var(--color-border-2); background: var(--color-bg-2); margin-bottom: 16px; }
.status-cell { min-height: 76px; padding: 14px 18px; border-right: 1px solid var(--color-border-2); display: flex; flex-direction: column; justify-content: center; }
.status-cell:last-child { border-right: none; }
.status-cell span { color: var(--color-text-3); font-size: 13px; }
.status-cell strong { margin-top: 7px; font-size: 19px; font-weight: 600; }
.status-cell-wide strong { font-size: 14px; }
.success { color: #00b42a; }.warning { color: #ff7d00; }.danger, .danger-text { color: #f53f3f; }
.table-panel { background: var(--color-bg-2); border: 1px solid var(--color-border-2); padding: 16px; }
.toolbar { display: flex; gap: 12px; margin-bottom: 16px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; color: var(--color-text-1); }
small { color: var(--color-text-3); }
.diff-group { margin-top: 18px; }.diff-group h3 { font-size: 15px; margin: 0 0 8px; }
.diff-row { padding: 9px 12px; margin: 6px 0; border-left: 3px solid; background: var(--color-fill-1); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.diff-row.added { border-color: #00b42a; }.diff-row.changed { border-color: #ff7d00; }.diff-row.removed { border-color: #f53f3f; }
.diagnosis-result { margin-top: 16px; max-height: 420px; overflow: auto; padding: 14px; background: var(--color-fill-2); border: 1px solid var(--color-border-2); white-space: pre-wrap; }
@media (max-width: 1280px) { .page-heading { flex-direction: column; }.status-strip { grid-template-columns: repeat(2, 1fr); }.status-cell { border-bottom: 1px solid var(--color-border-2); } }
</style>
