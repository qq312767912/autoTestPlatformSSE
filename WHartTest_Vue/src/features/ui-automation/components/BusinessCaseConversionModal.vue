<template>
  <a-modal :visible="visible" title="业务用例转 UI 用例" :width="1080" :footer="false" :mask-closable="false" @cancel="close">
    <a-alert type="info" style="margin-bottom:16px">使用平台内置 excel-to-ui-case 转换规则。原业务用例保留；本次保存待采集草稿，不会自动采集元素或执行被测系统。</a-alert>
    <a-steps :current="stage" style="margin-bottom:20px">
      <a-step title="选择业务用例" /><a-step title="确认转换预览" /><a-step title="查看保存结果" />
    </a-steps>
    <template v-if="stage === 1">
      <div class="conversion-toolbar">
        <div class="conversion-module">
          <span>目标 UI 模块 <span class="conversion-required">*</span></span>
        <a-select v-model="targetModule" placeholder="请选择目标 UI 模块（必选）" style="width:300px" :disabled="busy" :error="moduleError">
          <a-option v-for="item in modules" :key="item.id" :value="item.id">{{ item.name }}</a-option>
        </a-select>
        </div>
        <a-input-search v-model="search" placeholder="搜索测试用例管理中的用例名称" style="width:360px" :disabled="busy" @search="searchSources" />
        <span class="conversion-selected">已选 {{ selected.length }} / 20 条</span>
      </div>
      <a-alert v-if="!modules.length" type="warning" style="margin-bottom:12px">当前项目暂无 UI 模块，请先在 UI 自动化中创建模块，再导入业务用例。</a-alert>
      <a-table :data="sources" :loading="loading" row-key="id" :selected-keys="selected" :row-selection="{ type:'checkbox', showCheckedAll:true }"
        :pagination="{ current:page, pageSize:20, total:total }" @selection-change="selectRows" @page-change="changePage">
        <template #columns>
        <a-table-column title="编号" data-index="id" :width="90"><template #cell="{record}">TC{{ record.id }}</template></a-table-column>
        <a-table-column title="业务用例" data-index="name" />
        <a-table-column title="来源模块" data-index="module_name" :width="180" />
        <a-table-column title="优先级" data-index="level" :width="90" />
        </template>
      </a-table>
      <a-empty v-if="!loading && !sources.length" description="当前项目暂无匹配的业务用例" />
      <div class="conversion-footer"><span class="conversion-hint">{{ !targetModule ? '请先选择目标 UI 模块' : !selected.length ? '请勾选需要转换的业务用例' : `将转换 ${selected.length} 条业务用例` }}</span><a-button @click="close">取消</a-button><a-button type="primary" :loading="busy" :disabled="loading || busy" @click="convert">生成转换预览</a-button></div>
    </template>
    <template v-else-if="stage === 2">
      <p>选中 {{ sourceCount }} 条业务用例，转换为 {{ entries.length }} 条 UI 草稿。目标模块：{{ targetModuleName }}</p>
      <a-alert type="warning" style="margin-bottom:12px">请核对角色、步骤及预期。保存后仍需采集真实定位、构建操作和断言，才能作为可执行用例使用。</a-alert>
      <div class="conversion-previews">
        <article v-for="(item,index) in entries" :key="index" class="conversion-card">
          <div class="conversion-card-title"><b>{{ item.name }}</b><a-tag>{{ item.level }}</a-tag></div>
          <p>来源：TC{{ item.source_id }} · {{ item.source_name }}　角色：{{ item.role }}</p>
          <p class="conversion-text">前置条件：{{ item.preconditions || '待补充' }}</p>
          <ol><li v-for="(step,n) in item.steps" :key="n"><div class="conversion-text">{{ step.action }}</div><div class="conversion-expected">预期：{{ step.expected_result }}</div></li></ol>
          <div class="conversion-gaps"><b>待补充：</b>{{ item.gaps.join('；') }}</div>
        </article>
      </div>
      <div class="conversion-footer"><a-button :disabled="busy" @click="back">返回选择</a-button><a-button type="primary" :loading="busy" @click="save">确认保存 UI 草稿</a-button></div>
    </template>
    <template v-else>
      <a-result status="success" title="UI 草稿已保存" :subtitle="`新增 ${saved?.created_count || 0} 条，复用 ${saved?.reused_count || 0} 条；已构建 0 条，已验证 0 条。`" />
      <a-table :data="saved?.results || []" :pagination="false" row-key="id">
        <template #columns>
        <a-table-column title="源用例" data-index="source_id"><template #cell="{record}">TC{{ record.source_id }}</template></a-table-column>
        <a-table-column title="UI 用例 ID" data-index="id" />
        <a-table-column title="名称" data-index="name" />
        <a-table-column title="处理结果"><template #cell="{record}"><a-tag :color="record.result === 'created' ? 'green':'blue'">{{ record.result === 'created' ? '新增待采集草稿':'复用已有用例，内容未覆盖' }}</a-tag><div v-if="record.source_changed" style="color:var(--color-text-3)">源内容已变化，请核对已有用例</div></template></a-table-column>
        </template>
      </a-table>
      <div class="conversion-footer"><a-button type="primary" @click="close">完成</a-button></div>
    </template>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import request from '@/utils/request'
