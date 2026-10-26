<template>
  <section class="benchmark">
    <header class="benchmark-head">
      <div><h3>代码审查</h3><p>内置公开集直接选、预检、试跑或全量运行；内部集沿用 JSON 导入。</p></div>
      <a-button size="small" :loading="loading" @click="refresh">刷新</a-button>
    </header>

    <a-tabs v-model:active-key="tab" type="rounded" size="small" class="benchmark-tabs">
      <a-tab-pane key="public" title="内置公开集" />
      <a-tab-pane key="internal" title="内部集" />
    </a-tabs>

    <div class="benchmark-layout">
      <aside class="benchmark-suites">
        <template v-if="tab === 'public'">
          <div class="subhead"><b>公开测评集</b><small>全局只读 · 版本固定</small></div>
          <div v-for="item in catalog" :key="item.benchmark_id" class="bench-card" :class="{ active: item.benchmark_id === activeBenchmarkId, disabled: !item.runnable }">
            <button type="button" class="bench-card-main" @click="selectBenchmark(item)">
              <b>{{ item.short_name }}</b>
              <small>{{ item.publisher }} · {{ item.license.spdx }}</small>
              <span class="bench-langs">
                <a-tag v-for="(count, lang) in item.languages.verified" :key="lang" size="small">{{ languageName(String(lang)) }} {{ count }}</a-tag>
                <a-tag v-for="lang in item.languages.uncovered" :key="lang" size="small" color="gray">{{ languageName(String(lang)) }} 未覆盖</a-tag>
              </span>
              <span class="bench-meta">
                <a-tag size="small" :color="stateColor(item.state)">{{ item.state_label }}</a-tag>
                <em v-if="item.version_detail">v{{ item.version_detail.version }} · 有效 {{ item.version_detail.case_valid }}</em>
                <em v-else>未准备，无法运行</em>
              </span>
            </button>
            <div class="bench-card-actions">
              <a-button v-if="item.state !== 'ready'" size="mini" :loading="preparing === item.benchmark_id" @click="prepareBenchmark(item)">准备数据</a-button>
              <a-button size="mini" :disabled="!item.runnable" @click="openPreflight(item)">预检</a-button>
            </div>
          </div>
          <a-empty v-if="!catalog.length && !loading" description="目录加载失败，请刷新" />
        </template>

        <template v-else>
          <div class="subhead"><b>内部评测集</b><a-button size="mini" type="primary" @click="showImport = !showImport">{{ showImport ? '收起' : '导入' }}</a-button></div>
          <form v-if="showImport" class="import-form" @submit.prevent="importCases">
            <a-input v-model="name" placeholder="测评集名称" />
            <a-input v-model="dataset" placeholder="来源说明，如线上问题复盘" />
            <a-input v-model="datasetVersion" placeholder="版本或批次" />
            <input type="file" accept=".json,application/json" aria-label="选择测评集 JSON" @change="chooseFile" />
            <a-button type="primary" html-type="submit" :loading="busy" long>导入 JSON</a-button>
            <small>内部集需要已登记仓库、base/head SHA、语言与 issues；空 issues 表示干净样本。公开集已改为内置目录，不再支持上传。</small>
          </form>
          <button v-for="suite in suites" :key="suite.id" type="button" class="suite-item" :class="{ active: suite.id === selectedSuiteId }" @click="selectSuite(suite)">
            <b>{{ suite.name }}</b><small>{{ suite.case_count }} 条样本 · {{ suite.description || '代码审查金标' }}</small>
          </button>
          <a-empty v-if="!suites.length && !loading" description="暂无代码审查测评集" />
        </template>
      </aside>

      <div class="benchmark-main">
        <template v-if="tab === 'public'">
          <div v-if="activeBenchmark" class="run-bar">
            <div>
              <b>{{ activeBenchmark.display_name }}</b>
              <small>{{ activeBenchmark.version_detail ? `版本 ${activeBenchmark.version_detail.version}` : '尚未准备' }} · <a :href="activeBenchmark.homepage" target="_blank" rel="noopener noreferrer">官方来源</a></small>
            </div>
            <a-select v-model="runMode" style="width:120px">
              <a-option value="pilot">试跑（≤10）</a-option>
              <a-option value="full">全量</a-option>
            </a-select>
            <a-checkbox v-model="allowPartial">仅运行可用样本</a-checkbox>
            <a-button type="primary" :disabled="!activeBenchmark.runnable || !preflightResult?.submittable" :loading="busy" @click="runPublic">运行</a-button>
          </div>
          <div v-if="preflightResult" class="preflight">
            <div class="preflight-head">
              <b>预检结果</b>
              <span>可用 {{ preflightResult.available }} / {{ preflightResult.total }} · 快照已缓存 {{ preflightResult.snapshot_cached }} · 待准备 {{ preflightResult.snapshot_pending }}</span>
              <a-tag :color="preflightResult.submittable ? 'green' : 'red'">{{ preflightResult.submittable ? '可提交' : '不可提交' }}</a-tag>
            </div>
            <p v-if="preflightResult.blocked_reason" class="error">{{ preflightResult.blocked_reason }}</p>
            <p class="hint">{{ preflightResult.estimate.basis }} · 预计 {{ preflightResult.estimate.tokens }} tokens</p>
            <div v-if="preflightResult.excluded.length" class="excluded">
              <b>排除清单（{{ preflightResult.excluded.length }}）</b>
              <span v-for="item in preflightResult.excluded.slice(0, 8)" :key="item.case_key">{{ item.case_key }}：{{ item.reasons.join('、') }}</span>
            </div>
          </div>
          <a-empty v-else description="选择测评集后点击「预检」，确认样本与快照可用再运行" />
        </template>

        <template v-else>
          <div v-if="selectedSuite" class="run-bar">
            <div><b>{{ selectedSuite.name }}</b><small>{{ selectedSuite.case_count }} 条样本 · 运行前请确认仓库、模型与访问凭据</small></div>
            <a-select :model-value="selectedRunId" placeholder="选择运行记录" style="width:220px" @change="value => selectRun(String(value))"><a-option v-for="item in runs" :key="item.id" :value="item.id">{{ item.name }} · {{ item.status }}</a-option></a-select>
            <a-button type="primary" :loading="busy" @click="run">运行审查</a-button>
          </div>
          <a-empty v-else description="选择内部评测集后运行" />
        </template>

        <template v-if="report && report.benchmark">
          <div class="report-state">
            <strong>{{ report.benchmark.display_name }} 报告</strong>
            <a-tag :color="report.status === 'completed' ? 'green' : 'blue'">{{ report.status === 'completed' ? '运行结束' : '运行中' }}</a-tag>
            <span>{{ report.benchmark.version }} · {{ report.benchmark.mode === 'pilot' ? '试跑' : '全量' }} · 许可 {{ report.benchmark.license.spdx }}</span>
            <a-button size="mini" @click="downloadReport">导出 JSON</a-button>
            <a-button size="mini" @click="loadReport">刷新报告</a-button>
          </div>
          <div class="report-kpis">
            <div><small>选中 / 排除</small><b>{{ report.benchmark.coverage.selected }} / {{ report.benchmark.coverage.excluded }}</b><em>失败 {{ report.benchmark.coverage.failed }} · 未核验 {{ report.benchmark.coverage.unverified }}</em></div>
            <div><small>缺陷召回</small><b>{{ percent(report.benchmark.summary.recall) }}</b><em>已核验 {{ report.benchmark.summary.n_verified }} 条</em></div>
            <div><small>准确率</small><b>{{ percent(report.benchmark.summary.precision) }}</b><em>{{ report.benchmark.summary.precision_note || `噪声率 ${percent(report.benchmark.summary.noise_rate)}` }}</em></div>
            <div><small>干净样本误报率</small><b>{{ percent(report.benchmark.summary.clean_pr_false_positive_rate) }}</b><em>干净样本 {{ report.benchmark.summary.clean_cases }}</em></div>
          </div>
          <p class="hint">{{ report.benchmark.native_metrics.official_metric }}：官方口径{{ report.benchmark.native_metrics.implemented ? '已实现' : '暂不可用' }}（不填 0，不用平台口径冒充）</p>
          <h4>按语言</h4>
          <div class="language-table">
            <div class="table-title"><span>语言</span><span>成功 / 选中</span><span>金标</span><span>召回</span><span>准确率</span><span>误报率</span><span>行级命中</span></div>
            <div v-for="(item, lang) in report.benchmark.groups.language" :key="lang" class="table-line">
              <b>{{ languageName(String(lang)) }}</b><span>{{ item.n_success }} / {{ item.n_selected }}</span><span>{{ item.gold_issues }}</span>
              <span>{{ percent(item.recall) }}</span><span>{{ percent(item.precision) }}</span><span>{{ percent(item.clean_pr_false_positive_rate) }}</span><span>{{ percent(item.line_precision) }}</span>
            </div>
          </div>
          <div class="case-head"><h4>逐样本证据</h4><a-select v-model="caseFilter" style="width:150px"><a-option value="all">全部</a-option><a-option value="pending">待核验</a-option><a-option value="failed">运行失败</a-option><a-option value="verified">已核验</a-option></a-select></div>
          <div v-for="item in filteredBenchmarkCases" :key="item.case_key" class="case-item">
            <button type="button" class="case-toggle" @click="item.upstream_instance_id && toggleBenchmarkCase(item)">
              <span><b>{{ item.upstream_instance_id }}</b><small>{{ item.repo }} · {{ languageName(item.language || '') }} · {{ item.status }}</small></span>
              <span>金标 {{ item.gold_count }} · 发现 {{ item.findings }} · 命中 {{ item.matches }}</span>
              <a-tag :color="item.verified ? 'green' : item.status === 'failed' ? 'red' : 'orange'">{{ item.verified ? '已核验' : item.status === 'failed' ? '运行失败' : '待核验' }}</a-tag>
            </button>
            <p v-if="item.error" class="error">{{ item.error }}</p>
          </div>
          <a-empty v-if="!filteredBenchmarkCases.length" description="当前筛选条件下没有样本" />
        </template>

        <template v-else-if="report">
          <div class="report-state"><strong>测评报告</strong><a-tag :color="report.status==='completed'?'green':'blue'">{{ report.status==='completed'?'运行结束':'运行中' }}</a-tag><span>{{ report.note }}</span><a-button size="mini" @click="downloadReport">导出 JSON</a-button><a-button size="mini" @click="loadReport">刷新报告</a-button></div>
          <div class="report-kpis">
            <div><small>完成 / 总数</small><b>{{ report.summary.completed }} / {{ report.summary.cases }}</b><em>失败 {{ report.summary.failed }}</em></div>
            <div><small>已核验缺陷召回</small><b>{{ percent(report.summary.verified_recall) }}</b><em>核验 {{ report.summary.verified_cases }} / {{ report.summary.completed }}</em></div>
            <div><small>已核验准确率</small><b>{{ percent(report.summary.verified_precision) }}</b><em>确定缺陷 {{ report.summary.reported_findings }}</em></div>
            <div><small>干净 PR 误报率</small><b>{{ percent(report.summary.clean_pr_false_positive_rate) }}</b><em>干净样本 {{ report.summary.clean_cases }}</em></div>
          </div>
          <div class="report-cost"><span>候选匹配 {{ report.summary.candidate_matches }} / {{ report.summary.gold_issues }} 个金标（非正式评分）</span><span>平均 {{ report.summary.avg_tokens ?? '—' }} Token / PR</span><span>平均 {{ duration(report.summary.avg_duration_ms) }} / PR</span></div>
          <h4>按语言查看</h4>
          <div class="language-table"><div class="table-title"><span>语言</span><span>完成 / 总数</span><span>金标</span><span>召回</span><span>准确率</span><span>干净 PR 误报</span><span>平均 Token</span><span>平均耗时</span></div>
            <div v-for="(item, lang) in report.languages" :key="lang" class="table-line"><b>{{ languageName(String(lang)) }}</b><span>{{ item.completed }} / {{ item.cases }}</span><span>{{ item.gold_issues }}</span><span>{{ percent(item.verified_recall) }}</span><span>{{ percent(item.verified_precision) }}</span><span>{{ percent(item.clean_pr_false_positive_rate) }}</span><span>{{ item.avg_tokens ?? '—' }}</span><span>{{ duration(item.avg_duration_ms) }}</span></div>
          </div>
          <div class="case-head"><h4>逐样本证据</h4><a-select v-model="caseFilter" style="width:150px"><a-option value="all">全部</a-option><a-option value="pending">待核验</a-option><a-option value="failed">运行失败</a-option><a-option value="verified">已核验</a-option></a-select></div>
          <div v-for="item in filteredCases" :key="item.result_id" class="case-item">
            <button type="button" class="case-toggle" @click="toggleCase(item)"><span><b>#{{ item.case_number }} · {{ languageName(item.language) }}</b><small>{{ item.base_sha?.slice(0,8) }} → {{ item.head_sha?.slice(0,8) }} · {{ item.status }}</small></span><span>金标 {{ item.issues.length }} · 确定缺陷 {{ item.findings.length }}</span><a-tag :color="item.adjudication.completed?'green':item.status==='failed'?'red':'orange'">{{ item.adjudication.completed?'已核验':item.status==='failed'?'运行失败':'待核验' }}</a-tag></button>
            <div v-if="expandedCase===item.result_id" class="case-detail">
              <p v-if="item.error && item.status==='failed'" class="error">{{ item.error }}</p>
              <p v-if="item.source_url"><a :href="item.source_url" target="_blank" rel="noopener noreferrer">查看来源 PR</a></p>
              <div v-for="(issue, index) in item.issues" :key="index" class="judgement-line"><div><b>金标 {{ index+1 }} · {{ issue.file || issue.path || '未标注文件' }}{{ issue.line ? `:${issue.line}` : '' }}</b><small>{{ issue.description }}</small></div><a-select v-if="canAdjudicate(item)" :model-value="draftMatches[index]" style="width:190px" @change="value=>setGold(index,String(value))"><a-option value="missed">未发现（漏报）</a-option><a-option v-for="(finding, findingIndex) in item.findings" :key="findingIndex" :value="String(findingIndex)">匹配发现 {{ findingIndex+1 }} · {{ finding.file || '未知文件' }}</a-option></a-select><span v-else>{{ matchedLabel(item,index) }}</span></div>
              <div v-for="(finding, index) in item.findings" :key="index" class="judgement-line"><div><b>发现 {{ index+1 }} · {{ finding.file || '未知文件' }}</b><small>{{ finding.change || finding.evidence || finding.impact || '无摘要' }}</small></div><a-select v-if="canAdjudicate(item) && !usedFindings.includes(index)" :model-value="draftFindings[index]" style="width:190px" @change="value=>draftFindings[index]=String(value)"><a-option value="pending">待判定</a-option><a-option value="valid">有效但金标缺失</a-option><a-option value="false">误报</a-option></a-select><span v-else>{{ findingLabel(item,index) }}</span></div>
              <div class="case-footer"><small>候选匹配 {{ item.candidate_matches.length }} · {{ item.tokens }} Token · {{ duration(item.duration_ms) }}</small><a-button v-if="canAdjudicate(item)" size="small" type="primary" :loading="busy" @click="saveAdjudication(item)">保存人工核验</a-button><a-button v-else-if="item.adjudication.completed&&['completed','degraded'].includes(item.status)" size="small" @click="editingCase=item.result_id;prepareDraft(item)">修改核验</a-button></div>
            </div>
          </div>
          <a-empty v-if="!filteredCases.length" description="当前筛选条件下没有样本" />
        </template>

        <a-empty v-else description="选择测评集并运行后查看报告" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { benchmarkGet as get, benchmarkPost as post } from './service';

