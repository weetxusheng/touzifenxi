"""由步骤2正文 YAML（`kr36_step_4_content_*.yaml`）生成可读 HTML/Markdown 汇编。"""

from __future__ import annotations

import html as html_module
from pathlib import Path

from utils.tools.analysis.models import ContentAnalysisInput, ContentAnalysisItem
from utils.tools.analysis.yaml_io import load_content_analysis_inputs


def _md_fence_for(text: str) -> str:
    """用可变长度围栏包正文，避免内容中出现闭合 ``` 时破坏 Markdown。"""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}\n{text}\n{fence}"


def _item_excerpt(
    item: ContentAnalysisItem, excerpts: dict[str, str] | None
) -> str:
    if not excerpts:
        return ""
    key = (item.original_url or "").strip()
    return (excerpts.get(key) or "").strip()


def render_content_fetch_markdown(payload: ContentAnalysisInput) -> str:
    """Markdown：按主题/条目列出元数据，正文用围栏代码块。"""
    lines: list[str] = [
        f"# 36Kr 原文汇编（步骤2：正文抓取）",
        "",
        f"- **report_date**: {payload.report_date}",
        f"- **generated_at**: {payload.generated_at}",
        f"- **input_path**: `{payload.input_path}`",
        "",
    ]
    excerpts = payload.topic_fulltext_excerpts_by_url
    n = 0
    for sec in payload.categories:
        lines.append(f"## {sec.topic.strip() or '（未命名主题）'}")
        lines.append("")
        for item in sec.items:
            n += 1
            doc = item.original_content
            title = (item.original_title or doc.title or "未命名").strip()
            lines.append(f"### {n}. {title}")
            lines.append("")
            lines.append(f"- **栏目**: {item.channel or '—'}")
            lines.append(f"- **链接**: {item.original_url or doc.url or '—'}")
            if item.original_published_at:
                lines.append(f"- **发布时间**: {item.original_published_at}")
            lines.append(f"- **抓取状态**: {doc.status or '—'}")
            if doc.error:
                lines.append(f"- **错误**: {doc.error}")
            if doc.summary:
                lines.append("")
                lines.append("**摘要**:")
                lines.append("")
                lines.append(_md_fence_for(doc.summary))
            ex = _item_excerpt(item, excerpts)
            if ex:
                lines.append("")
                lines.append("**专题/全文摘录**（若有）:")
                lines.append("")
                lines.append(_md_fence_for(ex))
            body = (doc.text or "").strip()
            lines.append("")
            lines.append("**正文**:")
            lines.append("")
            if body:
                lines.append(_md_fence_for(body))
            else:
                lines.append("（无正文）")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_content_fetch_html(payload: ContentAnalysisInput) -> str:
    """单页 HTML：便于浏览器阅读、检索。"""
    excerpts = payload.topic_fulltext_excerpts_by_url
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8" />',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
        f"<title>{html_module.escape('36Kr 原文汇编（步骤2）')}</title>",
        "<style>",
        "body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:24px;"
        "background:#f5f5f5;color:#1a1a1a;line-height:1.6;}",
        ".wrap{max-width:920px;margin:0 auto;background:#fff;padding:28px 32px;"
        "border-radius:12px;box-shadow:0 1px 6px rgba(0,0,0,.06);}",
        "h1{font-size:1.45rem;margin:0 0 8px;}",
        ".meta{font-size:.88rem;color:#555;margin-bottom:24px;}",
        "h2{font-size:1.15rem;margin:28px 0 12px;border-bottom:1px solid #e0e0e0;"
        "padding-bottom:6px;}",
        "article{margin:20px 0;padding:16px 0;border-top:1px solid #eee;}",
        "article:first-of-type{border-top:none;}",
        "h3{font-size:1rem;margin:0 0 10px;}",
        ".kv{font-size:.88rem;color:#444;margin:6px 0;}",
        ".kv a{color:#0b5fff;}",
        ".label{font-weight:600;color:#333;}",
        ".body,.excerpt,.summary{margin-top:12px;padding:12px 14px;background:#fafafa;"
        "border-radius:8px;white-space:pre-wrap;word-break:break-word;font-size:.9rem;}",
        ".err{color:#b00020;}",
        "</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        "<h1>36Kr 原文汇编（步骤2：正文抓取）</h1>",
        '<p class="meta">',
        f"report_date: {html_module.escape(payload.report_date)} · "
        f"generated_at: {html_module.escape(payload.generated_at)}",
        "</p>",
    ]
    n = 0
    for sec in payload.categories:
        topic = sec.topic.strip() or "（未命名主题）"
        parts.append(f"<h2>{html_module.escape(topic)}</h2>")
        for item in sec.items:
            n += 1
            doc = item.original_content
            title = (item.original_title or doc.title or "未命名").strip()
            parts.append("<article>")
            parts.append(f"<h3>{n}. {html_module.escape(title)}</h3>")
            parts.append(
                f'<p class="kv"><span class="label">栏目</span>：'
                f"{html_module.escape(item.channel or '—')}</p>"
            )
            url = item.original_url or doc.url or ""
            if url:
                esc = html_module.escape(url, quote=True)
                parts.append(
                    f'<p class="kv"><span class="label">链接</span>：'
                    f'<a href="{esc}" target="_blank" rel="noopener">'
                    f"{html_module.escape(url)}</a></p>"
                )
            else:
                parts.append('<p class="kv"><span class="label">链接</span>：—</p>')
            if item.original_published_at:
                parts.append(
                    f'<p class="kv"><span class="label">发布时间</span>：'
                    f"{html_module.escape(item.original_published_at)}</p>"
                )
            parts.append(
                f'<p class="kv"><span class="label">抓取状态</span>：'
                f"{html_module.escape(doc.status or '—')}</p>"
            )
            if doc.error:
                parts.append(
                    f'<p class="kv err"><span class="label">错误</span>：'
                    f"{html_module.escape(doc.error)}</p>"
                )
            if doc.summary:
                parts.append('<p class="label">摘要</p>')
                parts.append(
                    f'<div class="summary">{html_module.escape(doc.summary)}</div>'
                )
            ex = _item_excerpt(item, excerpts)
            if ex:
                parts.append('<p class="label">专题/全文摘录</p>')
                parts.append(f'<div class="excerpt">{html_module.escape(ex)}</div>')
            parts.append('<p class="label">正文</p>')
            body = (doc.text or "").strip()
            if body:
                parts.append(f'<div class="body">{html_module.escape(body)}</div>')
            else:
                parts.append('<div class="body">（无正文）</div>')
            parts.append("</article>")
    parts.extend(["</div>", "</body>", "</html>"])
    return "\n".join(parts)


def write_content_fetch_reading_docs(
    content_yaml: Path,
) -> tuple[Path, Path]:
    """
    读取步骤2正文 YAML，在同目录写出：
    - ``{stem}_reading.html``
    - ``{stem}_reading.md``
    """
    path = content_yaml.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"正文 YAML 不存在: {path}")
    payload = load_content_analysis_inputs(path)
    base = path.with_suffix("")
    html_path = Path(str(base) + "_reading.html")
    md_path = Path(str(base) + "_reading.md")
    html_path.write_text(render_content_fetch_html(payload), encoding="utf-8")
    md_path.write_text(render_content_fetch_markdown(payload), encoding="utf-8")
    return html_path, md_path
