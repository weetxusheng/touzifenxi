"""爱集微（ijiwei）channel 抓取。

实测：
- 桌面站 https://www.laoyaoba.com/ 是 SSR；移动站是 SPA + /api（被 robots 禁）
- 文章 URL /n/<numeric-id>
- 列表上携带相对/绝对时间（"3小时前" / "05-14 18:18"）
- 详情页 selector：h1.media-title / span.published-time / div.media-source / div.media-article-content

依赖：仅 stdlib（urllib + html.parser）。
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from chip.channels.datetime_parse import SHANGHAI, parse_laoyaoba_published_time
from utils.tools.content_models import RawArticleDetail, RawArticleRef

if TYPE_CHECKING:
    from chip.channels.spec import ChipChannelSpec

LAOYAOBA_BASE = "https://www.laoyaoba.com"
LAOYAOBA_LISTING_URLS = (
    f"{LAOYAOBA_BASE}/xinyaowen",
    f"{LAOYAOBA_BASE}/jwfocus",
)

# IDs 实测为静态页（版权声明 / 关于我们 / 联系我们），抓取层硬过滤。
EXCLUDED_LAOYAOBA_IDS: frozenset[int] = frozenset({683317, 683318, 729927})

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_ARTICLE_HREF_RE = re.compile(r"^/n/(\d+)/?$")
# 列表 anchor 文本里时间可能裹在前后；用宽松匹配抽取
_TIME_HINTS = (
    re.compile(r"(\d+\s*(?:分钟|小时|天)前)"),
    re.compile(r"(昨天\s+\d{1,2}:\d{2})"),
    re.compile(r"(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})"),
    re.compile(r"(20\d{2}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)"),
)
_REQUEST_TIMEOUT = 20.0
_THROTTLE_SECONDS = 1.0
_MIN_TITLE_LEN = 6


class _LaoyaobaAnchorCollector(HTMLParser):
    """Collect <a href="/n/<id>"> with their inner text from a listing page."""

    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str]] = []  # (href, anchor_text)
        self._capture_href: str | None = None
        self._capture_parts: list[str] = []
        self._depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        if self._capture_href is not None:
            self._depth += 1
            return
        href = ""
        for k, v in attrs:
            if k == "href":
                href = (v or "").strip()
                break
        if not href or not _ARTICLE_HREF_RE.match(href):
            return
        self._capture_href = href
        self._capture_parts = []
        self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._capture_href is None:
            return
        self._depth -= 1
        if self._depth <= 0:
            text = " ".join("".join(self._capture_parts).split()).strip()
            if text:
                self.refs.append((self._capture_href, text))
            self._capture_href = None
            self._capture_parts = []
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._capture_href is not None:
            self._capture_parts.append(data)


def parse_listing_html(
    html: str,
    *,
    reference_now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract article candidates from a laoyaoba listing page."""

    reference = reference_now or datetime.now(SHANGHAI)
    collector = _LaoyaobaAnchorCollector()
    collector.feed(html)
    collector.close()

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for href, anchor_text in collector.refs:
        m = _ARTICLE_HREF_RE.match(href)
        if not m:
            continue
        article_id = m.group(1)
        if int(article_id) in EXCLUDED_LAOYAOBA_IDS:
            continue
        url = urljoin(LAOYAOBA_BASE + "/", href)
        if url in seen:
            continue
        if len(anchor_text) < _MIN_TITLE_LEN:
            continue

        title, time_text = _split_title_and_time(anchor_text)
        if len(title) < _MIN_TITLE_LEN:
            continue
        published_at = ""
        if time_text:
            parsed = parse_laoyaoba_published_time(time_text, reference_now=reference)
            if parsed is not None:
                published_at = parsed.isoformat()

        seen.add(url)
        items.append(
            {
                "url": url,
                "title": title,
                "article_id": article_id,
                "channel": "laoyaoba",
                "source_bucket": "laoyaoba",
                "published_at": published_at,
            }
        )
    return items


def _split_title_and_time(text: str) -> tuple[str, str]:
    """Pull a time substring out of the anchor text, return (title, time_text)."""

    for rx in _TIME_HINTS:
        m = rx.search(text)
        if m:
            time_text = m.group(1)
            title = (text[: m.start()] + text[m.end():]).strip()
            return title, time_text
    return text.strip(), ""


