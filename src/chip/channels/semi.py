"""SEMI 中国 channel 抓取。

实测结构：
- 首页/栏目页是服务端渲染，<a href="https://www.semi.org.cn/site/semi/article/<32hex>.html">
- 列表上没有日期，需要进详情页才能拿到 publish_date

依赖：仅 stdlib（urllib + html.parser）。
"""

from __future__ import annotations

import re
import time
from datetime import date as _date
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from utils.tools.content_models import RawArticleDetail, RawArticleRef

if TYPE_CHECKING:
    from datetime import date

    from chip.channels.spec import ChipChannelSpec

SEMI_BASE = "https://www.semi.org.cn"
SEMI_LISTING_URLS = (
    f"{SEMI_BASE}/site/semi/",
    f"{SEMI_BASE}/site/semi/column/26595298402893836.html",
)
SEMI_ARTICLE_URL_RE = re.compile(r"^https?://www\.semi\.org\.cn/site/semi/article/[a-f0-9]{32}\.html$")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REQUEST_TIMEOUT = 20.0
_THROTTLE_SECONDS = 1.0


class _SemiAnchorCollector(HTMLParser):
    """Collect <a href="...semi article..."> with their inner text."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.refs: list[tuple[str, str]] = []  # (url, anchor_text)
        self._capture_url: str | None = None
        self._capture_parts: list[str] = []
        self._depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        if self._capture_url is not None:
            # Nested <a> (unusual): just bump depth so the right end-tag closes it
            self._depth += 1
            return
        href = ""
        for k, v in attrs:
            if k == "href":
                href = (v or "").strip()
                break
        if not href or "${" in href:
            return
        url = urljoin(self.base_url, href)
        if SEMI_ARTICLE_URL_RE.match(url):
            self._capture_url = url
            self._capture_parts = []
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._capture_url is None:
            return
        self._depth -= 1
        if self._depth <= 0:
            text = " ".join("".join(self._capture_parts).split()).strip()
            if text:
                self.refs.append((self._capture_url, text))
            self._capture_url = None
            self._capture_parts = []
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._capture_url is not None:
            self._capture_parts.append(data)


def parse_listing_html(html: str, *, base_url: str) -> list[dict[str, Any]]:
    """Extract article refs from a SEMI listing HTML page.

    Returns plain dicts (not RawArticleRef) so the function is purely about
    HTML structure; the channel-level `fetch_listing` will adapt to
    RawArticleRef and request details.
    """

    parser = _SemiAnchorCollector(base_url)
    parser.feed(html)
    parser.close()

    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    for url, text in parser.refs:
        if url in seen:
            continue
        seen.add(url)
        refs.append(
            {
                "url": url,
                "title": text,
                "channel": "semi",
                "source_bucket": "semi",
                "article_id": _extract_article_id(url),
            }
        )
    return refs


def _extract_article_id(url: str) -> str:
    match = re.search(r"/article/([a-f0-9]{32})\.html", url)
    return match.group(1) if match else url


_DATE_SPAN_RE = re.compile(r"^(20\d{2})-(\d{1,2})-(\d{1,2})$")


class _SemiArticleParser(HTMLParser):
    """Walk a SEMI article page once and collect every relevant field."""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.summary_parts: list[str] = []
        self.body_parts: list[str] = []
        self.date_text_candidates: list[str] = []
        self.source_org: str = ""

        self._in_title = False
        self._in_summary = False
        self._in_body = 0  # depth counter for nested <div>s inside body

        # Span-level state
        self._span_buffers: list[list[str]] = []  # stack of buffers, one per open <span>
        self._in_source_outer = False  # True while inside a <span> that started with "来源："

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = ""
        for k, v in attrs:
            if k == "class":
                cls = v or ""
                break
        classes = set(cls.split())

        if tag == "h2" and not self._in_title and "col-lg-8" in classes:
            self._in_title = True
            return
        if tag == "div" and not self._in_summary and {"ct-p", "ct-bzjy"}.issubset(classes):
            self._in_summary = True
            return
        if tag == "div" and "single-post-content" in classes and self._in_body == 0:
            self._in_body = 1
            return

        if self._in_body > 0 and tag == "div":
            self._in_body += 1
            return

        if tag == "span":
            self._span_buffers.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._in_title:
            self._in_title = False
            return
        if tag == "div" and self._in_summary:
            self._in_summary = False
            return
        if tag == "div" and self._in_body > 0:
            self._in_body -= 1
            return
        if tag == "span" and self._span_buffers:
            raw = "".join(self._span_buffers.pop())
            text = raw.strip()
            # Bubble this span's text up into any enclosing span so the outer
            # buffer sees the merged content (e.g. <span>来源：<span>综合报道</span></span>
            # — the outer span needs to observe "来源：综合报道" to identify itself).
            if self._span_buffers:
                self._span_buffers[-1].append(raw)
            # Two roles this span might play:
            # (a) date span — bare YYYY-MM-DD
            if _DATE_SPAN_RE.match(text):
                self.date_text_candidates.append(text)
            # (b) source span — starts with "来源：" possibly with nested span value already.
            #     The nested <span>综合报道</span> contributed its data via the bubble above,
            #     so `tail` contains the source organization.
            if text.startswith("来源："):
                tail = text[len("来源："):].strip()
                if tail and not self.source_org:
                    self.source_org = tail

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_summary:
            self.summary_parts.append(data)
        if self._in_body > 0:
            self.body_parts.append(data)
        # Always also push into top-of-stack span buffer if any
        if self._span_buffers:
            self._span_buffers[-1].append(data)


def parse_article_html(html: str, *, url: str) -> dict[str, Any]:
    """Extract title / date / summary / source / body from a SEMI article page."""

    parser = _SemiArticleParser()
    parser.feed(html)
    parser.close()

    title = " ".join("".join(parser.title_parts).split()).strip()
    summary = " ".join("".join(parser.summary_parts).split()).strip()
    if not summary:
        # Fallback to <meta name="description" content="..."> if no body summary div
        meta_match = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        if meta_match:
            summary = meta_match.group(1).strip()

    content_text = "\n".join(
        line.strip() for line in "".join(parser.body_parts).splitlines() if line.strip()
    )

    published_date: _date | None = None
    for candidate in parser.date_text_candidates:
        m = _DATE_SPAN_RE.match(candidate)
        if m:
            try:
                published_date = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
            except ValueError:
                continue

    return {
        "url": url,
        "title": title,
        "summary": summary,
        "content_text": content_text,
        "published_date": published_date,
        "source_org": parser.source_org,
        "article_id": _extract_article_id(url),
    }


# --- HTTP fetching ---


def _default_http_get(url: str) -> str:
    req = Request(url, headers={"User-Agent": _DEFAULT_UA})
    with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def fetch_listing(
    spec: "ChipChannelSpec",
    report_date: "date",
    *,
    http_get=None,
    detail_get=None,
) -> list[RawArticleRef]:
    """Fetch SEMI listing pages and filter to articles published on report_date.

    Because SEMI listing pages do not carry publish dates, we must request each
    article detail to learn its date. Articles whose date != report_date are
    dropped here.
    """

    get_listing = http_get or _default_http_get
    get_detail = detail_get or _default_http_get

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for listing_url in spec.listing_urls:
        try:
            html = get_listing(listing_url)
        except (URLError, OSError, RuntimeError):
            continue
        time.sleep(_THROTTLE_SECONDS)
        for item in parse_listing_html(html, base_url=listing_url):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            candidates.append(item)

    refs: list[RawArticleRef] = []
    for cand in candidates:
        try:
            article_html = get_detail(cand["url"])
        except (URLError, OSError, RuntimeError):
            continue
        time.sleep(_THROTTLE_SECONDS)
        parsed = parse_article_html(article_html, url=cand["url"])
        if parsed["published_date"] != report_date:
            continue
        refs.append(
            RawArticleRef(
                source_site="chip",
                article_id=parsed["article_id"],
                title=parsed["title"] or cand["title"],
                url=cand["url"],
                published_at=parsed["published_date"].isoformat(),
                channel="semi",
                source_bucket="semi",
                summary=parsed["summary"],
                metadata={
                    "source_org": parsed["source_org"],
                    "content_text": parsed["content_text"],
                    "listing_title": cand["title"],
                },
            )
        )
    return refs


def fetch_article(
    spec: "ChipChannelSpec",
    ref: RawArticleRef,
    *,
    http_get=None,
) -> RawArticleDetail:
    """Fetch and parse one SEMI article."""

    get_html = http_get or _default_http_get
    html = get_html(ref.url)
    parsed = parse_article_html(html, url=ref.url)

    return RawArticleDetail(
        source_site="chip",
        article_id=parsed["article_id"],
        title=parsed["title"] or ref.title,
        url=ref.url,
        published_at=parsed["published_date"].isoformat() if parsed["published_date"] else ref.published_at,
        author=parsed["source_org"],
        channel="semi",
        source_bucket="semi",
        tags=[],
        summary=parsed["summary"] or ref.summary,
        content_text=parsed["content_text"],
        metadata={
            "source_org": parsed["source_org"],
            "listing_title": ref.title,
        },
    )
