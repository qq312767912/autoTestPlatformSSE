<template>
  <section v-if="experiment.assessment_config?.schema" class="assessment-results">
    <h3>通用测评 <small>九维诊断 · 不与业务指标合并计分</small></h3>
    <template v-if="report.general">
      <div class="scroll-table"><table><thead><tr><th>维度 / 权重</th><th v-for="label in labels" :key="label">{{ label }}</th></tr></thead>
        <tbody><tr v-for="dimension in report.general.rubric" :key="dimension.id"><td>{{ dimension.name }} / {{ dimension.weight }}</td><td v-for="label in labels" :key="label"><b>{{ generalDimension(label,dimension.id)?.score ?? '—' }}</b><details v-if="generalDimension(label,dimension.id)"><summary>依据</summary><p>{{ generalDimension(label,dimension.id)?.reason }}</p><small>{{ generalDimension(label,dimension.id)?.evidence?.join('；') }}</small></details></td></tr>
          <tr><th>通用总分 / 100</th><td v-for="label in labels" :key="label"><b>{{ report.general.results[label]?.total ?? '—' }}</b><small v-if="report.general.results[label]?.reason">{{ report.general.results[label]?.reason }}</small></td></tr>
        </tbody></table></div>
      <p v-for="label in labels" :key="label" class="hint">{{ label }} 优先短板：{{ report.general.results[label]?.shortfalls?.slice(0,3).map(item=>`${item.name}（可改善 ${item.weighted_gap} 分）`).join('；') || '尚无完整评分' }}</p>
    </template>
    <p v-else class="hint">真实重跑后执行通用九维测评。</p>
    <h3>业务测评 <small>按当前 Skill 冻结的业务指标</small></h3>
    <template v-if="report.business">
      <div class="scroll-table"><table><thead><tr><th>指标 / 判定规则</th><th v-for="label in labels" :key="label">{{ label }}</th></tr></thead>
        <tbody><tr v-for="metric in report.business.metrics" :key="metric.id"><td><b>{{ metric.name }}</b><small>{{ sourceName(metric.source) }} · {{ metric.required?'必需':'观察' }} · {{ metric.direction==='higher'?'越高越好':'越低越好' }} · 容差 {{ metric.tolerance }}<template v-if="metric.target!==null"> · 目标 {{ metric.target }}</template></small></td>
          <td v-for="label in labels" :key="label"><b>{{ businessValue(label,metric.id)?.value ?? '—' }}</b><small>{{ statusText(businessValue(label,metric.id)?.status) }} {{ businessValue(label,metric.id)?.reason }}</small><details v-if="businessValue(label,metric.id)?.evidence?.length"><summary>来源证据</summary>{{ businessValue(label,metric.id)?.evidence?.join('；') }}</details></td></tr>
        </tbody></table></div>
      <p v-if="!report.business.metrics.length" class="hint warning">未配置业务指标，本轮不能选优发布。请在新建实验时配置。</p>
    </template>
    <p v-else class="hint">重跑后读取产出 Excel 的 Sheet 统计，并执行自定义评委量表。</p>
    <template v-if="humanMetrics.length && experiment.state==='awaiting_choice'">
      <h4>补充人工业务评分</h4>
      <p class="hint">依据各变体的实际产出填写；不能把基线的人评结果直接当作候选结果。提交后保留评分人、时间和证据。</p>
      <article v-for="metric in humanMetrics" :key="metric.id" class="human-metric"><b>{{ metric.name }}</b><p class="hint">{{ metric.criterion }}</p><div class="human-grid"><label v-for="label in labels" :key="label">{{ label }}<a-input-number v-model="form[label][metric.id]!.value" placeholder="实际评估值" allow-clear/><a-textarea v-model="form[label][metric.id]!.evidence" placeholder="产出位置和评分依据" :auto-size="{minRows:2,maxRows:4}"/></label></div></article>
      <a-button :loading="saving" @click="saveScores">保存人工业务评分并重新判定</a-button>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { recordEvolutionBusinessScores } from './service';
