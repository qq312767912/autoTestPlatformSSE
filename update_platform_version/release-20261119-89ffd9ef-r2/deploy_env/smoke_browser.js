const { chromium } = require('/app/ui_automation/recorder/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium-browser', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage();
    await page.setContent('<html><head><title>WHartTest ARM64 smoke</title></head><body>浏览器运行正常</body></html>');
    if (await page.title() !== 'WHartTest ARM64 smoke') throw new Error('browser smoke failed');
    console.log('Backend Node Playwright + Chromium:', browser.version());
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
