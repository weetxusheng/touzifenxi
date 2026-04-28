"""统一承载 `run` 主流程的调度入口。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from c114.weekend_brief_merge import merge_weekend_step6_markdown
from utils.tools.output.email import render_c114_brief_email
from utils.tools.settings import AppPaths
from utils.tools.orchestration import (
    run_c114_builtin_daily_pipeline,
    run_c114_controller_daily_pipeline,
    run_source_daily_pipeline,
)

_SHANGHAI = timezone(timedelta(hours=8))
_C114_HTML_MISSING_ALERT_TO = ["zx944532395@sina.com"]


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


def _find_latest_c114_brief_markdown(reports_dir: Path, report_date: date_cls) -> Path | None:
    """Find latest step6 markdown across search/range run layouts."""

    date_token = report_date.strftime("%Y%m%d")
    patterns = [
        f"*/c114_step_6_brief_{date_token}.md",
        f"*/*/c114_step_6_brief_{date_token}.md",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(reports_dir.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _send_c114_missing_html_alert(
    *,
    step6_path: Path,
    html_path: Path,
    report_date: date_cls | None,
) -> None:
    """Send alert mail when step6 markdown exists but paired HTML is missing."""

    from utils.tools.output.email import send_email

    report_label = report_date.isoformat() if report_date else "auto"
    subject = f"[C114告警] Step6 HTML 缺失（{report_label}）"
    body_text = (
        "C114 简报发送前校验失败：缺少与 Markdown 同目录的 HTML 模板。\n\n"
        f"report_date: {report_label}\n"
        f"step6_md: {step6_path}\n"
        f"expected_html: {html_path}\n"
    )
    send_email(
        recipient_emails=_C114_HTML_MISSING_ALERT_TO,
        subject=subject,
        body_text=body_text,
        body_html=None,
    )


def send_latest_c114_brief_email(
    *,
    project_root: Path,
    recipients: list[str],
    report_date: date_cls | None = None,
    require_modified_not_before: datetime | None = None,
    step6_md_path: Path | None = None,
) -> EmailSendResult:
    """找到指定日期的 step6 MD 文件并发邮件。report_date 为 None 时取昨天。

    若传入 ``step6_md_path``，则直接发送该 Markdown（须位于 ``c114_report`` 下且同目录有配套 HTML）。
    """
    from utils.tools.output.email import render_c114_brief_email, send_email

    reports_dir = project_root / "output" / "reports" / "c114_report"

    if step6_md_path is not None:
        step6_path = step6_md_path.resolve()
        try:
            step6_path.relative_to(reports_dir.resolve())
        except ValueError:
            return EmailSendResult(
                succeeded=False,
                error_detail=f"Step6 路径必须在 c114_report 目录下: {step6_path}",
            )
        if not step6_path.is_file() or step6_path.suffix.lower() != ".md":
            return EmailSendResult(
                succeeded=False,
                error_detail=f"Step6 Markdown 不存在或不是 .md: {step6_path}",
            )
    else:
        if report_date is None:
            report_date = shanghai_yesterday()

        step6_path = _find_latest_c114_brief_markdown(reports_dir, report_date)
        if step6_path is None:
            return EmailSendResult(
                succeeded=False,
                error_detail=f"未找到 {report_date.strftime('%Y%m%d')} 的 step6 文件",
            )
    html_path = step6_path.with_suffix(".html")
    if not html_path.is_file():
        alert_error = ""
        try:
            _send_c114_missing_html_alert(
                step6_path=step6_path,
                html_path=html_path,
                report_date=report_date,
            )
        except Exception as exc:  # pragma: no cover - alert must not hide primary error
            alert_error = f"；告警邮件发送失败: {exc}"
        return EmailSendResult(
            succeeded=False,
            step6_path=str(step6_path),
            error_detail=f"同目录缺少与 step6 配套的 HTML，请先生成后再发信: {html_path}{alert_error}",
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

    is_default_weekend_batch = False
    weekend_dates: tuple[date_cls, date_cls] | None = None

    def _resolve_dates(cli_args: argparse.Namespace) -> list[object]:
        nonlocal is_default_weekend_batch, weekend_dates
        # 默认获取 T-1；周一默认补齐周六+周日（仅在未显式传日期参数时生效）。
        if not getattr(cli_args, "date", None) and not getattr(cli_args, "start_date", None) and not getattr(
            cli_args, "end_date", None
        ):
            today = datetime.now(_SHANGHAI).date()
            if today.weekday() == 0:  # Monday
                saturday = today - timedelta(days=2)
                sunday = today - timedelta(days=1)
                is_default_weekend_batch = True
                weekend_dates = (saturday, sunday)
                return [saturday, sunday]
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

    if is_default_weekend_batch and weekend_dates is not None:
        _merge_weekend_brief(paths.reports_dir, weekend_dates[0], weekend_dates[1])


def _merge_weekend_brief(reports_dir: Path, saturday: date_cls, sunday: date_cls) -> None:
    """Merge Saturday/Sunday step6 into one Sunday-token brief under range root."""

    c114_root = reports_dir / "c114_report"
    prefix = f"c114_range_{saturday.strftime('%Y%m%d')}_{sunday.strftime('%Y%m%d')}_"
    range_dirs = sorted(
        [entry for entry in c114_root.iterdir() if entry.is_dir() and entry.name.startswith(prefix)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not range_dirs:
        return
    range_dir = range_dirs[0]

    sat_md = range_dir / saturday.isoformat() / f"c114_step_6_brief_{saturday.strftime('%Y%m%d')}.md"
    sun_md = range_dir / sunday.isoformat() / f"c114_step_6_brief_{sunday.strftime('%Y%m%d')}.md"
    if not sat_md.is_file() or not sun_md.is_file():
        return

    sat_text = sat_md.read_text(encoding="utf-8").strip()
    sun_text = sun_md.read_text(encoding="utf-8").strip()
    merged_md = merge_weekend_step6_markdown(sat_text, sun_text, saturday, sunday)

    sunday_token = sunday.strftime("%Y%m%d")
    merged_md_path = range_dir / f"c114_step_6_brief_{sunday_token}.md"
    merged_html_path = range_dir / f"c114_step_6_brief_{sunday_token}.html"
    merged_md_path.write_text(merged_md, encoding="utf-8")
    merged_html_path.write_text(render_c114_brief_email(merged_md).html, encoding="utf-8")
