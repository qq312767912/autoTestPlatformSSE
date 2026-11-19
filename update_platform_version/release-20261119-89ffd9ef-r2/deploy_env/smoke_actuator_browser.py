import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/chromium-browser', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            page = await browser.new_page()
            await page.set_content('<html><head><title>WHartTest ARM64 smoke</title></head><body>执行器正常</body></html>')
            assert await page.title() == 'WHartTest ARM64 smoke'
            print('Actuator Python Playwright + Chromium:', browser.version)
        finally:
            await browser.close()

asyncio.run(main())
