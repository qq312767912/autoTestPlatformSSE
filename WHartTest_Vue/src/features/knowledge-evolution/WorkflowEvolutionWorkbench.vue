<template>
  <section class="fw-workbench">
    <header class="fw-command">
      <div>
        <h2>Agent × Skill 进化工作台</h2>
        <p>从受控执行到人工确认、归因、候选评测与上线，所有状态以服务端记录为准。</p>
      </div>
      <div class="command-actions">
        <a-tag color="green">受控模式</a-tag>
        <a-button :loading="loading" @click="loadAll()"><template #icon><icon-refresh/></template>刷新</a-button>
        <a-button type="primary" @click="$emit('start-workflow')"><template #icon><icon-plus/></template>发起流程</a-button>
      </div>
    </header>

    <div class="fw-rail-layout">
      <aside class="flow-index">
        <header><b>运行批次</b><span>{{ cockpit.workflows.length }}</span></header>
        <div v-for="flow in cockpit.workflows" :key="flow.workflow_id" class="flow-batch" :class="{active:flow.workflow_id===selectedFlowId}">
          <button class="flow-select" type="button" @click="selectFlow(flow.workflow_id)">
            <small>{{ flow.stage_template==='legacy'?'历史批次':'当前批次' }}</small>
            <b>{{ flow.workflow_id }}</b>
            <i><u :style="{width:`${flowPercent(flow)}%`}"/></i>
            <span>{{ passedCount(flow) }}/{{ flow.stages.length }} 阶段 · {{ flow.completed?'已完成':'进行中' }}</span>
          </button>
          <a-button class="flow-delete" type="text" status="danger" size="mini" :loading="actionBusy===`delete:${flow.workflow_id}`" title="删除批次" @click.stop="requestDeleteFlow(flow)"><template #icon><icon-delete/></template></a-button>
        </div>
        <a-empty v-if="!cockpit.workflows.length" description="尚未发起质量飞轮流程"/>
      </aside>

      <main class="flow-main">
        <template v-if="activeFlow">
          <div class="stage-rail">
            <button v-for="(stage,index) in activeFlow.stages" :key="stage.stage" type="button" :class="[stageState(stage),{active:stage.stage===selectedStageKey}]" @click="selectStage(stage.stage)">
              <i>{{ String(index+1).padStart(2,'0') }}</i>
              <span><b>{{ stageLabel(stage.stage) }}</b><small>{{ statusLabel(stage.status) }}</small></span>
              <em/>
            </button>
          </div>

          <section class="stage-console">
            <div class="stage-title">
              <div><span>当前阶段</span><h3>{{ stageLabel(selectedStageKey) }}</h3></div>
              <div class="stage-actions">
                <a-button v-if="activeStage?.output_id" @click="$emit('open-stage-output',activeFlow.workflow_id,selectedStageKey)">深度查看</a-button>
                <a-button v-if="!activeStage?.output_id" type="primary" :loading="actionBusy==='execute'" :disabled="!['ready','running'].includes(activeStage?.status||'')" @click="executeCurrent"><template #icon><icon-play-arrow/></template>跳转到 Agent 执行</a-button>
                <a-button v-if="activeStage?.output_id && activeStage.confirmable" type="primary" status="success" :loading="actionBusy==='confirm'" @click="confirmCurrent">确认进入下一阶段</a-button>
              </div>
            </div>
            <div class="stage-facts">
              <article><small>门禁状态</small><b>{{ statusLabel(activeStage?.status||'') }}</b><span>{{ activeStage?.reason||'暂无门禁说明' }}</span></article>
              <article><small>锁定 Skill</small><b>{{ activeStage?.skill_name||'未纳管' }}</b><span>{{ activeStage?.skill_version||'无版本' }}</span></article>
              <article><small>正式产出</small><b>{{ activeStage?.output_id?'已登记':'尚未登记' }}</b><span>{{ activeStage?.task_id||'—' }}</span></article>
              <article><small>最新执行</small><b>{{ attempt?.status||'无记录' }}</b><span>{{ attempt?.requested_by_username||'—' }}</span></article>
            </div>
          </section>

          <nav class="work-zones" aria-label="工作台分区">
            <button v-for="zone in zones" :key="zone.key" type="button" :class="{active:activeZone===zone.key}" @click="activeZone=zone.key">
              <component :is="zone.icon"/><span>{{ zone.label }}</span><small>{{ zone.hint }}</small>
            </button>
          </nav>

          <section class="zone-body">
            <template v-if="activeZone==='overview'">
              <div class="overview-grid">
                <section>
                  <div class="zone-heading"><div><span>实时执行轨迹</span><h3>执行状态摘要</h3></div><a-tag :color="attempt?.status==='failed'?'red':'arcoblue'">{{ attempt?.status||'未执行' }}</a-tag></div>
                  <div v-if="trace" class="metric-strip">
                    <article><b>{{ trace.summary.steps.total }}</b><small>步骤</small></article><article><b>{{ trace.summary.groups.retrieval?.evidence_count||0 }}</b><small>知识证据</small></article><article><b>{{ trace.summary.groups.tool?.count||0 }}</b><small>工具调用</small></article><article><b>{{ trace.summary.steps.failed }}</b><small>失败</small></article>
                  </div>
                  <a-empty v-else description="派发 Agent 后展示实时执行摘要"/>
                </section>
                <section>
                  <div class="zone-heading"><div><span>人工门禁</span><h3>人工确认状态</h3></div><a-tag :color="review?.latest_review?.evolvable?'green':'orange'">{{ review?.latest_review?.evolvable?'可进化':'待完成' }}</a-tag></div>
                  <template v-if="review?.latest_review"><p class="lead">{{ review.latest_review.state==='submitted'?'确认稿已正式提交':'确认稿草稿已保存' }}</p><p>已审核 {{ review.latest_review.statistics['已审核数']||0 }} 项，留空 {{ review.latest_review.blank_count }} 项。</p></template>
                  <a-empty v-else description="尚未上传人工确认稿"/>
                </section>
                <section class="span-two">
                  <div class="zone-heading"><div><span>下一步操作</span><h3>系统建议下一步</h3></div></div>
                  <div class="next-action"><i>{{ nextAction.index }}</i><div><b>{{ nextAction.title }}</b><p>{{ nextAction.detail }}</p></div><a-button type="primary" @click="goNextAction">{{ nextAction.button }}</a-button></div>
                </section>
                <section class="span-two operations-desk">
                  <div class="zone-heading"><div><h3>旁路产出纳管与登记补偿</h3><p>业务产物成功不等于飞轮登记成功；两类状态分别呈现、分别处理。</p></div><a-button v-if="registration.open" status="warning" :loading="actionBusy==='retry-registration'" @click="retryRegistrations">重试 {{ registration.open }} 条</a-button></div>
                  <div class="ops-grid">
                    <div><header><b>待纳管旁路产出</b><span>{{ unmanagedOutputs.length }}</span></header><article v-for="output in unmanagedOutputs.slice(0,5)" :key="output.id"><div><b>{{ output.task_id||output.id.slice(0,8) }}</b><small>{{ stageLabel(output.task_type) }} · {{ output.created_at.slice(0,16).replace('T',' ') }}</small></div><a-button size="mini" :loading="actionBusy===`admit:${output.id}`" @click="admitOutput(output)">纳入当前流程</a-button></article><a-empty v-if="!unmanagedOutputs.length" description="没有待纳管旁路产出"/></div>
                    <div><header><b>飞轮登记失败</b><span :class="{danger:registration.dead_letter}">{{ registration.open }} 未决 / {{ registration.dead_letter }} 死信</span></header><article v-for="item in registration.items||[]" :key="item.id"><div><b>{{ stageLabel(item.stage) }} · {{ item.status }}</b><small>{{ item.last_error||`已尝试 ${item.attempts} 次` }}</small></div><a-tag :color="item.status==='dead_letter'?'red':'orange'">{{ item.attempts }} 次</a-tag></article><a-empty v-if="!registration.items?.length" description="没有登记失败"/></div>
                  </div>
                </section>
              </div>
            </template>

            <template v-else-if="activeZone==='trace'">
              <div class="zone-heading"><div><span>证据图谱</span><h3>Agent 执行证据链</h3><p>展示显式步骤、工具和知识原句；不采集模型隐藏思维链。</p></div></div>
              <div v-if="trace?.spans.length" class="evidence-lane">
                <article v-for="span in trace.spans" :key="span.id" :class="span.status">
                  <div class="lane-node"><i>{{ span.sequence }}</i><em/></div>
                  <div><header><b>{{ trace.summary.labels[span.group]||span.step_type }}</b><a-tag size="small" :color="span.status==='failed'?'red':span.status==='running'?'orange':'green'">{{ span.status }}</a-tag></header><p>{{ span.agent_name||'Agent' }}<template v-if="span.tool_name"> · {{ span.tool_name }}</template> · {{ span.latency_ms||0 }} ms</p><blockquote v-for="(evidence,i) in span.evidence" :key="i">{{ evidenceText(evidence) }}</blockquote><small v-if="span.error_summary">{{ span.error_summary }}</small></div>
                </article>
              </div>
              <a-empty v-else description="当前阶段暂无执行 Span"/>
            </template>

            <template v-else-if="activeZone==='review'">
              <div class="zone-heading"><div><span>最小人工录入</span><h3>人工反馈与补充文件</h3><p>人工只填写人工结论、修改类型、修改内容、备注；初始为空，不设“待确认”。</p></div><a-button :disabled="!activeStage?.output_id" @click="$emit('open-stage-output',activeFlow.workflow_id,selectedStageKey)">打开确认工作区</a-button></div>
              <div class="review-board">
                <article><small>确认状态</small><b>{{ review?.latest_review?.state||'未开始' }}</b><span>{{ review?.latest_review?.actor||'—' }}</span></article>
                <article><small>采纳率</small><b>{{ review?.latest_review?.statistics['采纳率']!==undefined?`${Math.round(Number(review.latest_review.statistics['采纳率'])*100)}%`:'—' }}</b><span>正式提交后计算</span></article>
                <article><small>补充文件</small><b>{{ attachments.length }}</b><span>只进入反馈，不直接改 Skill</span></article>
                <article><small>进化资格</small><b>{{ review?.latest_review?.evolvable?'满足':'未满足' }}</b><span>草稿或存在空白时不允许进化</span></article>
              </div>
              <div class="file-ledger"><header><span>文件</span><span>用途</span><span>上传人</span><span>备注</span></header><article v-for="file in attachments" :key="file.id"><b>{{ file.filename }}</b><span>{{ file.purpose_label }}</span><span>{{ file.uploaded_by }}</span><span>{{ file.note||'—' }}</span></article><a-empty v-if="!attachments.length" description="暂无人工补充文件"/></div>
            </template>

            <template v-else-if="activeZone==='attribution'">
              <div class="zone-heading"><div><span>原因复核</span><h3>差异反查与归因审批</h3><p>AI 只提出待确认归因，未经人工确认不得生成 Skill 候选。</p></div><div><a-button :disabled="!diff?.review_rows_available" :loading="actionBusy==='attribute'" @click="runAttribution">重新归因</a-button><a-button type="primary" :disabled="!confirmedAttributionIds.length" :loading="actionBusy==='proposal'" @click="createSkillProposal">生成 Skill 内容候选</a-button></div></div>
              <div class="attribution-summary"><span>人工差异 <b>{{ diff?.diff.items?.length||0 }}</b></span><span>待确认 <b>{{ diff?.attribution_summary.proposed||0 }}</b></span><span>已确认 <b>{{ diff?.attribution_summary.confirmed||0 }}</b></span><span>可用于补丁 <b>{{ diff?.attribution_summary.usable_for_content_patch||0 }}</b></span></div>
              <div class="attribution-list">
                <article v-for="item in attributionRows" :key="String(item.id)" :class="String(item.state)"><header><div><a-tag size="small">{{ item.category_label||item.category }}</a-tag><a-tag size="small" :color="item.state==='confirmed'?'green':item.state==='rejected'?'red':'orange'">{{ item.state_label||item.state }}</a-tag></div><small>置信度 {{ Math.round(Number(item.confidence||0)*100) }}%</small></header><p>{{ item.hypothesis }}</p><footer v-if="item.state==='proposed'"><a-button size="mini" status="danger" @click="decideAttribution(String(item.id),'reject')">驳回</a-button><a-button size="mini" type="primary" @click="decideAttribution(String(item.id),'confirm')">确认归因</a-button></footer><small v-if="item.excluded_reason">{{ item.excluded_reason }}</small></article>
                <a-empty v-if="!attributionRows.length" description="正式提交确认稿并运行归因后显示候选"/>
              </div>
            </template>

            <template v-else>
              <div class="zone-heading"><div><span>Skill 进化</span><h3>候选、评测与上线</h3><p>每一步独立执行；派生不会自动评测，评测不会自动激活。</p></div></div>
              <div class="evolution-layout">
                <aside class="proposal-index"><button v-for="proposal in skillProposals" :key="proposal.id" type="button" :class="{active:proposal.id===selectedProposalId}" @click="selectProposal(proposal.id)"><small>{{ proposal.state }}</small><b>{{ proposal.title }}</b><span>{{ proposal.summary||proposal.expected_benefit }}</span></button><a-empty v-if="!skillProposals.length" description="暂无 Skill 内容候选"/></aside>
                <section class="proposal-console" v-if="activeProposal">
                  <header><div><span>候选 {{ activeProposal.id.slice(0,8) }}</span><h4>{{ activeProposal.title }}</h4></div><a-tag :color="plan?.gate_passed?'green':'orange'">{{ plan?.gate_passed?'门禁通过':activeProposal.state }}</a-tag></header>
                  <div class="version-track"><article><small>基线</small><b>{{ plan?.baseline_version||'待解析' }}</b></article><i>→</i><article><small>候选</small><b>{{ plan?.candidate_version||'尚未派生' }}</b></article><i>→</i><article><small>发布状态</small><b>{{ plan?.candidate_release_state||'未创建' }}</b></article></div>
                  <div class="patch-list"><b>最小变更范围</b><span v-for="path in plan?.changed_paths||[]" :key="path"><code>{{ path }}</code></span><small v-if="!plan?.changed_paths.length">派生候选后显示实际修改文件</small></div>
                  <div class="gate-grid"><article v-for="check in plan?.gate_checks||[]" :key="check.code" :class="{ok:check.ok}"><i/><div><b>{{ check.label||check.code }}</b><small>{{ check.detail||'等待评测结果' }}</small></div></article><p v-if="plan?.missing_conditions.length">缺失条件：{{ plan.missing_conditions.join('；') }}</p></div>
                  <div class="evolution-actions"><a-button :loading="actionBusy==='materialize'" :disabled="!!plan?.candidate_skill_version_id" @click="materializeProposal">派生候选版本</a-button><a-button :disabled="!plan?.candidate_skill_version_id" @click="showEvaluationForm=!showEvaluationForm">冻结集对照评测</a-button><a-button :disabled="!plan?.gate_passed" @click="transition('submit-approval')">提交审批</a-button><a-button type="primary" status="success" :disabled="!plan?.gate_passed" @click="transition('activate')">激活</a-button><a-button status="warning" :disabled="!plan?.candidate_skill_version_id" @click="transition('rollback')">回滚</a-button></div>
                  <div v-if="showEvaluationForm" class="evaluation-form"><a-select v-model="evaluationForm.gold" placeholder="冻结金标版本"><a-option v-for="v in frozenVersions" :key="v.id" :value="v.id">{{ v.version }} · {{ v.case_count }} 条</a-option></a-select><a-select v-model="evaluationForm.baseline" placeholder="基线运行"><a-option v-for="run in completedRuns" :key="run.id" :value="run.id">{{ run.name||run.id.slice(0,8) }}</a-option></a-select><a-select v-model="evaluationForm.candidate" placeholder="候选运行"><a-option v-for="run in completedRuns" :key="run.id" :value="run.id">{{ run.name||run.id.slice(0,8) }}</a-option></a-select><a-button type="primary" :loading="actionBusy==='evaluate'" :disabled="!evaluationReady" @click="evaluateProposal">运行硬门禁</a-button></div>
                </section>
                <a-empty v-else description="从左侧选择一个 Skill 内容候选"/>
              </div>
            </template>
          </section>
        </template>
        <a-empty v-else description="请选择或发起一个流程"/>
      </main>

      <aside class="context-inspector" v-if="activeFlow">
        <span class="eyebrow">审计上下文</span><h3>运行上下文</h3>
        <dl><dt>流程</dt><dd>{{ activeFlow.workflow_id }}</dd><dt>当前阶段</dt><dd>{{ stageLabel(selectedStageKey) }}</dd><dt>锁定版本</dt><dd>{{ activeStage?.skill_version||'—' }}</dd><dt>包哈希</dt><dd class="mono">{{ activeStage?.output_id?.slice(0,12)||'产出前不可用' }}</dd><dt>决策人</dt><dd>{{ activeStage?.decided_by||'未决策' }}</dd></dl>
        <div class="audit-rule"><icon-safe/><p><b>审计边界</b><span>展示显式决策与可观察事件；不保存隐藏思维链、凭据和完整敏感参数。</span></p></div>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, markRaw, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Message, Modal } from '@arco-design/web-vue';