class _LaoyaobaArticleParser(HTMLParser):
    """Single-pass extraction of all relevant fields from a laoyaoba article page."""

    def __init__(self) -> None:
        super().__init__()
        # Output buckets
        self.title_parts: list[str] = []
        self.published_time_text: str = ""
        self.author_parts: list[str] = []
        self.body_parts: list[str] = []
        self.summary_meta: str = ""
        self.source_org: str = ""
        self.tags: list[str] = []

        # Capture flags
        self._in_title = False
        self._in_published_span = False  # collecting <span class="published-time">
        self._in_author_anchor = False  # collecting <a class="author-item">
        self._in_body = 0  # depth counter for media-article-content div

        # media-source state — we're inside a div whose class contains "media-source"
        self._in_media_source_depth = 0
        # While inside media-source, we collect spans by their class:
        self._span_class: str | None = None  # class on current span (top of stack)
        self._span_buf: list[list[str]] = []  # nested span text buffers
        self._span_class_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = ""
        for k, v in attrs:
            if k == "class":
                cls = v or ""
                break
        classes = set(cls.split())

        # <meta name="description" content="...">  → capture content
        if tag == "meta":
            name = ""
            content = ""
            for k, v in attrs:
                if k == "name":
                    name = (v or "").lower()
                elif k == "content":
                    content = v or ""
            if name == "description" and not self.summary_meta:
                self.summary_meta = content.strip()
            return

        if tag == "h1" and "media-title" in classes and not self._in_title:
            self._in_title = True
            return

        if tag == "div" and "media-article-content" in classes and self._in_body == 0:
            self._in_body = 1
            return
        if self._in_body > 0 and tag == "div":
            self._in_body += 1
            return

        if tag == "div" and "media-source" in classes and self._in_media_source_depth == 0:
            self._in_media_source_depth = 1
            return
        if self._in_media_source_depth > 0 and tag == "div":
            self._in_media_source_depth += 1
            return

        if tag == "span":
            self._span_buf.append([])
            self._span_class_stack.append(cls)
            if "published-time" in classes and not self._in_published_span and not self.published_time_text:
                self._in_published_span = True
            return

        if tag == "a" and "author-item" in classes and not self._in_author_anchor:
            self._in_author_anchor = True
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_title:
            self._in_title = False
            return

        if tag == "div":
            if self._in_body > 0:
                self._in_body -= 1
                return
            if self._in_media_source_depth > 0:
                self._in_media_source_depth -= 1
                return

        if tag == "span" and self._span_buf:
            buf = self._span_buf.pop()
            cls = self._span_class_stack.pop() if self._span_class_stack else ""
            text = " ".join("".join(buf).split()).strip()
            classes = set(cls.split())

            # Bubble text up to outer span so nested-span source pattern works
            if self._span_buf:
                self._span_buf[-1].append(text)

            if self._in_published_span and "published-time" in classes:
                self._in_published_span = False
                if not self.published_time_text:
                    self.published_time_text = text
            # Within media-source div, this span might be the source or a tag.
            if self._in_media_source_depth > 0:
                if "media-tag-item" in classes:
                    tag_text = text.strip("#").strip()
                    if tag_text and tag_text not in self.tags:
                        self.tags.append(tag_text)
                elif text.startswith("来源："):
                    tail = text[len("来源："):].strip()
                    if tail and not self.source_org:
                        self.source_org = tail
            return

        if tag == "a" and self._in_author_anchor:
            self._in_author_anchor = False
            return

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_body > 0:
            self.body_parts.append(data)
        if self._in_author_anchor:
            self.author_parts.append(data)
        if self._span_buf:
            self._span_buf[-1].append(data)


