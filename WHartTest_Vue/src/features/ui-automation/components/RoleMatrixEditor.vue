<template>
  <a-form-item label="执行角色">
    <div class="roles-editor">
      <p class="roles-hint">每个角色绑定对应账号，同一份脚本按所选角色分别登录执行。游客无需登录。</p>
      <section v-for="(row,index) in modelValue" :key="index" class="role-card">
        <div class="role-heading">
          <strong>角色 {{index+1}}</strong>
          <a-checkbox v-model="row.guest" @change="row.auth_state_id=null">游客（不登录）</a-checkbox>
          <a-button type="text" status="danger" @click="emit('update:modelValue',modelValue.filter((_,i)=>i!==index))">移除</a-button>
        </div>
        <div class="role-fields">
          <label>角色名称<a-input v-model="row.name" placeholder="如：主用户、认证用户" :max-length="80" /></label>
          <template v-if="!row.guest">
            <label>登录账号<a-input v-model="row.account" placeholder="手机号 / 用户名" autocomplete="off" @input="row.auth_state_id=null" /></label>
            <label>登录密码<a-input-password v-model="row.password" :placeholder="row.password_ref?'已保存，留空保留原密码':'请输入密码（无需密码可留空）'" autocomplete="new-password" /></label>
            <label>万能验证码<a-input v-model="row.captcha" placeholder="A1ad" /></label>
          </template>
        </div>
        <details v-if="!row.guest && row.auth_state_id && !row.account" class="legacy-login">
          <summary>已有登录态配置（兼容旧用例）</summary>
          <a-select v-model="row.auth_state_id" allow-clear placeholder="原有登录态">
            <a-option v-for="state in states" :key="state.id" :value="state.id">{{state.name}}</a-option>
          </a-select>
          <a-input v-model="row.identity_selector" placeholder="原身份校验 CSS" />
          <a-input v-model="row.identity_text" placeholder="原身份校验文本" />
        </details>
      </section>
      <a-button @click="addRole" :disabled="modelValue.length>=20">＋ 添加角色</a-button>
      <details v-if="modelValue.some(row=>!row.guest)" class="login-settings">
        <summary>共同登录方式（首次设置一次，应用到这组角色）</summary>
        <p class="roles-hint">账号、密码和验证码在上方填写。这里配置登录页及页面控件，登录成功标记需仅在登录后可见，避免登录失败仍继续执行。</p>
        <div class="role-fields">
          <label v-for="field in loginFields" :key="field.key">{{field.label}}
            <a-input :model-value="commonLogin[field.key]" :placeholder="field.placeholder" @input="value=>setLogin(field.key,value)" />
          </label>
        </div>
      </details>
      <span v-if="error">{{error}}</span>
    </div>
  </a-form-item>
</template>
<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {authStateApi} from '../api'
import {extractResponseData,type UiRoleConfig,type UiAuthState} from '../types'
const props=defineProps<{modelValue:UiRoleConfig[];envId?:number}>()
const emit=defineEmits<{(e:'update:modelValue',value:UiRoleConfig[]):void}>()
const states=ref<UiAuthState[]>([]),error=ref('')
type Login=NonNullable<UiRoleConfig['login']>
const emptyLogin=():Login=>({url:'',account_selector:'',password_selector:'',captcha_selector:'',submit_selector:'',success_selector:''})
const commonLogin=computed(()=>props.modelValue.find(row=>!row.guest&&row.login)?.login||emptyLogin())
const loginFields:{key:keyof Login;label:string;placeholder:string}[]=[
 {key:'url',label:'登录页地址',placeholder:'https://…'},
 {key:'account_selector',label:'账号输入框 CSS',placeholder:'使用登录页面的实际定位'},
 {key:'password_selector',label:'密码输入框 CSS（无密码可留空）',placeholder:'使用登录页面的实际定位'},
 {key:'captcha_selector',label:'验证码输入框 CSS',placeholder:'使用登录页面的实际定位'},
 {key:'submit_selector',label:'登录按钮 CSS',placeholder:'使用登录页面的实际定位'},
 {key:'success_selector',label:'登录成功标记 CSS',placeholder:'仅在登录后可见的账号菜单等'},
]
function setLogin(key:keyof Login,value:string){const login={...commonLogin.value,[key]:value};for(const row of props.modelValue)if(!row.guest)row.login={...login}}
function addRole(){emit('update:modelValue',[...props.modelValue,{name:'',guest:false,auth_state_id:null,account:'',password:'',captcha:'A1ad',login:{...commonLogin.value}}])}
watch(()=>props.envId,async env=>{
 states.value=[];error.value=''
 if(!env)return
 try{
  const response=await authStateApi.list({env_config:env,is_active:true})
  const data=extractResponseData<UiAuthState[]|{results:UiAuthState[]}>(response)
  if(props.envId===env)states.value=Array.isArray(data)?data:data?.results||[]
 }catch{if(props.envId===env)error.value='旧登录态加载失败'}
},{immediate:true})
</script>
<style scoped>
.roles-editor { width:100%; }
.roles-hint { margin:0 0 14px; font-size:13px; line-height:1.7; color:var(--color-text-3); }
.role-card { margin-bottom:12px; padding:16px; border:1px solid var(--color-border-2); border-radius:6px; background:var(--color-fill-1); }
.role-heading { display:flex; align-items:center; gap:16px; margin-bottom:12px; }
.role-heading strong { flex:1; }
.role-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px 20px; }
.role-fields label { display:flex; flex-direction:column; gap:6px; color:var(--color-text-2); font-size:13px; }
.login-settings { margin-top:20px; padding-top:16px; border-top:1px solid var(--color-border-2); }
summary { cursor:pointer; color:var(--color-text-2); margin-bottom:12px; }
.legacy-login { margin-top:12px; }
@media(max-width:600px){.role-fields{grid-template-columns:1fr}}
</style>
