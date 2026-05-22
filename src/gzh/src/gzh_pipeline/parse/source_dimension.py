"""来源维度：从导出 HTML 解析原文地址，生成可点击链接（不经大模型）。"""

from __future__ import annotations

from gzh_pipeline.parse.extract import SourceArticle
from gzh_pipeline.util.text import escape_html


def build_source_dimension_html(articles: list[SourceArticle]) -> str:
    """
    将各篇 ``SourceArticle.source_url`` 渲染为 HTML 列表，链接可跳转原文。

    同一 URL 只保留一条；无链接时给出说明。
    """
    seen: set[str] = set()
    items: list[str] = []
    for ar in articles:
        url = (ar.source_url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = escape_html((ar.title or ar.stem or "未命名").strip())
        url_esc = escape_html(url)
        items.append(
            "<li>"
            f'<span class="source-article-title">{title}</span>：'
            f'<a class="source-original-link" href="{url_esc}" target="_blank" '
            f'rel="noopener noreferrer">{url_esc}</a>'
            "</li>"
        )

    if not items:
        return (
            '<p class="dim-empty">（未能从导出 HTML 解析到原文链接；'
            "请确认抓取结果中含「原文链接」段落。）</p>"
        )
    if len(items) == 1:
        inner = items[0].removeprefix("<li>").removesuffix("</li>")
        return f'<p class="source-links">{inner}</p>'
    return '<ul class="source-links-list">\n' + "\n".join(items) + "\n</ul>"


def attach_source_dimension(dimensions: dict[str, str], articles: list[SourceArticle]) -> dict[str, str]:
    out = dict(dimensions)
    out["source"] = build_source_dimension_html(articles)
    return out


def source_dimension_coverage(articles: list[SourceArticle]) -> str:
    if any((a.source_url or "").strip() for a in articles):
        return "extracted"
    return "missing"
