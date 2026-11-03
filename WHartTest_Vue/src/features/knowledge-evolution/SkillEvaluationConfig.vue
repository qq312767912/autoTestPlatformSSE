<template>
  <section class="assessment-config">
    <h3>通用测评</h3>
    <p class="hint">所有 Skill 复用九维，共 100 分。通用分数用于诊断短板，与业务测评分别展示。</p>
    <div class="rubric-grid"><span v-for="item in profile?.general_rubric" :key="item.id">{{ item.name }} <b>{{ item.weight }}</b></span></div>
    <h3>业务测评 <a-tag size="small">当前 Skill 独立配置</a-tag></h3>
    <p class="hint">从产出物 Sheet 选择统计项，或增加自定义量表、人工评分项。需明确数值口径、好坏方向和允许波动；百分数按 0–1 表示。</p>
    <a-alert v-for="warning in profile?.warnings" :key="warning" type="warning">{{ warning }}</a-alert>
    <a-alert v-if="error" type="error">{{ error }} <a-button size="mini" @click="load">重新读取</a-button></a-alert>
    <a-spin :loading="loading" style="width:100%">
      <article v-for="(metric,index) in metrics" :key="metric.id" class="metric-card">
        <div class="metric-head"><b>指标 {{ index+1 }}</b><a-button size="mini" status="danger" @click="metrics.splice(index,1)">移除</a-button></div>
        <div class="fields">
          <label>指标名称<a-input v-model="metric.name" placeholder="例如：需求覆盖率"/></label>
          <label>指标来源<a-select v-model="metric.source" @change="()=>ensureSelector(metric)"><a-option value="sheet">产出物 Sheet 统计</a-option><a-option value="judge">自定义评委量表</a-option><a-option value="human">人工评分</a-option></a-select></label>
          <label>好坏方向<a-select v-model="metric.direction" placeholder="请选择，不自动推断"><a-option value="higher">越高越好</a-option><a-option value="lower">越低越好</a-option></a-select></label>
          <label>允许波动<a-input-number v-model="metric.tolerance" :min="0"/></label>
          <label>目标值（选填）<a-input-number v-model="metric.target" allow-clear placeholder="不设置固定目标"/></label>
          <label>必需指标<a-switch v-model="metric.required"/><small>缺失、退化或未达目标会阻止选优</small></label>
        </div>
        <template v-if="metric.source==='sheet' && metric.selector">
          <label>从原产出物快速选择统计项<a-select allow-search allow-clear placeholder="选择后可继续编辑定位规则" @change="value=>selectStatistic(metric,value)"><a-option v-for="option in statistics" :key="option.value" :value="option.value">{{ option.label }}</a-option></a-select></label>
          <div class="fields">
            <label>Sheet 名称<a-auto-complete v-model="metric.selector.sheet" :data="sheetNames" placeholder="可填写其他 Sheet"/></label>
            <label>聚合方式<a-select v-model="metric.selector.aggregate"><a-option value="cell">单个统计值</a-option><a-option value="sum">匹配行求和</a-option><a-option value="mean">匹配行均值</a-option><a-option value="count">匹配行非空数量</a-option></a-select></label>
            <label v-if="metric.selector.aggregate!=='cell'">聚合数据起始行<a-input-number v-model="metric.selector.start_row" :min="1" :max="20000" :precision="0" placeholder="排除表头，例如 4"/></label>
            <label>数值列字母<a-input v-model="metric.selector.column" placeholder="例如 B"/></label>
            <label>固定单元格（选填）<a-input v-model="metric.selector.cell" placeholder="例如 B5；填后优先读取此格"/></label>
            <label>标签匹配列（选填）<a-input v-model="metric.selector.match_column" placeholder="例如 A"/></label>
            <label>标签匹配值（选填）<a-input v-model="metric.selector.match_value" placeholder="例如 需求覆盖率"/></label>
          </div>
          <p class="hint">优先按统计标签定位。单值必须唯一匹配；聚合时请用起始行或标签条件排除表头和非数值行。无缓存的公式不会被当成 0。</p>
        </template>
        <label v-else>评估标准与数值口径<a-textarea v-model="metric.criterion" :auto-size="{minRows:2,maxRows:6}" placeholder="写明如何评估、取值范围，以及每个分值代表什么；评委缺少证据会标为不可验证。"/></label>
      </article>
      <div class="actions"><a-button :disabled="loading||metrics.length>=30" @click="addMetric">增加业务指标</a-button><a-button :loading="saving" :disabled="loading||!!error" @click="save">保存当前 Skill 配置</a-button></div>
    </a-spin>
    <p v-if="!metrics.some(item=>item.required)" class="hint warning">尚未配置必需业务指标：可查看通用测评，但不能选优发布。</p>
    <p class="hint">创建实验时冻结配置；修改这里不会追溯改变已创建实验。</p>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getSkillEvaluationProfile, saveSkillEvaluationProfile } from './service';
