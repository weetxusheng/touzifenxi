from __future__ import annotations

from datetime import date

import pytest

from chip.channels.spec import ChipChannelSpec
from chip.source_adapter import ChipSourceAdapter, cross_channel_dedupe
from utils.tools.content_models import RawArticleDetail, RawArticleRef


def _make_ref(channel: str, article_id: str, title: str, url: str = "") -> RawArticleRef:
    return RawArticleRef(
        source_site="chip",
        article_id=article_id,
        title=title,
        url=url or f"https://example.com/{channel}/{article_id}",
        published_at="2026-05-19",
        channel=channel,
        source_bucket=channel,
    )


def _spec(key: str, listing_refs: list[RawArticleRef], detail: RawArticleDetail | None = None):
    def fl(spec, report_date):
        return listing_refs

    def fa(spec, ref):
        if detail is None:
            raise RuntimeError("no detail configured")
        return detail

    return ChipChannelSpec(
        key=key,
        name=key.upper(),
        listing_urls=(),
        fetch_listing=fl,
        fetch_article=fa,
    )


def test_adapter_routes_to_each_channel(monkeypatch):
    semi_refs = [_make_ref("semi", "a1", "AAA")]
    laoyaoba_refs = [_make_ref("laoyaoba", "b1", "BBB")]
    fake_channels = {
        "semi": _spec("semi", semi_refs),
        "laoyaoba": _spec("laoyaoba", laoyaoba_refs),
    }
    monkeypatch.setattr("chip.source_adapter.CHANNELS", fake_channels)
    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(date(2026, 5, 19))
    assert len(refs) == 2
    assert {r.channel for r in refs} == {"semi", "laoyaoba"}


def test_adapter_dedupes_within_channel(monkeypatch):
    dup = _make_ref("semi", "a1", "AAA", url="https://example.com/semi/a1")
    refs_with_dup = [dup, dup]
    fake_channels = {"semi": _spec("semi", refs_with_dup), "laoyaoba": _spec("laoyaoba", [])}
    monkeypatch.setattr("chip.source_adapter.CHANNELS", fake_channels)
    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(date(2026, 5, 19))
    assert len(refs) == 1


def test_adapter_continues_when_one_channel_fails(monkeypatch):
    def failing(spec, report_date):
        raise RuntimeError("network down")

    good = _spec("laoyaoba", [_make_ref("laoyaoba", "b1", "BBB")])
    bad = ChipChannelSpec(
        key="semi",
        name="SEMI",
        listing_urls=(),
        fetch_listing=failing,
        fetch_article=lambda *a: None,
    )
    monkeypatch.setattr("chip.source_adapter.CHANNELS", {"semi": bad, "laoyaoba": good})
    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(date(2026, 5, 19))
    assert len(refs) == 1
    assert refs[0].channel == "laoyaoba"
    assert "semi" in adapter.failed_channels


def test_adapter_fetch_article_routes_to_channel(monkeypatch):
    detail = RawArticleDetail(
        source_site="chip", article_id="b1", title="BBB",
        url="https://example.com/laoyaoba/b1", published_at="2026-05-19",
        author="", channel="laoyaoba", source_bucket="laoyaoba",
        tags=[], summary="", content_text="body",
    )
    ch = _spec("laoyaoba", [], detail=detail)
    monkeypatch.setattr("chip.source_adapter.CHANNELS", {"laoyaoba": ch, "semi": _spec("semi", [])})
    adapter = ChipSourceAdapter()
    ref = _make_ref("laoyaoba", "b1", "BBB")
    got = adapter.fetch_article(ref)
    assert got is detail


def test_cross_channel_dedupe_picks_semi_when_titles_similar():
    semi_ref = _make_ref("semi", "a1", "格罗方德推出CPO硅光子方案")
    lyb_ref = _make_ref("laoyaoba", "b1", "格罗方德推出 CPO 硅光子方案")
    kept = cross_channel_dedupe([lyb_ref, semi_ref], similarity_threshold=0.6)
    assert len(kept) == 1
    assert kept[0].channel == "semi"
    assert lyb_ref.url in kept[0].metadata.get("alt_urls", [])


def test_cross_channel_dedupe_keeps_distinct_titles():
    a = _make_ref("semi", "a1", "中芯国际发布Q1财报")
    b = _make_ref("laoyaoba", "b1", "特斯拉放弃印度建厂")
    kept = cross_channel_dedupe([a, b], similarity_threshold=0.6)
    assert len(kept) == 2
