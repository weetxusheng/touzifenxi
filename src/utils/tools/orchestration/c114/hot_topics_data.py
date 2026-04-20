"""C114 第 0/1 步原始取数模块。"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from utils.tools.output.briefing import build_step1_csv_rows
from .source_adapter import C114SourceAdapter

ARTICLE_URL_RE = re.compile(r"https://www\.c114\.com\.cn/(?:[\w-]+/\d+|news/\d+)/a\d+\.html$")
ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.+-]{1,30}")
CHINESE_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
DATETIME_RE = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})\s+\d{1,2}:\d{1,2}")
SLASH_DATE_RE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")
CN_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
C114_NEWS_DATE_RE = re.compile(r"C114讯\s*(\d{1,2})月(\d{1,2})日(?:消息|讯)")
TITLE_SUFFIX_RE = re.compile(r"\s*[-—]\s*.*?C114通信网\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
PUBLISH_LABEL_DATE_RE = re.compile(
    r"(?:发布时间|发稿时间|更新日期|发布日期|时间|日期)\s*[：:]\s*"
    r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})"
)
ARTICLE_TIME_DATE_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*\btime\b[^"\']*["\'][^>]*>\s*'
    r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\s+\d{1,2}:\d{1,2}",
    re.I,
)
_DIV_OPEN_RE = re.compile(r"<div\b", re.I)
_DIV_CLOSE_RE = re.compile(r"</div\s*>", re.I)
_CLASS_ATTR_RE = re.compile(r'\bclass=["\']([^"\']*)["\']', re.I)
CSV_FIELDNAMES = [
    "统计日期",
    "栏目键",
    "栏目名称",
    "栏目链接",
    "栏目文章数",
    "栏目热点词",
    "文章标题",
    "发布时间",
    "关键词",
    "摘要",
    "文章链接",
]


@dataclass(frozen=True)
class ChannelSpec:
    """描述一个 C114 栏目的抓取配置。"""
    key: str
    name: str
    url: str
    path_prefixes: tuple[str, ...]
    # CSS class names of sections to scrape; empty = scrape full page.
    include_section_classes: tuple[str, ...] = ()
    # CSS class names that disqualify an otherwise-matching section div.
    exclude_section_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArticleCandidate:
    """表示栏目页上提取到的文章候选链接。"""
    url: str
    anchor_text: str
    anchor_date: date | None


@dataclass(frozen=True)
class ArticleMetadata:
    """表示单篇文章的规范化元数据。"""
    url: str
    title: str
    publish_date: date | None
    keywords: list[str]
    summary: str


@dataclass(frozen=True)
class ChannelDailyReport:
    """表示单个栏目在某一天的汇总结果。"""
    channel_key: str
    channel_name: str
    channel_url: str
    article_count: int
    hot_topics: list[tuple[str, int]]
    articles: list[ArticleMetadata]


CHANNELS: dict[str, ChannelSpec] = {
    "home": ChannelSpec(
        key="home",
        name="首页",
        # Use ?c=web to get the standard web layout with 专题推荐 and 热点新闻 sections.
        url="https://www.c114.com.cn/?c=web",
        path_prefixes=("/news/", "/ftth/", "/video/", "/cloud/", "/5g/", "/ai/", "/quantum/", "/satellite/", "/la/"),
        # 专题推荐 section (topic_list) + 热点新闻 section (page_content_center_list).
        # Excludes: operator tab panels (content_l_list_top_two) which surface old
        # pinned 《对话》 videos and vendor PR articles with stale publication dates.
        include_section_classes=("topic_list", "page_content_center_list"),
    ),
    "quantum": ChannelSpec(
        key="quantum",
        name="量子信息",
        url="https://www.c114.com.cn/quantum/",
        path_prefixes=("/quantum/",),
        # 最新报道 articles are inside choose-videos; 热门文章 are inside new_video.
        # 特别策划 shares the new_video class but also carries special_planning → excluded.
        include_section_classes=("choose-videos", "new_video"),
        exclude_section_classes=("special_planning",),
    ),
    "satellite": ChannelSpec(
        key="satellite",
        name="卫星互联网",
        url="https://www.c114.com.cn/satellite/",
        path_prefixes=("/satellite/",),
        include_section_classes=("choose-videos", "new_video"),
        exclude_section_classes=("special_planning",),
    ),
    "la": ChannelSpec(
        key="la",
        name="数智低空",
        url="https://www.c114.com.cn/la/",
        path_prefixes=("/la/",),
        include_section_classes=("choose-videos", "new_video"),
        exclude_section_classes=("special_planning",),
    ),
    "ai": ChannelSpec(
        key="ai",
        name="Cloud&AI",
        url="https://www.c114.com.cn/ai/",
        path_prefixes=("/ai/",),
        include_section_classes=("choose-videos", "new_video"),
        exclude_section_classes=("special_planning",),
    ),
}


def _find_closing_div(html: str, open_start: int) -> int:
    """Return the index just past the </div> that closes the div opened at open_start."""
    tag_end = html.find(">", open_start)
    if tag_end < 0:
        return len(html)
    pos = tag_end + 1
    depth = 1
    while pos < len(html) and depth > 0:
        next_open = html.lower().find("<div", pos)
        next_close = html.lower().find("</div", pos)
        if next_open < 0:
            next_open = len(html)
        if next_close < 0:
            next_close = len(html)
        if next_open < next_close:
            depth += 1
            tag_end2 = html.find(">", next_open)
            pos = (tag_end2 + 1) if tag_end2 >= 0 else len(html)
        else:
            depth -= 1
            close_end = next_close + len("</div>")
            pos = close_end
    return pos


def extract_section_html(
    html: str,
    include_classes: tuple[str, ...],
    exclude_classes: tuple[str, ...] = (),
) -> str:
    """Return only HTML from divs whose class list contains any of include_classes.

    Divs that also contain any of exclude_classes are skipped.  When
    include_classes is empty the full HTML is returned unchanged.
    """
    if not include_classes:
        return html
    blocks: list[str] = []
    seen_ranges: list[tuple[int, int]] = []
    pos = 0
    while pos < len(html):
        m = _DIV_OPEN_RE.search(html, pos)
        if not m:
            break
        tag_end = html.find(">", m.start())
        if tag_end < 0:
            break
        tag_html = html[m.start(): tag_end + 1]
        cm = _CLASS_ATTR_RE.search(tag_html)
        if cm:
            classes = cm.group(1).split()
            if any(inc in classes for inc in include_classes):
                if not exclude_classes or not any(exc in classes for exc in exclude_classes):
                    end = _find_closing_div(html, m.start())
                    # Skip if already covered by a previous (outer) block.
                    if not any(s <= m.start() and end <= e for s, e in seen_ranges):
                        blocks.append(html[m.start():end])
                        seen_ranges.append((m.start(), end))
                    pos = end
                    continue
        pos = tag_end + 1
    return "\n".join(blocks)


class AnchorParser(HTMLParser):
    """从栏目页面中提取锚点链接与锚文本。"""

    def __init__(self) -> None:
        """初始化锚点解析器的内部缓存。"""
        super().__init__()
        self.links: list[tuple[str | None, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._capture = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """在遇到 a 标签时开始记录链接与文本。"""
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href")
        self._buf = []
        self._capture = True

    def handle_data(self, data: str) -> None:
        """累积当前 a 标签中的可见文本。"""
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        """在 a 标签结束时写入一条解析结果。"""
        if tag.lower() != "a" or not self._capture:
            return
        self.links.append((self._href, normalize_whitespace("".join(self._buf))))
        self._href = None
        self._buf = []
        self._capture = False


def normalize_whitespace(value: str) -> str:
    """Collapse HTML whitespace and entities into readable plain text."""

    return " ".join(unescape(value).replace("\xa0", " ").split())


def strip_title_suffix(value: str) -> str:
    """去掉标题尾部常见的站点后缀。"""
    cleaned = normalize_whitespace(value)
    return TITLE_SUFFIX_RE.sub("", cleaned).strip()


def fetch_text(url: str, timeout: float = 20.0) -> str:
    """Fetch a C114 page and decode it with the site's legacy encoding."""

    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return payload.decode("gb18030", errors="ignore")


