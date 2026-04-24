"""统一承载 `run` 主流程的调度入口。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from utils.tools.settings import AppPaths
from utils.tools.orchestration import (
    run_c114_builtin_daily_pipeline,
    run_c114_controller_daily_pipeline,
    run_source_daily_pipeline,
)

_SHANGHAI = timezone(timedelta(hours=8))


def shanghai_yesterday() -> date_cls:
    return (datetime.now(_SHANGHAI) - timedelta(days=1)).date()


def shanghai_local_today_at(hour: int, minute: int) -> datetime:
    now = datetime.now(_SHANGHAI)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_clock_hh_mm(s: str) -> tuple[int, int]:
    parts = str(s).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"期望 HH:MM 格式，得到: {s!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间超出范围: {s!r}")
    return h, m


@dataclass
class EmailSendResult:
    succeeded: bool
    step6_path: str = ""
    error_detail: str = ""


def send_latest_c114_brief_email(
    *,
    project_root: Path,
    recipients: list[str],
    report_date: date_cls | None = None,
    require_modified_not_before: datetime | None = None,
) -> EmailSendResult:
    """找到指定日期的 step6 MD 文件并发邮件。report_date 为 None 时取昨天。"""
    from utils.tools.output.email import render_c114_brief_email, send_email

    if report_date is None:
        report_date = shanghai_yesterday()

    date_token = report_date.strftime("%Y%m%d")
    reports_dir = project_root / "output" / "reports" / "c114_report"

    # 找所有匹配日期的 step6 文件，取 mtime 最新的
    candidates = sorted(
        reports_dir.glob(f"*/c114_step_6_brief_{date_token}.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return EmailSendResult(
            succeeded=False,
            error_detail=f"未找到 {date_token} 的 step6 文件",
        )

    step6_path = candidates[0]
    html_path = step6_path.with_suffix(".html")
    if not html_path.is_file():
        return EmailSendResult(
            succeeded=False,
            step6_path=str(step6_path),
            error_detail=f"同目录缺少与 step6 配套的 HTML，请先生成后再发信: {html_path}",
        )

    if require_modified_not_before is not None:
        mtime = datetime.fromtimestamp(step6_path.stat().st_mtime, tz=_SHANGHAI)
        if mtime < require_modified_not_before:
            return EmailSendResult(
                succeeded=False,
                step6_path=str(step6_path),
                error_detail=(
                    f"Step6 文件修改时间 {mtime.strftime('%H:%M')} "
                    f"早于门控时间 {require_modified_not_before.strftime('%H:%M')}，跳过"
                ),
            )

    md_text = step6_path.read_text(encoding="utf-8")
    rendered = render_c114_brief_email(md_text)

    try:
        send_email(
            recipient_emails=recipients,
            subject=rendered.subject,
            body_text=rendered.text,
            body_html=rendered.html,
        )
    except Exception as exc:
        return EmailSendResult(
            succeeded=False,
            step6_path=str(step6_path),
            error_detail=str(exc),
        )

    return EmailSendResult(succeeded=True, step6_path=str(step6_path))


def run_daily_pipeline(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 `run --source c114` 的完整日报流程。"""

    def _resolve_dates(cli_args: argparse.Namespace) -> list[object]:
        # 默认获取t-1
        return facade.resolve_c114_date_range(cli_args, default_to_today=True, default_days_ago=1)

    def _run_controller_for_day(target_date: object, day_dir: object, runtime_config: object) -> None:
        run_c114_controller_daily_pipeline(
            args=args,
            paths=paths,
            facade=facade,
            runtime_config=runtime_config,
            target_date=target_date,
            day_dir=day_dir,
        )

    def _run_builtin_for_day(target_date: object, day_dir: object, runtime_config: object, llm_client: object) -> None:
        run_c114_builtin_daily_pipeline(
            args=args,
            paths=paths,
            facade=facade,
            runtime_config=runtime_config,
            llm_client=llm_client,
            target_date=target_date,
            day_dir=day_dir,
        )

    run_source_daily_pipeline(
        args=args,
        paths=paths,
        facade=facade,
        resolve_dates=_resolve_dates,
        run_builtin_for_day=_run_builtin_for_day,
        run_controller_for_day=_run_controller_for_day,
    )
