"""Send latest generated 36Kr brief email from markdown artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path

from utils.tools.output.email import send_email

from .template import render_kr36_brief_email

_SHANGHAI = timezone(timedelta(hours=8))


def shanghai_yesterday() -> date_cls:
    return (datetime.now(_SHANGHAI) - timedelta(days=1)).date()


@dataclass
class EmailSendResult:
    succeeded: bool
    step6_path: str = ""
    error_detail: str = ""


def _kr36_report_roots(project_root: Path) -> list[Path]:
    roots = [
        project_root / "output" / "reports" / "kr36_report",
        project_root / "reports" / "kr36_report",
    ]
    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def _find_latest_kr36_brief_markdown(project_root: Path, report_date: date_cls) -> Path | None:
    date_token = report_date.strftime("%Y%m%d")
    patterns = [
        f"*/kr36_step4_brief_{date_token}.md",
        f"*/kr36_step_6_brief_{date_token}.md",
    ]
    candidates: list[Path] = []
    for root in _kr36_report_roots(project_root):
        if not root.exists():
            continue
        for pattern in patterns:
            candidates.extend(root.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def send_latest_kr36_brief_email(
    *,
    project_root: Path,
    recipients: list[str],
    report_date: date_cls | None = None,
    require_modified_not_before: datetime | None = None,
) -> EmailSendResult:
    """Find the latest kr36 brief markdown for a date and send rendered email."""
    if report_date is None:
        report_date = shanghai_yesterday()

    brief_md_path = _find_latest_kr36_brief_markdown(project_root, report_date)
    if brief_md_path is None:
        return EmailSendResult(
            succeeded=False,
            error_detail=f"未找到 {report_date.strftime('%Y%m%d')} 的 36Kr 简报 Markdown 文件",
        )

    html_path = brief_md_path.with_suffix(".html")
    if not html_path.is_file():
        return EmailSendResult(
            succeeded=False,
            step6_path=str(brief_md_path),
            error_detail=f"同目录缺少与简报配套的 HTML，请先生成后再发信: {html_path}",
        )

    if require_modified_not_before is not None:
        modified_at = datetime.fromtimestamp(brief_md_path.stat().st_mtime, tz=_SHANGHAI)
        if modified_at < require_modified_not_before:
            return EmailSendResult(
                succeeded=False,
                step6_path=str(brief_md_path),
                error_detail=(
                    f"简报文件修改时间 {modified_at.strftime('%H:%M')} "
                    f"早于门控时间 {require_modified_not_before.strftime('%H:%M')}，跳过发送。"
                ),
            )

    markdown_text = brief_md_path.read_text(encoding="utf-8")
    rendered = render_kr36_brief_email(
        markdown_text, brief_md_path, omit_source_links=True,
    )

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
            step6_path=str(brief_md_path),
            error_detail=str(exc),
        )

    return EmailSendResult(succeeded=True, step6_path=str(brief_md_path))