def filter_candidates_for_channel(html: str, channel: ChannelSpec) -> list[ArticleCandidate]:
    """Extract article candidates from a channel page for the configured URL prefixes."""

    scoped_html = extract_section_html(
        html,
        include_classes=channel.include_section_classes,
        exclude_classes=channel.exclude_section_classes,
    )
    parser = AnchorParser()
    parser.feed(scoped_html)
    seen: set[str] = set()
    candidates: list[ArticleCandidate] = []
    for href, anchor_text in parser.links:
        if not href or not anchor_text:
            continue
        full_url = urljoin(channel.url, href)
        if full_url in seen or not ARTICLE_URL_RE.match(full_url):
            continue
        if channel.path_prefixes and not any(
            urljoin(channel.url, prefix) in full_url for prefix in channel.path_prefixes
        ):
            continue
        seen.add(full_url)
        candidates.append(
            ArticleCandidate(
                url=full_url,
                anchor_text=anchor_text,
                anchor_date=parse_anchor_date(anchor_text),
            )
        )
    return candidates


def extract_article_metadata(url: str, html: str) -> ArticleMetadata:
    """Extract normalized title, summary, keywords, and publish date from one article page."""

    title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    description_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html, re.I)
    keywords_match = re.search(r'<meta[^>]+name="keywords"[^>]+content="([^"]*)"', html, re.I)
    title = strip_title_suffix(title_match.group(1)) if title_match else url
    summary = normalize_whitespace(description_match.group(1)) if description_match else ""
    keywords = split_keywords(keywords_match.group(1) if keywords_match else "")
    publish_date = parse_publish_date(html, summary)
    if not keywords:
        keywords = derive_keywords_from_text(f"{title} {summary}")
    return ArticleMetadata(
        url=url,
        title=title,
        publish_date=publish_date,
        keywords=keywords,
        summary=summary,
    )


