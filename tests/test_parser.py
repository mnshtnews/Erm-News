"""
tests/test_parser.py
─────────────────────
Unit tests for the Eram News HTML parser.
Tests use synthetic HTML that mirrors the real Newspaper WP theme structure.
"""

from __future__ import annotations

import pytest
from datetime import datetime

from src.scraper.eremnews_parser import (
    parse_article_list,
    parse_article_detail,
    _extract_image,
    _extract_date,
    _extract_full_content,
)
from src.core.models import RawArticle
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: Synthetic HTML matching Newspaper WP theme
# ─────────────────────────────────────────────────────────────────────────────

LISTING_HTML = """
<html>
<body>
<div class="td-container">
  <div class="td-main-content">
    <article class="post td_module_wrap">
      <div class="td-module-thumb">
        <a href="https://www.eremnews.com/sports/football/1234">
          <img class="entry-thumb" src="https://cdn.eremnews.com/img/article1.jpg"
               width="400" height="300" alt="مباراة كرة قدم"/>
        </a>
      </div>
      <div class="td-module-meta-info">
        <h3 class="entry-title">
          <a href="https://www.eremnews.com/sports/football/1234">
            الهلال يحقق فوزاً كبيراً في دوري أبطال آسيا
          </a>
        </h3>
        <time class="entry-date td-module-date" datetime="2025-05-20T10:30:00+00:00">
          20 مايو 2025
        </time>
      </div>
    </article>
    <article class="post td_module_wrap">
      <div class="td-module-thumb">
        <a href="https://www.eremnews.com/sports/uae/5678">
          <img class="entry-thumb"
               data-src="https://cdn.eremnews.com/img/article2.jpg"
               src="data:image/gif;base64,R0lGODlhAQAB"
               alt="رياضة الإمارات"/>
        </a>
      </div>
      <div class="td-module-meta-info">
        <h3 class="entry-title">
          <a href="https://www.eremnews.com/sports/uae/5678">
            الإمارات تستضيف بطولة دولية للتنس
          </a>
        </h3>
        <time class="entry-date" datetime="2025-05-19T08:00:00+00:00">
          19 مايو 2025
        </time>
      </div>
    </article>
  </div>
</div>
</body>
</html>
"""

DETAIL_HTML = """
<html>
<head>
  <meta property="og:image" content="https://cdn.eremnews.com/img/hero.jpg"/>
  <meta property="article:published_time" content="2025-05-20T10:30:00+00:00"/>
  <title>الهلال يحقق فوزاً | إرم نيوز</title>
</head>
<body>
  <article>
    <h1 class="entry-title">الهلال يحقق فوزاً كبيراً في دوري أبطال آسيا</h1>
    <time class="entry-date" datetime="2025-05-20T10:30:00+00:00">20 مايو 2025</time>
    <div class="td-post-featured-image">
      <img src="https://cdn.eremnews.com/img/hero.jpg" alt="الهلال"/>
    </div>
    <div class="td-post-content">
      <p>حقق نادي الهلال السعودي فوزاً كبيراً على نظيره الياباني في دور الستة عشر من بطولة دوري أبطال آسيا.</p>
      <p>وجاء الفوز بنتيجة ثلاثة أهداف مقابل هدف واحد في مباراة مثيرة أقيمت على أرضية ملعب الملك فهد الدولي.</p>
      <div class="td-a-rec">إعلان</div>
      <p>وسجّل أهداف الهلال كل من محمد الدوسري وسالم الدوسري وأندرسون تاليسكا، فيما جاء الهدف الياباني في وقت متأخر من اللقاء.</p>
    </div>
  </article>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Listing parser tests
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_listing_returns_articles():
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    assert len(articles) == 2


def test_parse_listing_title():
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    assert "الهلال" in articles[0].title


def test_parse_listing_url():
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    assert "eremnews.com" in articles[0].url


def test_parse_listing_date():
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    assert articles[0].publish_date is not None
    assert articles[0].publish_date.year == 2025


def test_parse_listing_image_direct_src():
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    assert articles[0].image_url is not None
    assert "eremnews.com" in articles[0].image_url or "cdn" in articles[0].image_url


def test_parse_listing_image_data_src():
    """data-src should be preferred over base64 placeholder src."""
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    # Second article uses data-src
    assert articles[1].image_url is not None
    assert "data:image" not in articles[1].image_url


def test_parse_listing_subcategory():
    articles = parse_article_list(LISTING_HTML, subcategory="رياضة")
    for a in articles:
        assert a.subcategory == "رياضة"


def test_parse_listing_empty_html():
    articles = parse_article_list("<html><body></body></html>", subcategory="رياضة")
    assert articles == []


# ─────────────────────────────────────────────────────────────────────────────
# Detail parser tests
# ─────────────────────────────────────────────────────────────────────────────

def make_stub() -> RawArticle:
    return RawArticle(
        title="الهلال يحقق فوزاً",
        url="https://www.eremnews.com/sports/football/1234",
        subcategory="رياضة",
    )


def test_parse_detail_extracts_content():
    result = parse_article_detail(DETAIL_HTML, make_stub())
    assert result.content is not None
    assert len(result.content) > 50
    assert "الهلال" in result.content


def test_parse_detail_removes_ad_noise():
    result = parse_article_detail(DETAIL_HTML, make_stub())
    assert "إعلان" not in (result.content or "")


def test_parse_detail_extracts_image_from_og():
    result = parse_article_detail(DETAIL_HTML, make_stub())
    assert result.image_url == "https://cdn.eremnews.com/img/hero.jpg"


def test_parse_detail_extracts_date():
    result = parse_article_detail(DETAIL_HTML, make_stub())
    assert result.publish_date is not None
    assert result.publish_date.year == 2025
    assert result.publish_date.month == 5


def test_parse_detail_does_not_overwrite_existing_image():
    stub = make_stub()
    stub = stub.model_copy(update={"image_url": "https://existing.com/img.jpg"})
    result = parse_article_detail(DETAIL_HTML, stub)
    assert result.image_url == "https://existing.com/img.jpg"


def test_parse_detail_does_not_overwrite_existing_date():
    stub = make_stub()
    existing_date = datetime(2025, 1, 1)
    stub = stub.model_copy(update={"publish_date": existing_date})
    result = parse_article_detail(DETAIL_HTML, stub)
    assert result.publish_date == existing_date


# ─────────────────────────────────────────────────────────────────────────────
# Article hash / deduplication tests
# ─────────────────────────────────────────────────────────────────────────────

def test_article_hash_is_deterministic():
    stub = make_stub()
    assert stub.article_hash == make_stub().article_hash


def test_article_hash_differs_for_different_urls():
    a = RawArticle(
        title="t",
        url="https://www.eremnews.com/sports/1",
        subcategory="رياضة",
    )
    b = RawArticle(
        title="t",
        url="https://www.eremnews.com/sports/2",
        subcategory="رياضة",
    )
    assert a.article_hash != b.article_hash
