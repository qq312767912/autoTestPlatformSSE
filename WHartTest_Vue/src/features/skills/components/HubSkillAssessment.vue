<template>
  <a-modal :visible="true" title="Skill 通用测评与进化" :footer="false" width="1080px" :mask-closable="false" @cancel="emit('close')">
    <p>从 Hub 精确版本建立项目副本。可只测评，也可生成、重测和迭代候选；确认提交前不写回 Hub。</p>
    <a-spin :loading="loading" style="width:100%">
      <a-form :model="{mode,cases}" layout="vertical">
        <a-form-item label="待测精确版本"><a-select v-model="versionId" placeholder="请选择版本" :disabled="busy" @change="current=null"><a-option v-for="version in versions" :key="version.id" :value="version.id" :disabled="version.archived||version.download_available===false">{{ skill.name }} · v{{ version.version }} · {{ version.package_sha256.slice(0,12) }}</a-option></a-select></a-form-item>
        <a-form-item label="测评方式"><a-radio-group v-model="mode" :disabled="busy"><a-radio value="structure">结构体检</a-radio><a-radio value="text">文本任务实测</a-radio></a-radio-group></a-form-item>
        <a-alert v-if="mode==='structure'">无需测试输入。评估八项可评维度，共 77 分；实测表现未执行，不显示完整百分制总分。仍可生成私有候选继续加工。</a-alert>
        <a-alert v-else-if="mode==='text'" type="warning">使用真实模型执行文本任务，不运行脚本、浏览器或外部工具。声明这些依赖的 Skill 会拒绝文本实测，请进入进化工坊关联业务任务。</a-alert>
        <p>真实业务产出的测评与 Skill 改进入口在数据飞轮的进化工坊，沿用业务任务绑定的版本和产出。</p>
        <a-button v-if="context!=='workshop'" :disabled="!versionId||busy" :loading="launching" @click="goToWorkshop">进入 Skill 进化工坊</a-button>
        <template v-if="mode==='text'">
          <article v-for="(item,index) in cases" :key="index" class="case">
            <b>测试输入 {{ index+1 }}</b><a-button v-if="cases.length>1" size="mini" @click="cases.splice(index,1)">移除</a-button>
            <a-textarea v-model="item.prompt" placeholder="典型用户请求与必要输入数据" :auto-size="{minRows:2,maxRows:5}"/>
            <a-textarea v-model="item.expected" placeholder="验收标准：什么结果才算完成，不能只写成功或正确" :auto-size="{minRows:2,maxRows:4}"/>
          </article>
          <a-button :disabled="cases.length>=5" size="small" @click="cases.push({prompt:'',expected:'',output:'',evidence_ref:''})">增加测试输入（最多5个）</a-button>
        </template>
        <details class="budget"><summary>执行预算</summary><label>最多候选轮数<a-input-number v-model="policy.max_rounds" :min="1" :max="3" :precision="0"/></label><label>调用上限<a-input-number v-model="policy.max_calls" :min="1" :max="120" :precision="0"/></label><label>估算 Token 上限<a-input-number v-model="policy.max_estimated_tokens" :min="10000" :max="2000000" :precision="0"/></label></details>
        <a-button type="primary" :loading="busy" :disabled="!versionId||loading" @click="start">新建并测评此版本</a-button>
      </a-form>
      <div class="history"><a-select placeholder="打开本项目的测评历史" :model-value="current?.id" :disabled="busy" @change="openHistory"><a-option v-for="item in history" :key="item.id" :value="item.id">{{ formatTime(item.created_at) }} · v{{ item.source_version }} · {{ modeName(item.mode) }} · {{ stateName(item.state) }}</a-option></a-select><a-button size="small" @click="loadHistory">刷新</a-button></div>
    </a-spin>
    <section v-if="current" class="results">
      <header><b>{{ current.skill_name }} @ {{ current.source_version }}</b><a-tag>{{ stateName(current.state) }}</a-tag><code>{{ current.source_sha256.slice(0,16) }}</code></header>
      <p>本会话方式：{{ modeName(current.mode) }} · 已使用 {{ current.budget.calls||0 }} 次调用 / {{ current.budget.estimated_tokens||0 }} 估算 Token。测试输入与量表已冻结。</p>
      <a-alert v-if="current.error" type="error">{{ current.error }}</a-alert>
      <a-alert v-if="isRunning" type="info">后台执行中，可关闭窗口，稍后从测评历史继续查看。<a-button size="mini" @click="refresh">刷新状态</a-button></a-alert>
      <a-alert v-if="current.mode==='external'" type="warning">此历史手工复核仅供查看。请在进化工坊关联原业务任务，历史粘贴的产出不再用于进化或提交。</a-alert>
      <div class="items">
        <article v-for="item in items" :key="item.id" class="result-card" :class="{selected:current.winner_id===item.id}">
          <header><b>{{ item.label }}</b><a-tag v-if="current.winner_id===item.id" color="green">下一轮基线</a-tag></header>
          <code>{{ item.sha256.slice(0,16) }}</code>
          <p v-if="item.general?.total!=null"><b>{{ item.general.total }} / 100</b> · 通用九维</p>
          <p v-else-if="item.general?.status==='partial'"><b>{{ item.general.assessed_score }} / {{ item.general.score_basis }}</b> 已评部分；实测表现未计分</p>
          <p v-if="item.general?.reason">{{ item.general.reason }}</p>
          <table v-if="item.general?.dimensions"><thead><tr><th>维度</th><th>权重</th><th>得分</th></tr></thead><tbody><tr v-for="dimension in item.general.dimensions" :key="dimension.id"><td>{{ dimension.name }}<details><summary>依据</summary>{{ dimension.reason }}<p>{{ dimension.evidence?.join('；') }}</p></details></td><td>{{ dimension.weight }}</td><td>{{ dimension.score ?? '未执行' }}</td></tr></tbody></table>
          <details v-if="item.output"><summary>实际产出与来源</summary><pre>{{ JSON.stringify(item.output,null,2) }}</pre></details>
          <details v-if="item.diff"><summary>候选补丁 diff</summary><p>{{ item.rationale }}</p><pre>{{ item.diff }}</pre></details>
          <p v-if="item.comparison">{{ item.comparison.reason }}</p>
          <details v-if="item.comparison?.votes?.length"><summary>独立评委证据</summary><p v-for="vote in item.comparison.votes" :key="vote.judge">评委 {{ vote.judge }}：{{ vote.reason||vote.error }} {{ vote.evidence?.join('；') }}</p></details>
          <template v-if="current.mode!=='external' && (!item.general || item.general.status==='failed')">
            <a-button size="small" :disabled="busy" @click="measure(item)">测评{{ item.id==='baseline'?'基线':'此候选' }}</a-button>
          </template>
          <a-input v-if="current.mode!=='external' && item.id!=='baseline' && ['partial','completed'].includes(item.general?.status||'')" v-model="selectionReasons[item.id]" placeholder="填写复核 diff 和测评后的选择理由" />
          <a-button v-if="current.mode!=='external' && item.id!=='baseline' && ['partial','completed'].includes(item.general?.status||'')" size="small" :disabled="busy||(current.mode==='text'&&!item.comparison?.eligible)" @click="pick(item)">选择为下一轮基线</a-button>
        </article>
      </div>
      <div class="actions"><a-button :loading="busy" :disabled="!canGenerate" @click="generate">根据测评短板生成候选（{{ current.candidates.length }}/{{ current.policy.max_rounds }}）</a-button><a-button type="primary" :disabled="busy||current.mode==='external'||!winner?.comparison?.eligible||current.state==='submitted'" @click="submit">确认 diff 并提交回 Hub 审批</a-button></div>
      <p v-if="current.mode==='structure'">结构体检的候选可继续加工；没有实测和成对验证，不允许提交回 Hub。</p>
      <a-alert v-if="current.state==='submitted'" type="success">已新增候选版本到 Hub 验证审批，原始包和活跃版本未替换。</a-alert>
    </section>
  </a-modal>
