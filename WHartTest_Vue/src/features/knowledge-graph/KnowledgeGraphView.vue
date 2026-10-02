<template>
  <div class="graph-page">
    <header v-if="!props.embedded" class="graph-header">
      <div>
        <span class="eyebrow">KNOWLEDGE FABRIC</span>
        <h1>知识图谱</h1>
        <p>统一沉淀代码、文档与测试知识，为影响分析和 LLM 检索提供可追踪语义关系。</p>
      </div>
      <div class="header-actions">
        <a-button @click="loadSources"><template #icon><icon-refresh /></template>刷新数据源</a-button>
        <a-button v-if="selectedSource?.type === 'code_repository'" type="primary" @click="$router.push('/code-analysis')"><template #icon><icon-code-block /></template>进入代码审查</a-button>
        <a-button v-if="selectedSource?.type === 'knowledge_document'" type="primary" @click="$router.push('/knowledge-management')"><template #icon><icon-file /></template>进入知识库</a-button>
        <a-button v-if="selectedSource?.type === 'requirement'" type="primary" @click="$router.push('/requirements')"><template #icon><icon-book /></template>进入需求</a-button>
        <a-button v-if="selectedSource?.type === 'test_case'" type="primary" @click="$router.push('/testcases')"><template #icon><icon-bug /></template>进入用例</a-button>
      </div>
    </header>

    <section class="source-strip">
      <button
        v-for="type in sourceTypes" :key="type.value" class="source-type"
        :class="{ active: activeSourceType === type.value, disabled: !type.enabled }"
        :disabled="!type.enabled" @click="activeSourceType = type.value"
      >
        <component :is="sourceTypeIcon(type.value)" />
        <span>{{ type.label }}</span><small>{{ type.enabled ? '已接入' : '扩展位' }}</small>
      </button>
      <div class="flywheel-note"><icon-loop />来源 → 实体/关系 → 审查与检索 → 人工反馈 → 知识沉淀</div>
    </section>

    <main class="graph-workbench">
      <aside class="source-panel">
        <div class="panel-heading"><div><span>GRAPH SOURCES</span><b>{{ sourcePanelTitle(activeSourceType) }}</b></div><a-tag>{{ sources.length }}</a-tag></div>
        <a-input v-model="sourceSearch" allow-clear :placeholder="sourceSearchPlaceholder(activeSourceType)"><template #prefix><icon-search /></template></a-input>
        <div v-if="sourcesLoading" class="panel-state"><a-spin />正在发现图谱...</div>
        <div v-else-if="!filteredSources.length" class="panel-state empty"><icon-relation />当前项目暂无可用图谱</div>
        <button
          v-for="source in filteredSources" :key="source.id" class="source-item"
          :class="{ selected: selectedSource?.id === source.id }" @click="selectSource(source)"
        >
          <span class="source-icon"><component :is="sourceTypeIcon(source.type)" /></span>
          <span class="source-copy"><b>{{ source.name }}</b><small>{{ source.project.name }}</small><code>{{ shortCommit(source.snapshot.commit) }}</code></span>
          <span class="status-dot"></span>
        </button>
        <div class="source-footer">
          <span>数据来源</span>
          <a-tag v-for="type in sourceTypes.filter(t => t.enabled)" :key="type.value" color="gray">{{ type.label }}</a-tag>
          <span v-if="!sourceTypes.some(t => t.enabled)" class="empty-note">暂无数据源</span>
        </div>
      </aside>

      <section class="canvas-panel">
        <div class="canvas-toolbar">
          <a-input-search v-model="search" placeholder="搜索节点名称或描述..." search-button @search="applyQuery" @press-enter="applyQuery" />
          <a-select v-model="selectedNodeKinds" multiple allow-clear :max-tag-count="1" placeholder="节点类型">
            <a-option v-for="item in nodeKindOptions" :key="item.value" :value="item.value">{{ item.label }} ({{ item.count }})</a-option>
          </a-select>
          <a-select v-model="selectedEdgeKinds" multiple allow-clear :max-tag-count="1" placeholder="关系类型">
            <a-option v-for="item in edgeKindOptions" :key="item.value" :value="item.value">{{ edgeLabel(item.value) }} ({{ item.count }})</a-option>
          </a-select>
          <a-button type="primary" @click="applyQuery"><template #icon><icon-filter /></template>应用</a-button>
        </div>

        <div class="canvas-meta" v-if="snapshot">
          <span><b>{{ snapshot.stats.nodes.toLocaleString() }}</b> 节点</span>
          <span><b>{{ snapshot.stats.edges.toLocaleString() }}</b> 关系</span>
          <span><b>{{ snapshot.nodes.length }}</b> 当前加载</span>
          <span v-if="snapshot.truncated" class="bounded"><icon-info-circle />按需加载，未一次传输全图</span>
          <span class="latency">{{ snapshot.duration_ms }} ms</span>
        </div>

        <div class="graph-stage" :class="{ loading: graphLoading }">
          <div v-if="graphLoading" class="stage-state"><a-spin :size="32" />正在读取图谱邻域...</div>
          <div v-else-if="graphError" class="stage-state error"><icon-exclamation-circle />{{ graphError }}<a-button size="small" @click="applyQuery">重试</a-button></div>
          <div v-else-if="!selectedSource" class="stage-state"><icon-relation />请从左侧选择图谱数据源</div>
          <div v-else-if="!snapshot?.nodes.length" class="stage-state"><icon-search />没有匹配当前条件的节点</div>
          <svg
            v-else ref="svgRef" class="graph-svg" viewBox="0 0 1000 680"
            @wheel.prevent="onWheel" @mousedown="startPan" @mousemove="movePan" @mouseup="endPan" @mouseleave="endPan"
          >
            <defs>
              <marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#a9b4c5" /></marker>
              <pattern id="dot-grid" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1" fill="#dfe6ef" /></pattern>
            </defs>
            <rect width="1000" height="680" fill="url(#dot-grid)" />
            <g :transform="`translate(${pan.x} ${pan.y}) scale(${zoom})`">
              <line
                v-for="edge in visibleEdges" :key="edge.id"
                :x1="position(edge.source).x" :y1="position(edge.source).y"
                :x2="position(edge.target).x" :y2="position(edge.target).y"
                :class="['graph-edge', `edge-${edge.kind.toLowerCase()}`]" marker-end="url(#graph-arrow)"
              />
              <g
                v-for="node in snapshot.nodes" :key="node.id"
                :transform="`translate(${position(node.id).x} ${position(node.id).y})`"
                :class="['graph-node', { selected: selectedNode?.id === node.id }]" @click.stop="selectedNode = node"
              >
                <circle :r="nodeRadius(node)" :fill="nodeColor(node.kind)" />
                <circle :r="nodeRadius(node) + 5" class="node-halo" />
                <text y="28" text-anchor="middle">{{ truncate(node.label, 20) }}</text>
                <title>{{ node.qualified_name }}</title>
              </g>
            </g>
          </svg>
          <div class="zoom-controls" v-if="snapshot?.nodes.length">
            <a-button size="mini" @click="zoom = Math.min(2.2, zoom + 0.15)"><icon-plus /></a-button>
            <span>{{ Math.round(zoom * 100) }}%</span>
            <a-button size="mini" @click="zoom = Math.max(0.45, zoom - 0.15)"><icon-minus /></a-button>
            <a-button size="mini" @click="resetViewport"><icon-fullscreen /></a-button>
          </div>
          <div class="graph-legend" v-if="snapshot?.nodes.length">
            <span v-for="kind in displayedKinds" :key="kind"><i :style="{ background: nodeColor(kind) }"></i>{{ nodeLabel(kind) }}</span>
          </div>
        </div>
      </section>

      <aside class="detail-panel">
        <div v-if="selectedNode" class="node-detail">
          <div class="detail-title"><span class="detail-kind" :style="{ color: nodeColor(selectedNode.kind) }">{{ nodeLabel(selectedNode.kind) }}</span><a-button size="mini" @click="selectedNode = null"><icon-close /></a-button></div>
          <h2>{{ selectedNode.label }}</h2>
          <code>{{ selectedNode.qualified_name }}</code>
          <dl>
            <div v-for="field in nodeDetailFields" :key="field.label">
              <dt>{{ field.label }}</dt>
              <dd :title="typeof field.value === 'string' ? field.value : ''">{{ field.value || '—' }}</dd>
            </div>
          </dl>
          <div v-if="selectedNode.properties.signature" class="signature"><span>SIGNATURE</span><code>{{ selectedNode.properties.signature }}</code></div>
          <a-button long type="primary" @click="expandNode"><template #icon><icon-relation /></template>展开一层关系</a-button>
          <a-button v-if="selectedSource?.type === 'code_repository' && selectedSource?.provenance.analysis_task_id" long @click="openReview"><template #icon><icon-code-block /></template>查看来源审查</a-button>
          <a-button v-if="selectedSource?.type === 'knowledge_document'" long @click="$router.push('/knowledge-management')"><template #icon><icon-file /></template>查看来源文档</a-button>
          <a-button v-if="selectedSource?.type === 'requirement'" long @click="$router.push('/requirements')"><template #icon><icon-book /></template>查看需求文档</a-button>
          <a-button v-if="selectedSource?.type === 'test_case'" long @click="$router.push('/testcases')"><template #icon><icon-bug /></template>查看测试用例</a-button>
          <section class="relation-list">
            <div class="section-label">CURRENT RELATIONS</div>
            <div v-for="edge in selectedRelations" :key="edge.id" class="relation-item">
              <span>{{ edge.source === selectedNode.id ? '出' : '入' }}</span>
              <b>{{ edgeLabel(edge.kind) }}</b>
              <small>{{ otherNodeLabel(edge) }}</small>
            </div>
            <p v-if="!selectedRelations.length">当前子图中暂无关系，可展开邻域查看。</p>
          </section>
          <section class="provenance-box"><icon-link /><div><b>来源可追踪</b><span>{{ selectedNode.provenance.source_type }} / {{ shortCommit(selectedNode.provenance.snapshot_id) }}</span></div></section>
        </div>
        <div v-else-if="selectedSource" class="source-detail">
          <span class="eyebrow">SNAPSHOT</span><h2>{{ selectedSource.name }}</h2><p>{{ selectedSource.description }}</p>
          <div class="snapshot-commit"><span>TARGET COMMIT</span><code>{{ selectedSource.snapshot.commit }}</code></div>
          <dl><div><dt>项目</dt><dd>{{ selectedSource.project.name }}</dd></div><div><dt>CRG</dt><dd>{{ selectedSource.snapshot.crg_version || '—' }}</dd></div><div><dt>快照时间</dt><dd>{{ formatDate(selectedSource.snapshot.created_at) }}</dd></div></dl>
          <div class="capabilities"><span v-for="item in selectedSource.capabilities" :key="item">{{ item }}</span></div>
          <section class="future-card"><icon-loop /><div><b>数据飞轮扩展位</b><p>后续文档实体、需求、测试与人工反馈将通过同一来源协议接入。</p></div></section>
        </div>
        <div v-else class="detail-empty"><icon-info-circle />选择数据源或图节点查看详情</div>
      </aside>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Message } from '@arco-design/web-vue';