import { IconBranch, IconDelete, IconExperiment, IconFile, IconPlayArrow, IconPlus, IconRefresh, IconRobot, IconSafe } from '@arco-design/web-vue/es/icon';
import { confirmWorkflowStage, decideStageAttribution, deleteWorkflowBatch, evaluateSkillContent, executeWorkflowStage, generateSkillContentProposal, getProjectQualityCockpit, getRegistrationFailures, getSkillContentPlan, getStageAttemptTrace, getStageDiff, getStageReviewStatus, listEvaluationRuns, listGenerationOutputs, listGoldDatasetVersions, listGoldDatasets, listOptimizationProposals, listStageAttachments, listStageAttempts, listWorkflowStageSubmissions, materializeSkillContent, preflightWorkflowStageSubmission, retryRegistrationFailures, runStageAttribution, submitWorkflowStageOutput, transitionSkillContent } from './service';
import type { EvaluationRun, GenerationOutput, GoldDatasetVersion, OptimizationProposal, ProjectQualityCockpit, ProjectWorkflowView, SkillContentPlanView, StageAttachmentView, StageAttemptTraceView, StageExecutionAttemptView, StageReviewStatusView, StageDiffView, WorkflowStageGateView } from './types';

const props=defineProps<{projectId:number}>();
const emit=defineEmits<{(e:'start-workflow'):void;(e:'open-stage-output',workflowId:string,stage:string):void}>();
const router=useRouter();
const emptyCockpit=():ProjectQualityCockpit=>({people:{leads:[],executors:[]},gold_by_type:{},single_capabilities:[],workflows:[],stage_order:[]});
const cockpit=ref<ProjectQualityCockpit>(emptyCockpit()),loading=ref(false),selectedFlowId=ref(''),selectedStageKey=ref(''),activeZone=ref('overview'),actionBusy=ref('');
const attempt=ref<StageExecutionAttemptView|null>(null),trace=ref<StageAttemptTraceView|null>(null),review=ref<StageReviewStatusView|null>(null),diff=ref<StageDiffView|null>(null),attachments=ref<StageAttachmentView[]>([]);
const skillProposals=ref<OptimizationProposal[]>([]),selectedProposalId=ref(''),plan=ref<SkillContentPlanView|null>(null),goldVersions=ref<GoldDatasetVersion[]>([]),runs=ref<EvaluationRun[]>([]),showEvaluationForm=ref(false),evaluationForm=ref({gold:'',baseline:'',candidate:''});
const outputs=ref<GenerationOutput[]>([]),submissions=ref<Array<Record<string,unknown>>>([]);
const registration=ref<{open:number;failed:number;dead_letter:number;alert:boolean;items?:Array<{id:string;output_id:string;workflow_id:string;stage:string;status:string;attempts:number;last_error:string}>}>({open:0,failed:0,dead_letter:0,alert:false,items:[]});
const zones=[{key:'overview',label:'流程总览',hint:'状态与下一步',icon:markRaw(IconRobot)},{key:'trace',label:'执行证据链',hint:'步骤与知识原句',icon:markRaw(IconBranch)},{key:'review',label:'人工反馈',hint:'确认稿与补充文件',icon:markRaw(IconFile)},{key:'attribution',label:'归因审批',hint:'差异反查与拍板',icon:markRaw(IconSafe)},{key:'evolution',label:'Skill 进化',hint:'候选、评测、上线',icon:markRaw(IconExperiment)}];
const activeFlow=computed(()=>cockpit.value.workflows.find(v=>v.workflow_id===selectedFlowId.value)||cockpit.value.workflows[0]);
const activeStage=computed(()=>activeFlow.value?.stages.find(v=>v.stage===selectedStageKey.value)||activeFlow.value?.stages[0]);
const activeProposal=computed(()=>skillProposals.value.find(v=>v.id===selectedProposalId.value)||skillProposals.value[0]);
const attributionRows=computed(()=>diff.value?.attributions||[]);
const confirmedAttributionIds=computed(()=>attributionRows.value.filter(v=>v.state==='confirmed'&&v.usable_for_content_patch!==false).map(v=>String(v.id)));
const frozenVersions=computed(()=>goldVersions.value.filter(v=>v.state==='frozen'));
const completedRuns=computed(()=>runs.value.filter(v=>v.status==='completed'));
const unmanagedOutputs=computed(()=>{const admitted=new Set(submissions.value.map(v=>String(v.output_id||'')));return outputs.value.filter(v=>!admitted.has(v.id)&&!String((v.metadata||{}).workflow_id||''));});
const evaluationReady=computed(()=>Boolean(evaluationForm.value.gold&&evaluationForm.value.baseline&&evaluationForm.value.candidate));
const passing=['passed','confirmed','overridden'];
const passedCount=(flow:ProjectWorkflowView)=>flow.stages.filter(v=>passing.includes(v.status)).length;
const flowPercent=(flow:ProjectWorkflowView)=>flow.stages.length?Math.round(passedCount(flow)/flow.stages.length*100):0;
const stageLabel=(value:string)=>({test_plan_generation:'方案生成',test_case_generation:'用例生成',testcase_generation:'用例生成',test_execution:'测试执行',test_report_generation:'报告产出',report_generation:'报告产出',risk_identification:'风险识别',issue_tracking:'问题跟踪',requirement_analysis:'需求分析'} as Record<string,string>)[value]||value;
const statusLabel=(value:string)=>({pending:'待测评',unscored:'无评分',passed:'已通过',failed:'未通过',confirmed:'人工确认',overridden:'负责人放行',ready:'可执行',blocked:'待前置',running:'执行中'} as Record<string,string>)[value]||value||'未知';
const stageState=(stage:WorkflowStageGateView)=>passing.includes(stage.status)?'done':stage.status==='failed'?'failed':stage.status==='running'?'running':'pending';
const evidenceText=(evidence:Record<string,unknown>)=>String(evidence.quote||evidence.text||evidence.snippet||evidence.title||evidence.source_id||'证据定位已记录');
const nextAction=computed(()=>{if(!activeStage.value?.output_id)return{index:'01',title:'派发当前阶段',detail:'创建执行尝试并携带服务端可信上下文进入原 Agent 页面。',button:'开始执行'};if(!review.value?.latest_review?.evolvable)return{index:'02',title:'完成人工确认',detail:'下载确认稿，填写四个人工列；可先保存草稿，正式提交后才可进化。',button:'打开确认'};if(!confirmedAttributionIds.value.length)return{index:'03',title:'确认失败归因',detail:'基于原始产出、人工修改、补充文件和证据链确认问题责任层。',button:'进入归因'};return{index:'04',title:'创建 Skill 候选',detail:'从已确认归因生成最小内容补丁，再进入冻结集对照评测。',button:'进入进化'};});
function selectFlow(id:string){selectedFlowId.value=id;selectedStageKey.value=activeFlow.value?.current_stage||activeFlow.value?.stages[0]?.stage||'';void loadStageContext()}
function selectStage(stage:string){selectedStageKey.value=stage;void loadStageContext()}
function requestDeleteFlow(flow:ProjectWorkflowView){Modal.warning({title:'删除运行批次',content:`确认删除批次“${flow.workflow_id}”吗？该批次会从工作台移除，已有产出和执行审计记录仍会保留。`,okText:'确认删除',cancelText:'取消',hideCancel:false,onOk:()=>deleteFlow(flow.workflow_id)})}
async function deleteFlow(workflowId:string){actionBusy.value=`delete:${workflowId}`;try{await deleteWorkflowBatch(props.projectId,workflowId);if(selectedFlowId.value===workflowId){selectedFlowId.value='';selectedStageKey.value=''}await loadAll(false);Message.success('运行批次已删除')}catch(error:unknown){const response=(error as {response?:{data?:{detail?:string;message?:string}}})?.response?.data;Message.error(response?.detail||response?.message||'删除批次失败，请确认当前账号具有测试负责人权限')}finally{actionBusy.value=''}}
function goNextAction(){if(!activeStage.value?.output_id)void executeCurrent();else if(!review.value?.latest_review?.evolvable)emit('open-stage-output',activeFlow.value!.workflow_id,selectedStageKey.value);else if(!confirmedAttributionIds.value.length)activeZone.value='attribution';else activeZone.value='evolution'}
async function loadAll(showError=true){loading.value=true;try{cockpit.value=await getProjectQualityCockpit(props.projectId);if(!selectedFlowId.value)selectedFlowId.value=cockpit.value.workflows[0]?.workflow_id||'';if(!selectedStageKey.value)selectedStageKey.value=activeFlow.value?.current_stage||activeFlow.value?.stages[0]?.stage||'';const optionalResults=await Promise.allSettled([listOptimizationProposals(props.projectId),listGoldDatasets(props.projectId),listGenerationOutputs(props.projectId),listWorkflowStageSubmissions(props.projectId),getRegistrationFailures(props.projectId),listEvaluationRuns()] as const);const [proposalResult,datasetResult,outputResult,submissionResult,registrationResult,runResult]=optionalResults;if(proposalResult.status==='fulfilled'){skillProposals.value=proposalResult.value.filter(v=>v.proposal_type==='skill_content');selectedProposalId.value=selectedProposalId.value||skillProposals.value[0]?.id||''}if(outputResult.status==='fulfilled')outputs.value=outputResult.value;if(submissionResult.status==='fulfilled')submissions.value=submissionResult.value;if(registrationResult.status==='fulfilled')registration.value=registrationResult.value;if(runResult.status==='fulfilled')runs.value=runResult.value;if(datasetResult.status==='fulfilled'){const versionResults=await Promise.allSettled(datasetResult.value.map(v=>listGoldDatasetVersions(v.id)));goldVersions.value=versionResults.flatMap(v=>v.status==='fulfilled'?v.value:[])}await loadStageContext();await loadPlan();}catch{if(showError)Message.error('加载质量飞轮工作台失败')}finally{loading.value=false}}
async function loadStageContext(){const flow=activeFlow.value;if(!flow||!selectedStageKey.value)return;attempt.value=null;trace.value=null;review.value=null;diff.value=null;attachments.value=[];const [attemptsResult,reviewResult,diffResult,attachmentResult]=await Promise.allSettled([listStageAttempts(props.projectId,flow.workflow_id,selectedStageKey.value),getStageReviewStatus(props.projectId,flow.workflow_id,selectedStageKey.value),getStageDiff(props.projectId,flow.workflow_id,selectedStageKey.value),listStageAttachments(props.projectId,flow.workflow_id,selectedStageKey.value)]);if(attemptsResult.status==='fulfilled'){attempt.value=attemptsResult.value.slice().sort((a,b)=>b.created_at.localeCompare(a.created_at))[0]||null;if(attempt.value)try{trace.value=await getStageAttemptTrace(attempt.value.id)}catch{trace.value=null}}if(reviewResult.status==='fulfilled')review.value=reviewResult.value;if(diffResult.status==='fulfilled')diff.value=diffResult.value;if(attachmentResult.status==='fulfilled')attachments.value=attachmentResult.value;}
async function executeCurrent(){if(!activeFlow.value)return;actionBusy.value='execute';try{const flowId=activeFlow.value.workflow_id;const result=await executeWorkflowStage(props.projectId,flowId,selectedStageKey.value);const controlledUrl=result.execution_context_id?`/test-plans?execution_context_id=${encodeURIComponent(result.execution_context_id)}`:'';const legacyUrl=result.module_key==='test_plan_generation'?`/test-plans?workflow_id=${encodeURIComponent(flowId)}`:'';const launchUrl=result.launch_url||controlledUrl||legacyUrl;if(launchUrl){await router.push(launchUrl);void loadAll(false)}else Message.info(result.hint||'阶段已派发，请进入对应 Agent 页面继续执行')}catch(error:unknown){const response=(error as {response?:{data?:{detail?:string;message?:string}}})?.response?.data;Message.error(response?.detail||response?.message||'阶段派发失败，请检查前置门禁')}finally{actionBusy.value=''}}
async function confirmCurrent(){if(!activeFlow.value)return;actionBusy.value='confirm';try{await confirmWorkflowStage(props.projectId,activeFlow.value.workflow_id,selectedStageKey.value);Message.success('当前阶段已确认放行');await loadAll()}catch{Message.error('确认放行失败')}finally{actionBusy.value=''}}
async function runAttribution(){if(!activeFlow.value)return;actionBusy.value='attribute';try{await runStageAttribution(props.projectId,activeFlow.value.workflow_id,selectedStageKey.value);diff.value=await getStageDiff(props.projectId,activeFlow.value.workflow_id,selectedStageKey.value);Message.success('已按最新人工差异生成待确认归因')}catch{Message.error('归因生成失败')}finally{actionBusy.value=''}}
async function decideAttribution(id:string,action:'confirm'|'reject'){try{await decideStageAttribution(id,action);if(activeFlow.value)diff.value=await getStageDiff(props.projectId,activeFlow.value.workflow_id,selectedStageKey.value);Message.success(action==='confirm'?'归因已确认':'归因已驳回')}catch{Message.error('归因决策失败')}}
async function createSkillProposal(){actionBusy.value='proposal';try{const created=await generateSkillContentProposal(confirmedAttributionIds.value);Message.success(`已生成 ${created.length} 个 Skill 内容候选`);activeZone.value='evolution';skillProposals.value=(await listOptimizationProposals(props.projectId)).filter(v=>v.proposal_type==='skill_content');selectedProposalId.value=created[0]?.id||skillProposals.value[0]?.id||'';await loadPlan()}catch{Message.error('生成 Skill 候选失败')}finally{actionBusy.value=''}}
async function selectProposal(id:string){selectedProposalId.value=id;await loadPlan()}
async function loadPlan(){if(!selectedProposalId.value){plan.value=null;return}try{plan.value=await getSkillContentPlan(selectedProposalId.value)}catch{plan.value=null}}
async function materializeProposal(){if(!activeProposal.value)return;actionBusy.value='materialize';try{await materializeSkillContent(activeProposal.value.id);Message.success('已派生新的不可变 Skill 候选版本');await loadPlan()}catch{Message.error('候选派生失败')}finally{actionBusy.value=''}}
async function evaluateProposal(){if(!activeProposal.value||!evaluationReady.value)return;actionBusy.value='evaluate';try{const result=await evaluateSkillContent(activeProposal.value.id,{gold_dataset_version:evaluationForm.value.gold,baseline_run:evaluationForm.value.baseline,candidate_run:evaluationForm.value.candidate});plan.value=result.plan;Message.success(result.plan.gate_passed?'硬门禁通过':'评测完成，但硬门禁未通过')}catch{Message.error('冻结集对照评测失败')}finally{actionBusy.value=''}}
async function transition(action:'submit-approval'|'activate'|'rollback'){if(!activeProposal.value)return;try{await transitionSkillContent(activeProposal.value.id,action);Message.success(action==='activate'?'候选已激活':action==='rollback'?'已回滚到基线':'已提交负责人审批');await loadAll()}catch{Message.error('状态操作失败，请检查权限和门禁条件')}}
async function admitOutput(output:GenerationOutput){if(!activeFlow.value)return;actionBusy.value=`admit:${output.id}`;try{const analysis=await preflightWorkflowStageSubmission({projectId:props.projectId,outputId:output.id,stage:output.task_type,workflowId:activeFlow.value.workflow_id});const conflict=analysis.stage_conflict as Record<string,unknown>|null;const submit=async(confirmReplace=false)=>{await submitWorkflowStageOutput({outputId:output.id,stage:output.task_type,workflowId:activeFlow.value!.workflow_id,replaceOutputId:String(conflict?.output_id||''),confirmReplace});Message.success('旁路产出已纳入当前受控流程，原产出协议保持不变');await loadAll()};if(analysis.requires_replace_confirmation===true){Modal.confirm({title:'确认替换当前阶段产出',content:'当前阶段已有正式产出。继续会保留旧门禁快照并将其标记为被替换，不会修改旁路产出自身协议。',okText:'确认替换',onOk:()=>submit(true)});}else if(analysis.admissible===true)await submit();else Message.error((analysis.messages as string[]||[]).join('；')||'该产出暂不能纳管');}catch{Message.error('旁路产出预检失败')}finally{actionBusy.value=''}}
async function retryRegistrations(){actionBusy.value='retry-registration';try{const result=await retryRegistrationFailures(props.projectId);Message.success(`已重试 ${result.retried} 条登记失败`);registration.value=await getRegistrationFailures(props.projectId)}catch{Message.error('登记失败重试未完成，请检查负责人权限')}finally{actionBusy.value=''}}
watch(()=>props.projectId,()=>void loadAll());
onMounted(()=>void loadAll());
</script>

