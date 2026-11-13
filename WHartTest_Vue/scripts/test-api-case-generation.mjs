import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function load(getInterfaceCases) {
  const source = fs.readFileSync(new URL('../src/features/api-testing/services/apiCaseGeneration.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const sandbox = { exports: {}, require: () => ({ getInterfaceCases }) };
  vm.runInNewContext(compiled, sandbox);
  return sandbox.exports;
}

test('deduplication includes cases after the first 500 rows', async () => {
  const pages = [];
  const service = load(async ({ page }) => {
    pages.push(page);
    return { success: true, total: 501, data: page === 1
      ? Array.from({ length: 500 }, (_, id) => ({ name: `other-${id}`, interface: 2 }))
      : [{ name: '末页重复用例', interface: 1 }] };
  });
  const names = await service.collectExistingInterfaceCaseNames([1]);
  assert.equal(names.length, 1);
  assert.equal(names[0], '末页重复用例');
  assert.deepEqual(pages, [1, 2]);
});

test('failed case reads stop generation instead of implying an empty catalog', async () => {
  const service = load(async () => ({ success: false }));
  await assert.rejects(() => service.collectExistingInterfaceCaseNames([1]), /读取已有用例失败/);
});

test('scenario messages include real functional steps without hardcoded Skill names', () => {
  const service = load(async () => ({}));
  const refs = [{ case_id: 7, name: '审批', module_name: '业务', test_type: 'functional', level: 'P1', detail: { precondition: '已登录', steps: [{ description: '提交申请', expected_result: '生成申请编号' }] } }];
  const message = service.buildGenerationMessage({ formData: { caseType: 'scenario', interfaceIds: [1,2], dimensions: [], functionalCaseRefs: refs, businessNotes: '', groupId: null }, interfaces: [], projectId: 3, existingNames: [], functionalCaseRefs: refs });
  assert.match(message, /^<!-- wharttest:generation_mode=scenario -->/);
  assert.match(message, /提交申请/);
  assert.match(message, /生成申请编号/);
  assert.doesNotMatch(message, /api-automation|whart-test/);
});