interface Issue { file?:string; path?:string; line?:number; description:string }
interface Finding { file?:string; change?:string; evidence?:string; impact?:string }
interface Match { gold_index:number; finding_index:number }
interface Adjudication { completed?:boolean; matches?:Match[]; false_findings?:number[]; valid_findings?:number[] }
interface Case { case_number:number; result_id:string; task_id:string|null; status:string; language:string; source_url:string; base_sha:string; head_sha:string; issues:Issue[]; findings:Finding[]; candidate_matches:Match[]; adjudication:Adjudication; tokens:number; duration_ms:number|null; error:string }
interface Metrics { cases:number; completed:number; failed:number; clean_cases:number; gold_issues:number; reported_findings:number; candidate_matches:number; verified_cases:number; verified_recall:number|null; verified_precision:number|null; clean_pr_false_positive_rate:number|null; avg_tokens:number|null; avg_duration_ms:number|null }
interface GroupMetrics { n_selected:number; n_success:number; n_verified:number; gold_issues:number; recall:number|null; precision:number|null; noise_rate:number|null; line_precision:number|null; clean_pr_false_positive_rate:number|null; clean_cases:number; precision_note?:string }
interface BenchmarkCaseRow { case_key:string; upstream_instance_id:string; repo:string; language:string; gold_count:number; findings:number; matches:number; status:string; verified:boolean; error:string|null }
interface BenchmarkBlock { benchmark_id:string; display_name:string; version:string; mode:string; license:{spdx:string}; homepage:string; coverage:{selected:number;excluded:number;failed:number;unverified:number}; summary:GroupMetrics & {verified_hits?:number}; groups:{language:Record<string,GroupMetrics>}; native_metrics:{official_metric:string;implemented:boolean;value:number|null}; cases:BenchmarkCaseRow[] }
interface Suite { id:string; name:string; description:string; case_count:number }
interface Run { id:string; name:string; status:string }
interface Report { status:string; note:string; summary:Metrics; languages:Record<string,Metrics>; cases:Case[]; benchmark?:BenchmarkBlock }
interface CatalogEntry {
  benchmark_id:string; display_name:string; short_name:string; publisher:string; homepage:string;
  license:{spdx:string}; languages:{verified:Record<string,number>;uncovered:string[]};
  state:string; state_label:string; runnable:boolean;
  version_detail:{version:string;case_valid:number;case_rejected:number;clean_total:number;gold_total:number}|null;
}
interface PreflightResult { total:number; available:number; snapshot_cached:number; snapshot_pending:number; submittable:boolean; blocked_reason:string; estimate:{basis:string;tokens:number}; excluded:{case_key:string;reasons:string[]}[] }

const props = defineProps<{ projectId:number }>();
const tab = ref<'public'|'internal'>('public');
const catalog = ref<CatalogEntry[]>([]);
const activeBenchmarkId = ref('');
const runMode = ref<'pilot'|'full'>('pilot');
const allowPartial = ref(false);
const preflightResult = ref<PreflightResult|null>(null);
const preparing = ref('');
const suites = ref<Suite[]>([]), runs = ref<Run[]>([]), report = ref<Report|null>(null), selectedSuiteId = ref(''), selectedRunId = ref('');
const name = ref(''), dataset = ref(''), datasetVersion = ref(''), file = ref<File|null>(null);
const showImport = ref(false), loading = ref(false), busy = ref(false), caseFilter = ref('all'), expandedCase = ref(''), editingCase = ref('');
const draftMatches = ref<Record<number,string>>({}), draftFindings = ref<Record<number,string>>({});
const percent = (value:number|null|undefined) => value == null ? '待核验' : `${(value*100).toFixed(1)}%`;
const duration = (value:number|null) => value == null ? '—' : `${(value/1000).toFixed(1)} 秒`;
const languageName = (value:string) => ({python:'Python',java:'Java','c++':'C++'}[value] || value || '未标注');
const stateColor = (state:string) => state === 'ready' ? 'green' : state === 'failed' ? 'red' : 'orange';
const filteredCases = computed(() => report.value?.cases.filter(item => caseFilter.value==='all'||(caseFilter.value==='failed'?item.status==='failed':caseFilter.value==='verified'?Boolean(item.adjudication.completed):!item.adjudication.completed&&item.status!=='failed')) || []);
const filteredBenchmarkCases = computed(() => report.value?.benchmark?.cases.filter(item => caseFilter.value==='all'||(caseFilter.value==='failed'?item.status==='failed':caseFilter.value==='verified'?item.verified:!item.verified&&item.status!=='failed')) || []);
const usedFindings = computed(() => Object.values(draftMatches.value).filter(value => value!=='missed').map(Number));
const activeBenchmark = computed(() => catalog.value.find(item => item.benchmark_id === activeBenchmarkId.value) || null);
const selectedSuite = computed(() => suites.value.find(item => item.id === selectedSuiteId.value));
const canAdjudicate = (item:Case) => ['completed','degraded'].includes(item.status) && (!item.adjudication.completed || editingCase.value === item.result_id);

