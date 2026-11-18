<template>
  <a-modal :visible="visible" title="从 Excel 导入接口场景用例" :width="900" :footer="false" @cancel="emit('update:visible',false)">
    <p>兼容旧平台多工作表导出（.xlsx / .xls），还原用例、步骤顺序、请求头、参数和 JSON 断言。导入后不会自动执行。</p>
    <input :key="String(project)+String(visible)" type="file" accept=".xlsx,.xls" :disabled="busy" @change="choose" />
    <a-select v-model="group" allow-clear placeholder="目标分组（可选）" style="width:260px;margin:12px 0 12px 16px">
      <a-option v-for="item in groups" :key="item.id" :value="item.id">{{item.name}}</a-option>
    </a-select>
    <a-button :disabled="!file" :loading="busy" @click="preview">解析并预览</a-button>
    <a-alert v-if="error" type="error" style="margin:12px 0">{{error}}</a-alert>
    <template v-if="plan">
      <a-alert style="margin:16px 0">识别 {{plan.case_count}} 条用例、{{plan.step_count}} 个接口步骤。历史执行记录不导入；已有同名用例不覆盖。</a-alert>
      <a-alert v-if="plan.duplicates.length" type="error">已有同名用例：{{plan.duplicates.join('、')}}。请修改文件内名称后重新预览。</a-alert>
      <a-alert v-if="plan.errors.length" type="error" style="margin:12px 0">存在 {{plan.errors.length}} 项无法迁移的配置，请处理下方问题后重新预览。</a-alert>
      <a-table :data="plan.cases" :pagination="false" row-key="source_id" :scroll="{y:240}">
        <template #columns>
          <a-table-column title="用例名称" data-index="name" />
          <a-table-column title="接口步骤（按执行顺序）"><template #cell="{record}"><div v-for="step in record.steps" :key="step.order">{{step.order}}. {{step.name}} · {{step.method}} {{step.url}} · {{step.assertions}} 项断言</div></template></a-table-column>
        </template>
      </a-table>
      <details v-if="plan.required_variables.length" style="margin:16px 0">
        <summary>执行前需补齐 {{plan.required_variables.length}} 个变量</summary>
        <p>旧平台外部数据源未包含在文件中。请在导入后的用例变量或执行环境中填写以下变量；缺失时会阻止执行，不发送请求。</p>
        <div style="max-height:180px;overflow:auto"><div v-for="item in plan.required_variables" :key="item.name"><code>{{item.name}}</code> ← {{item.source}}</div></div>
      </details>
      <details v-if="plan.errors.length || plan.warnings.length" :open="!!plan.errors.length" style="margin:16px 0">
        <summary>详细迁移说明（{{plan.errors.length}} 项错误，{{plan.warnings.length}} 项提示）</summary>
        <div style="max-height:200px;overflow:auto"><p v-for="(item,index) in [...plan.errors,...plan.warnings]" :key="index">{{item.sheet}} 第{{item.row}}行：{{item.message}}</p></div>
      </details>
      <a-button type="primary" :loading="busy" :disabled="!!plan.errors.length||!!plan.duplicates.length" @click="confirm">确认导入 {{plan.case_count}} 条用例</a-button>
    </template>
  </a-modal>
</template>
<script setup lang="ts">
import {ref,watch} from 'vue'
import {Message} from '@arco-design/web-vue'
import {testcaseService} from '../../services/testcaseService'
import {testcaseGroupService} from '../../services/testcaseGroupService'
import {toArray} from '../../services/responseHelpers'
import type {ApiTestCaseGroup} from '../../types/testcase'
interface Issue{sheet:string;row:number;message:string}
interface Plan{sha256:string;case_count:number;step_count:number;duplicates:string[];errors:Issue[];warnings:Issue[];required_variables:{name:string;source:string}[];cases:{name:string;source_id:string;steps:{name:string;method:string;url:string;order:number;assertions:number}[]}[]}
const props=defineProps<{visible:boolean;project:number}>()
const emit=defineEmits<{(e:'update:visible',v:boolean):void;(e:'imported'):void}>()
const file=ref<File>(),plan=ref<Plan>(),busy=ref(false),error=ref(''),groups=ref<ApiTestCaseGroup[]>([]),group=ref<number>()
let version=0
function choose(event:Event){version++;file.value=(event.target as HTMLInputElement).files?.[0];plan.value=undefined;error.value=''}
function failure(e:unknown){const v=e as {message?:string;error?:string;errors?:unknown};error.value=v.message||v.error||'导入失败，请检查文件后重试'}
function payload(){const data=new FormData();data.append('file',file.value!);if(group.value)data.append('group_id',String(group.value));return data}
async function preview(){if(!file.value||busy.value)return;const current=++version;busy.value=true;plan.value=undefined;error.value='';try{const result=await testcaseService.importExcel(props.project,payload());if(current===version)plan.value=result as unknown as Plan}catch(e){if(current===version)failure(e)}finally{if(current===version)busy.value=false}}
async function confirm(){if(!plan.value||!file.value||busy.value)return;busy.value=true;error.value='';try{const data=payload();data.append('confirm','true');data.append('sha256',plan.value.sha256);await testcaseService.importExcel(props.project,data);Message.success('Excel 用例导入完成');emit('imported');emit('update:visible',false);file.value=undefined;plan.value=undefined}catch(e){failure(e)}finally{busy.value=false}}
watch(()=>[props.visible,props.project],async()=>{version++;busy.value=false;if(!props.visible)return;file.value=undefined;plan.value=undefined;group.value=undefined;error.value='';try{groups.value=toArray(await testcaseGroupService.list(props.project))}catch{groups.value=[]}})
</script>
