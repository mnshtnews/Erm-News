"""
src/scraper/browser.py
───────────────────────
Playwright browser lifecycle manager with Cloudflare bypass.

Key anti-detection measures applied:
  • Full stealth init script (webdriver, plugins, languages, chrome object)
  • Realistic Chrome 125 user-agent + sec-ch-ua headers
  • Human-like navigation: visit homepage first, then follow internal links
  • Per-request Referer header mimicking in-site navigation
  • Random mouse movement + scroll before page extraction
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.core.config import Settings

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_EXTRA_HEADERS = {
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Full stealth script — hides all automation fingerprints
_STEALTH_SCRIPT = """
// 1. Hide webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Realistic languages
Object.defineProperty(navigator, 'languages', { get: () => ['ar', 'en-US', 'en'] });

// 3. Realistic plugins (non-empty)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ];
        arr.__proto__ = PluginArray.prototype;
        return arr;
    }
});

// 4. Inject window.chrome so Cloudflare sees a real Chrome
if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false, InstallState: {}, RunningState: {} },
        runtime: {
            OnInstalledReason: {},
            OnRestartRequiredReason: {},
            PlatformArch: {},
            PlatformNaclArch: {},
            PlatformOs: {},
            RequestUpdateCheckStatus: {},
        },
    };
}

// 5. Permissions API — real Chrome returns 'granted' for notifications query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);

// 6. WebGL vendor spoofing
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};
"""


class BrowserManager:
    """
    Manages a single Playwright Chromium browser instance.

    For article detail pages, use navigate_as_internal() instead of
    goto() directly — it sets a proper Referer and mimics in-site navigation.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._warmed_up = False

    async def start(self) -> None:
        """Launch browser and create a fully stealthed context."""
        self._playwright = await async_playwright().start()

        launch_kwargs: dict = {
            "headless": self._settings.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-plugins-discovery",
                "--no-first-run",
                "--no-default-browser-check",
                "--lang=ar",
                "--window-size=1366,768",
                # Make Chrome look more real
                "--disable-ipc-flooding-protection",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-background-timer-throttling",
            ],
        }

        if self._settings.proxy_url:
            launch_kwargs["proxy"] = {"server": self._settings.proxy_url}

        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent=_USER_AGENT,
            locale="ar-AE",
            timezone_id="Asia/Dubai",
            extra_http_headers=_EXTRA_HEADERS,
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
            accept_downloads=False,
            # Realistic screen size
            screen={"width": 1920, "height": 1080},
        )

        await self._context.add_init_script(_STEALTH_SCRIPT)
        logger.info("Browser started (headless={headless})", headless=self._settings.headless)

    async def warmup(self, base_url: str = "https://www.eremnews.com") -> None:
        """
        Visit the homepage first so Cloudflare sets its cookies.
        Call this once after start() before any article fetching.
        """
        if self._warmed_up:
            return
        page = await self.new_page()
        try:
            logger.info("Warming up browser — visiting homepage …")
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
            await self._human_pause(2, 4)
            await self._random_scroll(page)
            await self._human_pause(1, 2)
            self._warmed_up = True
            logger.info("Browser warmup complete")
        except Exception as exc:
            logger.warning(f"Warmup failed (non-fatal): {exc}")
        finally:
            await page.close()

    async def navigate_as_internal(
        self,
        page: Page,
        url: str,
        referrer: str = "https://www.eremnews.com/sports",
    ) -> None:
        """
        Navigate to a URL with a Referer that makes it look like an
        in-site click rather than a direct bot visit.
        """
        await page.set_extra_http_headers({
            **_EXTRA_HEADERS,
            "Referer": referrer,
            "Sec-Fetch-Site": "same-origin",   # coming from same site
        })
        await page.goto(url, wait_until="domcontentloaded", timeout=self._settings.page_load_timeout)
        # Small human-like pause after load
        await self._human_pause(1, 3)
        await self._random_scroll(page)

    async def stop(self) -> None:
        """Gracefully close context, browser, and Playwright."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning(f"Browser cleanup error: {exc}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._warmed_up = False
        logger.info("Browser stopped")

    async def restart(self) -> None:
        """Hard restart — stop then start fresh."""
        logger.warning("Restarting browser …")
        await self.stop()
        await asyncio.sleep(3)
        await self.start()
        # Re-warm after restart
        await self.warmup()

    async def new_page(self) -> Page:
        """Open a new page in the shared context."""
        if not self._context:
            raise RuntimeError("BrowserManager not started — call start() first")
        return await self._context.new_page()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _human_pause(min_s: float = 1.0, max_s: float = 3.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    @staticmethod
    async def _random_scroll(page: Page) -> None:
        """Scroll down slightly — mimics a human reading the page."""
        try:
            scroll_y = random.randint(200, 600)
            await page.evaluate(f"window.scrollBy(0, {scroll_y})")
            await asyncio.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()