import {
  IconBranch, IconBook, IconBug, IconClose, IconCodeBlock, IconExclamationCircle, IconFile,
  IconFilter, IconFullscreen, IconInfoCircle, IconLink, IconLoop, IconMinus, IconPlus,
  IconRefresh, IconRelation, IconSearch,
} from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import { getGraphSnapshot, getGraphSources } from './service';
import type { GraphEdge, GraphNode, GraphSnapshot, GraphSource, GraphSourceType } from './types';

const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false });

const router = useRouter();
const projectStore = useProjectStore();
const sources = ref<GraphSource[]>([]);
const sourceTypes = ref<Array<{ value: GraphSourceType; label: string; enabled: boolean }>>([]);
const activeSourceType = ref<GraphSourceType>('code_repository');
const selectedSource = ref<GraphSource | null>(null);
const selectedNode = ref<GraphNode | null>(null);
const snapshot = ref<GraphSnapshot | null>(null);
const sourcesLoading = ref(false);
const graphLoading = ref(false);
const graphError = ref('');
const sourceSearch = ref('');
const search = ref('');
const selectedNodeKinds = ref<string[]>([]);
const selectedEdgeKinds = ref<string[]>([]);
const zoom = ref(1);
const pan = ref({ x: 0, y: 0 });
const dragging = ref(false);
const dragOrigin = ref({ x: 0, y: 0 });
const panOrigin = ref({ x: 0, y: 0 });
const svgRef = ref<SVGSVGElement | null>(null);