<style scoped>
.fw-workbench{--ink:#15202b;--paper:#f5f2ea;--signal:#146c94;--ok:#2d7d46;--warn:#c66a1b;color:var(--ink)}
.fw-command{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:20px 22px;border:1px solid #d9d5ca;border-bottom:4px solid var(--ink);background:linear-gradient(120deg,#f8f6f0 0 72%,#e8edf0 72%)}.fw-command h2{margin:0 0 4px;font-family:"Noto Serif SC","Songti SC",serif;font-size:24px}.fw-command p{margin:0;color:#66717b}.eyebrow{font-family:"IBM Plex Mono","SFMono-Regular",monospace;font-size:10px;letter-spacing:.16em;color:var(--signal)}.command-actions{display:flex;align-items:center;gap:8px}
.fw-rail-layout{display:grid;grid-template-columns:210px minmax(560px,1fr) 220px;min-height:690px;margin-top:12px;border:1px solid #d9d5ca;background:#fff}.flow-index,.context-inspector{background:#f3f0e8}.flow-index{padding:14px;border-right:1px solid #d9d5ca}.flow-index>header{display:flex;justify-content:space-between;margin-bottom:10px}.flow-batch{position:relative;margin-bottom:7px;border:1px solid transparent}.flow-batch.active{border-color:#93a9b4;background:#fff}.flow-select{display:block;width:100%;padding:11px 34px 11px 11px;border:0;text-align:left;background:transparent;cursor:pointer}.flow-select small,.flow-select b,.flow-select span{display:block}.flow-select small{font-family:"IBM Plex Mono",monospace;font-size:9px;color:#7a858e}.flow-select b{overflow:hidden;margin:4px 0;font-size:12px;text-overflow:ellipsis}.flow-select span{font-size:10px;color:#7a858e}.flow-select i{display:block;height:3px;margin:8px 0;background:#dfe3e3}.flow-select u{display:block;height:100%;background:var(--signal)}.flow-delete{position:absolute;top:6px;right:4px;opacity:.2}.flow-batch:hover .flow-delete,.flow-batch:focus-within .flow-delete{opacity:1}
.flow-main{min-width:0;padding:18px}.stage-rail{display:grid;grid-template-columns:repeat(4,1fr);gap:0}.stage-rail button{display:grid;grid-template-columns:32px 1fr;position:relative;gap:8px;padding:8px 6px 15px;border:0;background:transparent;text-align:left;cursor:pointer}.stage-rail button>i{display:flex;z-index:2;align-items:center;justify-content:center;width:28px;height:28px;border:2px solid #adb5ba;border-radius:50%;background:#fff;font-family:"IBM Plex Mono",monospace;font-size:10px;font-style:normal}.stage-rail button>em{position:absolute;top:21px;right:0;left:28px;height:2px;background:#d5dade}.stage-rail button:last-child>em{display:none}.stage-rail button span b,.stage-rail button span small{display:block}.stage-rail button span b{font-size:12px}.stage-rail button span small{margin-top:2px;color:#7a858e;font-size:10px}.stage-rail button.done>i{border-color:var(--ok);color:#fff;background:var(--ok)}.stage-rail button.running>i,.stage-rail button.active>i{border-color:var(--signal);color:var(--signal)}.stage-rail button.failed>i{border-color:#b93b3b;color:#b93b3b}.stage-rail button.active{background:#eef5f7}
.stage-console{margin-top:8px;padding:14px 16px;border-left:4px solid var(--signal);background:#eef3f4}.stage-title{display:flex;align-items:center;justify-content:space-between}.stage-title h3{margin:2px 0}.stage-title span{font-family:"IBM Plex Mono",monospace;font-size:9px;color:#71808a}.stage-actions{display:flex;gap:8px}.stage-facts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}.stage-facts article{padding:9px 10px;border:1px solid #d5dfe1;background:#fff}.stage-facts small,.stage-facts b,.stage-facts span{display:block}.stage-facts small{color:#7a858e;font-size:9px}.stage-facts b{margin-top:4px;font-size:12px}.stage-facts span{overflow:hidden;margin-top:2px;color:#7a858e;font-size:10px;text-overflow:ellipsis;white-space:nowrap}
.work-zones{display:grid;grid-template-columns:repeat(5,1fr);margin-top:14px;border-bottom:1px solid #cfd5d6}.work-zones button{display:grid;grid-template-columns:18px 1fr;gap:1px 7px;padding:10px 9px;border:0;border-bottom:3px solid transparent;background:transparent;text-align:left;cursor:pointer}.work-zones button svg{grid-row:1/3}.work-zones button span{font-size:11px;font-weight:700}.work-zones button small{font-size:9px;color:#869099}.work-zones button.active{border-color:var(--signal);color:var(--signal);background:#eef5f7}.zone-body{min-height:350px;padding:16px 0}.zone-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.zone-heading h3{margin:2px 0;font-size:16px}.zone-heading p{margin:3px 0;color:#74808a;font-size:11px}.overview-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:10px}.overview-grid>section{padding:14px;border:1px solid #dce0df;background:#faf9f5}.overview-grid .span-two{grid-column:1/-1}.metric-strip,.review-board{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:14px}.metric-strip article,.review-board article{padding:10px;background:#fff;border-left:2px solid var(--signal)}.metric-strip b,.metric-strip small,.review-board b,.review-board small,.review-board span{display:block}.metric-strip b,.review-board b{font-size:18px}.metric-strip small,.review-board small,.review-board span{color:#7a858e;font-size:9px}.next-action{display:grid;grid-template-columns:42px 1fr auto;align-items:center;gap:12px;padding-top:10px}.next-action>i{font-family:"IBM Plex Mono",monospace;font-size:28px;color:var(--signal);font-style:normal}.next-action p{margin:4px 0;color:#6d7780;font-size:11px}.ops-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.ops-grid>div{border:1px solid #d9dedd;background:#fff}.ops-grid>div>header{display:flex;justify-content:space-between;padding:8px 10px;background:#eceae3;font-size:10px}.ops-grid article{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border-top:1px solid #ecece8}.ops-grid article b,.ops-grid article small{display:block}.ops-grid article b{font-size:10px}.ops-grid article small{overflow:hidden;max-width:340px;margin-top:2px;color:#7b858c;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.ops-grid .danger{color:#b93b3b}
.evidence-lane{display:grid;margin-top:14px}.evidence-lane>article{display:grid;grid-template-columns:38px 1fr;gap:10px}.lane-node{position:relative}.lane-node i{display:flex;z-index:2;position:relative;align-items:center;justify-content:center;width:28px;height:28px;border:2px solid var(--signal);border-radius:50%;background:#fff;font-family:"IBM Plex Mono",monospace;font-size:10px;font-style:normal}.lane-node em{position:absolute;top:26px;bottom:-10px;left:13px;width:2px;background:#cad4d7}.evidence-lane>article:last-child em{display:none}.evidence-lane>article>div:last-child{margin-bottom:10px;padding:10px;border:1px solid #d9dfe0;background:#faf9f5}.evidence-lane header{display:flex;justify-content:space-between}.evidence-lane p{margin:4px 0;color:#77818a;font-size:10px}.evidence-lane blockquote{margin:6px 0;padding:6px 8px;border-left:3px solid #b7cbd3;background:#fff;font-size:11px}.evidence-lane article.failed small{color:#b93b3b}
.file-ledger{margin-top:14px}.file-ledger header,.file-ledger article{display:grid;grid-template-columns:1.3fr .7fr .6fr 1fr;gap:8px;padding:8px 10px;border-bottom:1px solid #e1e3df;font-size:10px}.file-ledger header{font-weight:700;background:#eceae3}.attribution-summary{display:flex;gap:20px;margin:12px 0;padding:10px;border-top:1px solid #d7dddc;border-bottom:1px solid #d7dddc;font-size:11px}.attribution-list{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.attribution-list article{padding:10px;border:1px solid #d9dedd;border-left:3px solid var(--warn);background:#faf9f5}.attribution-list article.confirmed{border-left-color:var(--ok)}.attribution-list article.rejected{opacity:.62;border-left-color:#a2a7a7}.attribution-list header,.attribution-list footer{display:flex;justify-content:space-between;gap:6px}.attribution-list p{font-size:11px;line-height:1.6}.attribution-list>article>small{color:#a25524;font-size:9px}
.evolution-layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:12px;margin-top:12px}.proposal-index{border-right:1px solid #d5dada;padding-right:10px}.proposal-index button{display:block;width:100%;margin-bottom:7px;padding:9px;border:1px solid transparent;background:#f3f1ea;text-align:left}.proposal-index button.active{border-color:#8ea8b2;background:#fff}.proposal-index small,.proposal-index b,.proposal-index span{display:block}.proposal-index small{font-family:"IBM Plex Mono",monospace;font-size:9px;color:var(--signal)}.proposal-index b{margin:4px 0;font-size:11px}.proposal-index span{color:#77818a;font-size:9px}.proposal-console>header{display:flex;justify-content:space-between}.proposal-console h4{margin:3px 0}.version-track{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;align-items:center;gap:7px;margin:12px 0}.version-track article{padding:9px;border:1px solid #d7dcdb;background:#faf9f5}.version-track small,.version-track b{display:block}.version-track small{font-size:9px;color:#79838b}.version-track b{margin-top:3px;font-family:"IBM Plex Mono",monospace;font-size:11px}.patch-list{display:flex;align-items:center;flex-wrap:wrap;gap:6px;padding:10px;background:#eef3f4;font-size:10px}.patch-list code{padding:3px 6px;background:#fff}.gate-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:10px}.gate-grid article{display:flex;gap:8px;padding:8px;border:1px solid #e0d2c7;background:#fff8f0}.gate-grid article.ok{border-color:#c3d8c8;background:#f2f8f3}.gate-grid article>i{width:7px;height:7px;margin-top:4px;border-radius:50%;background:var(--warn)}.gate-grid article.ok>i{background:var(--ok)}.gate-grid b,.gate-grid small{display:block}.gate-grid b{font-size:10px}.gate-grid small{color:#7b858c;font-size:9px}.gate-grid>p{grid-column:1/-1;color:#a25524;font-size:10px}.evolution-actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.evaluation-form{display:grid;grid-template-columns:repeat(3,1fr) auto;gap:7px;margin-top:10px;padding:10px;border-top:1px dashed #b9c3c4;background:#faf9f5}
.context-inspector{padding:16px;border-left:1px solid #d9d5ca}.context-inspector h3{margin:5px 0 14px}.context-inspector dl{margin:0}.context-inspector dt{margin-top:10px;color:#7b858c;font-size:9px;text-transform:uppercase}.context-inspector dd{overflow:hidden;margin:3px 0;font-size:11px;text-overflow:ellipsis}.context-inspector .mono{font-family:"IBM Plex Mono",monospace}.audit-rule{display:flex;gap:8px;margin-top:20px;padding:10px;border:1px solid #d9c8b5;background:#fff8ee}.audit-rule svg{flex:none;color:var(--warn)}.audit-rule p{margin:0}.audit-rule b,.audit-rule span{display:block}.audit-rule b{font-size:10px}.audit-rule span{margin-top:3px;color:#7d6c5e;font-size:9px;line-height:1.5}
.lead{font-weight:700}.zone-body :deep(.arco-empty){padding:24px 0}
/* 与质量飞轮主页面统一的蓝白/青色视觉层，覆盖早期独立工作台的暖灰配色。 */
.fw-workbench{--ink:var(--color-text-1);--paper:#f5f8fc;--signal:#1683c7;--ok:#2d8a55;--warn:#e07a24}
.fw-command{border-color:#d9e6f2;border-bottom-color:var(--signal);background:linear-gradient(120deg,#fff 0 72%,#eef8fb 72%)}
.fw-command h2{font-family:inherit}.fw-command p,.zone-heading p,.next-action p{color:var(--color-text-3)}
.fw-rail-layout{border-color:#d9e6f2}.flow-index,.context-inspector{background:#f5f8fc}
.flow-index{border-right-color:#d9e6f2}.flow-batch.active{border-color:#96c8e6;box-shadow:0 4px 12px rgba(20,108,148,.08)}
.flow-select i{background:#dce8f2}.flow-select u{background:linear-gradient(90deg,#1683c7,#1aa3a8)}
.stage-rail button.active,.work-zones button.active{background:#eef8fc}.stage-console{background:#eef8fc}
.stage-facts article{border-color:#d5e5ee}.work-zones{border-bottom-color:#d9e6f2}
.overview-grid>section,.evidence-lane>article>div:last-child,.attribution-list article,.version-track article,.evaluation-form{border-color:#dce7f0;background:#f8fbfe}
.ops-grid>div{border-color:#d9e6f2}.ops-grid>div>header,.file-ledger header{background:#edf4f9}
.proposal-index button{background:#edf4f9}.proposal-index button.active{border-color:#96c8e6;background:#fff}
.patch-list{background:#eef8fc}.context-inspector{border-left-color:#d9e6f2}
.audit-rule{border-color:#cfe2ef;background:#eef8fc}.audit-rule svg{color:var(--signal)}.audit-rule span{color:#5d7382}
@media(max-width:1280px){.fw-rail-layout{grid-template-columns:190px minmax(520px,1fr)}.context-inspector{display:none}}
@media(max-width:900px){.fw-rail-layout{grid-template-columns:1fr}.flow-index{border-right:0;border-bottom:1px solid #d9d5ca}.stage-facts,.review-board{grid-template-columns:repeat(2,1fr)}.work-zones{grid-template-columns:repeat(3,1fr)}.overview-grid,.evolution-layout{grid-template-columns:1fr}.overview-grid .span-two{grid-column:auto}.attribution-list{grid-template-columns:1fr}}
</style>
