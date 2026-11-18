<template>
  <a-form-item label="执行角色（可选择多个）">
    <a-space direction="vertical" fill>
      <span>同一份脚本依次遍历这组角色，全部通过才算通过。登录态来自当前执行环境。</span>
      <a-space v-for="(row,index) in modelValue" :key="index" wrap>
        <a-input v-model="row.name" placeholder="角色名称" :max-length="80" />
        <a-checkbox v-model="row.guest" @change="row.auth_state_id=null">游客</a-checkbox>
        <a-select v-if="!row.guest" v-model="row.auth_state_id" placeholder="选择登录态（可稍后配置）" allow-clear style="width:260px">
          <a-option v-for="state in states" :key="state.id" :value="state.id">{{state.name}}</a-option>
        </a-select>
        <a-input v-if="!row.guest" v-model="row.identity_selector" placeholder="身份校验 CSS，如 .username" />
        <a-input v-if="!row.guest" v-model="row.identity_text" placeholder="该账号应显示的身份文本" />
        <a-button @click="emit('update:modelValue',modelValue.filter((_,i)=>i!==index))">移除</a-button>
      </a-space>
      <a-button :disabled="modelValue.length>=20" @click="emit('update:modelValue',[...modelValue,{name:'',guest:false,auth_state_id:null}])">添加执行角色</a-button>
      <span v-if="error">{{error}}</span>
    </a-space>
  </a-form-item>
</template>
<script setup lang="ts">
import {ref,watch} from 'vue'
import {authStateApi} from '../api'
import {extractResponseData,type UiRoleConfig,type UiAuthState} from '../types'
const props=defineProps<{modelValue:UiRoleConfig[];envId?:number}>()
const emit=defineEmits<{(e:'update:modelValue',value:UiRoleConfig[]):void}>()
const states=ref<UiAuthState[]>([]),error=ref('')
watch(()=>props.envId,async env=>{
  states.value=[];error.value=''
  if(!env)return
  try{
    const response=await authStateApi.list({env_config:env,is_active:true})
    const data=extractResponseData<UiAuthState[]|{results:UiAuthState[]}>(response)
    if(props.envId===env)states.value=Array.isArray(data)?data:data?.results||[]
  }catch{if(props.envId===env)error.value='登录态加载失败，请重新打开用例编辑'}
},{immediate:true})
</script>
