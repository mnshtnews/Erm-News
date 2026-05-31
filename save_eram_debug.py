"""
save_eram_debug.py
──────────────────
يفتح صفحة إرم نيوز رياضة بـ Playwright ويحفظ HTML كامل في listing_debug.html
شغّله مرة واحدة:  python save_eram_debug.py
"""
import asyncio
from playwright.async_api import async_playwright

URL = "https://www.eremnews.com/sports"
OUT = "listing_debug.html"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-blink-features=AutomationControlled"
        ])
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="ar-AE",
        )
        page = await ctx.new_page()
        print(f"Opening {URL} …")
        await page.goto(URL, wait_until="networkidle", timeout=90_000)
        await asyncio.sleep(3)   # let JS finish rendering
        html = await page.content()
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved {len(html):,} bytes → {OUT}")
        await browser.close()

asyncio.run(main())