function chooseFile(event:Event){ file.value = (event.target as HTMLInputElement).files?.[0] || null }

async function loadCatalog(){
  try {
    const payload = await get<{benchmarks:CatalogEntry[]}>('/evaluation-benchmarks/');
    catalog.value = payload.benchmarks;
    if (!activeBenchmarkId.value && payload.benchmarks.length) activeBenchmarkId.value = payload.benchmarks[0].benchmark_id;
  } catch { Message.error('加载公开测评集目录失败'); }
}
async function load(){
  loading.value = true;
  try {
    await loadCatalog();
    suites.value = await get<Suite[]>('/evaluation-suites/', { project: props.projectId, task_type: 'code_review' });
    if (selectedSuiteId.value && !suites.value.some(item => item.id === selectedSuiteId.value)) { selectedSuiteId.value = ''; selectedRunId.value = ''; report.value = null; }
    if (selectedSuiteId.value) await loadRuns();
  } catch { Message.error('加载测评集失败'); } finally { loading.value = false; }
}
function refresh(){ void load(); if (activeBenchmarkId.value) void openPreflight({ benchmark_id: activeBenchmarkId.value } as CatalogEntry); }
async function loadRuns(){ if (!selectedSuiteId.value) return; runs.value = await get<Run[]>('/evaluation-runs/', { suite: selectedSuiteId.value }); }
async function selectSuite(suite:Suite){ selectedSuiteId.value = suite.id; selectedRunId.value = ''; report.value = null; try { await loadRuns(); } catch { Message.error('加载运行记录失败'); } }
function selectBenchmark(item:CatalogEntry){ activeBenchmarkId.value = item.benchmark_id; preflightResult.value = null; report.value = null; selectedRunId.value = ''; }
async function prepareBenchmark(item:CatalogEntry){
  preparing.value = item.benchmark_id;
  try { await post(`/evaluation-benchmarks/${item.benchmark_id}/prepare/`, {}); Message.success('准备作业已提交，完成后刷新目录'); await loadCatalog(); }
  catch { Message.error('提交准备失败，需要维护角色或检查网络'); }
  finally { preparing.value = ''; }
}
async function openPreflight(item:CatalogEntry){
  if (!item.benchmark_id) return;
  activeBenchmarkId.value = item.benchmark_id;
  try {
    const languages = Object.keys(catalog.value.find(entry => entry.benchmark_id === item.benchmark_id)?.languages.verified || {}).join(',');
    preflightResult.value = await get<PreflightResult>(`/evaluation-benchmarks/${item.benchmark_id}/preflight/`, { languages });
    if (!preflightResult.value.submittable) Message.warning(preflightResult.value.blocked_reason || '当前不可提交');
  } catch { Message.error('预检失败，请确认数据版本已准备'); }
}
async function runPublic(){
  if (!activeBenchmark.value) return;
  if (runMode.value === 'full' && !window.confirm('全量运行会消耗较多 Token，确认继续？')) return;
  busy.value = true;
  try {
    const languages = Object.keys(activeBenchmark.value.languages.verified || {});
    const result = await post<Run>('/evaluation-runs/run-code-review-public/', {
      project: props.projectId, benchmark_id: activeBenchmark.value.benchmark_id,
      mode: runMode.value, allow_partial: allowPartial.value, languages,
    });
    Message.success('公开集运行已提交');
    selectedRunId.value = result.id;
    await loadCatalog();
    await loadReport();
  } catch (error) { Message.error(error instanceof Error ? error.message : '提交失败，请检查模型配置与预检结果'); }
  finally { busy.value = false; }
}
async function importCases(){
  if (!file.value || !name.value.trim()) { Message.warning('请填写名称并选择 JSON 文件'); return; }
  busy.value = true;
  try {
    const parsed:unknown = JSON.parse(await file.value.text());
    if (typeof parsed !== 'object' || parsed === null || !('cases' in parsed) || !Array.isArray(parsed.cases)) throw new Error('文件缺少 cases 数组');
    await post('/evaluation-suites/import-code-review/', { project: props.projectId, name: name.value, source: 'internal', dataset: dataset.value, dataset_version: datasetVersion.value, cases: parsed.cases });
    Message.success('内部测评集已导入'); showImport.value = false; await load();
  } catch (error) { Message.error(error instanceof Error ? error.message : '导入失败'); }
  finally { busy.value = false; }
}
async function run(){ if (!selectedSuiteId.value) return; busy.value = true; try { const result = await post<Run>('/evaluation-runs/run-code-review/', { suite: selectedSuiteId.value }); Message.success('审查任务已提交'); await loadRuns(); selectRun(result.id); } catch { Message.error('提交失败，请检查仓库、凭据与模型配置'); } finally { busy.value = false; } }
function selectRun(runId:string){ selectedRunId.value = runId; expandedCase.value = ''; void loadReport(); }
async function loadReport(){ if (!selectedRunId.value) return; try { report.value = await get<Report>(`/evaluation-runs/${selectedRunId.value}/code-review-report/`); } catch { Message.error('加载报告失败'); } }
function downloadReport(){ if (!report.value) return; const blob = new Blob([JSON.stringify(report.value, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `code-review-benchmark-${selectedRunId.value}.json`; link.click(); URL.revokeObjectURL(url); }
function toggleBenchmarkCase(item:BenchmarkCaseRow){ expandedCase.value = expandedCase.value === item.case_key ? '' : item.case_key; }
function prepareDraft(item:Case){ draftMatches.value = {}; draftFindings.value = {}; for (let i = 0; i < item.issues.length; i++) draftMatches.value[i] = 'missed'; const matches = item.adjudication.completed ? item.adjudication.matches || [] : item.candidate_matches; for (const match of matches) draftMatches.value[match.gold_index] = String(match.finding_index); for (let i = 0; i < item.findings.length; i++) draftFindings.value[i] = 'pending'; for (const index of item.adjudication.false_findings || []) draftFindings.value[index] = 'false'; for (const index of item.adjudication.valid_findings || []) draftFindings.value[index] = 'valid'; }
function toggleCase(item:Case){ expandedCase.value = expandedCase.value === item.result_id ? '' : item.result_id; editingCase.value = ''; if (expandedCase.value) prepareDraft(item); }
function setGold(index:number, value:string){ if (value !== 'missed' && Object.entries(draftMatches.value).some(([key, current]) => Number(key) !== index && current === value)) { Message.warning('同一发现只能匹配一个金标'); return; } draftMatches.value[index] = value; }
function matchedLabel(item:Case, index:number){ const pair = item.adjudication.matches?.find(match => match.gold_index === index); return pair ? `匹配发现 ${pair.finding_index+1}` : '未发现'; }
function findingLabel(item:Case, index:number){ if (item.adjudication.matches?.some(match => match.finding_index === index)) return '已匹配金标'; if (item.adjudication.valid_findings?.includes(index)) return '有效但金标缺失'; if (item.adjudication.false_findings?.includes(index)) return '误报'; return '待核验'; }
async function saveAdjudication(item:Case){
  const matches = Object.entries(draftMatches.value).filter(([, value]) => value !== 'missed').map(([gold, value]) => ({ gold_index: Number(gold), finding_index: Number(value) }));
  const used = new Set(matches.map(match => match.finding_index));
  const remaining = item.findings.map((_, index) => index).filter(index => !used.has(index));
  if (remaining.some(index => draftFindings.value[index] === 'pending')) { Message.warning('请逐条判定未匹配的确定缺陷'); return; }
  busy.value = true;
  try {
    report.value = await post<Report>(`/evaluation-runs/${selectedRunId.value}/adjudicate-code-review/`, { result_id: item.result_id, matches, false_findings: remaining.filter(index => draftFindings.value[index] === 'false'), valid_findings: remaining.filter(index => draftFindings.value[index] === 'valid') });
    editingCase.value = ''; Message.success('核验结果已保存');
  } catch (error) { Message.error(error instanceof Error ? error.message : '保存核验失败'); }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<style scoped>
.benchmark{margin-top:18px;border-top:1px solid var(--color-border-2);padding-top:18px}.benchmark-head,.subhead,.run-bar,.report-state,.case-head,.case-footer,.preflight-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.benchmark-head h3,.case-head h4,.benchmark-main h4{margin:0;font-size:15px}.benchmark-head p{margin:4px 0 0;color:var(--color-text-3);font-size:12px}.benchmark-tabs{margin-top:12px}.benchmark-layout{display:grid;grid-template-columns:270px minmax(0,1fr);gap:14px;margin-top:10px}.benchmark-suites{padding:12px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.benchmark-main{min-width:0}.subhead{margin-bottom:10px}.subhead small{color:var(--color-text-3);font-size:11px}.suite-item{display:block;width:100%;margin:5px 0;padding:11px;text-align:left;border:1px solid var(--color-border-2);border-radius:7px;background:var(--color-bg-2);cursor:pointer}.suite-item.active{border-color:#165dff;background:rgb(var(--arcoblue-1))}.suite-item b,.suite-item small,.run-bar b,.run-bar small{display:block}.suite-item small,.run-bar small{margin-top:4px;color:var(--color-text-3);font-size:11px}.bench-card{margin:8px 0;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-bg-2);overflow:hidden}.bench-card.active{border-color:#165dff}.bench-card.disabled{opacity:.72}.bench-card-main{display:grid;gap:5px;width:100%;padding:11px;text-align:left;border:0;background:transparent;cursor:pointer}.bench-card-main b{font-size:13px}.bench-card-main small{color:var(--color-text-3);font-size:11px}.bench-langs{display:flex;flex-wrap:wrap;gap:4px}.bench-meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:2px}.bench-meta em{color:var(--color-text-3);font-size:11px;font-style:normal}.bench-card-actions{display:flex;justify-content:flex-end;gap:8px;padding:0 11px 10px}.import-form{display:grid;gap:7px;margin:8px 0 13px}.import-form input[type=file]{max-width:220px;font-size:11px}.import-form small{color:var(--color-text-3);line-height:1.5}.run-bar{flex-wrap:wrap;padding:10px 12px;border:1px solid var(--color-border-2);border-radius:8px}.run-bar>div{flex:1}.preflight{margin-top:10px;padding:12px;border:1px solid var(--color-border-2);border-radius:8px;background:var(--color-fill-1)}.preflight .hint,.preflight .excluded{color:var(--color-text-3);font-size:11px}.preflight .excluded{display:grid;gap:2px;margin-top:6px}.report-state{justify-content:flex-start;flex-wrap:wrap;margin-top:14px}.report-state span{flex:1;color:var(--color-text-3);font-size:11px}.report-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}.report-kpis>div{padding:12px;border:1px solid var(--color-border-2);border-radius:7px}.report-kpis small,.report-kpis b,.report-kpis em{display:block}.report-kpis small,.report-kpis em{color:var(--color-text-3);font-size:11px;font-style:normal}.report-kpis b{margin:7px 0;font-size:21px}.report-cost{display:flex;flex-wrap:wrap;gap:16px;padding:10px 0;color:var(--color-text-3);font-size:12px}.language-table{margin-top:8px;border:1px solid var(--color-border-2);border-radius:8px;overflow:hidden}.table-title,.table-line{display:grid;grid-template-columns:1.1fr .9fr .7fr .8fr .8fr .9fr .9fr;gap:6px;padding:8px 11px;font-size:12px}.table-title{background:var(--color-fill-1);color:var(--color-text-3)}.table-line{border-top:1px solid var(--color-border-2)}.case-head{margin-top:16px}.case-item{margin-top:8px;border:1px solid var(--color-border-2);border-radius:8px;overflow:hidden}.case-toggle{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:10px 12px;border:0;background:var(--color-bg-2);cursor:pointer}.case-toggle b,.case-toggle small{display:block;font-size:12px}.case-toggle small{color:var(--color-text-3);font-size:11px}.case-detail{padding:10px 12px;border-top:1px solid var(--color-border-2);background:var(--color-fill-1)}.judgement-line{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px dashed var(--color-border-2)}.judgement-line b,.judgement-line small{display:block;font-size:12px}.judgement-line small{color:var(--color-text-3);font-size:11px}.error{padding:8px 11px;color:#cb2634;font-size:12px}.hint{color:var(--color-text-3);font-size:11px}
</style>