const filteredSources = computed(() => {
  const keyword = sourceSearch.value.trim().toLowerCase();
  return sources.value.filter(source => source.type === activeSourceType.value && (!keyword || `${source.name} ${source.project.name} ${source.snapshot.commit}`.toLowerCase().includes(keyword)));
});
const nodeKindOptions = computed(() => Object.entries(snapshot.value?.facets.node_kinds || {}).map(([value, count]) => ({ value, count, label: nodeLabel(value) })));
const edgeKindOptions = computed(() => Object.entries(snapshot.value?.facets.edge_kinds || {}).map(([value, count]) => ({ value, count })));
const displayedKinds = computed(() => [...new Set((snapshot.value?.nodes || []).map(node => node.kind))]);
const visibleEdges = computed(() => snapshot.value?.edges || []);
const selectedRelations = computed(() => selectedNode.value ? visibleEdges.value.filter(edge => edge.source === selectedNode.value?.id || edge.target === selectedNode.value?.id) : []);
function sourcePanelTitle(type: GraphSourceType) {
  return ({ code_repository: '代码仓库', knowledge_document: '知识库文档', requirement: '需求文档', test_case: '测试用例' } as Record<GraphSourceType, string>)[type];
}
function sourceSearchPlaceholder(type: GraphSourceType) {
  return ({ code_repository: '搜索仓库或 Commit', knowledge_document: '搜索文档标题', requirement: '搜索需求标题', test_case: '搜索用例或模块' } as Record<GraphSourceType, string>)[type];
}
function sourceTypeIcon(type: GraphSourceType) {
  return ({ code_repository: IconBranch, knowledge_document: IconFile, requirement: IconBook, test_case: IconBug } as Record<GraphSourceType, any>)[type];
}
const nodeDetailFields = computed(() => {
  if (!selectedNode.value) return [];
  const kind = selectedNode.value.kind;
  const props = selectedNode.value.properties || {};
  const common = [];
  if (props.source_type || props.source_id) {
    common.push({ label: '来源', value: `${props.source_type || ''}:${props.source_id || ''}`.replace(/^:$/, '—') });
  }
  if (props.external_id) {
    common.push({ label: '外部 ID', value: props.external_id });
  }
  if (['document', 'section', 'chunk', 'fact', 'procedure'].includes(kind)) {
    const fields = [
      { label: '节点类型', value: nodeLabel(kind) },
      { label: '块类型', value: props.block_type || '—' },
      { label: '章节', value: props.section || '—' },
    ];
    if (props.chunk_index !== undefined) fields.push({ label: '分块索引', value: String(props.chunk_index) });
    if (props.content_preview) fields.push({ label: '内容预览', value: props.content_preview });
    return [...fields, ...common];
  }
  if (['asset', 'version', 'candidate', 'rule', 'concept', 'evidence'].includes(kind)) {
    const fields = [{ label: '节点类型', value: nodeLabel(kind) }];
    if (props.level) fields.push({ label: '级别', value: props.level });
    if (props.asset_type) fields.push({ label: '资产类型', value: props.asset_type });
    if (props.version !== undefined) fields.push({ label: '版本', value: String(props.version) });
    if (props.confidence !== undefined) fields.push({ label: '置信度', value: String(props.confidence) });
    if (props.state) fields.push({ label: '状态', value: props.state });
    if (props.content_hash) fields.push({ label: '内容哈希', value: props.content_hash });
    return [...fields, ...common];
  }
  if (kind === 'requirement_document') {
    const fields = [{ label: '节点类型', value: nodeLabel(kind) }];
    if (props.category) fields.push({ label: '文档分类', value: props.category });
    if (props.status) fields.push({ label: '状态', value: props.status });
    if (props.version) fields.push({ label: '版本', value: props.version });
    if (props.description) fields.push({ label: '描述', value: props.description });
    return [...fields, ...common];
  }
  if (kind === 'requirement_module') {
    const fields = [{ label: '节点类型', value: nodeLabel(kind) }];
    if (props.order !== undefined) fields.push({ label: '排序', value: String(props.order) });
    if (props.is_auto_generated !== undefined) fields.push({ label: 'AI生成', value: props.is_auto_generated ? '是' : '否' });
    if (props.confidence !== undefined) fields.push({ label: '置信度', value: String(props.confidence) });
    if (props.content_preview) fields.push({ label: '内容预览', value: props.content_preview });
    return [...fields, ...common];
  }
  if (['test_module', 'test_case', 'test_step'].includes(kind)) {
    const fields = [{ label: '节点类型', value: nodeLabel(kind) }];
    if (props.level) fields.push({ label: '优先级', value: props.level });
    if (props.test_type) fields.push({ label: '测试类型', value: props.test_type });
    if (props.review_status) fields.push({ label: '审核状态', value: props.review_status });
    if (props.execution_mode) fields.push({ label: '执行模式', value: props.execution_mode });
    if (props.step_number !== undefined) fields.push({ label: '步骤编号', value: String(props.step_number) });
    if (props.description) fields.push({ label: '描述', value: props.description });
    if (props.expected_result) fields.push({ label: '预期结果', value: props.expected_result });
    if (props.precondition) fields.push({ label: '前置条件', value: props.precondition });
    return [...fields, ...common];
  }
  return [
    { label: '文件', value: selectedNode.value.path || '—' },
    { label: '位置', value: lineRange(selectedNode.value) },
    { label: '语言', value: selectedNode.value.language || '—' },
    { label: '调用方', value: props.caller_count || 0 },
    { label: '测试覆盖', value: props.test_coverage || 'unknown' },
  ];
});

