// Exercise the production step dispatcher against real Chromium tabs.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { chromium } = require('../WHartTest_Vue/node_modules/playwright');
const source = fs.readFileSync(require('node:path').join(__dirname, '../WHartTest_Django/ui_automation/recorder/recorder_server.js'), 'utf8');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.RECORDER_TEST_BROWSER ? {executablePath:process.env.RECORDER_TEST_BROWSER} : {})});
  try {
    const context = await browser.newContext();
    await context.route('http://tabs.test/**', route => route.fulfill({contentType:'text/html',body:'<title>Target tab</title><button onclick="setTimeout(()=>window.open(\'/target\'),300)">Open</button>'}));
    const page = await context.newPage();
    await page.goto('http://tabs.test/source');
    const state = {page};
    const sandbox = {state, setTimeout, console, stopFrameLoop(){}, stopFrameStream(){}, attachPageDiagnostics(){}, async startFrameStream(){return true}, startFrameLoop(){}};
    vm.createContext(sandbox);
    vm.runInContext(source.slice(source.indexOf('async function runOneStep('), source.indexOf('async function cmdAddWait(')), sandbox);
    const run = step => sandbox.runOneStep(state.page, step);
    await page.getByRole('button').click();
    assert.equal(await run({ope_key:'switch_tab',ope_value:{value:'1'}}),null);
    assert.equal(state.page.url(),'http://tabs.test/target');
    assert.equal(await run({ope_key:'assert_url',ope_value:{expected:'http://tabs.test/target'}}),null);
    assert.equal(await run({ope_key:'switch_tab',ope_value:{value:'0'}}),null);
    assert.equal(state.page,page);
    assert.equal(await run({ope_key:'switch_tab',ope_value:{value:'/target'}}),null);
    await page.close(); // verify closed tabs are not selected
    assert.equal(await run({ope_key:'switch_tab',ope_value:{value:'Target tab'}}),null);
    assert.match(await run({ope_key:'switch_tab',ope_value:{value:''}}),/参数为空/);
    assert.match(await run({ope_key:'assert_url',ope_value:{expected:'http://tabs.test/wrong'}}),/URL断言失败/);
    assert.match(await run({ope_key:'switch_tab',ope_value:{value:'99'}}),/未找到目标页签/);
    console.log('PASS: delayed popup, index/URL/title switch, active page, closed tab, URL assertion and failure paths');
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