def split_keywords(raw_value: str) -> list[str]:
    """Parse a loose C114 keyword string into a clean keyword list."""

    parts = re.split(r"[,，/|、\s]+", normalize_whitespace(raw_value))
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip()
        if not cleaned or cleaned in seen:
            continue
        deduped.append(cleaned)
        seen.add(cleaned)
    return deduped


def parse_publish_date(html: str, summary: str) -> date | None:
    """Infer article publish date, preferring story metadata over page refresh timestamps."""

    html_without_comments = strip_html_comments(html)
    labeled_match = PUBLISH_LABEL_DATE_RE.search(html_without_comments)
    if labeled_match:
        year, month, day = (int(value) for value in labeled_match.groups())
        return date(year, month, day)
    # C114 的部分频道详情页只在 article_top 的 time 块给出发布时间，
    # 这里限定 class=time，避免重新误读页面页脚注释或侧栏里的任意日期。
    article_time_match = ARTICLE_TIME_DATE_RE.search(html_without_comments)
    if article_time_match:
        year, month, day = (int(value) for value in article_time_match.groups())
        return date(year, month, day)
    summary_news_date = parse_summary_news_date(summary, fallback_year=datetime.now().year)
    if summary_news_date:
        return summary_news_date
    return None


def strip_html_comments(html: str) -> str:
    """删除 HTML 注释，避免把页面生成时间误判成文章发布时间。"""

    return HTML_COMMENT_RE.sub("", html)


