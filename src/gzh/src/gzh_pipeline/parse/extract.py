"""从本仓库 ``wrap_html()`` 及同类导出页抽取标题、正文与纯文本。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from gzh_pipeline.util.text import escape_html


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class SourceArticle:
    path: Path
    stem: str
    title: str
    source_url: str
    body_html: str
    sha256_hex: str
    plain_text: str = ""


def extract_from_export_html(raw: str, filepath: Path) -> SourceArticle:
    title_m = re.search(r"<title>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    h1_m = re.search(r"<h1>(.*?)</h1>", raw, re.IGNORECASE | re.DOTALL)
    title = (title_m.group(1).strip() if title_m else "") or (h1_m.group(1).strip() if h1_m else filepath.stem)
    title = re.sub(r"\s+", " ", _unescape_minimal(title))

    link_m = re.search(r'原文链接：<a[^>]+href="([^"]+)"', raw)
    source_url = link_m.group(1).strip() if link_m else ""

    body_html = _extract_body_fragment(raw)
    plain = _html_to_plain(body_html)
    st = filepath.stem
    h = sha256_bytes(raw.encode("utf-8"))
    return SourceArticle(
        path=filepath,
        stem=st,
        title=title,
        source_url=source_url,
        body_html=body_html,
        sha256_hex=h,
        plain_text=plain,
    )


def _unescape_minimal(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')


def _extract_body_fragment(raw: str) -> str:
    body_m = re.search(r"<body[^>]*>(.*)</body>", raw, re.IGNORECASE | re.DOTALL)
    if not body_m:
        return raw
    inner = body_m.group(1).strip()
    inner = re.sub(r"^\s*<h1>.*?</h1>\s*", "", inner, count=1, flags=re.IGNORECASE | re.DOTALL)
    inner = re.sub(
        r"<p>\s*原文链接：\s*<a[^>]+>.*?</a>\s*</p>\s*",
        "",
        inner,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return inner.strip() or "<p><i>（未能从源文件解析正文）</i></p>"


def _html_to_plain(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    txt = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", txt)


def _escape(s: str) -> str:
    return escape_html(s)


# 供测试与聚合引用
__all__ = ["SourceArticle", "extract_from_export_html", "sha256_bytes", "_escape"]