const positions = computed(() => {
  const result = new Map<string, { x: number; y: number }>();
  const nodes = snapshot.value?.nodes || [];
  const groups = new Map<string, GraphNode[]>();
  nodes.forEach(node => groups.set(node.kind, [...(groups.get(node.kind) || []), node]));
  const kinds = [...groups.keys()];
  kinds.forEach((kind, groupIndex) => {
    const group = groups.get(kind) || [];
    const groupAngle = (Math.PI * 2 * groupIndex) / Math.max(kinds.length, 1) - Math.PI / 2;
    const centerX = 500 + Math.cos(groupAngle) * 205;
    const centerY = 340 + Math.sin(groupAngle) * 180;
    group.forEach((node, index) => {
      const ring = Math.floor(index / 12);
      const angle = (Math.PI * 2 * (index % 12)) / Math.min(12, group.length) + groupAngle;
      const radius = 42 + ring * 46;
      result.set(node.id, { x: centerX + Math.cos(angle) * radius, y: centerY + Math.sin(angle) * radius });
    });
  });
  return result;
});

function position(id: string) { return positions.value.get(id) || { x: 500, y: 340 }; }
function nodeColor(kind: string) {
  return ({
    File: '#165DFF', Class: '#0FC6C2', Function: '#FF7D00', Method: '#FF9A2E', Test: '#00B42A', Type: '#F53F3F',
    document: '#165DFF', section: '#0FC6C2', chunk: '#FF7D00', fact: '#FF9A2E', procedure: '#F7BA1E',
    concept: '#7B61FF', rule: '#F53F3F', asset: '#00B42A', version: '#14C9C9', candidate: '#FF7D00',
    evidence: '#86909C', source: '#5F5E5A', workflow_output: '#F53F3F',
    requirement_document: '#165DFF', requirement_module: '#0FC6C2',
    test_module: '#7B61FF', test_case: '#00B42A', test_step: '#FF9A2E',
  } as Record<string, string>)[kind] || '#86909C';
}
function nodeLabel(kind: string) {
  return ({
    File: '文件', Class: '类', Function: '函数', Method: '方法', Test: '测试', Type: '类型',
    document: '文档', section: '章节', chunk: '分块', fact: '事实', procedure: '步骤',
    concept: '概念', rule: '规则', asset: '知识资产', version: '知识版本', candidate: '候选',
    evidence: '证据', source: '来源快照', workflow_output: '流程产出',
    requirement_document: '需求文档', requirement_module: '需求模块',
    test_module: '测试模块', test_case: '测试用例', test_step: '测试步骤',
  } as Record<string, string>)[kind] || kind;
}
function edgeLabel(kind: string) {
  return ({
    CALLS: '调用', IMPORTS_FROM: '导入', INHERITS: '继承', REFERENCES: '引用',
    CONTAINS: '包含', PART_OF: '属于', NEXT: '下一项', MENTIONS: '提及', DEFINES: '定义',
    REFINES: '细化', CONTRADICTS: '矛盾', SUPERSEDES: '取代', SUPPORTED_BY: '由…支持',
    DERIVED_FROM: '派生自', ACCEPTED_BY: '被…采纳', REFUTED_BY: '被…反驳', FEEDS_INTO: '流转到',
  } as Record<string, string>)[kind] || kind;
}
function nodeRadius(node: GraphNode) { return selectedNode.value?.id === node.id ? 12 : node.kind === 'File' ? 9 : node.is_test ? 8 : 7; }
function truncate(value: string, length: number) { return value.length > length ? `${value.slice(0, length - 1)}…` : value; }
function shortCommit(value: string) { return String(value || '').slice(0, 10); }
function formatDate(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'; }
function lineRange(node: GraphNode) { const start = node.location.line_start; const end = node.location.line_end; return start ? `${start}${end && end !== start ? ` – ${end}` : ''}` : '—'; }
function otherNodeLabel(edge: GraphEdge) { const id = edge.source === selectedNode.value?.id ? edge.target : edge.source; return snapshot.value?.nodes.find(node => node.id === id)?.label || id; }

async function loadSources() {
  sourcesLoading.value = true;
  try {
    const data = await getGraphSources(projectStore.currentProjectId || undefined);
    sources.value = data.results;
    sourceTypes.value = data.source_types;
    if (!selectedSource.value || !sources.value.some(item => item.id === selectedSource.value?.id)) {
      selectedSource.value = sources.value[0] || null;
    }
    if (selectedSource.value) await loadGraph();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '图谱数据源加载失败');
  } finally { sourcesLoading.value = false; }
}

