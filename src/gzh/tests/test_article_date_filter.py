"""文章列表日期过滤（post_history / post_condition 共用）。"""

from gzh_pipeline.dajiala.client import (
    _filter_articles_by_date,
    article_post_date_iso,
    history_page_should_stop_paging,
)


def test_article_post_date_iso_from_post_time_str():
    assert article_post_date_iso({"post_time_str": "2026-05-20 10:00:00"}) == "2026-05-20"


def test_article_post_date_iso_from_unix_timestamp():
    assert article_post_date_iso({"post_time": 1744372317}) == "2025-04-11"


def test_filter_articles_by_date_exact_day():
    rows = [
        {"post_time_str": "2026-05-20 10:00:00"},
        {"post_time_str": "2026-05-19 23:59:59"},
    ]
    assert len(_filter_articles_by_date(rows, "2026-05-20")) == 1


def test_history_page_should_stop_paging():
    only_older = [{"post_time_str": "2026-05-19 10:00:00"}, {"post_time_str": "2026-05-18 10:00:00"}]
    assert history_page_should_stop_paging(only_older, "2026-05-20") is True
    mixed = [{"post_time_str": "2026-05-20 10:00:00"}, {"post_time_str": "2026-05-19 10:00:00"}]
    assert history_page_should_stop_paging(mixed, "2026-05-20") is True
    only_target = [{"post_time_str": "2026-05-20 10:00:00"}]
    assert history_page_should_stop_paging(only_target, "2026-05-20") is False
