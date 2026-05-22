"""业务日 yyyyMMdd 目录约定。"""

import pytest

from gzh_pipeline.util.text import biz_date_for_path, biz_date_iso_for_display, parse_biz_date


def test_biz_date_for_path_compact():
    assert biz_date_for_path("20260518") == "20260518"


def test_biz_date_for_path_iso_normalized():
    assert biz_date_for_path("2026-05-18") == "20260518"


def test_biz_date_iso_for_display():
    assert biz_date_iso_for_display("20260518") == "2026-05-18"


def test_parse_biz_date_invalid():
    with pytest.raises(ValueError):
        parse_biz_date("not-a-date")
