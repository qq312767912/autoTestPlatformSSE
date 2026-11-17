<template>
  <a-modal :visible="visible" title="UI 用例批量执行并自愈" :width="900" :footer="false" @cancel="close">
    <a-alert style="margin-bottom:16px">先执行脚本，仅对失败项尝试一次 AI 修复，再重跑验证。保留业务断言；关闭窗口不停止后台任务。使用平台浏览器，登录态沿用各用例的页面步骤配置。</a-alert>
    <template v-if="!session">
      <a-form :model="form" layout="vertical">
        <a-form-item label="执行环境" required><a-select v-model="form.env" placeholder="请选择环境"><a-option v-for="env in environments" :key="env.id" :value="env.id">{{ env.name }} · {{ env.base_url }}</a-option></a-select></a-form-item>
        <p>已选择 {{ ids.length }} 条 UI 用例。修复会保存到原有资产，共享元素与步骤的修改也会影响引用它们的用例。</p>
        <a-button type="primary" :loading="busy" @click="start">开始批量执行并自愈</a-button>
      </a-form>
    </template>
    <template v-else>
      <a-space style="margin-bottom:12px">
        <a-tag>{{ label(run?.status || 'queued') }}</a-tag>
        <a-button :loading="busy" @click="refresh">刷新进度</a-button>
        <a-button @click="openChat">批次会话</a-button>
        <a-dropdown v-if="active && !run?.cancel_requested" @select="confirmCancel"><a-button type="text">更多操作</a-button><template #content><a-doption>停止后续执行</a-doption></template></a-dropdown>
        <a-button v-if="!active" @click="reset">配置新批次</a-button>
      </a-space>
      <a-alert v-if="error" type="error" style="margin-bottom:12px">{{ error }}</a-alert>
      <a-alert v-if="run?.cancel_requested" style="margin-bottom:12px">正在停止后续任务；已下发的脚本可能仍在收尾。</a-alert>
      <a-table :data="run?.items || []" :pagination="false" row-key="id" :scroll="{y:420}"><template #columns>
        <a-table-column title="UI 用例" data-index="name" />
        <a-table-column title="状态" :width="120"><template #cell="{record}">{{ label(record.status) }}</template></a-table-column>
        <a-table-column title="首跑记录" data-index="initial_record_id" :width="90" />
        <a-table-column title="重跑记录" data-index="retry_record_id" :width="90" />
        <a-table-column title="说明" data-index="message" />
      </template></a-table>
    </template>
    <div style="display:flex;justify-content:flex-end;margin-top:20px"><a-button @click="close">关闭</a-button></div>
  </a-modal>
</template>
<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { Modal } from '@arco-design/web-vue'
import { useRouter } from 'vue-router'
import request from '@/utils/request'
import { extractResponseData, type UiEnvironmentConfig } from '../types'
const props = defineProps<{visible:boolean; project:number; ids:number[]; environments:UiEnvironmentConfig[]; initialEnv?:number}>()
const emit = defineEmits<{(e:'update:visible', value:boolean):void; (e:'finished'):void}>()
interface Run {status:string; cancel_requested?:boolean; error?:string; items:{id:number;name:string;status:string;message?:string;initial_record_id?:number;retry_record_id?:number}[]}
const router=useRouter(), session=ref(''), run=ref<Run>(), busy=ref(false), error=ref(''), form=ref<{env?:number}>({})
const active=computed(()=>['queued','running'].includes(run.value?.status || ''))
let timer:ReturnType<typeof setTimeout>|undefined, epoch=0
const labels:Record<string,string>={queued:'排队中',running:'执行中',healing:'AI 修复中',verifying:'重跑验证中',passed:'首跑通过',healed:'自愈通过',failed:'失败／待处理',cancelled:'已停止',completed:'全部通过',partial_failure:'部分未通过'}
const label=(value:string)=>labels[value] || value
const key=()=>`ui-healing-run:${props.project}`
function fail(e:unknown){const data=(e as any)?.response?.data || e;error.value=data?.data?.detail || data?.detail || data?.message || '请求失败，请重试'}
function close(){emit('update:visible',false)}
function reset(){epoch++;clearTimeout(timer);localStorage.removeItem(key());session.value='';run.value=undefined;error.value=''}
function openChat(){router.push({name:'LangGraphChat',query:{skill_task:session.value,project:String(props.project)}})}
async function refresh(){const id=epoch, project=props.project, target=session.value;if(!target)return;clearTimeout(timer);busy.value=true;try{const res=await request.get('/ui-automation/healing-batches/',{params:{project,session_id:target}});if(id!==epoch)return;const data=extractResponseData<{run:Run}>(res);run.value=data?.run;error.value=run.value?.error || '';if(active.value && props.visible)timer=setTimeout(refresh,3000);else if(run.value)emit('finished')}catch(e){if(id===epoch)fail(e)}finally{if(id===epoch)busy.value=false}}
async function start(){if(!form.value.env||!props.ids.length){error.value='请选择 UI 用例和执行环境';return}const id=epoch;busy.value=true;error.value='';try{const res=await request.post('/ui-automation/healing-batches/',{project:props.project,ids:props.ids,env_config_id:form.value.env});if(id!==epoch)return;const data=extractResponseData<{session_id:string;run:Run}>(res);if(!data?.session_id)throw Error('未取得批次');session.value=data.session_id;run.value=data.run;localStorage.setItem(key(),session.value);void refresh()}catch(e){if(id===epoch)fail(e)}finally{if(id===epoch)busy.value=false}}
function confirmCancel(){Modal.confirm({title:'停止后续自愈？',content:'已完成的结果和修复会保留。当前已下发的脚本可能仍在收尾。',onOk:async()=>{try{await request.post('/ui-automation/healing-batches/cancel/',{project:props.project,session_id:session.value});await refresh()}catch(e){fail(e)}}})}
watch(()=>[props.visible,props.project] as const,([visible])=>{epoch++;clearTimeout(timer);busy.value=false;if(!visible)return;form.value={env:props.initialEnv || props.environments.find(x=>x.is_default)?.id || props.environments[0]?.id};error.value='';session.value=localStorage.getItem(key()) || '';run.value=undefined;if(session.value)void refresh()})
onUnmounted(()=>{epoch++;clearTimeout(timer)})
</script>