def parse_article_html(
    html: str,
    *,
    url: str,
    reference_now: datetime | None = None,
) -> dict[str, Any]:
    """Extract title / time / author / source / tags / body from a laoyaoba article page."""

    reference = reference_now or datetime.now(SHANGHAI)
    parser = _LaoyaobaArticleParser()
    parser.feed(html)
    parser.close()

    title = " ".join("".join(parser.title_parts).split()).strip()
    author = " ".join("".join(parser.author_parts).split()).strip()
    body = "\n".join(line.strip() for line in "".join(parser.body_parts).splitlines() if line.strip())

    published_at = ""
    if parser.published_time_text:
        parsed_dt = parse_laoyaoba_published_time(parser.published_time_text, reference_now=reference)
        if parsed_dt is not None:
            published_at = parsed_dt.isoformat()

    article_id_match = re.search(r"/n/(\d+)", url)
    article_id = article_id_match.group(1) if article_id_match else url

    return {
        "url": url,
        "article_id": article_id,
        "title": title,
        "published_at": published_at,
        "author": author,
        "source_org": parser.source_org,
        "tags": parser.tags,
        "summary": parser.summary_meta,
        "content_text": body,
    }


# --- HTTP fetching ---

def _default_http_get(url: str) -> str:
    # laoyaoba 在 UA 之外还需要常规浏览器的 Accept / Accept-Language，否则边缘只下发
    # ~1.8KB 占位壳页；带上这些头之后才会回 ~84KB 的完整 SSR 页。
    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Referer": f"{LAOYAOBA_BASE}/",
    }
    req = Request(url, headers=headers)
    with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def fetch_listing(
    spec: "ChipChannelSpec",
    report_date: date,
    *,
    http_get=None,
    detail_get=None,
    reference_now: datetime | None = None,
) -> list[RawArticleRef]:
    """Fetch laoyaoba listing pages, filter to report_date.

    - If a listing item already carries a published_at on report_date, no detail
      request is needed (saves ~50% requests).
    - For items without a listing time or whose time differs, request detail
      and re-check.
    - Discard items whose final time != report_date.
    """

    get_listing = http_get or _default_http_get
    get_detail = detail_get or _default_http_get
    reference = reference_now or datetime.now(SHANGHAI)
    report_iso = report_date.isoformat()

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for listing_url in spec.listing_urls:
        try:
            html = get_listing(listing_url)
        except (URLError, OSError, RuntimeError):
            continue
        time.sleep(_THROTTLE_SECONDS)
        for item in parse_listing_html(html, reference_now=reference):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            candidates.append(item)

    refs: list[RawArticleRef] = []
    for cand in candidates:
        published_at = cand.get("published_at") or ""
        needs_detail = not published_at.startswith(report_iso)
        detail_parsed: dict[str, Any] | None = None
        if needs_detail:
            try:
                detail_html = get_detail(cand["url"])
            except (URLError, OSError, RuntimeError):
                continue
            time.sleep(_THROTTLE_SECONDS)
            detail_parsed = parse_article_html(
                detail_html,
                url=cand["url"],
                reference_now=reference,
            )
            published_at = detail_parsed.get("published_at") or ""
            if not published_at.startswith(report_iso):
                continue

        summary = ""
        content_text = ""
        tags: list[str] = []
        source_org = ""
        if detail_parsed is not None:
            summary = detail_parsed.get("summary") or ""
            content_text = detail_parsed.get("content_text") or ""
            tags = detail_parsed.get("tags") or []
            source_org = detail_parsed.get("source_org") or ""

        refs.append(
            RawArticleRef(
                source_site="chip",
                article_id=cand["article_id"],
                title=cand["title"],
                url=cand["url"],
                published_at=published_at,
                channel="laoyaoba",
                source_bucket="laoyaoba",
                summary=summary,
                metadata={
                    "source_org": source_org,
                    "content_text": content_text,
                    "tags": tags,
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
    reference_now: datetime | None = None,
) -> RawArticleDetail:
    """Fetch and parse one laoyaoba article."""

    get_html = http_get or _default_http_get
    html = get_html(ref.url)
    parsed = parse_article_html(html, url=ref.url, reference_now=reference_now)

    return RawArticleDetail(
        source_site="chip",
        article_id=parsed["article_id"],
        title=parsed["title"] or ref.title,
        url=ref.url,
        published_at=parsed["published_at"] or ref.published_at,
        author=parsed["author"],
        channel="laoyaoba",
        source_bucket="laoyaoba",
        tags=parsed["tags"],
        summary=parsed["summary"] or ref.summary,
        content_text=parsed["content_text"],
        metadata={
            "source_org": parsed["source_org"],
            "listing_title": ref.title,
            "tags": parsed["tags"],
        },
    )
