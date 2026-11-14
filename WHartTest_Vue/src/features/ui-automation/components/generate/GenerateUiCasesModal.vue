<template>
  <a-modal :visible="visible" :title="tl('AI 生成 UI 自动化用例')" :width="780" :confirm-loading="busy" :ok-button-props="{ disabled: loading || busy }" :mask-closable="false" @ok="submit" @cancel="close">
    <a-alert style="margin-bottom:16px">{{ tl('每条功能用例生成一条 UI 用例；试跑成功后绑定，失败保留记录。需要登录时请先采集登录态。') }}</a-alert>
    <a-form :model="form" layout="vertical">
      <a-form-item :label="tl('执行 Skill')" required>
        <a-select v-model="skillId" :loading="loading" allow-search @change="loadVersions">
          <a-option v-for="s in options.skills" :key="s.id" :value="s.id">{{ s.name }}</a-option>
        </a-select>
      </a-form-item>
      <a-form-item :label="tl('精确版本')" required>
        <a-select v-model="form.skillVersionId" :loading="versionLoading">
          <a-option v-for="v in versions" :key="v.id" :value="v.id">{{ v.version }}</a-option>
        </a-select>
      </a-form-item>
      <a-form-item :label="tl('功能用例')" required>
        <a-space><a-button @click="picker=true">{{ tl('选择功能用例') }}</a-button><span>{{ form.functional_case_ids.length }} / 100</span></a-space>
      </a-form-item>
      <a-form-item :label="tl('保存模块')" required>
        <a-select v-model="form.module_id" allow-search><a-option v-for="m in options.modules" :key="m.id" :value="m.id">{{ m.name }}</a-option></a-select>
      </a-form-item>
      <a-form-item :label="tl('执行环境')" required>
        <a-select v-model="form.env_config_id" @change="loadAuth"><a-option v-for="env in options.environments" :key="env.id" :value="env.id">{{ env.name }} · {{ env.base_url || tl('未配置 URL') }}</a-option></a-select>
      </a-form-item>
      <a-form-item :label="tl('登录态')">
        <a-select v-model="form.auth_state_id" :loading="authLoading" :disabled="!form.env_config_id">
          <a-option :value="0">{{ tl('无鉴权') }}</a-option><a-option v-for="state in authStates" :key="state.id" :value="state.id">{{ state.name }}</a-option>
        </a-select>
      </a-form-item>
      <a-form-item :label="tl('业务补充')"><a-textarea v-model="form.businessNotes" :max-length="2000" /></a-form-item>
    </a-form>
    <SelectFunctionalCasesModal v-model:visible="picker" :selected-ids="form.functional_case_ids" @confirm="ids => form.functional_case_ids=ids" />
  </a-modal>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue';
import { Message, Notification } from '@arco-design/web-vue';
import { useRouter } from 'vue-router';
import { useAppI18n } from '@/composables/useAppI18n';
import { useProjectStore } from '@/store/projectStore';
import SelectFunctionalCasesModal from './SelectFunctionalCasesModal.vue';
import { loadUiGenerationOptions, loadUiGenerationVersions, loadUiGenerationAuth, startUiGeneration, type UiGenerationFormData } from '../../services/uiCaseGeneration';
const props=defineProps<{visible:boolean}>();
const emit=defineEmits<{(e:'update:visible',value:boolean):void}>();
const {tl}=useAppI18n(); const projectStore=useProjectStore(); const router=useRouter();
const empty=():UiGenerationFormData=>({functional_case_ids:[],module_id:null,env_config_id:null,auth_state_id:0,skillVersionId:'',businessNotes:''});
const form=ref(empty()); const skillId=ref<number>(); const versions=ref<any[]>([]); const authStates=ref<any[]>([]);
const options=ref<{modules:any[];environments:any[];skills:any[]}>({modules:[],environments:[],skills:[]});
const busy=ref(false),loading=ref(false),picker=ref(false),versionLoading=ref(false),authLoading=ref(false);
let epoch=0,versionRequest=0,authRequest=0; let openingProject:number|null=null;
const close=()=>{if(!busy.value){epoch++;picker.value=false;emit('update:visible',false);}};
const fail=(e:any)=>Message.error(e?.message || tl('读取生成配置失败'));
async function loadVersions(){const id=++versionRequest;const project=openingProject;const skill=skillId.value;form.value.skillVersionId='';versions.value=[];if(!project||!skill)return;versionLoading.value=true;try{const rows=await loadUiGenerationVersions(project,skill);if(id!==versionRequest||openingProject!==project)return;versions.value=rows;const active=options.value.skills.find(s=>s.id===skill)?.version_id;form.value.skillVersionId=rows.find(v=>v.id===active)?.id||'';}catch(e){if(id===versionRequest)fail(e);}finally{if(id===versionRequest)versionLoading.value=false;}}
async function loadAuth(){const id=++authRequest;const env=form.value.env_config_id;form.value.auth_state_id=0;authStates.value=[];if(!env)return;authLoading.value=true;try{const rows=await loadUiGenerationAuth(env);if(id===authRequest&&env===form.value.env_config_id)authStates.value=rows;}catch(e){if(id===authRequest)fail(e);}finally{if(id===authRequest)authLoading.value=false;}}
watch(()=>props.visible,async visible=>{if(!visible)return;const id=++epoch;versionRequest++;authRequest++;form.value=empty();skillId.value=undefined;versions.value=[];authStates.value=[];options.value={modules:[],environments:[],skills:[]};openingProject=projectStore.currentProjectId; if(!openingProject)return;loading.value=true;try{const data=await loadUiGenerationOptions(openingProject);if(id===epoch)options.value=data;}catch(e){if(id===epoch)fail(e);}finally{if(id===epoch)loading.value=false;}});
watch(()=>projectStore.currentProjectId,()=>{if(props.visible)close();});
async function submit(){if(busy.value||loading.value||versionLoading.value||authLoading.value)return;if(!openingProject||projectStore.currentProjectId!==openingProject)return fail(new Error(tl('项目已切换，请重新选择')));if(!form.value.skillVersionId||!form.value.module_id||!form.value.env_config_id||!form.value.functional_case_ids.length||form.value.functional_case_ids.length>100)return Message.warning(tl('请选择 Skill 精确版本、1至100条功能用例、模块和环境'));busy.value=true;try{await startUiGeneration(openingProject,{...form.value,auth_state_id:form.value.auth_state_id||null},message=>Notification.error({title:tl('生成任务失败'),content:message,duration:8000}));emit('update:visible',false);Notification.info({title:tl('UI 生成任务已启动'),content:tl('可在 LLM 对话页查看进度'),duration:8000});router.push({name:'LangGraphChat'});}catch(e){fail(e);}finally{busy.value=false;}}
</script>
