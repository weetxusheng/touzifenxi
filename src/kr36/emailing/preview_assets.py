"""Build HTML/TXT/DOC preview assets from step6 markdown."""

from __future__ import annotations

from pathlib import Path

from .template import render_kr36_brief_email


def save_brief_preview_assets(step6_markdown_path: Path, markdown_text: str) -> tuple[Path, Path, Path]:
    """Write HTML/TXT/DOC preview files, return (html, txt, doc_html)."""

    rendered = render_kr36_brief_email(
        markdown_text, step6_markdown_path, omit_source_links=True,
    )
    html_output = step6_markdown_path.with_suffix(".html")
    text_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.txt")
    doc_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_doc.html")
    html_output.write_text(rendered.html, encoding="utf-8")
    text_output.write_text(rendered.text, encoding="utf-8")
    doc_output.write_text(rendered.html, encoding="utf-8")
    return html_output, text_output, doc_output

