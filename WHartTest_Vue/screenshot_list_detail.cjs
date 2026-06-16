const { chromium } = require('playwright')

async function main() {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })
  
  await page.goto('http://localhost:5173/login')
  await page.waitForTimeout(1000)
  const loginTab = page.getByText('账号登录')
  if (await loginTab.isVisible()) { await loginTab.click(); await page.waitForTimeout(500) }
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[placeholder*="密码"]', 'admin123456')
  await page.getByRole('button', { name: '登录', exact: true }).click({ force: true })
  await page.waitForTimeout(2000)
  
  await page.goto('http://localhost:5173/api-testing')
  await page.waitForTimeout(3000)
  
  // Switch to list view
  const switchEl = page.locator('.arco-switch').first()
  await switchEl.click()
  await page.waitForTimeout(1500)
  
  // Take full list view screenshot
  await page.screenshot({ path: '/tmp/list_view_full.png', fullPage: false })
  
  // Crop just the right panel (list area)
  const rightPanel = page.locator('.flex-1.min-w-0').first()
  if (await rightPanel.isVisible()) {
    await rightPanel.screenshot({ path: '/tmp/list_view_right.png' })
    console.log('Captured right panel')
  }
  
  // Check computed styles on the table header and search area
  const styles = await page.evaluate(() => {
    const results = {}
    
    // Check table header
    const th = document.querySelector('.arco-table-th')
    if (th) {
      const cs = window.getComputedStyle(th)
      results.tableHeader = {
        bgColor: cs.backgroundColor,
        color: cs.color,
        borderBottom: cs.borderBottom
      }
    }
    
    // Check table body
    const table = document.querySelector('.arco-table')
    if (table) {
      const cs = window.getComputedStyle(table)
      results.table = {
        bgColor: cs.backgroundColor,
        color: cs.color
      }
    }
    
    // Check search input
    const searchInput = document.querySelector('.arco-input-wrapper')
    if (searchInput) {
      const cs = window.getComputedStyle(searchInput)
      results.searchInput = {
        bgColor: cs.backgroundColor,
        borderColor: cs.borderColor,
        color: cs.color
      }
    }
    
    // Check the tag "0 个接口"
    const tag = document.querySelector('.arco-tag')
    if (tag) {
      const cs = window.getComputedStyle(tag)
      results.tag = {
        bgColor: cs.backgroundColor,
        color: cs.color,
        text: tag.textContent
      }
    }
    
    // Check empty state
    const empty = document.querySelector('.arco-empty-description')
    if (empty) {
      const cs = window.getComputedStyle(empty)
      results.empty = {
        color: cs.color,
        text: empty.textContent
      }
    }
    
    // Check arco-theme attribute
    results.arcoTheme = document.documentElement.getAttribute('arco-theme')
    results.bodyArcoTheme = document.body.getAttribute('arco-theme')
    
    // Check if list view wrapper exists
    const listWrapper = document.querySelector('[class*="bg-gray-800"]')
    if (listWrapper) {
      const cs = window.getComputedStyle(listWrapper)
      results.listWrapper = {
        bgColor: cs.backgroundColor,
        display: cs.display,
        height: cs.height
      }
    }
    
    return results
  })
  
  console.log('Computed styles:', JSON.stringify(styles, null, 2))
  
  await browser.close()
}

main().catch(e => { console.error(e); process.exit(1) })
