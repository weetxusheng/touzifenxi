import re

from gzh_pipeline.util.text import escape_html

_SOURCE_LINK_RE = re.compile(
    r'<p>\s*原文链接：\s*<a[^>]+href="([^"]+)"',
    re.IGNORECASE,
)


def source_link_paragraph(title: str, url: str) -> str:
    """与 ``wrap_html`` / 解析 ``extract_from_export_html`` 一致的原文链接标记。"""
    return (
        f"  <h1>{escape_html(title)}</h1>\n"
        f'  <p>原文链接：<a href="{escape_html(url)}">{escape_html(url)}</a></p>\n'
    )


def inject_source_url_into_page(page_html: str, title: str, url: str) -> str:
    """
    在整页响应 HTML 中写入标题与原文链接（供 url 直连 ``full`` 模式落盘）。

    若已有本流水线写入的链接段落则不再重复插入。
    """
    if not (page_html or "").strip():
        return wrap_html(title, url, "")
    if _SOURCE_LINK_RE.search(page_html):
        return page_html
    banner = (
        '<div class="gzh-pipeline-source-banner">\n'
        + source_link_paragraph(title, url)
        + "</div>\n"
    )
    match = re.search(r"<body[^>]*>", page_html, flags=re.IGNORECASE)
    if match:
        pos = match.end()
        return page_html[:pos] + "\n" + banner + page_html[pos:]
    return banner + page_html


def wrap_html(title: str, url: str, body: str) -> str:
    if "<html" in body.lower():
        return body
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        f"  <title>{escape_html(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{source_link_paragraph(title, url)}"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )
