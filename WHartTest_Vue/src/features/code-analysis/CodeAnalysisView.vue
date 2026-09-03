<template>
  <div class="analysis-page">
    <header class="hero">
      <div>
        <div class="eyebrow">QUALITY INTELLIGENCE</div>
        <h1>代码审查</h1>
        <p>只读分析 GitLab 代码变化，识别风险并转化为可执行的测试需求。</p>
      </div>
      <div class="hero-actions">
        <a-button @click="configVisible = true"><template #icon><icon-settings /></template>GitLab 配置</a-button>
        <a-button type="primary" @click="openCreate"><template #icon><icon-plus /></template>新建分析</a-button>
      </div>
    </header>

    <section class="metric-grid">
      <div class="metric"><span>分析任务</span><strong>{{ tasks.length }}</strong></div>
      <div class="metric danger"><span>高风险</span><strong>{{ highRiskTotal }}</strong></div>
      <div class="metric"><span>测试需求点</span><strong>{{ testPointTotal }}</strong></div>
      <div class="metric"><span>完成率</span><strong>{{ completionRate }}%</strong></div>
    </section>

    <section class="content-card">
      <div class="section-head"><div><h2>审查记录</h2><p>结果在项目内共享，GitLab Token 始终按用户隔离。</p></div><a-button type="text" @click="() => loadTasks()"><icon-refresh /> 刷新</a-button></div>
      <a-empty v-if="!loading && !tasks.length" description="还没有代码审查任务" />
      <a-spin :loading="loading" style="width:100%">
        <div v-for="task in tasks" :key="task.id" class="task-row" @click="selectedTask = task">
          <div class="task-mark" :class="task.status"></div>
          <div class="task-main"><div class="task-title">{{ task.title || task.repository_name }}</div><div class="task-meta">{{ task.repository_name }} · {{ sourceLabel(task) }} · {{ task.creator_name }}</div></div>
          <div class="task-score"><span>风险</span><b>{{ task.change_report?.summary?.risk_count || 0 }}</b></div>
          <div class="task-score"><span>测试点</span><b>{{ task.test_report?.summary?.test_point_count || 0 }}</b></div>
          <a-tag :color="statusColor(task.status)">{{ statusLabel(task.status) }}</a-tag>
          <a-progress :percent="task.progress / 100" :show-text="false" size="small" style="width:90px" />
          <a-button v-if="isRunning(task)" type="text" status="warning" @click.stop="cancelAnalysis(task)">取消</a-button>
          <a-button type="text" status="danger" @click.stop="removeTask(task)"><icon-delete /></a-button>
        </div>
      </a-spin>
    </section>

    <a-drawer :visible="!!selectedTask" :width="780" unmount-on-close @cancel="selectedTask = null">
      <template #title>{{ selectedTask?.title || '分析详情' }}</template>
      <div v-if="selectedTask" class="iteration-overview">
        <div class="overview-title"><div><span>本次迭代分析</span><h2>{{ iterationConclusion(selectedTask) }}</h2></div><a-tag :color="selectedTask.change_report?.summary?.high_risk_count ? 'red' : 'green'">{{ selectedTask.change_report?.summary?.high_risk_count ? '建议重点回归' : '常规回归' }}</a-tag></div>
        <div class="overview-numbers">
          <div><b>{{ selectedTask.change_report?.summary?.changed_files || 0 }}</b><span>变更文件</span></div>
          <div class="addition"><b>+{{ selectedTask.change_report?.summary?.additions || 0 }}</b><span>新增行</span></div>
          <div class="deletion"><b>-{{ selectedTask.change_report?.summary?.deletions || 0 }}</b><span>删除行</span></div>
          <div><b>{{ selectedTask.change_report?.summary?.changed_lines || 0 }}</b><span>总变更行</span></div>
          <div class="danger"><b>{{ selectedTask.change_report?.summary?.high_risk_count || 0 }}</b><span>高风险</span></div>
          <div><b>{{ selectedTask.change_report?.summary?.risk_count || 0 }}</b><span>风险提示</span></div>
          <div><b>{{ selectedTask.test_report?.summary?.test_point_count || 0 }}</b><span>测试需求点</span></div>
        </div>
        <div class="overview-focus" v-if="selectedTask.change_report?.impact_summary?.length"><b>重点影响</b><span v-for="item in selectedTask.change_report.impact_summary.slice(0,3)" :key="item">{{ item }}</span></div>
        <div class="overview-files" v-if="selectedTask.change_report?.files?.length">
          <b>变更文件</b>
          <div v-for="file in selectedTask.change_report.files.slice(0,5)" :key="file.path"><code>{{ file.path }}</code><span class="line-stat"><i>+{{ file.additions || 0 }}</i><em>-{{ file.deletions || 0 }}</em></span></div>
          <small v-if="selectedTask.change_report.files.length > 5">其余 {{ selectedTask.change_report.files.length - 5 }} 个文件请在报告中查看</small>
        </div>
      </div>
      <a-collapse v-if="selectedTask" :bordered="false" class="input-collapse">
        <a-collapse-item key="input" header="分析输入与运行信息">
      <div class="analysis-context">
        <div class="context-head">
          <div><span class="context-kicker">ANALYSIS INPUT</span><h3>{{ selectedTask.repository_name }}</h3></div>
          <a-tag color="arcoblue">{{ modeLabel(selectedTask.mode) }}</a-tag>
        </div>
        <div class="context-grid">
          <div><span>分析来源</span><b>{{ sourceLabel(selectedTask) }}</b></div>
          <div><span>提交范围</span><b class="mono">{{ shortSha(selectedTask.base_sha) }} → {{ shortSha(selectedTask.head_sha) }}</b></div>
          <div><span>发起人</span><b>{{ selectedTask.creator_name }}</b></div>
          <div><span>完成时间</span><b>{{ formatTime(selectedTask.completed_at) }}</b></div>
          <div><span>机器分析覆盖</span><b>{{ selectedTask.machine_coverage }}%</b></div>
          <div><span>AI分析覆盖</span><b>{{ selectedTask.ai_coverage }}%</b></div>
          <div class="wide"><span>AI用量</span><b>{{ selectedTask.token_usage || 0 }} Token <a-tooltip content="本次AI分析读取的输入内容与生成内容的计量单位，用于观察上下文规模和推理成本，不代表分析质量。"><icon-question-circle class="help-icon" /></a-tooltip></b></div>
        </div>
      </div>
        </a-collapse-item>
      </a-collapse>
      <a-tabs v-if="selectedTask" default-active-key="change" class="report-tabs">
        <a-tab-pane key="change" title="代码审查报告">
          <div class="report-toolbar"><div><h3>风险与影响</h3><p>确定性规则与 AI 风险提示的融合结果</p></div><a-button type="outline" @click="download(selectedTask, 'change')"><template #icon><icon-download /></template>下载报告</a-button></div>
          <div class="report-summary">
            <span>风险总数 <b>{{ selectedTask.change_report?.summary?.risk_count || 0 }}</b></span>
            <span class="risk-number">高风险 <b>{{ selectedTask.change_report?.summary?.high_risk_count || 0 }}</b></span>
          </div>
          <a-alert v-if="selectedTask.change_report?.analysis_note">{{ selectedTask.change_report.analysis_note }}</a-alert>
          <div v-for="item in selectedTask.change_report?.findings || []" :key="item.key" class="finding">
            <div class="finding-head"><div><a-tag :color="severityColor(item.severity)">{{ severityLabel(item.severity) }}</a-tag><strong>{{ item.change }}</strong></div><span class="confidence">{{ sourceName(item.source) }} · {{ confidenceText(item.confidence) }}</span></div>
            <p>{{ item.file }}</p><code>{{ item.evidence }}</code><div class="impact">影响：{{ item.impact }}</div>
          </div>
          <a-empty v-if="!selectedTask.change_report?.findings?.length" description="未发现确定性风险" />
        </a-tab-pane>
        <a-tab-pane key="test" title="测试分析报告">
          <div class="report-toolbar"><div><h3>测试需求与回归</h3><p>测试点仍为草稿，确认后再转正式用例</p></div><a-button type="outline" @click="download(selectedTask, 'test')"><template #icon><icon-download /></template>下载报告</a-button></div>
          <div class="report-summary"><span>测试需求点 <b>{{ selectedTask.test_report?.summary?.test_point_count || 0 }}</b></span><span class="risk-number">高优先级 <b>{{ selectedTask.test_report?.summary?.high_priority_count || 0 }}</b></span></div>
          <div v-for="item in selectedTask.test_report?.test_requirements || []" :key="item.id" class="test-point">
            <div class="finding-head"><div><a-tag color="arcoblue">{{ item.test_type }}</a-tag><strong>{{ item.title }}</strong></div><a-tag :color="item.priority === 'high' ? 'red' : 'orange'">{{ priorityLabel(item.priority) }}</a-tag></div>
            <p><b>测试目标：</b>{{ item.objective }}</p><div class="expected"><b>预期结果：</b>{{ item.expected_result }}</div><div class="source-key">来源：{{ item.source_finding_key || '综合分析' }}</div>
          </div>
          <a-empty v-if="!selectedTask.test_report?.test_requirements?.length" description="暂无测试需求点" />
          <div class="two-column-sections">
            <div class="report-section"><h3>回归建议</h3><ul v-if="selectedTask.test_report?.regression_suggestions?.length"><li v-for="item in selectedTask.test_report.regression_suggestions" :key="item">{{ item }}</li></ul><a-empty v-else description="暂无回归建议" /></div>
            <div class="report-section gap"><h3>覆盖缺口</h3><ul v-if="selectedTask.test_report?.coverage_gaps?.length"><li v-for="item in selectedTask.test_report.coverage_gaps" :key="item">{{ item }}</li></ul><a-empty v-else description="未记录覆盖缺口" /></div>
          </div>
        </a-tab-pane>
      </a-tabs>
    </a-drawer>

    <a-modal v-model:visible="createVisible" title="新建代码审查" :ok-loading="submitting" @ok="submitTask">
      <a-form :model="form" layout="vertical">
        <a-form-item label="代码仓库" required><a-select v-model="form.repository" @change="onRepositoryChange"><a-option v-for="r in repositories" :key="r.id" :value="r.id">{{ r.name }} · {{ r.path_with_namespace }}</a-option></a-select></a-form-item>
        <a-form-item label="分析来源"><a-radio-group v-model="form.source_type" type="button"><a-radio value="merge_request">Merge Request</a-radio><a-radio value="commits">两个 Commit</a-radio></a-radio-group></a-form-item>
        <a-form-item v-if="form.source_type === 'merge_request'" label="Merge Request" required><a-select v-model="form.merge_request_iid" :loading="mrLoading"><a-option v-for="mr in mergeRequests" :key="mr.iid" :value="mr.iid">!{{ mr.iid }} {{ mr.title }}</a-option></a-select></a-form-item>
        <template v-else><a-form-item label="基准 Commit" required><a-input v-model="form.base_sha" /></a-form-item><a-form-item label="目标 Commit" required><a-input v-model="form.head_sha" /></a-form-item></template>
        <a-form-item label="分析模式"><a-radio-group v-model="form.mode" type="button"><a-radio value="quick">快速</a-radio><a-radio value="standard">标准</a-radio><a-radio value="deep">深度</a-radio></a-radio-group></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="configVisible" title="GitLab 只读配置" :footer="false" width="680px">
      <a-tabs>
        <a-tab-pane key="connection" title="GitLab连接">
          <a-form :model="connectionForm" layout="vertical"><a-form-item label="名称"><a-input v-model="connectionForm.name" /></a-form-item><a-form-item label="GitLab地址"><a-input v-model="connectionForm.base_url" placeholder="https://gitlab.example.com" /></a-form-item><a-button type="primary" @click="addConnection">保存连接</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="repository" title="项目仓库">
          <a-form :model="repoForm" layout="vertical"><a-form-item label="GitLab连接"><a-select v-model="repoForm.connection"><a-option v-for="c in connections" :key="c.id" :value="c.id">{{ c.name }}</a-option></a-select></a-form-item><a-form-item label="GitLab项目ID"><a-input v-model="repoForm.gitlab_project_id" /></a-form-item><a-form-item label="仓库名称"><a-input v-model="repoForm.name" /></a-form-item><a-form-item label="项目路径"><a-input v-model="repoForm.path_with_namespace" /></a-form-item><a-button type="primary" @click="addRepository">关联仓库</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="token" title="我的Token">
          <a-form :model="tokenForm" layout="vertical"><a-form-item label="GitLab连接"><a-select v-model="tokenForm.connection"><a-option v-for="c in connections" :key="c.id" :value="c.id">{{ c.name }}</a-option></a-select></a-form-item><a-form-item label="Personal Access Token"><a-input-password v-model="tokenForm.token" placeholder="仅用于当前用户只读访问" /></a-form-item><a-button type="primary" @click="saveToken">加密保存</a-button></a-form>
        </a-tab-pane>
      </a-tabs>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { IconDelete, IconDownload, IconFile, IconPlus, IconQuestionCircle, IconRefresh, IconSettings } from '@arco-design/web-vue/es/icon';
