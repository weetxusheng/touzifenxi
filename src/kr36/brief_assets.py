"""从 Markdown 简报生成 HTML/邮件预览等；供 pipeline 与 CLI 共用。"""

from __future__ import annotations

from pathlib import Path

from .email import render_kr36_brief_email


def save_brief_preview_assets(step4_markdown_path: Path, markdown_text: str) -> tuple[Path, Path, Path]:
    """写出与主 Markdown 同 stem 的 .html、*_email、*_doc、*.txt 。"""

    rendered = render_kr36_brief_email(markdown_text, step4_markdown_path)
    html_output = step4_markdown_path.with_suffix(".html")
    legacy_email_output = step4_markdown_path.with_name(f"{step4_markdown_path.stem}_email.html")
    doc_output = step4_markdown_path.with_name(f"{step4_markdown_path.stem}_doc.html")
    text_output = step4_markdown_path.with_name(f"{step4_markdown_path.stem}_email.txt")
    html_output.write_text(rendered.html, encoding="utf-8")
    legacy_email_output.write_text(rendered.html, encoding="utf-8")
    doc_output.write_text(rendered.html, encoding="utf-8")
    text_output.write_text(rendered.text, encoding="utf-8")
    return html_output, text_output, doc_output
