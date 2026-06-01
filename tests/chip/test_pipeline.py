from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from chip.pipeline import (
    ChipDailyBriefRunResult,
    run_chip_daily_brief,
    send_latest_chip_brief_email,
    shanghai_yesterday,
)


def test_shanghai_yesterday_returns_date():
    d = shanghai_yesterday()
    assert isinstance(d, date)


def test_run_chip_daily_brief_failure_path_returns_result(tmp_path, monkeypatch):
    """If the underlying chip.cli step fails, run_chip_daily_brief surfaces it."""

    def boom(*args, **kwargs):
        raise RuntimeError("fixtures missing")

    monkeypatch.setattr("chip.cli.run_with_args", boom)

    result = run_chip_daily_brief(
        project_root=tmp_path,
        report_date=date(2026, 5, 19),
        recipients=["test@example.com"],
        send_mail=False,
    )
    assert isinstance(result, ChipDailyBriefRunResult)
    assert result.succeeded is False
    assert "fixtures missing" in result.failure_reason


def test_send_latest_chip_brief_email_missing_step6(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = send_latest_chip_brief_email(
        project_root=tmp_path,
        recipients=["test@example.com"],
        report_date=date(2026, 5, 19),
        require_modified_not_before=None,
        step6_md_path=None,
    )
    assert result.succeeded is False
    assert "step6" in result.error_detail.lower() or "未找到" in result.error_detail
