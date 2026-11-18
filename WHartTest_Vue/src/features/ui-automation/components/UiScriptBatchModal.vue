<template>
  <a-modal :visible="visible" :title="mode==='configure'?'设置所选用例的默认角色':'普通执行 · 角色与进度'" :width="900" :footer="false" @cancel="emit('update:visible',false)">
    <a-alert>同一份用例脚本按所选角色分别运行。批次按可用执行槽位排队，录制器浏览器串行执行；关闭窗口不停止后台任务。</a-alert>
    <a-alert v-if="error" type="error" style="margin-top:12px">{{error}}</a-alert>
    <a-form v-if="!batch" layout="vertical" style="margin-top:16px">
      <a-form-item label="执行环境"><a-select v-model="env"><a-option v-for="item in environments" :key="item.id" :value="item.id">{{item.name}}</a-option></a-select></a-form-item>
      <a-checkbox v-if="mode==='execute'" v-model="shared">为这次执行统一选择角色（可选多个，不修改默认配置）</a-checkbox>
      <p v-if="mode==='execute'&&!shared">按每条用例已有配置执行；没有角色配置的用例按原步骤运行一次。</p>
      <RoleMatrixEditor v-if="shared || mode==='configure'" v-model="roles" :env-id="env" />
      <p v-if="mode==='configure'">会将所选 {{ids.length}} 条用例的默认角色配置替换为这组角色；每条用例仍可遍历多个角色，不复制脚本。</p>
      <a-button type="primary" :loading="busy" @click="start">{{mode==='configure'?'保存到所选用例':`开始执行 ${ids.length} 条用例`}}</a-button>
    </a-form>
    <template v-else>
      <a-space style="margin:12px 0"><a-tag>{{statusText}}</a-tag><a-button :loading="busy" @click="refresh">刷新进度</a-button><a-button v-if="active&&!batch.cancel_requested" @click="cancel">停止后续执行</a-button><a-button v-if="!active" @click="newBatch">配置新批次</a-button></a-space>
      <p>通过 {{batch.passed}} / {{batch.total}}；失败 {{batch.failed}}。{{batch.cancel_requested?'已请求停止，未开始的项目不会执行。':''}}</p>
      <a-table :data="batch.items" :pagination="false" row-key="id" :scroll="{y:420}"><template #columns>
        <a-table-column title="用例" data-index="name" />
        <a-table-column title="状态" :width="100"><template #cell="{record}">{{labels[record.status]||record.status}}</template></a-table-column>
        <a-table-column title="执行记录" data-index="record_id" :width="100" />
        <a-table-column title="进度与结果"><template #cell="{record}"><div>{{record.message}}</div><div v-for="row in record.role_results||[]" :key="row.role">{{row.role}}：{{row.status==='success'?'通过':row.status==='not_run'?'未执行':'失败'}} · {{row.record_id||'无记录'}} {{row.message||''}}</div></template></a-table-column>
      </template></a-table>
    </template>
  </a-modal>
</template>
<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {Modal,Message} from '@arco-design/web-vue'
import request from '@/utils/request'
import RoleMatrixEditor from './RoleMatrixEditor.vue'
import {extractResponseData,type UiEnvironmentConfig,type UiRoleConfig} from '../types'
const props=defineProps<{visible:boolean;project:number;ids:number[];environments:UiEnvironmentConfig[];initialEnv?:number;actuatorId?:string;mode:'execute'|'configure'}>()
const emit=defineEmits<{(e:'update:visible',v:boolean):void;(e:'finished'):void}>()
interface RoleResult{role:string;status:string;record_id?:number;message?:string}
interface Batch{ id:number;status:number;cancel_requested:boolean;passed:number;failed:number;total:number;items:{id:number;name:string;status:string;record_id?:number;message?:string;role_results?:RoleResult[]}[] }
const batch=ref<Batch>(),roles=ref<UiRoleConfig[]>([]),env=ref<number>(),shared=ref(false),busy=ref(false),error=ref('')
let timer:ReturnType<typeof setTimeout>|undefined,epoch=0
const active=computed(()=>batch.value && [0,1].includes(batch.value.status))
const statusText=computed(()=>batch.value?.cancel_requested&&!active.value?'已停止':['排队中','执行中','全部通过','部分未通过','未通过'][batch.value?.status||0])
const labels:Record<string,string>={queued:'排队中',running:'执行中',waiting:'等待槽位',passed:'通过',failed:'失败',cancelled:'未执行/停止'}
const key=()=>`ui-script-batch:${props.project}`
function failure(e:unknown){const v=e as {error?:string;message?:string;errors?:{detail?:string}|string[]};error.value=v.error||v.message||(Array.isArray(v.errors)?v.errors.join('；'):v.errors?.detail)||'请求失败，请重试'}
function newBatch(){clearTimeout(timer);batch.value=undefined;error.value='';localStorage.removeItem(key())}
async function refresh(){if(!batch.value)return;const current=epoch,id=batch.value.id;clearTimeout(timer);busy.value=true;try{const res=await request.get(`/ui-automation/script-batches/${id}/`);if(current!==epoch)return;batch.value=extractResponseData<Batch>(res);error.value='';if(!active.value)emit('finished')}catch(e){if(current===epoch){failure(e);const v=e as {status?:number;response?:{status?:number}};if([403,404].includes(v.status||v.response?.status||0)){batch.value=undefined;localStorage.removeItem(key())}}}finally{if(current===epoch){busy.value=false;if(active.value&&props.visible)timer=setTimeout(refresh,3000)}}}
async function start(){if(busy.value)return;error.value='';if(!props.ids.length){error.value='请先选择用例';return}if((shared.value||props.mode==='configure')&&!roles.value.length){error.value='请添加要遍历的角色';return}if(props.mode==='execute'&&!env.value){error.value='请选择执行环境';return}busy.value=true;const current=epoch;try{if(props.mode==='configure'){await request.post('/ui-automation/script-batches/configure-roles/',{project:props.project,ids:props.ids,role_matrix:roles.value});if(current!==epoch)return;Message.success(`已为 ${props.ids.length} 条用例配置角色`);emit('finished');emit('update:visible',false)}else{const res=await request.post('/ui-automation/script-batches/',{project:props.project,ids:props.ids,env_config_id:env.value,actuator_id:props.actuatorId||'recorder-browser',role_matrix:shared.value?roles.value:null});if(current!==epoch)return;batch.value=extractResponseData<Batch>(res);if(!batch.value?.id)throw Error('未取得批次');localStorage.setItem(key(),String(batch.value.id));timer=setTimeout(refresh,1000)}}catch(e){if(current===epoch)failure(e)}finally{if(current===epoch)busy.value=false}}
function cancel(){Modal.confirm({title:'停止批次？',content:'停止当前请求及后续用例，保留已有结果。',onOk:async()=>{try{await request.post(`/ui-automation/script-batches/${batch.value?.id}/cancel/`);await refresh()}catch(e){failure(e)}}})}
watch(()=>[props.visible,props.project,props.mode] as const,([visible])=>{epoch++;clearTimeout(timer);busy.value=false;if(!visible)return;error.value='';env.value=props.initialEnv||props.environments.find(x=>x.is_default)?.id||props.environments[0]?.id;batch.value=undefined;roles.value=[];shared.value=props.mode==='configure';if(props.mode==='execute'){const saved=Number(localStorage.getItem(key()));if(saved){batch.value={id:saved,status:0,items:[],passed:0,failed:0,total:0,cancel_requested:false};void refresh()}}})
onUnmounted(()=>{epoch++;clearTimeout(timer)})
</script>