import type { UiModule } from '../types'
import { extractResponseData } from '../types'
interface Source { id:number; name:string; module_name:string; level:string }
interface Entry { source_id:number;source_name:string;name:string;role:string;level:string;preconditions:string;steps:Array<{action:string;expected_result:string}>;gaps:string[] }
interface Saved { created_count:number;reused_count:number;results:Array<{source_id:number;role:string;id:number;name:string;result:string;source_changed?:boolean;note?:string}> }
const props=defineProps<{visible:boolean;projectId:number;modules:UiModule[];initialModuleId?:number}>()
const emit=defineEmits<{(e:'update:visible',value:boolean):void;(e:'saved'):void}>()
const stage=ref(1),page=ref(1),total=ref(0),sourceCount=ref(0)
const loading=ref(false),busy=ref(false),search=ref(''),targetModule=ref<number>()
const sources=ref<Source[]>([]),selected=ref<number[]>([]),entries=ref<Entry[]>([]),previewToken=ref(''),saved=ref<Saved>()
const moduleError=ref(false)
const targetModuleName=computed(()=>props.modules.find(item=>item.id===targetModule.value)?.name || '')
const errorText=(error:unknown)=>{
  const e=error as {response?:{data?:unknown};message?:string;error?:string}
  const body=e.response?.data
  if(Array.isArray(body))return body.join('；')
  if(body && typeof body==='object' && 'detail' in body)return String((body as {detail:unknown}).detail)
  return e.error || e.message || '操作失败，请稍后重试'
}
let version=0
async function loadSources(){
  const current=++version;loading.value=true
  try{
    const res=await request.get('/ui-automation/testcases/conversion-sources/',{params:{project:props.projectId,search:search.value,page:page.value,page_size:20}})
    if(current!==version)return
    const data=extractResponseData<{count:number;results:Source[]}>(res)
    if(!data || !Array.isArray(data.results))throw new Error('业务用例列表响应无效')
    sources.value=data.results;total.value=data.count
  }catch(error){if(current===version)Message.error(errorText(error))}
  finally{if(current===version)loading.value=false}
}
function selectRows(keys:(string|number)[]){
  const visible=new Set(sources.value.map(item=>item.id))
  const next=[...selected.value.filter(id=>!visible.has(id)),...keys.map(Number).filter(id=>visible.has(id))]
  if(next.length>20){Message.warning('每次最多转换 20 条，请分批操作');return}
  selected.value=Array.from(new Set(next))
}
function changePage(value:number){page.value=value;void loadSources()}
function searchSources(){page.value=1;void loadSources()}
function close(){if(busy.value){Message.info('正在处理，请稍候');return}version++;emit('update:visible',false)}
function back(){stage.value=1;previewToken.value='';entries.value=[]}
async function convert(){
  if(!targetModule.value){moduleError.value=true;Message.warning(props.modules.length ? '请先选择目标 UI 模块' : '请先在 UI 自动化中创建模块');return}
  if(!selected.value.length){Message.warning('请先勾选需要转换的业务用例');return}
  busy.value=true
  const generation=version
  try{
    const res=await request.post('/ui-automation/testcases/conversion-preview/',{project:props.projectId,module:targetModule.value,source_ids:selected.value})
    if(generation!==version || !props.visible)return
    const data=extractResponseData<{entries:Entry[];preview_token:string;source_count:number}>(res)
    if(!data || !data.preview_token || !Array.isArray(data.entries))throw new Error('转换预览响应无效')
    entries.value=data.entries;previewToken.value=data.preview_token;sourceCount.value=data.source_count;stage.value=2
  }catch(error){Message.error(errorText(error))}finally{busy.value=false}
}
async function save(){
  busy.value=true
  try{
    const res=await request.post('/ui-automation/testcases/conversion-apply/',{preview_token:previewToken.value})
    saved.value=extractResponseData<Saved>(res);stage.value=3;emit('saved')
  }catch(error){Message.error(errorText(error))}finally{busy.value=false}
}
watch(()=>props.visible,value=>{if(value){stage.value=1;page.value=1;selected.value=[];search.value='';entries.value=[];previewToken.value='';saved.value=undefined;moduleError.value=false;targetModule.value=props.modules.some(item=>item.id===props.initialModuleId) ? props.initialModuleId : props.modules.length===1 ? props.modules[0]?.id : undefined;void loadSources()}},{immediate:true})
watch(targetModule,()=>{moduleError.value=false})
watch(()=>props.modules,modules=>{if(props.visible && !targetModule.value && modules.length===1)targetModule.value=modules[0]?.id})
watch(()=>props.projectId,()=>{version++;if(props.visible){emit('update:visible',false);Message.info('项目已切换，请重新选择业务用例')}})
</script>

<style scoped>
.conversion-toolbar{display:flex;gap:12px;align-items:flex-end;margin-bottom:16px;flex-wrap:wrap}
.conversion-selected{display:inline-flex;align-items:center;min-height:32px}
.conversion-module{display:flex;flex-direction:column;gap:6px}.conversion-required{color:rgb(var(--danger-6))}.conversion-hint{margin-right:auto;color:var(--color-text-3)}
.conversion-footer{align-items:center;display:flex;justify-content:flex-end;gap:12px;margin-top:20px}
.conversion-previews{max-height:52vh;overflow:auto}.conversion-card{border:1px solid var(--color-border-2);border-radius:8px;padding:16px;margin-bottom:12px}
.conversion-card-title{display:flex;justify-content:space-between;gap:12px}.conversion-card p{color:var(--color-text-2)}
.conversion-text{white-space:pre-wrap;overflow-wrap:anywhere}.conversion-card li{margin:10px 0}.conversion-expected{white-space:pre-wrap;color:var(--color-text-2);margin-top:4px}
.conversion-gaps{padding:10px;background:var(--color-fill-1);color:var(--color-text-2);border-radius:4px}
</style>
