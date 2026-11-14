import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function load(mocks={}){
 const source=fs.readFileSync(new URL('../src/features/ui-automation/services/uiCaseGeneration.ts',import.meta.url),'utf8');
 const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
 const sandbox={exports:{},require:()=>mocks,localStorage:{setItem:(...args)=>mocks.storage?.push(args)}};vm.runInNewContext(compiled,sandbox);return sandbox.exports;
}
test('UI options read every page',async()=>{const s=load();const seen=[];const rows=await s.allRows(async page=>{seen.push(page);return {data:{count:3,next:page===1?'page2':null,results:page===1?[{id:1},{id:2}]:[{id:3}]}}});assert.equal(rows.length,3);assert.deepEqual(seen,[1,2]);});
test('failed or incomplete option reads block generation',async()=>{const s=load();await assert.rejects(()=>s.allRows(async()=>({})),/读取生成范围失败/);await assert.rejects(()=>s.allRows(async()=>({data:{count:1,results:[]}})),/分页不完整/);});
test('UI generation uses server context and validated binding without hardcoded Skill',()=>{const message=load().buildUiMessage('审批业务');assert.match(message,/^<!-- wharttest:generation_mode=ui_case -->/);assert.match(message,/get_ui_generation_context/);assert.match(message,/bind_generated_ui_case/);assert.doesNotMatch(message,/whart-test|ui-automation|playwright-cli/);});

test('UI start binds context and registers a callable stream callback',async()=>{const storage=[];let posted;const service=load({storage,createSkillAgentTask:async(...args)=>{posted=args;return {session_id:'locked-session'};},sendChatMessageStream:async(data,onStart)=>{assert.equal(typeof onStart,'function');assert.equal(data.auth_state_id,9);onStart('locked-session');}});await service.startUiGeneration(7,{functional_case_ids:[2],module_id:3,env_config_id:4,auth_state_id:9,skillVersionId:'exact-version',businessNotes:'业务补充'},()=>{});assert.equal(posted[0],7);assert.equal(posted[1],'exact-version');assert.equal(posted[2],'ui_case');assert.equal(posted[3].auth_state_id,9);assert.equal(posted[3].businessNotes,undefined);assert.ok(storage.some(([k,v])=>k==='langgraph_session_id'&&v==='locked-session'));});
