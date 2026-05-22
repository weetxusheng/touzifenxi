"""通过 ``post_condition`` 返回的微信文章链接 GET 页面 HTML。"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from bs4 import BeautifulSoup

from gzh_pipeline.audit.trace import TraceRecorder, monotonic_ms
from gzh_pipeline.audit.redact import maybe_truncate_body

DEFAULT_WEIXIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

UrlFetchMode = Literal["full", "extract"]


def parse_url_fetch_mode(raw: str | None = None) -> UrlFetchMode:
    """
    ``GZH_DAJIALA_URL_FETCH_MODE``：

    - ``full``（默认）：响应体原样作为 HTML 落盘（整页，含 head/script）
    - ``extract``：仅抽取 ``#js_content`` 再 ``wrap_html``（旧行为）
    """
    text = (raw if raw is not None else os.getenv("GZH_DAJIALA_URL_FETCH_MODE", "full")).strip().lower()
    if text in ("full", "page", "raw", "response"):
        return "full"
    if text in ("extract", "fragment", "js_content"):
        return "extract"
    raise ValueError(f"invalid GZH_DAJIALA_URL_FETCH_MODE={text!r}; use full | extract")


def fetch_weixin_page_html(
    session: Any,
    url: str,
    *,
    timeout: int = 30,
    trace: TraceRecorder | None = None,
    http_label: str = "weixin_article_page",
) -> str:
    """GET 文章链接，返回完整响应 HTML（不做正文抽取）。"""
    t0 = time.monotonic()
    try:
        response = session.get(
            url,
            headers=DEFAULT_WEIXIN_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        elapsed = monotonic_ms(t0)
        if response.encoding:
            response.encoding = response.apparent_encoding or response.encoding
        text = response.text or ""
        preview = maybe_truncate_body(text[:8000] if len(text) > 8000 else text, 500_000)
        if trace:
            trace.add_http_request(
                http_label,
                "GET",
                url,
                request_headers=DEFAULT_WEIXIN_HEADERS,
                request_body=None,
                response_status=response.status_code,
                response_body={
                    "byte_length": len(text.encode("utf-8", errors="replace")),
                    "verify_page": is_weixin_verify_page(text),
                    "preview": preview,
                },
                elapsed_ms=elapsed,
                ok=response.ok,
                err=None if response.ok else response.reason,
            )
        response.raise_for_status()
        return text
    except Exception as exc:
        if trace:
            trace.add_http_request(
                http_label,
                "GET",
                url,
                request_headers=DEFAULT_WEIXIN_HEADERS,
                request_body=None,
                response_status=0,
                response_body=None,
                elapsed_ms=monotonic_ms(t0),
                ok=False,
                err=str(exc),
            )
        raise


def fetch_weixin_article_content_html(
    session: Any,
    url: str,
    *,
    timeout: int = 30,
    trace: TraceRecorder | None = None,
) -> str:
    """GET 后抽取 ``#js_content`` / ``.rich_media_content`` 片段（``extract`` 模式）。"""
    page = fetch_weixin_page_html(session, url, timeout=timeout, trace=trace, http_label="weixin_article_page")
    return extract_weixin_content_html(page)


def fetch_weixin_article_by_mode(
    session: Any,
    url: str,
    *,
    mode: UrlFetchMode | None = None,
    timeout: int = 30,
    trace: TraceRecorder | None = None,
) -> str:
    mode = mode or parse_url_fetch_mode()
    if mode == "full":
        return fetch_weixin_page_html(session, url, timeout=timeout, trace=trace, http_label="weixin_article_page")
    return fetch_weixin_article_content_html(session, url, timeout=timeout, trace=trace)


def is_weixin_verify_page(page_html: str) -> bool:
    if not (page_html or "").strip():
        return False
    lowered = page_html.lower()
    return "secitptpage" in lowered or "环境异常" in page_html or ("verify" in lowered and "captcha" in lowered)


def extract_weixin_content_html(page_html: str) -> str:
    if not (page_html or "").strip():
        return ""
    if is_weixin_verify_page(page_html):
        return ""
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    if node is not None:
        return str(node)
    title = soup.select_one("#activity-name") or soup.select_one(".rich_media_title")
    if title is not None:
        return str(title.parent) if title.parent else str(title)
    return ""
