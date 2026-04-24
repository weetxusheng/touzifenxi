"""Tests for ``brief_link_dedupe_key`` and step5 supplement vs source logic."""

from utils.tools.brief.links import brief_link_dedupe_key


def test_dedupe_key_strips_trailing_underscore_site_suffix() -> None:
    a = brief_link_dedupe_key("菜鸟入股后 无人车公司九识智能拟在港IPO融资6亿美元", "2026-04-23")
    b = brief_link_dedupe_key("菜鸟入股后 无人车公司九识智能拟在港IPO融资6亿美元_腾讯新闻", "2026-04-23")
    assert a == b


def test_dedupe_key_differs_when_title_or_date_differs() -> None:
    a = brief_link_dedupe_key("传阿里系自动驾驶公司九识智能拟赴港IPO", "2026-04-23")
    b = brief_link_dedupe_key("【IPO前哨】传无人驾驶独角兽九识智能拟赴港上市_腾讯新闻", "2026-04-23")
    assert a != b


def test_dedupe_key_normalizes_ymd() -> None:
    assert (
        brief_link_dedupe_key("同一标题_腾讯新闻", "2026-4-3")
        == brief_link_dedupe_key("同一标题", "2026-04-03")
    )


def test_dedupe_key_picks_date_from_title_when_pub_empty() -> None:
    a = brief_link_dedupe_key("Same story (2026-04-23)", "")
    b = brief_link_dedupe_key("Same story_tencent", "2026-04-23")
    assert a == b
