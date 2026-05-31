"""
tests/test_classifier.py
─────────────────────────
Unit tests for the article classification engine.
Tests keyword matching for UAE / Arab / Global sports news.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.classifier.engine import ArticleClassifier
from src.core.models import NewsClassification, RawArticle


def make_settings(use_openai: bool = False):
    s = MagicMock()
    s.use_openai = use_openai
    s.openai_api_key = None
    s.openai_model = "gpt-4o-mini"
    return s


def make_article(title: str, summary: str = "", content: str = "") -> RawArticle:
    return RawArticle(
        title=title,
        url="https://www.eremnews.com/sports/test/123",
        summary=summary or None,
        content=content or None,
        subcategory="رياضة",
    )


@pytest.mark.asyncio
async def test_uae_classification():
    classifier = ArticleClassifier(make_settings())
    article = make_article(
        title="الإمارات تفوز بذهبية الرماية في دورة الألعاب الآسيوية"
    )
    result = await classifier.classify(article)
    assert result.classification == NewsClassification.UAE
    assert result.confidence >= 0.7


@pytest.mark.asyncio
async def test_uae_club_classification():
    classifier = ArticleClassifier(make_settings())
    article = make_article(
        title="العين يتصدر الدوري الإماراتي بعد الفوز على الجزيرة"
    )
    result = await classifier.classify(article)
    assert result.classification == NewsClassification.UAE


@pytest.mark.asyncio
async def test_arab_classification():
    classifier = ArticleClassifier(make_settings())
    article = make_article(
        title="الأهلي المصري يتأهل لنصف نهائي دوري أبطال أفريقيا"
    )
    result = await classifier.classify(article)
    assert result.classification == NewsClassification.ARAB


@pytest.mark.asyncio
async def test_global_classification():
    classifier = ArticleClassifier(make_settings())
    article = make_article(
        title="ريال مدريد يتوج بلقب دوري أبطال أوروبا للمرة الخامسة عشرة"
    )
    result = await classifier.classify(article)
    assert result.classification == NewsClassification.GLOBAL


@pytest.mark.asyncio
async def test_uae_takes_priority_over_arab():
    """If UAE entity and Arab entity both appear, UAE wins."""
    classifier = ArticleClassifier(make_settings())
    article = make_article(
        title="الإمارات تواجه مصر في كأس العرب"
    )
    result = await classifier.classify(article)
    assert result.classification == NewsClassification.UAE


@pytest.mark.asyncio
async def test_classification_returns_uae_entities():
    classifier = ArticleClassifier(make_settings())
    article = make_article(title="أبوظبي تستضيف سباق الفورمولا 1")
    result = await classifier.classify(article)
    assert len(result.uae_entities) > 0


@pytest.mark.asyncio
async def test_classification_method_is_keyword():
    classifier = ArticleClassifier(make_settings())
    article = make_article(title="دبي تستضيف بطولة عالمية للغولف")
    result = await classifier.classify(article)
    assert result.method == "keyword"