import type { SkillBusinessMetric, SkillEvaluationProfileView } from './types';
const props=defineProps<{projectId:number;reviewId:string}>();
const profile=ref<SkillEvaluationProfileView|null>(null),metrics=ref<SkillBusinessMetric[]>([]);
const loading=ref(false),saving=ref(false),error=ref('');
const sheetNames=computed(()=>profile.value?.sheet_catalog.map(item=>item.name)||[]);
const statistics=computed(()=>profile.value?.sheet_catalog.flatMap(sheet=>sheet.numeric_cells.map(cell=>({value:JSON.stringify(cell.selector),label:`${sheet.name}!${cell.address} · ${cell.label} = ${cell.value}`})))||[]);
function detail(error:unknown){const value=error as {response?:{data?:{detail?:string}}};return value?.response?.data?.detail||'读取或保存指标配置失败'}
async function load(){
  const projectId=props.projectId,reviewId=props.reviewId;
  if(!reviewId)return;loading.value=true;error.value='';profile.value=null;metrics.value=[];
  try{const result=await getSkillEvaluationProfile(projectId,reviewId);if(props.projectId===projectId&&props.reviewId===reviewId){profile.value=result;metrics.value=structuredClone(result.metrics)}}
  catch(cause){error.value=detail(cause)}finally{loading.value=false}
}
function addMetric(){metrics.value.push({id:`metric_${Date.now()}_${metrics.value.length}`,name:'',source:'sheet',direction:'',required:true,tolerance:0,target:null,criterion:'',selector:{sheet:'',column:'B',cell:'',aggregate:'cell',start_row:1,match_column:'',match_value:''}})}
function ensureSelector(metric:SkillBusinessMetric){if(metric.source==='sheet'&&!metric.selector)metric.selector={sheet:'',column:'B',cell:'',aggregate:'cell',start_row:1,match_column:'',match_value:''}}
function selectStatistic(metric:SkillBusinessMetric,value:unknown){if(typeof value==='string'&&value){metric.selector=JSON.parse(value);if(!metric.name)metric.name=metric.selector?.match_value||`${metric.selector?.sheet}!${metric.selector?.cell}`}}
async function save():Promise<boolean>{
  if(loading.value||saving.value||error.value)return false;
  saving.value=true;
  try{const result=await saveSkillEvaluationProfile(props.projectId,props.reviewId,metrics.value);profile.value=result;metrics.value=structuredClone(result.metrics);Message.success('业务指标已按当前 Skill 保存');return true}
  catch(cause){Message.error(detail(cause));return false}finally{saving.value=false}
}
watch(()=>[props.projectId,props.reviewId],()=>{void load()},{immediate:true});
defineExpose({save});
</script>
<style scoped>
.assessment-config{margin:16px 0;padding:16px;border:1px solid var(--color-border-2);border-radius:8px}.assessment-config h3{margin:0 0 10px}.assessment-config h3:not(:first-child){margin-top:20px}.hint{color:var(--color-text-3);font-size:12px;line-height:1.7}.rubric-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.rubric-grid span{display:flex;justify-content:space-between;padding:8px;background:var(--color-fill-1);border-radius:4px}.metric-card{margin-top:12px;padding:12px;border:1px solid var(--color-border-2);border-radius:6px}.metric-head,.actions{display:flex;align-items:center;justify-content:space-between;gap:8px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}label{display:flex;flex-direction:column;gap:6px;font-size:12px}label small{color:var(--color-text-3)}label :deep(.arco-switch){align-self:flex-start;width:auto}.actions{justify-content:flex-start;margin-top:12px}.warning{color:rgb(var(--orange-6))}@media(max-width:640px){.rubric-grid,.fields{grid-template-columns:1fr}}
</style>