async function loadGraph(center = '') {
  if (!selectedSource.value) return;
  graphLoading.value = true; graphError.value = '';
  try {
    snapshot.value = await getGraphSnapshot(selectedSource.value.id, { search: center ? '' : search.value, nodeKinds: selectedNodeKinds.value, edgeKinds: selectedEdgeKinds.value, center, depth: 1, limit: 140 });
    if (center) selectedNode.value = snapshot.value.nodes.find(node => node.id === center) || null;
    else selectedNode.value = null;
    resetViewport();
  } catch (error) {
    graphError.value = error instanceof Error ? error.message : '图谱加载失败';
  } finally { graphLoading.value = false; }
}
async function selectSource(source: GraphSource) { selectedSource.value = source; search.value = ''; selectedNodeKinds.value = []; selectedEdgeKinds.value = []; await loadGraph(); }
async function applyQuery() { await loadGraph(); }
async function expandNode() { if (selectedNode.value) await loadGraph(selectedNode.value.id); }
function openReview() { const id = selectedSource.value?.provenance.analysis_task_id; router.push(id ? { path: '/code-analysis', query: { task: id } } : '/code-analysis'); }
function resetViewport() { zoom.value = 1; pan.value = { x: 0, y: 0 }; }
function onWheel(event: WheelEvent) { zoom.value = Math.max(0.45, Math.min(2.2, zoom.value + (event.deltaY < 0 ? 0.1 : -0.1))); }
function startPan(event: MouseEvent) { dragging.value = true; dragOrigin.value = { x: event.clientX, y: event.clientY }; panOrigin.value = { ...pan.value }; }
function movePan(event: MouseEvent) { if (!dragging.value) return; pan.value = { x: panOrigin.value.x + event.clientX - dragOrigin.value.x, y: panOrigin.value.y + event.clientY - dragOrigin.value.y }; }
function endPan() { dragging.value = false; }

