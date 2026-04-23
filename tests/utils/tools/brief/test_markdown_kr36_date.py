"""36Kr 简报标题日期区间。"""

from utils.tools.brief.markdown import format_kr36_crawl_week_range_label


def test_format_kr36_crawl_week_range_label_monday_only() -> None:
    assert format_kr36_crawl_week_range_label("2026-04-20") == "2026-04-20"


def test_format_kr36_crawl_week_range_label_friday() -> None:
    assert format_kr36_crawl_week_range_label("2026-04-24") == "2026-04-20 至 2026-04-24"


def test_format_kr36_crawl_week_range_label_sunday() -> None:
    assert format_kr36_crawl_week_range_label("2026-04-26") == "2026-04-20 至 2026-04-26"


def test_format_kr36_accepts_datetime_prefix() -> None:
    assert format_kr36_crawl_week_range_label("2026-04-24T12:00:00") == "2026-04-20 至 2026-04-24"