</template>
<script setup lang="ts">
import { useRouter } from 'vue-router'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { SkillHubService, hubAssessmentCall, toErrorMessage } from '../services/skillHubService'
import { skillWorkshopLocation } from '../utils/workshopNavigation'
import type { SkillVersionSummary } from '../types/hub'
import type { AssessmentCase, AssessmentHistory, AssessmentItem, HubAssessment } from '../types/assessment'
const props=defineProps<{projectId:number;skill:{id:number;name:string;version?:string;version_sha256?:string};initialVersionId?:string;initialAssessmentId?:string;context?:'hub'|'workshop'}>()
const emit=defineEmits<{close:[];submitted:[]}>()
const versions=ref<SkillVersionSummary[]>([]),versionId=ref(''),loading=ref(false),pending=ref(false)
const router=useRouter(),launching=ref(false)
const mode=ref<'structure'|'text'>('structure')
const cases=ref<AssessmentCase[]>([{prompt:'',expected:'',output:'',evidence_ref:''}])
const policy=ref({max_rounds:3,max_calls:120,max_estimated_tokens:400000})
const current=ref<HubAssessment|null>(null),history=ref<AssessmentHistory[]>([])
const selectionReasons=ref<Record<string,string>>({})
const isRunning=computed(()=>['queued','running'].includes(current.value?.state||''))
const busy=computed(()=>pending.value||launching.value||isRunning.value)
const items=computed(()=>current.value?[current.value.baseline,...current.value.candidates]:[])
const winner=computed(()=>items.value.find(item=>item.id===current.value?.winner_id))
const parent=computed(()=>winner.value||current.value?.baseline)
const canGenerate=computed(()=>current.value&&current.value.mode!=='external'&&!busy.value&&current.value.state!=='submitted'&&current.value.candidates.length<current.value.policy.max_rounds&&['partial','completed'].includes(parent.value?.general?.status||'')&&(current.value.mode==='structure'||parent.value?.id==='baseline'||parent.value?.comparison?.clear_gain))
const formatTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{hour12:false})
const modeName=(value:string)=>({structure:'结构体检',text:'文本实测',external:'历史手工复核（仅查看）'} as Record<string,string>)[value]||value
const stateName=(value:string)=>({ready:'待测评',queued:'排队中',running:'执行中',completed:'已完成',failed:'失败',submitted:'已提交'} as Record<string,string>)[value]||value
let disposed=false
let timer:ReturnType<typeof setTimeout>|undefined
function stopPolling(){if(timer)clearTimeout(timer);timer=undefined}
async function loadHistory(){try{history.value=(await hubAssessmentCall<{items:AssessmentHistory[]}>(props.projectId,'','GET',{skill_id:props.skill.id})).items}catch(error){Message.error(toErrorMessage(error,'读取测评历史失败'))}}
async function refresh(){if(disposed||!current.value)return;const id=current.value.id;try{const result=await hubAssessmentCall<HubAssessment>(props.projectId,`${id}/`,'GET');if(!disposed&&current.value?.id===id)current.value=result}catch(error){Message.error(toErrorMessage(error,'读取测评状态失败'))}}
watch(()=>[current.value?.id,current.value?.state],()=>{stopPolling();if(isRunning.value)timer=setTimeout(async()=>{await refresh();if(isRunning.value)poll()},2500)})
function poll(){if(disposed)return;stopPolling();timer=setTimeout(async()=>{await refresh();if(isRunning.value)poll();else void loadHistory()},2500)}
onBeforeUnmount(()=>{disposed=true;stopPolling()})
async function openHistory(value:unknown){if(typeof value!=='string')return;pending.value=true;try{current.value=await hubAssessmentCall<HubAssessment>(props.projectId,`${value}/`,'GET');versionId.value=current.value.source_version_id}catch(error){Message.error(toErrorMessage(error,'读取测评失败'))}finally{pending.value=false}}
async function action(path:string,data:Record<string,unknown>={}){if(!current.value)return;pending.value=true;try{current.value=await hubAssessmentCall<HubAssessment>(props.projectId,`${current.value.id}/${path}/`,'POST',data);if(current.value.state==='submitted')emit('submitted');void loadHistory()}catch(error){Message.error(toErrorMessage(error,'操作失败'));await refresh()}finally{pending.value=false}}
async function start(){pending.value=true;try{current.value=await hubAssessmentCall<HubAssessment>(props.projectId,'','POST',{source_version_id:versionId.value,mode:mode.value,cases:cases.value,policy:policy.value,confirmed:false});await action('assess');void loadHistory()}catch(error){Message.error(toErrorMessage(error,'新建测评失败'))}finally{pending.value=false}}
async function measure(item:AssessmentItem){await action('assess',{candidate_id:item.id})}
async function goToWorkshop(){if(!versionId.value||busy.value)return;launching.value=true;try{await router.push(skillWorkshopLocation(props.projectId,props.skill.id,versionId.value,current.value?.id||''));emit('close')}catch(error){Message.error(toErrorMessage(error,'进入工坊失败'))}finally{launching.value=false}}
async function generate(){await action('generate')}
function pick(item:AssessmentItem){const reason=selectionReasons.value[item.id]?.trim();if(!reason){Message.warning('请填写选择理由');return}Modal.confirm({title:'选择项目内候选基线',content:'确认已查看测评结果与 diff？选择后下轮从此私有副本继续，Hub 原件保持不变。',onOk:()=>action('select',{candidate_id:item.id,reason})})}
function submit(){Modal.confirm({title:'提交候选回 Hub',content:'确认已查看完整测评、产出与 diff？将新增候选版本并提交验证审批，不自动激活。仅项目负责人可提交。',onOk:()=>action('submit',{confirmed:true})})}
watch(()=>[props.projectId,props.skill.id,props.initialVersionId,props.initialAssessmentId],async()=>{loading.value=true;current.value=null;stopPolling();try{versions.value=await SkillHubService.listVersions(props.projectId,props.skill.id);versionId.value=props.initialVersionId||versions.value.find(item=>item.version===props.skill.version&&item.package_sha256===props.skill.version_sha256)?.id||'';await loadHistory();if(props.initialAssessmentId){await openHistory(props.initialAssessmentId);if(current.value&&props.initialVersionId&&current.value.source_version_id!==props.initialVersionId){current.value=null;throw new Error('测评会话与所选精确版本不一致')}}}catch(error){Message.error(toErrorMessage(error,'读取 Skill 版本失败'))}finally{loading.value=false}},{immediate:true})
</script>
<style scoped>
.case{padding:12px;margin:12px 0;border:1px solid var(--color-border-2);border-radius:6px;display:grid;gap:8px}.budget{margin:12px 0}.budget label{display:inline-flex;flex-direction:column;gap:5px;margin:10px}.history,.actions,header{display:flex;align-items:center;gap:12px;margin:14px 0}.history>:first-child{flex:1}.results{margin-top:24px}.items{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.result-card{border:1px solid var(--color-border-2);padding:12px;border-radius:8px;min-width:0}.result-card.selected{border-color:rgb(var(--green-6))}.result-card table{width:100%;border-collapse:collapse;margin:12px 0}.result-card th,.result-card td{padding:7px;text-align:left;border-bottom:1px solid var(--color-border-2);vertical-align:top}.result-card pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;padding:10px;background:var(--color-fill-1)}summary{cursor:pointer;color:rgb(var(--arcoblue-6))}code{font-size:11px;color:var(--color-text-3)}.result-card :deep(.arco-btn){margin-top:8px;margin-right:6px}header{flex-wrap:wrap}.actions{flex-wrap:wrap}
</style>
