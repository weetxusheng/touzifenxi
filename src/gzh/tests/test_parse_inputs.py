from datetime import date as real_date
from pathlib import Path

from gzh_pipeline.parse.inputs import list_export_accounts, resolve_parse_accounts, resolve_parse_biz_date


def test_resolve_parse_biz_date_explicit(tmp_path):
    assert resolve_parse_biz_date(tmp_path, "2026-05-20") == "20260520"


def test_resolve_parse_biz_date_today_mode(tmp_path, monkeypatch):
    class _FixedToday(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 5, 22)

    monkeypatch.setattr("gzh_pipeline.dajiala.fetch_mode.date", _FixedToday)
    (tmp_path / "20260521" / "A").mkdir(parents=True)
    (tmp_path / "20260521" / "A" / "1.html").write_text("<html></html>", encoding="utf-8")
    assert resolve_parse_biz_date(tmp_path, None, fetch_mode="today") == "20260522"


def test_resolve_parse_biz_date_yesterday_from_env(tmp_path, monkeypatch):
    class _FixedToday(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 5, 22)

    monkeypatch.setattr("gzh_pipeline.dajiala.fetch_mode.date", _FixedToday)
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MODE", "yesterday")
    assert resolve_parse_biz_date(tmp_path, None) == "20260521"


def test_resolve_parse_biz_date_today_from_env(tmp_path, monkeypatch):
    class _FixedToday(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 5, 22)

    monkeypatch.setattr("gzh_pipeline.dajiala.fetch_mode.date", _FixedToday)
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MODE", "today")
    (tmp_path / "20260522").mkdir()
    assert resolve_parse_biz_date(tmp_path, None) == "20260522"


def test_list_export_accounts_skips_empty_dirs(tmp_path):
    day = tmp_path / "20260520"
    (day / "证券时报").mkdir(parents=True)
    (day / "证券时报" / "a.html").write_text("<html></html>", encoding="utf-8")
    (day / "_empty").mkdir()
    assert list_export_accounts(tmp_path, "20260520") == ["证券时报"]


def test_resolve_parse_accounts_all(tmp_path):
    day = tmp_path / "20260520"
    (day / "A").mkdir(parents=True)
    (day / "B").mkdir(parents=True)
    (day / "A" / "1.html").write_text("<html></html>", encoding="utf-8")
    (day / "B" / "2.html").write_text("<html></html>", encoding="utf-8")
    assert resolve_parse_accounts(tmp_path, "20260520", None) == ["A", "B"]
    assert resolve_parse_accounts(tmp_path, "20260520", "A") == ["A"]
