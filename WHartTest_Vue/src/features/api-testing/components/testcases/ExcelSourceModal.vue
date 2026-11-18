<template>
  <a-modal :visible="visible" title="Excel 导入源数据" :width="1100" :footer="false" @cancel="emit('update:visible',false)">
    <p>保留全部工作表及历史数据，供核对输入、数据关联和预期来源。这里的旧记录不作为本平台新执行报告，脚本内容不执行。</p>
    <a-alert v-if="error" type="error">{{error}}</a-alert>
    <a-select v-model="sheet" style="width:400px;margin:12px 0" @change="load(1)">
      <a-option v-for="(count,name) in counts" :key="name" :value="name">{{name}}（{{count}} 行）</a-option>
    </a-select>
    <a-table :data="rows" :columns="columns" :loading="busy" row-key="_row" :scroll="{x:Math.max(columns.length*190,800),y:450}" :pagination="{current:page,pageSize:20,total,showTotal:true}" @page-change="load">
      <template #cell="{record,column}"><div style="max-height:140px;overflow:auto;white-space:pre-wrap;word-break:break-all">{{format(record[column.dataIndex])}}</div></template>
    </a-table>
  </a-modal>
</template>
<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {testcaseService} from '../../services/testcaseService'
const props=defineProps<{visible:boolean;project:number;caseId:number;kind?:'scenario'|'interface'}>()
const emit=defineEmits<{(e:'update:visible',v:boolean):void}>()
const counts=ref<Record<string,number>>({}),sheet=ref(''),rows=ref<Record<string,unknown>[]>([]),page=ref(1),total=ref(0),busy=ref(false),error=ref('')
const columns=computed(()=>Object.keys(rows.value[0]||{}).filter(x=>x!=='_row').map(key=>({title:key,dataIndex:key,width:190,slotName:'cell'})))
function format(value:unknown){return typeof value==='object'?JSON.stringify(value,null,2):String(value??'')}
let epoch=0
async function load(target=1){const current=epoch;page.value=target;busy.value=true;error.value='';try{const data=await testcaseService.excelSource(props.project,props.caseId,{sheet:sheet.value,page:target},props.kind);if(current!==epoch)return;rows.value=data.rows||[];total.value=data.total||0}catch(e){if(current===epoch)error.value=e instanceof Error?e.message:'源数据加载失败'}finally{if(current===epoch)busy.value=false}}
watch(()=>[props.visible,props.caseId,props.project],async()=>{const current=++epoch;if(!props.visible)return;rows.value=[];counts.value={};error.value='';busy.value=true;try{const data=await testcaseService.excelSource(props.project,props.caseId,undefined,props.kind);if(current!==epoch)return;counts.value=data.sheet_counts;sheet.value=Object.keys(counts.value).find(name=>name==='AutomationParameter')||Object.keys(counts.value)[0]||'';await load(1)}catch(e){if(current===epoch){error.value=e instanceof Error?e.message:'源数据加载失败';busy.value=false}}})
</script>
