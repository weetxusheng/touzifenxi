from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest

from chip.channels.datetime_parse import parse_laoyaoba_published_time

SHANGHAI = timezone(timedelta(hours=8))


def _ref() -> datetime:
    return datetime(2026, 5, 19, 18, 0, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("3小时前", datetime(2026, 5, 19, 15, 0, 0, tzinfo=SHANGHAI)),
        ("30分钟前", datetime(2026, 5, 19, 17, 30, 0, tzinfo=SHANGHAI)),
        ("2天前", datetime(2026, 5, 17, 18, 0, 0, tzinfo=SHANGHAI)),
        ("昨天 09:15", datetime(2026, 5, 18, 9, 15, 0, tzinfo=SHANGHAI)),
    ],
)
def test_relative_time(text, expected):
    assert parse_laoyaoba_published_time(text, reference_now=_ref()) == expected


def test_mm_dd_current_year():
    # 05-14 is before today (05-19) in 2026 → same year
    got = parse_laoyaoba_published_time("05-14 18:18", reference_now=_ref())
    assert got == datetime(2026, 5, 14, 18, 18, 0, tzinfo=SHANGHAI)


def test_mm_dd_cross_year():
    # 12-25 is after today (05-19) in 2026 → previous year 2025
    got = parse_laoyaoba_published_time("12-25 23:59", reference_now=_ref())
    assert got == datetime(2025, 12, 25, 23, 59, 0, tzinfo=SHANGHAI)


def test_yyyy_mm_dd():
    got = parse_laoyaoba_published_time("2019-09-26", reference_now=_ref())
    assert got == datetime(2019, 9, 26, 0, 0, 0, tzinfo=SHANGHAI)


def test_yyyy_mm_dd_hh_mm():
    got = parse_laoyaoba_published_time("2018-08-12 14:30", reference_now=_ref())
    assert got == datetime(2018, 8, 12, 14, 30, 0, tzinfo=SHANGHAI)


def test_unknown_returns_none():
    assert parse_laoyaoba_published_time("never", reference_now=_ref()) is None
    assert parse_laoyaoba_published_time("", reference_now=_ref()) is None