watch(() => projectStore.currentProjectId, () => loadSources(), { immediate: true });
</script>

<style scoped>
.graph-page{min-height:calc(100vh - 58px);padding:22px;background:#f2f4f7;color:#1d2129}.graph-header{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:16px}.eyebrow,.panel-heading span,.section-label,.signature span,.snapshot-commit span{font-size:10px;font-weight:700;letter-spacing:.14em;color:#86909c}.graph-header h1{margin:3px 0 4px;font-size:28px}.graph-header p{margin:0;color:#4e5969}.header-actions{display:flex;gap:10px}.source-strip{display:flex;align-items:stretch;gap:8px;margin-bottom:12px}.source-type{display:flex;align-items:center;gap:9px;padding:9px 14px;border:1px solid #d9e0e9;background:#fff;color:#4e5969;cursor:pointer}.source-type.active{border-color:#165dff;box-shadow:inset 3px 0 #165dff;color:#165dff}.source-type.disabled{opacity:.5;cursor:not-allowed}.source-type small{padding-left:8px;border-left:1px solid #e5e8ef;color:#86909c}.flywheel-note{margin-left:auto;display:flex;align-items:center;gap:7px;padding:0 14px;border:1px dashed #c9d2df;background:#f8fafc;color:#65758b;font-size:12px}.graph-workbench{display:grid;grid-template-columns:248px minmax(520px,1fr) 294px;height:calc(100vh - 190px);min-height:610px;border:1px solid #d9e0e9;background:#fff;box-shadow:0 12px 32px rgba(29,33,41,.07)}.source-panel,.detail-panel{padding:14px;background:#fbfcfe;overflow:auto}.source-panel{border-right:1px solid #e5e8ef}.detail-panel{border-left:1px solid #e5e8ef}.panel-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.panel-heading div{display:flex;flex-direction:column;gap:2px}.panel-heading b{font-size:16px}.panel-state,.detail-empty,.stage-state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:#86909c;text-align:center}.panel-state{min-height:140px}.panel-state.empty svg{font-size:30px}.source-item{position:relative;display:flex;width:100%;align-items:flex-start;gap:10px;margin-top:8px;padding:11px;border:1px solid transparent;background:transparent;text-align:left;cursor:pointer}.source-item:hover{background:#f2f6ff}.source-item.selected{border-color:#b8cdfb;background:#edf3ff}.source-icon{display:grid;place-items:center;width:30px;height:30px;background:#e8f0ff;color:#165dff}.source-copy{display:flex;min-width:0;flex:1;flex-direction:column;gap:3px}.source-copy b,.source-copy small,.source-copy code{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.source-copy small{color:#86909c}.source-copy code{font-size:11px;color:#4e5969}.status-dot{width:7px;height:7px;margin-top:7px;border-radius:50%;background:#00b42a}.source-footer{display:flex;flex-wrap:wrap;gap:6px;margin-top:18px;padding-top:14px;border-top:1px solid #e5e8ef}.source-footer>span{width:100%;font-size:11px;color:#86909c}.canvas-panel{display:flex;min-width:0;flex-direction:column}.canvas-toolbar{display:grid;grid-template-columns:minmax(200px,1fr) 150px 150px auto;gap:8px;padding:12px;border-bottom:1px solid #e5e8ef}.canvas-meta{display:flex;align-items:center;gap:18px;padding:7px 14px;border-bottom:1px solid #edf0f5;font-size:12px;color:#65758b}.canvas-meta b{color:#1d2129}.bounded{color:#ff7d00}.latency{margin-left:auto}.graph-stage{position:relative;flex:1;min-height:0;overflow:hidden;background:#f8fafc}.stage-state{position:absolute;inset:0;z-index:2}.stage-state.error{color:#f53f3f}.graph-svg{width:100%;height:100%;cursor:grab}.graph-svg:active{cursor:grabbing}.graph-edge{stroke:#a9b4c5;stroke-width:1;opacity:.65}.edge-calls{stroke:#ff9a2e}.edge-imports_from{stroke:#4f7fe8}.graph-node{cursor:pointer}.graph-node circle:first-child{stroke:#fff;stroke-width:2}.node-halo{fill:transparent;stroke:transparent;stroke-width:2}.graph-node.selected .node-halo{stroke:#1d2129}.graph-node text{font-size:10px;fill:#4e5969;paint-order:stroke;stroke:#f8fafc;stroke-width:3px;stroke-linejoin:round}.zoom-controls,.graph-legend{position:absolute;display:flex;align-items:center;gap:7px;border:1px solid #d9e0e9;background:rgba(255,255,255,.94);box-shadow:0 4px 14px rgba(29,33,41,.08)}.zoom-controls{right:12px;bottom:12px;padding:5px}.zoom-controls span{min-width:40px;text-align:center;font-size:11px}.graph-legend{left:12px;bottom:12px;flex-wrap:wrap;max-width:70%;padding:8px 10px;font-size:11px}.graph-legend span{display:flex;align-items:center;gap:4px}.graph-legend i{width:7px;height:7px;border-radius:50%}.detail-title{display:flex;align-items:center;justify-content:space-between}.detail-kind{font-size:11px;font-weight:700;letter-spacing:.08em}.node-detail h2,.source-detail h2{margin:7px 0;font-size:18px;word-break:break-word}.node-detail>code{display:block;padding:8px;background:#eef1f5;color:#4e5969;word-break:break-all}.detail-panel dl{margin:16px 0}.detail-panel dl div{display:grid;grid-template-columns:72px 1fr;gap:8px;padding:7px 0;border-bottom:1px solid #edf0f5}.detail-panel dt{color:#86909c}.detail-panel dd{margin:0;word-break:break-word}.signature,.snapshot-commit{margin:12px 0;padding:10px;border-left:3px solid #165dff;background:#edf3ff}.signature code,.snapshot-commit code{display:block;margin-top:5px;word-break:break-all}.node-detail>.arco-btn{margin-top:8px}.relation-list{margin-top:18px}.relation-item{display:grid;grid-template-columns:22px 70px 1fr;gap:5px;padding:7px 0;border-bottom:1px solid #edf0f5}.relation-item span{color:#165dff}.relation-item small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.relation-list p{font-size:12px;color:#86909c}.provenance-box,.future-card{display:flex;gap:9px;margin-top:16px;padding:11px;border:1px solid #d9e0e9;background:#fff}.provenance-box div,.future-card div{display:flex;flex-direction:column;gap:3px}.provenance-box span,.future-card p{margin:0;color:#86909c;font-size:11px}.source-detail>p{color:#65758b}.capabilities{display:flex;flex-wrap:wrap;gap:5px}.capabilities span{padding:3px 6px;background:#e8f0ff;color:#165dff;font-size:10px}.future-card{border-color:#ffd8a8;background:#fff8ed;color:#ff7d00}.detail-empty{height:100%;font-size:13px}.detail-empty svg{font-size:28px}@media(max-width:1200px){.graph-workbench{grid-template-columns:210px minmax(480px,1fr) 250px}.flywheel-note{display:none}.canvas-toolbar{grid-template-columns:1fr 130px 130px auto}}@media(max-width:900px){.graph-workbench{grid-template-columns:1fr;height:auto}.source-panel,.detail-panel{border:0;border-bottom:1px solid #e5e8ef}.source-panel{max-height:260px}.canvas-panel{height:620px}.detail-panel{min-height:300px}.graph-header{align-items:flex-start;gap:12px}.canvas-toolbar{grid-template-columns:1fr 1fr}.canvas-toolbar>*:first-child{grid-column:1/-1}}
</style>
