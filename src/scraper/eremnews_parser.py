"""
src/scraper/eremnews_parser.py
───────────────────────────────
HTML parsing layer for eremnews.com — Next.js / Tailwind CSS site.

Structure discovered via real HTML inspection (listing_debug.html):

Listing page (https://www.eremnews.com/sports):
  div.grid.grid-cols-1.tablet:grid-cols-2.desktop:grid-cols-4
    div                           ← each article card (direct child)
      a[href="/sports/XXXXX"][title="..."]   ← image link (title = article title)
        picture > img[src]        ← article image (cdn.eremnews.com)
      div > a[href="/sports/XXXXX"][title="..."]  ← title link
        h3                        ← title text

Article detail page (Next.js rendered):
  h1                              ← article title
  time[datetime]                  ← publish date
  picture > img                   ← hero image
  div with article body paragraphs
  og:image / article:published_time meta  ← reliable fallbacks
  JSON-LD                         ← date fallback
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import dateparser
from bs4 import BeautifulSoup, Tag
from loguru import logger

from src.core.models import RawArticle

ERAM_BASE = "https://www.eremnews.com"

# Use lxml if available (faster), otherwise fall back to the built-in html.parser
try:
    import lxml  # noqa: F401
    _BS_PARSER = "lxml"
except ImportError:
    _BS_PARSER = "html.parser"

# Noise selectors to strip from article body
_NOISE_SELECTORS = [
    "script", "style", "noscript", "iframe",
    "ins", "[class*='ad-']", "[id*='ad-']",
    "[class*='share']", "[class*='related']",
    "[class*='newsletter']", "[class*='social']",
]

# Regex to match sports article slug URLs
_SPORTS_SLUG_RE = re.compile(r"^/sports/[a-z0-9\-]+$")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_article_list(
    html: str,
    subcategory: str,
    base_url: str = ERAM_BASE,
) -> list[RawArticle]:
    """
    Parse the Eram News sports listing page.

    Strategy 1 (primary): Find the Tailwind grid container with
      'desktop:grid-cols-4' and 'tablet:grid-cols-2' classes.
    Strategy 2 (fallback): Search all <a> links matching /sports/SLUG pattern,
      deduplicate, and build article stubs from them.
    """
    soup = BeautifulSoup(html, _BS_PARSER)

    # ── Strategy 1: grid container ────────────────────────────────────────────
    grid = _find_sports_grid(soup)

    if grid:
        cards = [c for c in grid.children if c.name]
        articles: list[RawArticle] = []
        for card in cards:
            try:
                article = _parse_card(card, subcategory, base_url)
                if article:
                    articles.append(article)
            except Exception as exc:
                logger.warning(f"Failed to parse card: {exc}")

        if articles:
            unique = _dedup(articles)
            logger.debug(f"Parsed {len(unique)} articles from Eram News grid")
            return unique

        logger.warning(
            f"Grid found but no cards parsed for subcategory={subcategory}. "
            f"Falling back to direct link search."
        )

    else:
        all_tags = {t.name for t in soup.find_all(True)}
        logger.warning(
            f"Sports grid not found for subcategory={subcategory}. "
            f"Falling back to direct link search. Tags present: {sorted(all_tags)}"
        )

    # ── Strategy 2: direct link search ───────────────────────────────────────
    return _parse_via_direct_links(soup, subcategory, base_url)


def parse_article_detail(html: str, raw: RawArticle) -> RawArticle:
    """
    Parse a full Eram News article detail page.
    Extracts clean content, image, and date.
    """
    soup = BeautifulSoup(html, _BS_PARSER)
    updates: dict = {}

    if not raw.image_url:
        image_url = _extract_image(soup)
        if image_url:
            updates["image_url"] = image_url

    if not raw.publish_date:
        publish_date = _extract_date(soup)
        if publish_date:
            updates["publish_date"] = publish_date

    content = _extract_full_content(soup)
    if content:
        updates["content"] = content

    if updates:
        raw = raw.model_copy(update=updates)

    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Grid finder
# ─────────────────────────────────────────────────────────────────────────────

def _find_sports_grid(soup: BeautifulSoup) -> Optional[Tag]:
    """
    Find the article grid container.
    Matches: div with both 'desktop:grid-cols-4' and 'tablet:grid-cols-2' in class.
    Falls back to any div with 'grid-cols' in class that contains sports links.
    """
    # Primary: exact Tailwind classes
    def _is_sports_grid(tag: Tag) -> bool:
        if tag.name != "div":
            return False
        cls = " ".join(tag.get("class", []))
        return "desktop:grid-cols-4" in cls and "tablet:grid-cols-2" in cls

    grid = soup.find(_is_sports_grid)
    if grid:
        return grid

    # Fallback: any grid-like div that has sports article links as children
    def _is_grid_with_sports(tag: Tag) -> bool:
        if tag.name not in ("div", "section", "ul"):
            return False
        cls = " ".join(tag.get("class", []))
        if "grid" not in cls and "list" not in cls:
            return False
        # Must have at least 2 sports links as descendants
        links = tag.find_all("a", href=_SPORTS_SLUG_RE)
        return len(links) >= 2

    return soup.find(_is_grid_with_sports)


# ─────────────────────────────────────────────────────────────────────────────
# Fallback: parse directly from all sports links on the page
# ─────────────────────────────────────────────────────────────────────────────

def _parse_via_direct_links(
    soup: BeautifulSoup,
    subcategory: str,
    base_url: str,
) -> list[RawArticle]:
    """
    Fallback parser: find all <a href="/sports/SLUG" title="..."> links
    on the page and build article stubs from them.
    This works even when the Next.js grid hasn't fully hydrated.
    """
    articles: list[RawArticle] = []

    # Find all anchor tags matching the sports slug pattern
    all_links = soup.find_all("a", href=_SPORTS_SLUG_RE)

    for a_tag in all_links:
        try:
            href = str(a_tag.get("href", ""))
            if not href:
                continue

            # Get title from title attr, h3, or text content
            title = str(a_tag.get("title", "")).strip()
            if not title or len(title) < 3:
                h3 = a_tag.find("h3")
                if h3:
                    title = _clean_text(h3.get_text())
                else:
                    title = _clean_text(a_tag.get_text())

            if not title or len(title) < 3:
                continue

            url = base_url + href

            # Try to get image from nearby img tag
            image_url: Optional[str] = None
            # Look in this link and parent for img
            parent = a_tag.parent
            search_scope = parent if parent else a_tag
            img = search_scope.find("img") if search_scope else None
            if not img:
                img = a_tag.find("img")
            if img:
                src = str(img.get("src", ""))
                if src and "data:image" not in src:
                    if "cdn.eremnews.com" in src and not src.endswith(".webp"):
                        src = src + ".webp"
                    image_url = src

            # Try to get date from nearby time tag
            publish_date: Optional[datetime] = None
            if parent:
                time_el = parent.find("time")
                if time_el:
                    dt_attr = time_el.get("datetime")
                    if dt_attr:
                        try:
                            publish_date = datetime.fromisoformat(
                                str(dt_attr).replace("Z", "+00:00")
                            )
                        except Exception:
                            publish_date = _parse_arabic_date(
                                time_el.get_text(strip=True)
                            )

            articles.append(RawArticle(
                title=title,
                url=url,
                image_url=image_url,
                publish_date=publish_date,
                subcategory=subcategory,
            ))

        except Exception as exc:
            logger.warning(f"Failed to parse link stub: {exc}")

    unique = _dedup(articles)
    if unique:
        logger.info(
            f"Fallback parser found {len(unique)} articles via direct link search"
        )
    else:
        logger.warning(
            f"No articles found for subcategory={subcategory} via any strategy."
        )

    return unique


# ─────────────────────────────────────────────────────────────────────────────
# Card parser (used when grid IS found)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_card(
    card: Tag,
    subcategory: str,
    base_url: str,
) -> Optional[RawArticle]:
    """
    Parse a single article card.

    Card structure (Tailwind Next.js):
      <div class="relative flex w-full ...">
        <a href="/sports/SLUG" title="TITLE">   ← image link
          <picture><img src="..."/></picture>
        </a>
        <div>
          <a href="/sports/SLUG" title="TITLE"> ← title link
            <h3>TITLE TEXT</h3>
          </a>
        </div>
      </div>
    """
    # Find the title link — has both href="/sports/..." AND title attribute
    title_a = card.find(
        "a",
        href=_SPORTS_SLUG_RE,
        title=True,
    )

    if not title_a:
        return None

    href  = str(title_a["href"])
    title = str(title_a.get("title", "")).strip()
    url   = base_url + href

    if not title or len(title) < 3:
        # Fallback: text inside h3
        h3 = title_a.find("h3")
        if h3:
            title = _clean_text(h3.get_text())
        if not title:
            return None

    # ── Image ────────────────────────────────────────────────────────────────
    image_url: Optional[str] = None
    img = card.find("img")
    if img:
        src = img.get("src", "")
        if src and "data:image" not in src:
            if "cdn.eremnews.com" in src and not src.endswith(".webp"):
                src = src + ".webp"
            image_url = src

    # ── Date ─────────────────────────────────────────────────────────────────
    publish_date: Optional[datetime] = None
    time_el = card.find("time")
    if time_el:
        dt_attr = time_el.get("datetime")
        if dt_attr:
            try:
                publish_date = datetime.fromisoformat(str(dt_attr).replace("Z", "+00:00"))
            except Exception:
                publish_date = _parse_arabic_date(time_el.get_text(strip=True))

    return RawArticle(
        title=title,
        url=url,
        image_url=image_url,
        publish_date=publish_date,
        subcategory=subcategory,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Detail page extractors
# ─────────────────────────────────────────────────────────────────────────────

def _extract_full_content(soup: BeautifulSoup) -> str:
    """
    Extract article body from the Next.js detail page.
    Strategy: find the div/section with the most <p> paragraph text.
    """
    # Remove noise first
    for selector in _NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    # Try common Next.js article body patterns
    body_candidates = [
        soup.select_one("[class*='article-body']"),
        soup.select_one("[class*='articleBody']"),
        soup.select_one("[class*='content-body']"),
        soup.select_one("[class*='post-content']"),
        soup.select_one("article"),
    ]
    body = next((c for c in body_candidates if c is not None), None)

    if body is None:
        body = _find_content_rich_div(soup)

    if body is None:
        return ""

    paragraphs: list[str] = []
    for p in body.find_all("p"):
        text = _clean_text(p.get_text(separator=" ", strip=True))
        if len(text) >= 20:
            paragraphs.append(text)

    return "\n\n".join(paragraphs)


def _find_content_rich_div(soup: BeautifulSoup) -> Optional[Tag]:
    """Fallback: div/section with the most paragraph text."""
    best: Optional[Tag] = None
    best_score = 0
    for tag in soup.find_all(["div", "section", "main"]):
        ps = tag.find_all("p")
        score = sum(len(p.get_text(strip=True)) for p in ps)
        if score > best_score:
            best_score = score
            best = tag
    return best if best_score > 100 else None


def _extract_image(soup: BeautifulSoup) -> Optional[str]:
    """Extract hero image — og:image is the most reliable on Next.js."""
    # 1. Open Graph
    og = soup.find("meta", property="og:image")
    if og:
        content = og.get("content", "")
        if content:
            return str(content)

    # 2. First large img from cdn.eremnews.com
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "cdn.eremnews.com" in src and "data:image" not in src:
            return src

    return None


def _extract_date(soup: BeautifulSoup) -> Optional[datetime]:
    """Extract publish date from Next.js article page."""
    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                date_str = data.get("datePublished") or data.get("dateModified")
                if date_str:
                    return datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        except Exception:
            continue

    # 2. Open Graph
    og = soup.find("meta", property="article:published_time")
    if og:
        content = og.get("content", "")
        if content:
            try:
                return datetime.fromisoformat(str(content).replace("Z", "+00:00"))
            except Exception:
                pass

    # 3. <time datetime="...">
    time_el = soup.find("time", attrs={"datetime": True})
    if time_el:
        dt = str(time_el["datetime"])
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return _parse_arabic_date(time_el.get_text(strip=True))

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _dedup(articles: list[RawArticle]) -> list[RawArticle]:
    """Deduplicate articles by URL, preserving order."""
    seen: set[str] = set()
    unique: list[RawArticle] = []
    for a in articles:
        if a.url not in seen:
            seen.add(a.url)
            unique.append(a)
    return unique


def _parse_arabic_date(date_str: str) -> Optional[datetime]:
    if not date_str or len(date_str) < 5:
        return None
    try:
        return dateparser.parse(
            date_str,
            languages=["ar"],
            settings={
                "PREFER_DAY_OF_MONTH": "first",
                "TIMEZONE": "Asia/Dubai",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
    except Exception:
        return None


def _clean_text(text: str) -> str:
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
