"""
src/scraper/eremnews_engine.py
───────────────────────────────
Scraping engine for eremnews.com (إرم نيوز).

Cloudflare bypass strategy:
  1. Browser warmup — visit homepage first so CF sets cookies
  2. Internal navigation — Referer: eremnews.com/sports on every article request
  3. Full stealth browser (see browser.py)
  4. Human-like delays between requests
  5. Cloudflare block detection — skip saving blocked pages
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.core.config import Settings
from src.core.models import RawArticle
from src.scraper.browser import BrowserManager
from src.scraper.eremnews_parser import parse_article_detail, parse_article_list

_LISTING_URL      = "https://www.eremnews.com/sports"
_HOME_URL         = "https://www.eremnews.com"

# Selectors that prove the sports listing has rendered.
# We try the most specific first (actual article links), then fall back.
_CONTENT_READY_SELECTORS = [
    "a[href^='/sports/']:not([href='/sports'])",  # an actual article link
    "h3",                                          # at least headings loaded
    "h1",                                          # page header at minimum
]

# How long to wait after content is detected for JS to finish rendering grid
_GRID_SETTLE_SECONDS = 3.0

# Strings present in Cloudflare challenge pages
_CF_BLOCK_SIGNALS = [
    "يستخدم هذا الموقع خدمة أمنية",
    "Just a moment",
    "cf-browser-verification",
    "Checking your browser",
    "Please wait while we verify",
    "Enable JavaScript and cookies",
    "cf_clearance",
    "challenge-running",
    "برامج الروبوتات",
]


def is_cloudflare_blocked(html: str) -> bool:
    """Return True if the page is a Cloudflare challenge/block page."""
    sample = html[:8000]
    return any(signal in sample for signal in _CF_BLOCK_SIGNALS)


class EremNewsScraper:
    """
    Playwright-based scraper for eremnews.com sports section.

    Usage::

        async with EremNewsScraper(settings) as scraper:
            articles = await scraper.poll_subcategory(subcategory)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._browser_manager = BrowserManager(settings)
        self._seen_urls: set[str] = set()

    async def start(self) -> None:
        await self._browser_manager.start()
        # Warm up: visit homepage so Cloudflare sets cookies before we hit articles
        await self._browser_manager.warmup(_HOME_URL)

    async def stop(self) -> None:
        await self._browser_manager.stop()

    async def __aenter__(self) -> "EremNewsScraper":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    async def poll_subcategory(self, subcategory: dict) -> list[RawArticle]:
        name = subcategory["name"]
        url  = subcategory["url"]

        logger.info(f"Polling Eram News: {name}", url=url)

        listing_html = await self._safe_load_page(
            url,
            referrer=_HOME_URL,
            wait_for_content=True,
        )
        if not listing_html:
            logger.error(f"Failed to load listing page for {name}")
            return []

        if is_cloudflare_blocked(listing_html):
            logger.error("Cloudflare blocked listing page — will retry next cycle")
            return []

        stubs = parse_article_list(listing_html, subcategory=name)
        new_stubs = [a for a in stubs if a.url not in self._seen_urls]
        logger.info(f"{len(new_stubs)} new articles in {name}")

        if not new_stubs:
            return []

        articles = await self._fetch_articles_batch(new_stubs, batch_size=2)

        for article in articles:
            self._seen_urls.add(article.url)

        return articles

    async def seed_seen_urls(self, known_urls: set[str]) -> None:
        self._seen_urls.update(known_urls)
        logger.info(f"Seeded {len(known_urls)} known article URLs into seen set")

    # ── Page loading ──────────────────────────────────────────────────────────

    async def _safe_load_page(
        self,
        url: str,
        referrer: str = _LISTING_URL,
        wait_for_content: bool = False,
    ) -> Optional[str]:
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._settings.max_retries + 1):
            page: Optional[Page] = None
            try:
                page = await self._browser_manager.new_page()
                html = await self._navigate_and_extract(
                    page, url, referrer, wait_for_content
                )
                return html

            except PlaywrightTimeoutError as exc:
                last_exc = exc
                logger.warning(f"Timeout loading {url} (attempt {attempt})")

            except Exception as exc:
                last_exc = exc
                logger.warning(f"Error loading {url} (attempt {attempt}): {exc}")
                if any(k in str(exc).lower() for k in ("browser", "target", "connection")):
                    await self._browser_manager.restart()

            finally:
                if page and not page.is_closed():
                    try:
                        await page.close()
                    except Exception:
                        pass

            backoff = min(self._settings.retry_backoff_base * (2 ** (attempt - 1)), 60.0)
            logger.debug(f"Retrying in {backoff:.0f}s …")
            await asyncio.sleep(backoff)

        logger.error(f"All {self._settings.max_retries} attempts failed for {url}: {last_exc}")
        return None

    async def _navigate_and_extract(
        self,
        page: Page,
        url: str,
        referrer: str,
        wait_for_content: bool,
    ) -> str:
        # Use internal navigation (sets Referer, mimics in-site click)
        await self._browser_manager.navigate_as_internal(page, url, referrer=referrer)

        if wait_for_content:
            for selector in _CONTENT_READY_SELECTORS:
                try:
                    await page.wait_for_selector(selector, timeout=25_000, state="attached")
                    break
                except PlaywrightTimeoutError:
                    continue
            # Give the Next.js hydration extra time to render the article grid
            await asyncio.sleep(_GRID_SETTLE_SECONDS)

        return await page.content()

    # ── Article batch fetching ────────────────────────────────────────────────

    async def _fetch_articles_batch(
        self, stubs: list[RawArticle], batch_size: int = 2
    ) -> list[RawArticle]:
        results: list[RawArticle] = []

        for i in range(0, len(stubs), batch_size):
            batch = stubs[i : i + batch_size]
            tasks = [self._fetch_article_detail(stub) for stub in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for stub, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch detail for {stub.url}: {result}")
                    results.append(stub)
                else:
                    results.append(result)  # type: ignore

            # Human-like pause between batches
            await asyncio.sleep(3)

        return results

    async def _fetch_article_detail(self, stub: RawArticle) -> RawArticle:
        html = await self._safe_load_page(
            stub.url,
            referrer=_LISTING_URL,   # came from the sports listing
            wait_for_content=False,
        )
        if not html:
            return stub

        if is_cloudflare_blocked(html):
            logger.warning(f"Cloudflare blocked article page: {stub.url} — keeping stub")
            return stub

        return parse_article_detail(html, stub)