import { useProjectStore } from '@/store/projectStore';
import type { AnalysisTask, CodeRepository, GitLabConnection, MergeRequest } from './types';
import * as api from './service';

const projectStore = useProjectStore();
const tasks = ref<AnalysisTask[]>([]), repositories = ref<CodeRepository[]>([]), connections = ref<GitLabConnection[]>([]), mergeRequests = ref<MergeRequest[]>([]);
const loading = ref(false), submitting = ref(false), mrLoading = ref(false), createVisible = ref(false), configVisible = ref(false);
const selectedTask = ref<AnalysisTask|null>(null);
const form = reactive<any>({ repository:null, source_type:'merge_request', merge_request_iid:null, base_sha:'', head_sha:'', mode:'standard' });
const connectionForm = reactive<any>({ name:'内网 GitLab', base_url:'', verify_ssl:true, is_active:true });
const repoForm = reactive<any>({ connection:null, gitlab_project_id:'', name:'', path_with_namespace:'', default_branch:'main' });
const tokenForm = reactive<any>({ connection:null, token:'' });
const highRiskTotal = computed(() => tasks.value.reduce((n,t) => n + (t.change_report?.summary?.high_risk_count || 0), 0));
const testPointTotal = computed(() => tasks.value.reduce((n,t) => n + (t.test_report?.summary?.test_point_count || 0), 0));
const completionRate = computed(() => tasks.value.length ? Math.round(tasks.value.filter(t => t.status === 'completed').length / tasks.value.length * 100) : 0);
const statusLabel = (s:string) => ({pending:'待执行',fetching:'获取代码中',machine_analyzing:'机器分析中',ai_analyzing:'AI分析中',generating_tests:'生成测试报告中',completed:'已完成',partial:'部分完成',failed:'失败',cancelled:'已取消'} as any)[s] || s;
const statusColor = (s:string) => ({completed:'green',failed:'red',partial:'orange',cancelled:'gray'} as any)[s] || 'arcoblue';
const sourceLabel = (t:AnalysisTask) => t.source_type === 'merge_request' ? `MR !${t.merge_request_iid}` : `${t.base_sha.slice(0,7)} → ${t.head_sha.slice(0,7)}`;
const shortSha = (sha:string) => sha ? sha.slice(0, 12) : '-';
const modeLabel = (mode:string) => ({quick:'快速模式',standard:'标准模式',deep:'深度模式'} as any)[mode] || mode;
const formatTime = (value?:string) => value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '-';
const severityColor = (value:string) => ({high:'red',medium:'orange',low:'blue'} as any)[value] || 'gray';
const severityLabel = (value:string) => ({high:'高风险',medium:'中风险',low:'低风险'} as any)[value] || value;
const sourceName = (value:string) => ({machine_rule:'机器规则',static_scan:'静态分析',ai_analysis:'AI提示',machine_ai:'机器+AI'} as any)[value] || value;
const confidenceText = (value:number) => Number.isFinite(value) ? `置信度 ${Math.round(value * 100)}%` : '置信度未知';
const priorityLabel = (value:string) => ({high:'高优先级',medium:'中优先级',low:'低优先级'} as any)[value] || value;
const terminalStatuses = new Set(['completed','partial','failed','cancelled']);
const isRunning = (task:AnalysisTask) => !terminalStatuses.has(task.status);
const iterationConclusion = (task:AnalysisTask) => {
  const high = task.change_report?.summary?.high_risk_count || 0;
  const risks = task.change_report?.summary?.risk_count || 0;
  if (high) return `发现 ${high} 项高风险变化，测试应优先覆盖关键影响链路`;
  if (risks) return `发现 ${risks} 项风险提示，建议结合影响范围执行回归`;
  return '未发现明显高风险变化，建议完成常规变更回归';
};
async function download(task:AnalysisTask,type:'change'|'test'){try{await api.downloadReport(task.id,type);Message.success('报告下载已开始')}catch(e:any){Message.error(e.message||'报告下载失败')}}
async function loadBase(){ const id=projectStore.currentProjectId; if(!id)return; [connections.value,repositories.value]=await Promise.all([api.getConnections(),api.getRepositories(id)]); }
async function loadTasks(silent=false){ const id=projectStore.currentProjectId;if(!id)return;if(!silent)loading.value=true;try{tasks.value=await api.getTasks(id);if(selectedTask.value){selectedTask.value=tasks.value.find(task=>task.id===selectedTask.value?.id)||null}}catch(e:any){if(!silent)Message.error(e.message)}finally{if(!silent)loading.value=false} }
async function openCreate(){ await loadBase(); if(!repositories.value.length){configVisible.value=true;Message.info('请先完成GitLab连接、仓库和个人Token配置');return} createVisible.value=true; }
async function onRepositoryChange(v:any){mergeRequests.value=[];form.merge_request_iid=null;if(!v)return;mrLoading.value=true;try{mergeRequests.value=await api.getMergeRequests(Number(v))}catch(e:any){Message.error(`读取MR失败：${e.message}`)}finally{mrLoading.value=false}}
async function submitTask(){ const project=projectStore.currentProjectId;if(!project)return;submitting.value=true;try{const task=await api.createTask({...form,project});createVisible.value=false;await api.runTask(task.id);Message.success('分析任务已提交，可在列表查看进度');await loadTasks()}catch(e:any){Message.error(e.message||'提交分析失败');await loadTasks()}finally{submitting.value=false} }
async function addConnection(){try{await api.createConnection(connectionForm);connections.value=await api.getConnections();Message.success('连接已保存')}catch(e:any){Message.error(e.message)} }
async function addRepository(){const project=projectStore.currentProjectId;if(!project)return;try{await api.createRepository({...repoForm,project});repositories.value=await api.getRepositories(project);Message.success('仓库已关联')}catch(e:any){Message.error(e.message)} }
async function saveToken(){const project=projectStore.currentProjectId;if(!project)return;try{await api.saveCredential({...tokenForm,project});tokenForm.token='';Message.success('Token已加密保存')}catch(e:any){Message.error(e.message)} }
async function cancelAnalysis(task:AnalysisTask){try{await api.cancelTask(task.id);Message.success('已取消分析任务');await loadTasks(true)}catch(e:any){Message.error(e.message||'取消失败')}}
async function removeTask(task:AnalysisTask){Modal.warning({title:'清空本次源码分析？',content:'Diff、源码证据、风险和测试分析报告将立即删除且无法恢复。正式测试用例不受影响。',hideCancel:false,onOk:async()=>{await api.deleteTask(task.id);if(selectedTask.value?.id===task.id)selectedTask.value=null;await loadTasks();}})}
watch(()=>projectStore.currentProjectId,async()=>{await loadBase();await loadTasks()});
let pollTimer:number|undefined;
onMounted(async()=>{await loadBase();await loadTasks();pollTimer=window.setInterval(()=>{if(tasks.value.some(isRunning))loadTasks(true)},2000)});
onBeforeUnmount(()=>{if(pollTimer)window.clearInterval(pollTimer)});
</script>

