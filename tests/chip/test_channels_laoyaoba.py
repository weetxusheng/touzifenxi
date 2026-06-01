from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from chip.channels.laoyaoba import (
    EXCLUDED_LAOYAOBA_IDS,
    fetch_article,
    fetch_listing,
    parse_article_html,
    parse_listing_html,
)
from chip.channels.spec import CHANNELS
from utils.tools.content_models import RawArticleRef

SHANGHAI = timezone(timedelta(hours=8))


def test_parse_listing_extracts_articles(laoyaoba_xinyaowen_html):
    items = parse_listing_html(
        laoyaoba_xinyaowen_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert len(items) >= 30
    for it in items:
        assert it["url"].startswith("https://www.laoyaoba.com/n/")
        assert it["channel"] == "laoyaoba"
        assert it["source_bucket"] == "laoyaoba"
        assert it["article_id"].isdigit()
        assert int(it["article_id"]) not in EXCLUDED_LAOYAOBA_IDS


def test_parse_listing_includes_published_at_when_present(laoyaoba_xinyaowen_html):
    items = parse_listing_html(
        laoyaoba_xinyaowen_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    with_time = [i for i in items if i.get("published_at")]
    assert len(with_time) >= 10


def test_parse_listing_excludes_short_titles(laoyaoba_xinyaowen_html):
    items = parse_listing_html(
        laoyaoba_xinyaowen_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    for it in items:
        assert len(it["title"]) >= 6


def test_excluded_ids_constant_contents():
    assert 729927 in EXCLUDED_LAOYAOBA_IDS
    assert 683317 in EXCLUDED_LAOYAOBA_IDS
    assert 683318 in EXCLUDED_LAOYAOBA_IDS


def test_parse_article_icboard_relative_time(laoyaoba_article_icboard_html):
    parsed = parse_article_html(
        laoyaoba_article_icboard_html,
        url="https://www.laoyaoba.com/n/1038379",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "IC载板" in parsed["title"]
    assert parsed["published_at"].startswith("2026-05-19T15:00")
    assert parsed["author"] == "爱集微"
    assert "海门" in parsed["source_org"]
    assert "海门" in parsed["tags"]
    assert "IC载板" in parsed["content_text"]
    assert "summary" in parsed and parsed["summary"]


def test_parse_article_smic_mmdd_format(laoyaoba_article_smic_html):
    parsed = parse_article_html(
        laoyaoba_article_smic_html,
        url="https://www.laoyaoba.com/n/1035371",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "中芯国际" in parsed["title"]
    assert parsed["published_at"].startswith("2026-05-14T18:18")
    assert "中芯国际" in parsed["tags"] or "第一季度财报" in parsed["tags"]
    assert isinstance(parsed["author"], str)


def test_parse_article_legacy_yyyy_format(laoyaoba_article_legacy_html):
    parsed = parse_article_html(
        laoyaoba_article_legacy_html,
        url="https://www.laoyaoba.com/n/729927",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "版权声明" in parsed["title"]
    assert parsed["published_at"].startswith("2019-09-26")


def test_parse_article_with_missing_time_returns_empty_string(laoyaoba_article_icboard_html):
    broken = laoyaoba_article_icboard_html.replace("3小时前", "wat?")
    parsed = parse_article_html(
        broken,
        url="https://www.laoyaoba.com/n/1038379",
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert parsed["published_at"] == ""


@pytest.fixture(autouse=True)
def _disable_laoyaoba_throttle(monkeypatch):
    import chip.channels.laoyaoba as _lyb_mod
    monkeypatch.setattr(_lyb_mod, "_THROTTLE_SECONDS", 0)


def test_fetch_listing_filters_by_date(laoyaoba_xinyaowen_html, laoyaoba_article_smic_html):
    refs = fetch_listing(
        CHANNELS["laoyaoba"],
        date(2026, 5, 14),
        http_get=lambda url: laoyaoba_xinyaowen_html,
        detail_get=lambda url: laoyaoba_article_smic_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert all(r.channel == "laoyaoba" for r in refs)
    assert all(r.published_at.startswith("2026-05-14") for r in refs)


def test_fetch_listing_uses_listing_time_when_available(laoyaoba_xinyaowen_html):
    # detail_get not called for items whose listing time already matches report_date
    detail_calls = []

    def detail_get(url):
        detail_calls.append(url)
        return ""  # would fail parse if reached

    refs = fetch_listing(
        CHANNELS["laoyaoba"],
        date(2026, 5, 19),  # "today" for the listing's "X小时前" items
        http_get=lambda url: laoyaoba_xinyaowen_html,
        detail_get=detail_get,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert all(r.published_at.startswith("2026-05-19") for r in refs)


def test_fetch_article_returns_RawArticleDetail(laoyaoba_article_icboard_html):
    ref = RawArticleRef(
        source_site="chip",
        article_id="1038379",
        title="placeholder",
        url="https://www.laoyaoba.com/n/1038379",
        channel="laoyaoba",
        source_bucket="laoyaoba",
    )
    detail = fetch_article(
        CHANNELS["laoyaoba"],
        ref,
        http_get=lambda url: laoyaoba_article_icboard_html,
        reference_now=datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI),
    )
    assert "IC载板" in detail.title
    assert detail.channel == "laoyaoba"
    assert "海门" in detail.tags
    assert detail.content_text


import os


@pytest.mark.chip_network
@pytest.mark.skipif(
    os.environ.get("CHIP_NETWORK_TEST") != "1",
    reason="set CHIP_NETWORK_TEST=1 to enable live fetch",
)
def test_live_laoyaoba_listing_returns_something():
    from chip.channels.laoyaoba import _default_http_get, parse_listing_html as live_parse_listing_html

    html = _default_http_get("https://www.laoyaoba.com/xinyaowen")
    items = live_parse_listing_html(
        html,
        reference_now=datetime.now(SHANGHAI),
    )
    assert len(items) >= 10
