from datetime import date

from gzh_pipeline.dajiala.fetch_mode import (
    canonical_fetch_mode,
    fetch_mode_from_env,
    resolve_export_biz_date,
    resolve_fetch_target_date,
)


def test_canonical_fetch_mode_aliases():
    assert canonical_fetch_mode("yesterday") == "yesterday"
    assert canonical_fetch_mode("prev_day") == "yesterday"
    assert canonical_fetch_mode("lastday") == "latest"


def test_fetch_mode_from_env(monkeypatch):
    monkeypatch.delenv("GZH_DAJIALA_FETCH_MODE", raising=False)
    assert fetch_mode_from_env() == "today"
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MODE", "yesterday")
    assert fetch_mode_from_env() == "yesterday"


def test_resolve_export_biz_date_yesterday():
    ref = date(2026, 5, 22)
    assert resolve_export_biz_date("yesterday", reference=ref) == "20260521"
    assert resolve_export_biz_date("today", reference=ref) == "20260522"


def test_resolve_fetch_target_date_yesterday():
    export_mode, iso = resolve_fetch_target_date("yesterday", "2026-05-21")
    assert export_mode == "yesterday"
    assert iso == "2026-05-20"
