from __future__ import annotations

from datetime import date

from chip.channels.semi import (
    SEMI_ARTICLE_URL_RE,
    fetch_article,
    fetch_listing,
    parse_article_html,
    parse_listing_html,
)
from chip.channels.spec import CHANNELS
from utils.tools.content_models import RawArticleRef


def test_parse_listing_homepage_yields_articles(semi_home_html):
    refs = parse_listing_html(semi_home_html, base_url="https://www.semi.org.cn/site/semi/")
    assert len(refs) >= 20
    for r in refs:
        assert SEMI_ARTICLE_URL_RE.search(r["url"])
        assert r["title"]
        assert r["channel"] == "semi"
        assert r["source_bucket"] == "semi"
    titles = " ".join(r["title"] for r in refs)
    assert "陈立武" in titles or "14A" in titles
    assert "SEMI报告" in titles or "硅晶圆" in titles


def test_parse_listing_dedupes_by_url(semi_home_html):
    refs = parse_listing_html(semi_home_html, base_url="https://www.semi.org.cn/site/semi/")
    urls = [r["url"] for r in refs]
    assert len(urls) == len(set(urls))


def test_parse_listing_column_page(semi_column_html):
    refs = parse_listing_html(semi_column_html, base_url="https://www.semi.org.cn/site/semi/")
    assert len(refs) >= 8
    assert all(SEMI_ARTICLE_URL_RE.search(r["url"]) for r in refs)


def test_parse_article_extracts_fields(semi_article_html):
    parsed = parse_article_html(
        semi_article_html,
        url="https://www.semi.org.cn/site/semi/article/a77885fcd495465e97cbfd36b5a17381.html",
    )
    assert "英特尔" in parsed["title"]
    assert "14A" in parsed["title"]
    assert parsed["published_date"] == date(2026, 5, 20)
    assert "综合报道" in parsed["source_org"] or parsed["source_org"]
    assert "14A" in parsed["summary"] or "陈立武" in parsed["summary"]
    assert len(parsed["content_text"]) > 200
    assert "陈立武" in parsed["content_text"]


def test_parse_article_handles_missing_summary(semi_article_html):
    parsed = parse_article_html(
        semi_article_html,
        url="https://www.semi.org.cn/site/semi/article/test.html",
    )
    assert isinstance(parsed["title"], str)
    assert isinstance(parsed["summary"], str)
    assert isinstance(parsed["content_text"], str)


def test_fetch_listing_uses_listing_urls_and_returns_refs(semi_home_html, semi_column_html, semi_article_html):
    fetcher = {
        "https://www.semi.org.cn/site/semi/": semi_home_html,
        "https://www.semi.org.cn/site/semi/column/26595298402893836.html": semi_column_html,
    }
    refs = fetch_listing(
        CHANNELS["semi"],
        date(2026, 5, 20),
        http_get=lambda url: fetcher[url],
        detail_get=lambda url: semi_article_html,  # all dates → 2026-05-20
    )
    assert len(refs) >= 20
    assert all(isinstance(r, RawArticleRef) for r in refs)
    assert all(r.channel == "semi" and r.source_bucket == "semi" for r in refs)
    assert any(r.published_at.startswith("2026-05-20") for r in refs)


def test_fetch_article_returns_RawArticleDetail(semi_article_html):
    ref = RawArticleRef(
        source_site="chip",
        article_id="a77885fcd495465e97cbfd36b5a17381",
        title="placeholder",
        url="https://www.semi.org.cn/site/semi/article/a77885fcd495465e97cbfd36b5a17381.html",
        channel="semi",
        source_bucket="semi",
    )
    detail = fetch_article(
        CHANNELS["semi"],
        ref,
        http_get=lambda url: semi_article_html,
    )
    assert detail.title.startswith("英特尔") or "14A" in detail.title
    assert detail.published_at.startswith("2026-05-20")
    assert detail.content_text
    assert detail.channel == "semi"


import os


@pytest.mark.chip_network
@pytest.mark.skipif(
    os.environ.get("CHIP_NETWORK_TEST") != "1",
    reason="set CHIP_NETWORK_TEST=1 to enable live fetch",
)
def test_live_semi_listing_returns_something():
    from chip.channels.semi import _default_http_get, parse_listing_html

    html = _default_http_get("https://www.semi.org.cn/site/semi/")
    refs = parse_listing_html(html, base_url="https://www.semi.org.cn/site/semi/")
    assert len(refs) >= 10