import type { EvolutionExperimentView } from './types';
const props=defineProps<{projectId:number;experiment:EvolutionExperimentView}>();
const emit=defineEmits<{updated:[value:EvolutionExperimentView]}>();
const labels=['baseline','A','B'] as const;
type Label=typeof labels[number];
const report=computed(()=>props.experiment.evaluation_report);
const humanMetrics=computed(()=>props.experiment.assessment_config?.business_metrics?.filter(item=>item.source==='human')||[]);
const saving=ref(false);
const form=ref<Record<Label,Record<string,{value:number|null;evidence:string}>>>({baseline:{},A:{},B:{}});
watch(()=>props.experiment,()=>{
  for(const label of labels){form.value[label]={};for(const metric of humanMetrics.value){const saved=report.value.manual_values?.[label]?.[metric.id];form.value[label][metric.id]={value:saved?.value??null,evidence:saved?.evidence||''}}}
},{immediate:true});
function generalDimension(label:Label,id:string){return report.value.general?.results[label]?.dimensions?.find(item=>item.id===id)}
function businessValue(label:Label,id:string){return report.value.business?.results[label]?.[id]}
const statusText=(status?:string)=>({completed:'已完成',pending:'待人工评分',missing:'缺少数据',failed:'测评失败',unverifiable:'无法验证'} as Record<string,string>)[status||'']||'待测评';
const sourceName=(source:string)=>({sheet:'Sheet 统计',judge:'自定义评委量表',human:'人工评分'} as Record<string,string>)[source]||source;
async function saveScores(){
  const values:Record<string,Record<string,{value:number;evidence:string}>>={};
  for(const label of labels){for(const metric of humanMetrics.value){const entry=form.value[label][metric.id];if(entry&&entry.value!==null&&entry.value!==undefined){if(!entry.evidence.trim()){Message.warning(`请填写 ${label}「${metric.name}」的评分依据`);return}(values[label]??={})[metric.id]={value:entry.value,evidence:entry.evidence}}}}
  if(!Object.keys(values).length){Message.warning('请先填写人工评分和依据');return}
  const projectId=props.projectId,experimentId=props.experiment.experiment_id;saving.value=true;
  try{const result=await recordEvolutionBusinessScores(projectId,experimentId,values);if(props.projectId===projectId&&props.experiment.experiment_id===experimentId){emit('updated',result);Message.success('人工业务评分已保存，选优资格已重新判定')}}
  catch(error){Message.error((error as {response?:{data?:{detail?:string}}})?.response?.data?.detail||'保存人工评分失败')}
  finally{saving.value=false}
}
</script>
<style scoped>
.assessment-results{margin:16px 0;padding:16px;border:1px solid var(--color-border-2);border-radius:8px}.assessment-results h3{margin:0 0 12px}.assessment-results h3:not(:first-child){margin-top:24px}.assessment-results h3 small{margin-left:8px;color:var(--color-text-3);font-size:12px;font-weight:400}.scroll-table{overflow:auto}table{border-collapse:collapse;width:100%;min-width:600px;table-layout:fixed}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--color-border-2);padding:10px;overflow-wrap:anywhere}th{background:var(--color-fill-1)}td small{display:block;color:var(--color-text-3);line-height:1.6;margin-top:5px}summary{color:rgb(var(--arcoblue-6));cursor:pointer;font-size:12px}.hint{font-size:12px;color:var(--color-text-3);line-height:1.7}.warning{color:rgb(var(--orange-6))}.human-metric{margin:12px 0;padding:12px;background:var(--color-fill-1);border-radius:6px}.human-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.human-grid label{display:flex;flex-direction:column;gap:8px}@media(max-width:700px){.human-grid{grid-template-columns:1fr}}
</style>
