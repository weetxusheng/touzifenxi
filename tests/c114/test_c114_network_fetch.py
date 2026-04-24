"""Live HTTPS checks for C114 fetch path (opt-in, off by default in CI)."""

import os
from datetime import date

import pytest

from utils.tools.orchestration.c114.hot_topics_data import CHANNELS, collect_daily_report, fetch_text

pytestmark = [
    pytest.mark.c114_network,
    pytest.mark.skipif(
        not os.environ.get("C114_NETWORK_TEST", "").strip()
        or os.environ.get("C114_NETWORK_TEST", "").strip().lower() in ("0", "false", "no"),
        reason="Set C114_NETWORK_TEST=1 to run live fetches to www.c114.com.cn",
    ),
]


def test_fetch_c114_home_html() -> None:
    url = CHANNELS["home"].url
    text = fetch_text(url, timeout=45.0)
    assert len(text) > 500
    assert "C114" in text or "c114" in text.lower() or "通信" in text


def test_collect_daily_report_home_channel() -> None:
    """完整走一遍首页频道：拉页、解析、生成日报结构（文章数可能为 0，但说明链路与取数已通）。"""
    reports = collect_daily_report(
        report_date=date.today(),
        channel_keys=["home"],
        timeout=45.0,
    )
    assert len(reports) == 1
    r = reports[0]
    assert r.channel_key == "home"
    assert r.channel_url == CHANNELS["home"].url
    assert r.article_count >= 0
