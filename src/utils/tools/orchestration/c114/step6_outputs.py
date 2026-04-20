"""Step 6 output helpers: markdown, html, and execution log."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from utils.tools.output.email import render_c114_brief_email


def write_step6_outputs(
    *,
    brief_output: Path,
    markdown_text: str,
    run_dir: Path,
    target_date: object,
    context: str,
) -> tuple[Path, Path]:
    """Write step 6 markdown/html and a per-run execution log."""

    brief_output.parent.mkdir(parents=True, exist_ok=True)
    brief_output.write_text(markdown_text, encoding="utf-8")

    html_output = brief_output.with_suffix(".html")
    html_output.write_text(render_c114_brief_email(markdown_text).html, encoding="utf-8")

    date_token = _date_token(target_date)
    log_path = (run_dir / f"c114_step_6_execution_log_{date_token}.md").resolve()
    log_path.write_text(
        "\n".join(
            [
                f"# C114 Step 6 执行日志（{date_token}）",
                "",
                f"- 执行时间: {datetime.now().isoformat(timespec='seconds')}",
                f"- 执行上下文: {context}",
                f"- Step 6 Markdown: {brief_output}",
                f"- Step 6 HTML: {html_output}",
                f"- 运行目录: {run_dir}",
            ]
        ),
        encoding="utf-8",
    )
    return html_output, log_path


def _date_token(target_date: object) -> str:
    strftime = getattr(target_date, "strftime", None)
    if callable(strftime):
        return strftime("%Y%m%d")
    return str(target_date)

