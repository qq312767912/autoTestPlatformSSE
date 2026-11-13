/* DOM component integration: real Arco upload events, synthetic in-memory files.
 * No Chrome extension, native picker, server, credentials, or user files involved.
 * npm install --prefix /tmp/whart-upload-unit jsdom@29.1.1
 * node scripts/docx_evaluation/test_upload_component.cjs
 */
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../../WHartTest_Vue');
const projectRequire = createRequire(path.join(root, 'package.json'));
const unitRequire = createRequire('/tmp/whart-upload-unit/package.json');
const { JSDOM } = unitRequire('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="app"></div></body></html>', { url: 'http://localhost:8913/' });
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'Event', 'File', 'FileReader', 'MutationObserver']) {
  global[key] = dom.window[key];
}
global.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
global.requestAnimationFrame = callback => setTimeout(callback, 0);
global.cancelAnimationFrame = clearTimeout;
global.sessionStorage = dom.window.sessionStorage;
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
dom.window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });

async function main() {
  const esbuild = projectRequire('esbuild');
  const compiler = projectRequire('@vue/compiler-sfc');
  const component = path.join(root, 'src/features/requirements/components/DocumentComparisonModal.vue');
  const result = await esbuild.build({
    entryPoints: [component], bundle: true, platform: 'node', format: 'cjs', write: false,
    external: ['vue', '@arco-design/web-vue'],
    plugins: [{ name: 'component-unit', setup(build) {
      build.onResolve({ filter: /comparisonService$/ }, () => ({ path: 'service-stub', namespace: 'stub' }));
      build.onResolve({ filter: /ComparisonDiffCell\.vue$/ }, () => ({ path: 'diff-cell-stub', namespace: 'stub' }));
      build.onLoad({ filter: /.*/, namespace: 'stub' }, args => ({ contents: args.path === 'service-stub'
        ? `export const compareFiles=()=>{throw Error('Unexpected network')};
export const extractComparison=async()=>new Blob(['synthetic zip'],{type:'application/zip'});
export const loadComparison=async()=>({id:'unit',old_name:'old.docx',new_name:'new.docx',iteration_title:'',confirmed_at:null,confirmed_hunks:[],result:{hunks:[{id:'h0',rows:[],pairs:[]}],added:1,deleted:1,warnings:[]}});
export const comparisonError=String;`
        : 'export default {render:()=>null};' }));
      build.onLoad({ filter: /\.vue$/ }, args => {
        const { descriptor } = compiler.parse(fs.readFileSync(args.path, 'utf8'));
        return { contents: compiler.compileScript(descriptor, { id: 'upload-unit', inlineTemplate: true }).content, loader: 'ts', resolveDir: path.dirname(args.path) };
      });
    } }],
  });
  const Module = require('node:module');
  const compiled = new Module(path.join(root, 'upload-component-unit.cjs'), module);
  compiled.filename = path.join(root, 'upload-component-unit.cjs');
  compiled.paths = Module._nodeModulePaths(root);
  compiled._compile(result.outputFiles[0].text, compiled.filename);
  const { createApp, nextTick } = projectRequire('vue');
  const Arco = projectRequire('@arco-design/web-vue');
  // Toast transitions are outside this component's file/download state contract.
  Arco.Message.success = () => ({ close() {} });
  sessionStorage.setItem('requirement-comparison:unit-project', 'unit');
  const app = createApp(compiled.exports.default, { visible: true, projectId: 'unit-project' });
  app.use(Arco.default || Arco).mount('#app');
  const flush = async () => { await nextTick(); await new Promise(resolve => setTimeout(resolve, 15)); await nextTick(); };
  await flush();
  const inputs = [...document.querySelectorAll('.document-upload input[type="file"]')];
  assert.equal(inputs.length, 2, 'Both standard upload controls must exist');
  const start = () => [...document.querySelectorAll('button')].find(button => button.textContent.trim() === '开始对比');
  const names = () => [...document.querySelectorAll('.file-name')].map(node => node.textContent);
  const choose = async (index, name) => {
    Object.defineProperty(inputs[index], 'files', { value: [new File(['synthetic'], name)], configurable: true });
    inputs[index].dispatchEvent(new Event('change', { bubbles: true }));
    await flush();
  };
  assert.equal(start().disabled, true);
  await choose(0, 'old.docx');
  assert.equal(names()[0], 'old.docx');
  assert.equal(start().disabled, true);
  const drop = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(drop, 'dataTransfer', { value: { files: [new File(['synthetic'], 'new.docx')] } });
  inputs[1].parentElement.dispatchEvent(drop);
  await flush();
  assert.equal(names()[1], 'new.docx', 'Dropping must select the new document');
  assert.equal(start().disabled, false);
  await choose(0, 'replacement.DOCX');
  assert.equal(names()[0], 'replacement.DOCX', 'Reselect and uppercase extension must work');
  await choose(0, 'invalid.pdf');
  assert.equal(names()[0], 'replacement.DOCX', 'Invalid selection must preserve the valid file');
  assert.match(document.body.textContent, /版本对比仅支持/);
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent.trim() === text);
  button('恢复上次对比').click();
  await flush();
  assert.equal(button('确认并提取所选 Diff').disabled, true, 'Review confirmation must be required');
  const confirm = [...document.querySelectorAll('label.arco-checkbox')].find(node => node.textContent.includes('我已核对'));
  confirm.querySelector('input').click();
  await flush();
  assert.equal(button('确认并提取所选 Diff').disabled, false, 'Missing name must produce an actionable validation, not a silent disabled button');
  button('确认并提取所选 Diff').click();
  await flush();
  assert.match(document.body.textContent, /请先填写本次迭代名称/);
  const name = document.querySelector('.compare-actions input');
  assert.equal(document.activeElement, name, 'Missing name must focus the name field');
  name.value = '迭代测试';
  name.dispatchEvent(new Event('input', { bubbles: true }));
  await flush();
  assert.equal(confirm.querySelector('input').checked, true, 'Editing the label must not reset review confirmation');
  document.addEventListener('click', event => { if (event.target.tagName === 'A') event.preventDefault(); });
  button('确认并提取所选 Diff').click();
  await flush();
  const download = document.querySelector('a[download]');
  assert.ok(download, 'Successful extraction must leave a user-clickable download link');
  assert.equal(download.download, '迭代测试-diff.zip');
  button('清空').click();
  await flush();
  assert.equal(document.querySelector('a[download]'), null, 'Changed selection must invalidate the previous download');
  app.unmount();
  dom.window.close();
  console.log('PASS: select old, drop new, reselect, uppercase DOCX, reject invalid file, compare enablement, name validation, focus, confirmation preservation and explicit download link; no network calls');
}
main().catch(error => { console.error(error); process.exitCode = 1; dom.window.close(); });