<style scoped>
.analysis-page{padding:28px;min-height:100%;background:#f5f7fa;color:#1d2939}.hero{display:flex;justify-content:space-between;align-items:flex-end;padding:30px 34px;border-radius:18px;background:linear-gradient(125deg,#102a43,#176b87 62%,#1b8f8a);color:white;box-shadow:0 14px 40px rgb(16 42 67 / 18%)}.eyebrow{font-size:11px;letter-spacing:.16em;color:#9fe1dd}.hero h1{margin:8px 0 6px;font-size:28px}.hero p{margin:0;color:#d8edf0}.hero-actions{display:flex;gap:10px}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.metric{padding:18px 20px;background:white;border:1px solid #e5eaf0;border-radius:12px}.metric span{display:block;color:#718096;font-size:13px}.metric strong{display:block;margin-top:5px;font-size:26px}.metric.danger strong{color:#d9485f}.content-card{padding:22px;background:white;border:1px solid #e4e9f0;border-radius:14px}.section-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.section-head h2{margin:0;font-size:17px}.section-head p{margin:4px 0 0;color:#8792a2;font-size:12px}.task-row{display:grid;grid-template-columns:4px minmax(240px,1fr) 70px 70px 90px 90px 48px 36px;gap:12px;align-items:center;padding:15px 6px;border-top:1px solid #edf0f4;cursor:pointer}.task-row:hover{background:#f8fafc}.task-mark{height:34px;border-radius:4px;background:#2d8cf0}.task-mark.completed{background:#16a085}.task-mark.failed{background:#d9485f}.task-title{font-weight:600}.task-meta{margin-top:4px;color:#8b96a5;font-size:12px}.task-score span{display:block;color:#8b96a5;font-size:11px}.task-score b{font-size:17px}.iteration-overview{padding:20px;border-radius:14px;background:linear-gradient(135deg,#102f46,#176b6f);color:#fff;box-shadow:0 10px 24px rgb(16 47 70 / 16%)}.overview-title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.overview-title span{font-size:11px;letter-spacing:.1em;color:#9bd9d4}.overview-title h2{max-width:560px;margin:6px 0 0;font-size:20px;line-height:1.45}.overview-numbers{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:18px}.overview-numbers>div{padding:10px 12px;border-radius:9px;background:rgb(255 255 255 / 9%)}.overview-numbers b,.overview-numbers span{display:block}.overview-numbers b{font-size:20px}.overview-numbers span{margin-top:2px;color:#c5dadd;font-size:11px}.overview-numbers .danger b,.overview-numbers .deletion b{color:#ffb0a8}.overview-numbers .addition b{color:#8ee6b5}.overview-focus{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px}.overview-focus b{margin-right:3px;font-size:12px}.overview-focus span{padding:5px 9px;border-radius:999px;background:rgb(255 255 255 / 12%);font-size:11px}.overview-files{margin-top:14px;padding-top:12px;border-top:1px solid rgb(255 255 255 / 14%)}.overview-files>b{display:block;margin-bottom:7px;font-size:12px}.overview-files>div{display:flex;justify-content:space-between;gap:12px;padding:5px 0;color:#e3f0f1}.overview-files code{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.line-stat{display:flex;gap:8px;white-space:nowrap}.line-stat i{color:#8ee6b5;font-style:normal}.line-stat em{color:#ffb0a8;font-style:normal}.overview-files small{display:block;margin-top:5px;color:#a9c5c8}.input-collapse{margin:10px 0 4px;background:transparent}.analysis-context{padding:14px;border:1px solid #dbe7ee;border-radius:10px;background:#f8fafc}.context-head,.report-toolbar,.finding-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.context-head h3,.report-toolbar h3{margin:3px 0 0}.context-kicker{font-size:10px;letter-spacing:.14em;color:#16827d}.context-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-top:16px}.context-grid span{display:block;color:#8591a2;font-size:11px}.context-grid b{display:block;margin-top:3px;font-size:13px}.context-grid .wide{grid-column:1/-1}.help-icon{margin-left:3px;color:#758397}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.report-tabs{margin-top:4px}.report-toolbar{align-items:center;margin:8px 0 14px}.report-toolbar p{margin:4px 0 0;color:#8893a2;font-size:12px}.report-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}.report-summary span{padding:10px 12px;border-radius:8px;background:#f3f6f8;color:#718096;font-size:11px}.report-summary b{display:block;margin-top:2px;color:#223143;font-size:18px}.report-summary .risk-number b{color:#d9485f}.finding,.test-point{margin-top:12px;padding:15px;border:1px solid #e7ebf0;border-radius:10px;background:#fff}.finding strong,.test-point strong{margin-left:8px}.finding p{color:#667085}.finding code{display:block;padding:10px;overflow:auto;background:#f7f8fa;border-radius:6px}.confidence{white-space:nowrap;color:#8792a2;font-size:11px}.impact,.expected{margin-top:10px;color:#526273}.source-key{margin-top:9px;color:#98a2b3;font-size:11px}.report-section{margin-top:18px;padding:16px;border:1px solid #e7ebf0;border-radius:10px}.report-section h3{margin:0 0 12px;font-size:14px}.chip-list{display:flex;flex-wrap:wrap;gap:8px}.chip-list span{padding:6px 10px;border-radius:999px;background:#eaf5f4;color:#176d69;font-size:12px}.file-list{display:flex;flex-direction:column;gap:8px}.file-list>div{display:flex;align-items:center;gap:8px}.file-list code{overflow:hidden;text-overflow:ellipsis;color:#526273}.two-column-sections{display:grid;grid-template-columns:1fr 1fr;gap:12px}.report-section ul{margin:0;padding-left:20px;color:#526273}.report-section li+li{margin-top:7px}.report-section.gap{border-color:#f0dfca;background:#fffaf3}.mode-description{font-size:12px;color:#8b96a5}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.task-row{grid-template-columns:4px 1fr 80px}.task-score,.task-row :deep(.arco-progress){display:none}.context-grid,.two-column-sections{grid-template-columns:1fr}}
</style>