def parse_cn_date(value: str, fallback_year: int) -> date | None:
    """Parse compact Chinese month/day strings using the supplied fallback year."""

    match = CN_DATE_RE.search(value)
    if not match:
        return None
    month, day = (int(part) for part in match.groups())
    return date(fallback_year, month, day)


def parse_summary_news_date(value: str, fallback_year: int) -> date | None:
    """Only parse summary date when it uses explicit C114 news lead format."""

    match = C114_NEWS_DATE_RE.search(value)
    if not match:
        return None
    month, day = (int(part) for part in match.groups())
    return date(fallback_year, month, day)


def _try_date(year: int, month: int, day: int) -> date | None:
    """Return a calendar date or None when components are invalid."""

    try:
        return date(year, month, day)
    except ValueError:
        return None


# List-page text may include `20/04/2026` (day/month/year); a naive M/D regex
# can match `20/04` and treat 20 as the month.
_TRIPLE_SLASH_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2})(?!\d)")
# Short M/D or D/M must not be a prefix of `/year` (handled by triple rule first).
_SHORT_SLASH_MD_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!/\d{4})(?!\d)")


def parse_anchor_date(value: str, fallback_year: int | None = None) -> date | None:
    """Extract a date from list-page anchor text such as `3/30 14:30`."""

    slash_match = SLASH_DATE_RE.search(value)
    if slash_match:
        year, month, day = (int(part) for part in slash_match.groups())
        parsed = _try_date(year, month, day)
        if parsed:
            return parsed
    triple = _TRIPLE_SLASH_DATE_RE.search(value)
    if triple:
        a, b, year = (int(part) for part in triple.groups())
        if a > 12:
            parsed = _try_date(year, b, a)
        elif b > 12:
            parsed = _try_date(year, a, b)
        else:
            parsed = _try_date(year, a, b)
            if parsed is None:
                parsed = _try_date(year, b, a)
        if parsed:
            return parsed
    fallback = fallback_year or datetime.now().year
    short_match = _SHORT_SLASH_MD_RE.search(value)
    if short_match:
        first, second = (int(part) for part in short_match.groups())
        month, day = first, second
        if month > 12 and 1 <= second <= 12:
            month, day = second, first
        parsed = _try_date(fallback, month, day)
        if parsed:
            return parsed
    return parse_cn_date(value, fallback_year=fallback)


def derive_keywords_from_text(text: str, limit: int = 5) -> list[str]:
    """Derive lightweight fallback keywords when detail-page metadata is incomplete."""

    counter: Counter[str] = Counter()
    for token in ASCII_WORD_RE.findall(text):
        if len(token) >= 3:
            counter[token] += 1
    for token in CHINESE_WORD_RE.findall(text):
        if token not in {"通信网", "日消息", "日报道", "日讯消息"}:
            counter[token] += 1
    return [token for token, _ in counter.most_common(limit)]


def build_keyword_summary(keyword_lists: Iterable[Iterable[str]], limit: int = 10) -> list[tuple[str, int]]:
    """统计多篇文章关键词频次，生成栏目热点词摘要。"""
    counter: Counter[str] = Counter()
    for keywords in keyword_lists:
        for keyword in keywords:
            cleaned = keyword.strip()
            if cleaned:
                counter[cleaned] += 1
    return counter.most_common(limit)


def collect_daily_report(
    report_date: date,
    channel_keys: Iterable[str] | None = None,
    timeout: float = 20.0,
    candidate_limit: int = 30,
) -> list[ChannelDailyReport]:
    """Fetch all requested channels and build one normalized daily report per channel."""

    selected_keys = list(channel_keys) if channel_keys else list(CHANNELS.keys())
    reports: list[ChannelDailyReport] = []
    for key in selected_keys:
        channel = CHANNELS[key]
        channel_html = fetch_text(channel.url, timeout=timeout)
        candidates = filter_candidates_for_channel(channel_html, channel)
        articles: list[ArticleMetadata] = []
        for candidate in candidates:
            if candidate.anchor_date and candidate.anchor_date != report_date:
                continue
            article_html = fetch_text(candidate.url, timeout=timeout)
            metadata = extract_article_metadata(candidate.url, article_html)
            if metadata.publish_date != report_date:
                continue
            articles.append(metadata)
            if len(articles) >= candidate_limit:
                break
        reports.append(
            ChannelDailyReport(
                channel_key=channel.key,
                channel_name=channel.name,
                channel_url=channel.url,
                article_count=len(articles),
                hot_topics=build_keyword_summary((article.keywords for article in articles)),
                articles=articles,
            )
        )
    return reports


