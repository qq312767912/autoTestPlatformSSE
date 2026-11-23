<template>
  <a-modal :visible="visible" title="从 Excel 导入接口与接口用例" :width="900" :footer="false" @cancel="emit('update:visible',false)">
    <p>兼容旧平台多工作表导出（.xlsx / .xls），按旧平台原用例保留请求步骤与断言，建立接口定义和接口用例，同时导入环境与全部源数据。导入后不会自动执行。</p>
    <div class="excel-import-upload">
      <input ref="fileInput" :key="String(project)+String(visible)" class="excel-file-input" type="file" accept=".xlsx,.xls" :disabled="busy" aria-label="选择 Excel 文件" @change="choose" />
      <a-button type="primary" size="large" :disabled="busy" @click="fileInput?.click()">
        <template #icon><IconUpload /></template>{{file ? '重新选择文件' : '选择 Excel 文件'}}
      </a-button>
      <div class="excel-file-info"><span class="excel-file-name" :title="file?.name">{{file?.name || '尚未选择文件'}}</span><span class="excel-file-hint">支持 .xlsx、.xls 格式</span></div>
    </div>
    <div class="excel-import-actions">
      <a-select v-model="group" allow-clear :disabled="busy" placeholder="目标分组（可选）" style="width:260px">
        <a-option v-for="item in groups" :key="item.id" :value="item.id">{{item.name}}</a-option>
      </a-select>
      <a-button :disabled="!file" :loading="busy" @click="preview">解析并预览</a-button>
    </div>
    <a-alert v-if="error" type="error" style="margin:12px 0">{{error}}</a-alert>
    <template v-if="plan">
      <a-alert style="margin:16px 0">识别 {{plan.case_count}} 条接口用例、{{plan.step_count}} 个接口定义/请求步骤、{{plan.environment_count}} 个环境；已恢复 {{plan.recovered_variable_count}} 个变量值，保留 {{plan.data_row_count}} 条历史数据。</a-alert>
      <a-alert v-if="plan.duplicates.length" type="error">已有同名用例：{{plan.duplicates.join('、')}}。请修改文件内名称后重新预览。</a-alert>
      <a-checkbox v-if="plan.updatable.length" v-model="updateExisting" style="margin:12px 0">补全或迁移此前从此文件导入的记录（保留已编辑请求、断言和变量）</a-checkbox>
      <a-alert v-if="plan.legacy_scene_count" style="margin:12px 0">将把旧版误建的 {{plan.legacy_scene_count}} 条场景迁入接口用例，并合并原拆分用例。旧记录与报告保留备查，不再出现在默认列表。</a-alert>
      <p>环境同名且地址相同则复用，不覆盖已有环境变量。历史请求值可能过期；断言只恢复记录中明确的预期，不使用实际响应生成预期。</p>
      <details style="margin:12px 0"><summary>完整导入范围（{{Object.keys(plan.sheet_counts).length}} 个工作表）</summary><div style="max-height:140px;overflow:auto"><div v-for="(count,name) in plan.sheet_counts" :key="name">{{name}}：{{count}} 行</div></div></details>
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
      <a-button type="primary" :loading="busy" :disabled="!!plan.errors.length||!!plan.duplicates.length||(!!plan.updatable.length&&!updateExisting)" @click="confirm">确认导入 {{plan.case_count}} 条接口用例</a-button>
    </template>
  </a-modal>
</template>
<script setup lang="ts">
import {ref,watch} from 'vue'
import {Message} from '@arco-design/web-vue'
import {IconUpload} from '@arco-design/web-vue/es/icon'
const fileInput=ref<HTMLInputElement>()
import {testcaseService} from '../../services/testcaseService'
import {testcaseGroupService} from '../../services/testcaseGroupService'
import {toArray} from '../../services/responseHelpers'
import type {ApiTestCaseGroup} from '../../types/testcase'
interface Issue{sheet:string;row:number;message:string}
interface Plan{legacy_scene_count:number;updatable:string[];environment_count:number;recovered_variable_count:number;data_row_count:number;sheet_counts:Record<string,number>;sha256:string;case_count:number;step_count:number;duplicates:string[];errors:Issue[];warnings:Issue[];required_variables:{name:string;source:string}[];cases:{name:string;source_id:string;steps:{name:string;method:string;url:string;order:number;assertions:number}[]}[]}
const props=defineProps<{visible:boolean;project:number}>()
const emit=defineEmits<{(e:'update:visible',v:boolean):void;(e:'imported'):void}>()
const file=ref<File>(),plan=ref<Plan>(),busy=ref(false),error=ref(''),groups=ref<ApiTestCaseGroup[]>([]),group=ref<number>()
const updateExisting=ref(true)
let version=0
function choose(event:Event){version++;file.value=(event.target as HTMLInputElement).files?.[0];plan.value=undefined;error.value=''}
function failure(e:unknown){const v=e as {message?:string;error?:string;errors?:unknown};error.value=v.message||v.error||'导入失败，请检查文件后重试'}
function payload(){const data=new FormData();data.append('file',file.value!);if(group.value)data.append('group_id',String(group.value));return data}
async function preview(){if(!file.value||busy.value)return;const current=++version;busy.value=true;plan.value=undefined;error.value='';try{const result=await testcaseService.importExcel(props.project,payload());if(current===version)plan.value=result as unknown as Plan}catch(e){if(current===version)failure(e)}finally{if(current===version)busy.value=false}}
async function confirm(){if(!plan.value||!file.value||busy.value)return;busy.value=true;error.value='';try{const data=payload();data.append('confirm','true');data.append('update_existing',String(updateExisting.value));data.append('sha256',plan.value.sha256);const result=await testcaseService.importExcel(props.project,data) as {converted_scenarios:number;consolidated_split_cases:number;created_interfaces:number;created_interface_cases:number;created_cases:number;updated_cases:number;created_environments:number;created_variables:number;imported_data_rows:number};Message.success(`导入完成：新增 ${result.created_interfaces} 个接口、${result.created_interface_cases} 条接口用例，补全 ${result.updated_cases} 条；迁移 ${result.converted_scenarios} 条旧场景、合并 ${result.consolidated_split_cases} 条拆分记录，创建 ${result.created_environments} 个环境、${result.created_variables} 个变量，保留 ${result.imported_data_rows} 条历史数据`);emit('imported');emit('update:visible',false);file.value=undefined;plan.value=undefined}catch(e){failure(e)}finally{busy.value=false}}
watch(()=>[props.visible,props.project],async()=>{version++;busy.value=false;if(!props.visible)return;file.value=undefined;plan.value=undefined;group.value=undefined;error.value='';try{groups.value=toArray(await testcaseGroupService.list(props.project))}catch{groups.value=[]}})
</script>

<style scoped>
.excel-file-input{display:none}
.excel-import-upload{display:flex;align-items:center;gap:16px;padding:20px;margin:16px 0;background:var(--color-fill-1);border:1px dashed var(--color-border-3);border-radius:8px}
.excel-file-info{display:flex;flex-direction:column;gap:5px;min-width:0}
.excel-file-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--color-text-1);font-weight:500}
.excel-file-hint{font-size:12px;color:var(--color-text-3)}
.excel-import-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
</style>
