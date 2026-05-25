"""Chip 每日简报 pipeline 入口（与 src/c114/pipeline.py 接口对齐，供 src/utils/cli.py 调用）。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_SHANGHAI = timezone(timedelta(hours=8))

_CHIP_BRIEF_FOOTER = (
    "本简报由 chip（SEMI 中国 + 爱集微）产业新闻流水线生成，仅供内部研究参考。"
)


def shanghai_yesterday() -> date_cls:
    """返回「上一个工作日」（严格早于今天的最近工作日，跳过周末）。

    chip 抓取中文半导体站点（SEMI + 爱集微），源站周末基本不更新。
    实现 = 从昨天起往前找第一个非周末日：
      周一 → 上周五   周二 → 周一   ...   周五 → 周四
      周六 → 周五     周日 → 上周五
    """
    d = (datetime.now(_SHANGHAI) - timedelta(days=1)).date()
    while d.weekday() >= 5:  # 5=周六, 6=周日
        d -= timedelta(days=1)
    return d


@dataclass
class ChipDailyBriefRunResult:
    succeeded: bool
    failure_step: str = ""
    failure_reason: str = ""
    run_dir: str = ""
    step6_path: str = ""
    log_path: str = ""


@dataclass
class ChipEmailSendResult:
    succeeded: bool
    step6_path: str = ""
    error_detail: str = ""


def run_chip_daily_brief(
    *,
    project_root: Path,
    report_date: date_cls,
    recipients: list[str],
    send_mail: bool,
) -> ChipDailyBriefRunResult:
    """Run chip pipeline for `report_date`, optionally email the brief."""

    import chip.cli as chip_cli
    from chip.settings import resolve_paths

    paths = resolve_paths()
    chip_args = argparse.Namespace(command="run", date=report_date.isoformat())
    try:
        chip_cli.run_with_args(chip_args, paths=paths)
    except Exception as exc:
        return ChipDailyBriefRunResult(
            succeeded=False,
            failure_step="chip.cli.run",
            failure_reason=str(exc),
        )

    step6_path = _locate_step6_markdown(paths.reports_dir, report_date)
    if step6_path is None:
        return ChipDailyBriefRunResult(
            succeeded=False,
            failure_step="locate_step6",
            failure_reason=f"no chip_step_6_brief_{report_date.strftime('%Y%m%d')}.md under {paths.reports_dir}",
        )

    if send_mail:
        send_result = send_latest_chip_brief_email(
            project_root=project_root,
            recipients=recipients,
            report_date=report_date,
            require_modified_not_before=None,
            step6_md_path=step6_path,
        )
        if not send_result.succeeded:
            return ChipDailyBriefRunResult(
                succeeded=False,
                failure_step="email",
                failure_reason=send_result.error_detail,
                run_dir=str(step6_path.parent),
                step6_path=str(step6_path),
            )

    return ChipDailyBriefRunResult(
        succeeded=True,
        run_dir=str(step6_path.parent),
        step6_path=str(step6_path),
    )


def send_latest_chip_brief_email(
    *,
    project_root: Path,
    recipients: list[str],
    report_date: Optional[date_cls],
    require_modified_not_before: Optional[datetime],
    step6_md_path: Optional[Path],
) -> ChipEmailSendResult:
    """Find latest chip step6 markdown and email it."""

    from chip.settings import resolve_paths
    from utils.tools.output.chip_email import render_chip_brief_email
    from utils.tools.output.email import send_email

    if step6_md_path is None:
        if report_date is None:
            return ChipEmailSendResult(succeeded=False, error_detail="report_date required when step6_md_path absent")
        paths = resolve_paths()
        step6_md_path = _locate_step6_markdown(paths.reports_dir, report_date)
    if step6_md_path is None or not step6_md_path.exists():
        return ChipEmailSendResult(succeeded=False, error_detail="未找到 chip step6 markdown 文件")

    if require_modified_not_before is not None:
        mtime = datetime.fromtimestamp(step6_md_path.stat().st_mtime, _SHANGHAI)
        if mtime < require_modified_not_before:
            return ChipEmailSendResult(
                succeeded=False,
                step6_path=str(step6_md_path),
                error_detail=f"step6 mtime {mtime} 早于门控 {require_modified_not_before}",
            )

    md_text = step6_md_path.read_text(encoding="utf-8")
    rendered = render_chip_brief_email(md_text, footer_disclaimer=_CHIP_BRIEF_FOOTER)
    try:
        send_email(
            recipient_emails=recipients,
            subject=rendered.subject,
            body_text=rendered.text,
            body_html=rendered.html,
        )
    except Exception as exc:
        return ChipEmailSendResult(
            succeeded=False,
            step6_path=str(step6_md_path),
            error_detail=f"send_email 失败: {exc}",
        )

    return ChipEmailSendResult(succeeded=True, step6_path=str(step6_md_path))


def _locate_step6_markdown(reports_dir: Path, report_date: date_cls) -> Optional[Path]:
    date_token = report_date.strftime("%Y%m%d")
    candidates: list[Path] = []
    candidates.extend(reports_dir.glob(f"*/chip_step_6_brief_{date_token}.md"))
    candidates.extend(reports_dir.glob(f"*/*/chip_step_6_brief_{date_token}.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