def resolve_hot_topics_output_path(
    project_root: Path,
    raw_dir: Path,
    report_date: date,
    output_override: str | None,
) -> Path:
    """Resolve the output path for the JSON hot-topics export."""

    if output_override:
        return (project_root / output_override).resolve()
    return (raw_dir / f"c114_hot_topics_{report_date.strftime('%Y%m%d')}.json").resolve()


def save_daily_report(output_path: Path, report_date: date, reports: list[ChannelDailyReport]) -> None:
    """Persist the raw per-channel JSON export for one report date."""

    def serialize_article(article: ArticleMetadata) -> dict[str, object]:
        payload = asdict(article)
        payload["publish_date"] = article.publish_date.isoformat() if article.publish_date else None
        return payload

    payload = {
        "report_date": report_date.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "channels": [
            {
                "channel_key": report.channel_key,
                "channel_name": report.channel_name,
                "channel_url": report.channel_url,
                "article_count": report.article_count,
                "hot_topics": [{"keyword": keyword, "count": count} for keyword, count in report.hot_topics],
                "articles": [serialize_article(article) for article in report.articles],
            }
            for report in reports
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    save_daily_report_csv(output_path.parent / "c114_hot_topics.csv", report_date, reports)


def save_daily_report_csv(csv_path: Path, report_date: date, reports: list[ChannelDailyReport]) -> None:
    """Replace one day's rows in the canonical C114 CSV and keep all other dates intact."""

    new_rows = flatten_reports_to_csv_rows(report_date, reports)
    existing_rows = load_existing_csv_rows(csv_path)
    date_str = report_date.isoformat()
    # Replace ALL rows for report_date; stale rows from old runs (with looser date
    # filtering) would otherwise linger and pollute downstream analysis steps.
    merged_rows = new_rows + [
        row for row in existing_rows if row.get("统计日期", "") != date_str
    ]

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(merged_rows)
    csv_path.write_text(buffer.getvalue(), encoding="utf-8")


def flatten_reports_to_csv_rows(report_date: date, reports: list[ChannelDailyReport]) -> list[dict[str, str]]:
    """Flatten channel reports into stable CSV rows shared by downstream steps."""

    adapter = C114SourceAdapter()
    articles = adapter.standard_articles_from_reports(report_date, reports)
    return build_step1_csv_rows(report_date.isoformat(), articles)


def load_existing_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    """读取现有 CSV 内容，便于按日期增量覆盖写入。"""
    if not csv_path.exists():
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def render_daily_report(reports: list[ChannelDailyReport], report_date: date) -> str:
    """Render a concise terminal summary for one day's raw collection result."""

    lines = [f"C114 当日热点汇总 {report_date.isoformat()}"]
    for report in reports:
        lines.append(f"\n[{report.channel_name}] {report.article_count} 篇")
        if report.hot_topics:
            lines.append("热点词: " + ", ".join(f"{keyword}({count})" for keyword, count in report.hot_topics))
        else:
            lines.append("热点词: 无")
        for article in report.articles:
            keyword_text = ", ".join(article.keywords) if article.keywords else "无"
            lines.append(f"- {article.title}")
            lines.append(f"  日期: {article.publish_date.isoformat() if article.publish_date else '未知'}")
            lines.append(f"  关键词: {keyword_text}")
            lines.append(f"  链接: {article.url}")
    return "\n".join(lines)
