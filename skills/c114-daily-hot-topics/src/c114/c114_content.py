"""C114 第 4 步正文抓取模块。"""

from __future__ import annotations

import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .c114_intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    step_3_results_name,
    step_4_content_name,
)
from .c114_search import (
    SearchResult,
    escape_yaml_scalar,
    extract_domain,
    normalize_url,
    parse_yaml_list_item,
    parse_yaml_value,
    unquote_yaml_scalar,
)
from .settings import AppPaths, load_c114_runtime_config


@dataclass(frozen=True)
class SearchContentArticleInput:
    """表示 step 4 输入中的单篇文章。"""
    topic: str
    channel: str
    original_title: str
    original_url: str
    original_published_at: str
    selected_results: list[SearchResult]


@dataclass(frozen=True)
class SearchContentCategoryInput:
    """表示按主题分组后的 step 4 输入。"""
    topic: str
    items: list[SearchContentArticleInput]


@dataclass(frozen=True)
class SearchResultsInputPayload:
    """表示从 step 3 读取后的整体输入载荷。"""
    report_date: str
    input_path: Path
    generated_at: str
    categories: list[SearchContentCategoryInput]


@dataclass(frozen=True)
class FetchResult:
    """表示一次正文抓取后的标准化结果。"""
    url: str
    domain: str
    content_title: str
    content_summary: str
    content_text: str
    fetch_status: str
    fetch_error: str
    content_source: str = "html_fallback"


@dataclass(frozen=True)
class AliyunSearchDocument:
    """表示阿里云 IQS 返回的一条候选文档。"""
    link: str
    title: str
    published_at: str
    main_text: str
    rich_main_body: str


@dataclass(frozen=True)
class SelectedContentPayload:
    """表示一条补充链接抓取后的落盘结构。"""
    query: str
    query_type: str
    url: str
    domain: str
    result_title: str
    published_at: str
    content_title: str
    content_summary: str
    content_text: str
    content_source: str
    fetch_status: str
    fetch_error: str


@dataclass(frozen=True)
class ArticleContentPayload:
    """表示单篇文章在 step 4 的完整正文结果。"""
    original_title: str
    topic: str
    channel: str
    original_url: str
    original_published_at: str
    original_content: FetchResult
    selected_contents: list[SelectedContentPayload]


@dataclass(frozen=True)
class ContentCategoryPayload:
    """表示按主题分组后的 step 4 输出分组。"""
    topic: str
    items: list[ArticleContentPayload]


@dataclass(frozen=True)
class ContentWorkflowPayload:
    """表示 step 4 的整体输出载荷。"""
    report_date: str
    input_path: Path
    generated_at: str
    categories: list[ContentCategoryPayload]


@dataclass(frozen=True)
class FetchOutputPaths:
    """表示 step 4 输入输出文件路径。"""
    input_path: Path
    output_path: Path


VALID_KEEP_LEVELS = {"strong", "weak", "drop"}


class AliyunIQSClient:
    """Minimal Aliyun IQS client used as the preferred正文提取 provider."""
    def __init__(
        self,
        api_key: str,
        timeout: float = 20.0,
        endpoint: str = "https://cloud-iqs.aliyuncs.com/search/unified",
    ) -> None:
        """保存阿里云 IQS 调用所需的基础配置。"""
        self.api_key = api_key
        self.timeout = timeout
        self.endpoint = endpoint

    @staticmethod
    def from_env(project_root: Path | None = None) -> AliyunIQSClient | None:
        """从本地运行配置中创建阿里云 IQS 客户端。"""
        root = project_root or Path(__file__).resolve().parents[2]
        api_key = load_c114_runtime_config(root).aliyun_iqs_api_key
        if not api_key:
            return None
        return AliyunIQSClient(api_key=api_key)

    def search(self, query_text: str) -> list[AliyunSearchDocument]:
        """调用阿里云 IQS 搜索正文候选。"""
        payload = {
            "query": query_text,
            "engineType": "Generic",
            "contents": {
                "mainText": True,
                "richMainBody": True,
                "markdownText": False,
                "summary": False,
                "rerankScore": True,
            },
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover - network-dependent
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"阿里云 IQS 请求失败: {error.code} {detail}") from error
        except URLError as error:  # pragma: no cover - network-dependent
            raise RuntimeError(f"阿里云 IQS 请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover - network-dependent
            raise RuntimeError("阿里云 IQS 请求超时。") from error

        documents: list[AliyunSearchDocument] = []
        for item in body.get("pageItems") or []:
            documents.append(
                AliyunSearchDocument(
                    link=str(item.get("link") or ""),
                    title=compact_text(str(item.get("title") or ""), limit=200),
                    published_at=normalize_aliyun_published_at(str(item.get("publishedTime") or "")),
                    main_text=compact_text(str(item.get("mainText") or ""), limit=12000),
                    rich_main_body=compact_text(str(item.get("richMainBody") or ""), limit=12000),
                )
            )
        return documents


class VisibleTextParser(HTMLParser):
    """从 HTML 中抽取可见正文文本的简易解析器。"""

    def __init__(self) -> None:
        """初始化正文提取解析器的内部状态。"""
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """遇到脚本或样式标签时进入跳过状态。"""
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """脚本或样式标签结束时退出跳过状态。"""
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        """收集非脚本区域的可见文本。"""
        if self._skip_depth:
            return
        text = compact_text(data, limit=10_000)
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        """返回清洗后的正文文本。"""
        return compact_text(" ".join(self._parts), limit=20_000)


BLOCKED_CONTENT_DOMAINS = {
    "maxyic.com",
    "supplypost.com",
}


def resolve_content_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    output_override: str | None = None,
) -> FetchOutputPaths:
    """Resolve the canonical step 3 input and step 4 output paths."""
    run_dir: Path | None = None
    if input_override:
        input_path = (paths.project_root / input_override).resolve()
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            input_path = (run_dir / step_3_results_name(report_date)).resolve()
        else:
            input_path = (c114_reports_root(paths.reports_dir) / step_3_results_name(report_date)).resolve()

    if output_override:
        output_path = (paths.project_root / output_override).resolve()
    else:
        if input_override or run_dir:
            output_path = (input_path.parent / step_4_content_name(report_date)).resolve()
        else:
            output_path = (c114_reports_root(paths.reports_dir) / step_4_content_name(report_date)).resolve()
    return FetchOutputPaths(input_path=input_path, output_path=output_path)


def load_search_results_yaml(input_path: Path) -> SearchResultsInputPayload:
    """Parse the step 3 YAML into the normalized payload consumed by step 4."""
    report_date = ""
    generated_at = ""
    categories: list[SearchContentCategoryInput] = []
    current_topic = ""
    current_items: list[SearchContentArticleInput] = []
    current_item: dict[str, object] | None = None
    current_selected: dict[str, object] | None = None
    current_section = ""
    matched_terms_mode = False

    def finalize_selected() -> None:
        nonlocal current_selected
        if current_item is None or current_selected is None:
            return
        selected_results = current_item.setdefault("selected_results", [])
        assert isinstance(selected_results, list)
        selected_results.append(
            SearchResult(
                query=str(current_selected.get("query", "")),
                query_type=str(current_selected.get("query_type", "")),
                result_title=str(current_selected.get("result_title", "")),
                url=str(current_selected.get("url", "")),
                domain=str(current_selected.get("domain", "")),
                published_at=str(current_selected.get("published_at", "")),
                snippet=str(current_selected.get("snippet", "")),
                score=float(current_selected.get("score", 0.0)),
                is_official=bool(current_selected.get("is_official", False)),
                source_tier=str(current_selected.get("source_tier", "normal")),
                matched_terms=list(current_selected.get("matched_terms", [])),
                extract_text=str(current_selected.get("extract_text", "")),
                extract_status=str(current_selected.get("extract_status", "")),
                review_status=str(current_selected.get("review_status", "pending")),
                keep_level=str(current_selected.get("keep_level", "")),
                review_reason=str(current_selected.get("review_reason", "")),
                relevance_note=str(current_selected.get("relevance_note", "")),
                value_type=str(current_selected.get("value_type", "")),
            )
        )
        current_selected = None

    def finalize_item() -> None:
        nonlocal current_item
        finalize_selected()
        if current_item is None:
            return
        current_items.append(
            SearchContentArticleInput(
                topic=current_topic,
                channel=str(current_item.get("channel", "")),
                original_title=str(current_item.get("original_title", "")),
                original_url=str(current_item.get("original_url", "")),
                original_published_at=str(current_item.get("original_published_at", "")),
                selected_results=list(current_item.get("selected_results", [])),
            )
        )
        current_item = None

    def finalize_topic() -> None:
        nonlocal current_items
        finalize_item()
        if current_topic:
            categories.append(SearchContentCategoryInput(topic=current_topic, items=current_items))
        current_items = []

    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("report_date: "):
            report_date = parse_yaml_value(line)
            continue
        if line.startswith("generated_at: "):
            generated_at = parse_yaml_value(line)
            continue
        if line.startswith("  - topic: "):
            finalize_topic()
            current_topic = parse_yaml_value(line)
            current_section = ""
            matched_terms_mode = False
            continue
        if line.startswith("      - original_title: "):
            finalize_item()
            current_item = {
                "original_title": parse_yaml_value(line),
                "channel": "",
                "original_url": "",
                "original_published_at": "",
                "selected_results": [],
            }
            current_section = ""
            matched_terms_mode = False
            continue
        if current_item is None:
            continue
        if line.startswith("        channel: "):
            current_item["channel"] = parse_yaml_value(line)
        elif line.startswith("        original_url: "):
            current_item["original_url"] = parse_yaml_value(line)
        elif line.startswith("        original_published_at: "):
            current_item["original_published_at"] = parse_yaml_value(line)
        elif line.startswith("        selected_results:"):
            finalize_selected()
            current_section = "selected_results"
            matched_terms_mode = False
        elif line.startswith("        search_results:") or line.startswith("        queries:"):
            finalize_selected()
            current_section = "ignore"
            matched_terms_mode = False
        elif current_section == "selected_results" and line.startswith("          - query: "):
            finalize_selected()
            current_selected = {
                "query": parse_yaml_value(line),
                "query_type": "",
                "result_title": "",
                "url": "",
                "domain": "",
                "published_at": "",
                "snippet": "",
                "score": 0.0,
                "is_official": False,
                "source_tier": "normal",
                "extract_status": "",
                "extract_text": "",
                "matched_terms": [],
                "review_status": "pending",
                "keep_level": "",
                "review_reason": "",
                "relevance_note": "",
                "value_type": "",
            }
            matched_terms_mode = False
        elif current_section == "selected_results" and current_selected is not None:
            if line.startswith("            query_type: "):
                current_selected["query_type"] = parse_yaml_value(line)
            elif line.startswith("            result_title: "):
                current_selected["result_title"] = parse_yaml_value(line)
            elif line.startswith("            url: "):
                current_selected["url"] = parse_yaml_value(line)
            elif line.startswith("            domain: "):
                current_selected["domain"] = parse_yaml_value(line)
            elif line.startswith("            published_at: "):
                current_selected["published_at"] = parse_yaml_value(line)
            elif line.startswith("            snippet: "):
                current_selected["snippet"] = parse_yaml_value(line)
            elif line.startswith("            score: "):
                current_selected["score"] = float(line.split(":", 1)[1].strip())
            elif line.startswith("            is_official: "):
                current_selected["is_official"] = line.endswith("true")
            elif line.startswith("            source_tier: "):
                current_selected["source_tier"] = parse_yaml_value(line)
            elif line.startswith("            is_trusted_media: "):
                current_selected["source_tier"] = "normal"
            elif line.startswith("            extract_status: "):
                current_selected["extract_status"] = parse_yaml_value(line)
            elif line.startswith("            extract_text: "):
                current_selected["extract_text"] = parse_yaml_value(line)
            elif line.startswith("            ai_review:"):
                matched_terms_mode = False
            elif line.startswith("              status: "):
                current_selected["review_status"] = parse_yaml_value(line)
            elif line.startswith("              keep_level: "):
                current_selected["keep_level"] = parse_yaml_value(line)
            elif line.startswith("              keep: "):
                current_selected["keep_level"] = "strong" if parse_yaml_value(line) == "true" else ""
            elif line.startswith("              reason: "):
                current_selected["review_reason"] = parse_yaml_value(line)
            elif line.startswith("              relevance_note: "):
                current_selected["relevance_note"] = parse_yaml_value(line)
            elif line.startswith("              value_type: "):
                current_selected["value_type"] = parse_yaml_value(line)
            elif line.startswith("            matched_terms:"):
                matched_terms_mode = True
            elif matched_terms_mode and line.startswith("              - "):
                matched_terms = current_selected.setdefault("matched_terms", [])
                assert isinstance(matched_terms, list)
                matched_terms.append(parse_yaml_list_item(line))
            else:
                matched_terms_mode = False

    finalize_topic()
    return SearchResultsInputPayload(
        report_date=report_date,
        input_path=input_path,
        generated_at=generated_at,
        categories=categories,
    )


def run_content_fetch_workflow(
    input_path: Path,
    report_date: str,
    fetcher: Callable[[str], FetchResult] | None = None,
    aliyun_client: AliyunIQSClient | None = None,
    generated_at: str | None = None,
) -> ContentWorkflowPayload:
    """Execute step 4 for one report date and return the full content payload."""
    source = load_search_results_yaml(input_path)
    if source.report_date and source.report_date != report_date:
        raise ValueError(f"输入搜索结果日期为 {source.report_date}，与命令日期 {report_date} 不一致。")
    validate_content_fetch_inputs(source)

    content_fetcher = fetcher or fetch_url_content
    content_search_client = aliyun_client if aliyun_client is not None else AliyunIQSClient.from_env()
    runtime_config = load_c114_runtime_config(Path(__file__).resolve().parents[2])
    cache: dict[str, FetchResult] = {}
    flattened_articles: list[tuple[int, int, SearchContentArticleInput]] = []
    for category_index, category in enumerate(source.categories):
        for item_index, article in enumerate(category.items):
            flattened_articles.append((category_index, item_index, article))

    max_workers = min(8, max(1, len(flattened_articles)))
    results_by_position: dict[tuple[int, int], ArticleContentPayload] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_pairs = [
            (
                (category_index, item_index),
                executor.submit(
                    fetch_article_contents,
                    article,
                    report_date=report_date,
                    fetcher=content_fetcher,
                    cache=cache,
                    aliyun_client=content_search_client,
                    allowed_keep_levels=runtime_config.content_fetch_keep_levels,
                ),
            )
            for category_index, item_index, article in flattened_articles
        ]
        for position, future in future_pairs:
            results_by_position[position] = future.result()

    categories = []
    for category_index, category in enumerate(source.categories):
        items = [
            results_by_position[(category_index, item_index)] for item_index, _article in enumerate(category.items)
        ]
        categories.append(ContentCategoryPayload(topic=category.topic, items=items))
    return ContentWorkflowPayload(
        report_date=report_date,
        input_path=input_path,
        generated_at=generated_at or datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )


def validate_content_fetch_inputs(source: SearchResultsInputPayload) -> None:
    """Fail fast when step 3 still contains pending AI review decisions."""

    pending: list[str] = []
    for category in source.categories:
        for article in category.items:
            invalid = [
                result
                for result in article.selected_results
                if result.review_status != "reviewed" or result.keep_level not in VALID_KEEP_LEVELS
            ]
            if not invalid:
                continue
            preview = "；".join((result.result_title or result.url) for result in invalid[:2])
            pending.append(f"{article.original_title} -> {preview}")
    if not pending:
        return

    details = "\n".join(f"- {item}" for item in pending[:5])
    raise ValueError(
        "step 3 中仍有补充链接未完成 ai_review，请先重新运行 c114-search，"
        "由 skill 内置模型为每条 selected_results 补齐 review_status=reviewed 与 keep_level=strong/weak/drop，"
        "再运行 c114-fetch-content。\n"
        f"{details}"
    )


def fetch_article_contents(
    article: SearchContentArticleInput,
    report_date: str,
    fetcher: Callable[[str], FetchResult],
    cache: dict[str, FetchResult] | None = None,
    aliyun_client: AliyunIQSClient | None = None,
    allowed_keep_levels: tuple[str, ...] = ("strong", "weak"),
) -> ArticleContentPayload:
    """Fetch the original article plus all eligible supplementary links for one item."""
    fetch_cache = cache if cache is not None else {}
    original_content = fetch_once(
        article.original_url,
        query_text=article.original_title,
        target_title=article.original_title,
        report_date=report_date,
        fetcher=fetcher,
        cache=fetch_cache,
        aliyun_client=aliyun_client,
    )
    selected_contents: list[SelectedContentPayload] = []
    for result in article.selected_results:
        if result.keep_level not in allowed_keep_levels:
            continue
        if should_skip_selected_url(result.url):
            continue
        fetched = fetch_selected_result_content(
            result=result,
            article=article,
            report_date=report_date,
            fetcher=fetcher,
            cache=fetch_cache,
            aliyun_client=aliyun_client,
        )
        selected_contents.append(
            SelectedContentPayload(
                query=result.query,
                query_type=result.query_type,
                url=result.url,
                domain=result.domain,
                result_title=result.result_title,
                published_at=result.published_at,
                content_title=fetched.content_title,
                content_summary=fetched.content_summary,
                content_text=fetched.content_text,
                content_source=fetched.content_source,
                fetch_status=fetched.fetch_status,
                fetch_error=fetched.fetch_error,
            )
        )
    return ArticleContentPayload(
        original_title=article.original_title,
        topic=article.topic,
        channel=article.channel,
        original_url=article.original_url,
        original_published_at=article.original_published_at,
        original_content=original_content,
        selected_contents=selected_contents,
    )


def fetch_selected_result_content(
    result: SearchResult,
    article: SearchContentArticleInput,
    report_date: str,
    fetcher: Callable[[str], FetchResult],
    cache: dict[str, FetchResult],
    aliyun_client: AliyunIQSClient | None = None,
) -> FetchResult:
    """抓取单条补充链接对应的正文内容。"""
    return fetch_once(
        result.url,
        query_text=result.result_title or article.original_title,
        target_title=result.result_title or article.original_title,
        report_date=result.published_at or report_date,
        fetcher=fetcher,
        cache=cache,
        aliyun_client=aliyun_client,
    )


def should_skip_selected_url(url: str) -> bool:
    """Block known bad domains before spending network calls on正文抓取."""
    domain = extract_domain(url)
    return any(domain == blocked or domain.endswith(f".{blocked}") for blocked in BLOCKED_CONTENT_DOMAINS)


def fetch_once(
    url: str,
    query_text: str,
    target_title: str,
    report_date: str,
    fetcher: Callable[[str], FetchResult],
    cache: dict[str, FetchResult],
    aliyun_client: AliyunIQSClient | None = None,
) -> FetchResult:
    """Fetch one URL with cache reuse and Aliyun-first / HTML-fallback routing."""
    normalized = normalize_url(url)
    if normalized not in cache:
        documents: list[AliyunSearchDocument] = []
        if aliyun_client:
            try:
                documents = aliyun_client.search(query_text)
            except Exception:
                documents = []
        cache[normalized] = build_aliyun_fetch_result(
            target_url=url,
            query_text=query_text,
            target_title=target_title,
            report_date=report_date,
            documents=documents,
            fallback_fetcher=fetcher,
        )
    return cache[normalized]


def build_aliyun_fetch_result(
    target_url: str,
    query_text: str,
    target_title: str,
    report_date: str,
    documents: list[AliyunSearchDocument],
    fallback_fetcher: Callable[[str], FetchResult],
) -> FetchResult:
    matched = match_aliyun_document(
        target_url=target_url,
        target_title=target_title,
        report_date=report_date,
        documents=documents,
    )
    if matched:
        content_text = matched.rich_main_body or matched.main_text
        if content_text:
            return FetchResult(
                url=target_url,
                domain=extract_domain(target_url),
                content_title=matched.title or target_title,
                content_summary=compact_text(content_text, limit=200),
                content_text=content_text,
                fetch_status="success",
                fetch_error="",
                content_source="aliyun",
            )
    fallback_result = fallback_fetcher(target_url)
    if fallback_result.content_source != "aliyun":
        return FetchResult(
            url=fallback_result.url,
            domain=fallback_result.domain,
            content_title=fallback_result.content_title,
            content_summary=fallback_result.content_summary,
            content_text=fallback_result.content_text,
            fetch_status=fallback_result.fetch_status,
            fetch_error=fallback_result.fetch_error,
            content_source="html_fallback",
        )
    return fallback_result


def match_aliyun_document(
    target_url: str,
    target_title: str,
    report_date: str,
    documents: list[AliyunSearchDocument],
) -> AliyunSearchDocument | None:
    normalized_target_url = normalize_url(target_url)
    normalized_target_title = normalize_compare_text(target_title)
    target_domain = extract_domain(target_url)

    for document in documents:
        if document.link and normalize_url(document.link) == normalized_target_url:
            return document

    for document in documents:
        if extract_domain(document.link) != target_domain:
            continue
        if title_similarity(normalized_target_title, normalize_compare_text(document.title)) >= 0.65:
            return document

    for document in documents:
        similarity = title_similarity(normalized_target_title, normalize_compare_text(document.title))
        if similarity < 0.78:
            continue
        if published_date_close(report_date, document.published_at):
            return document
    return None


def fetch_url_content(url: str, timeout: float = 20.0) -> FetchResult:
    """Fetch one page directly and extract the best-effort readable content."""
    request = Request(normalize_request_url(url), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
            charset = response.headers.get_content_charset()
        html = decode_html(payload, charset)
        title = extract_html_title(html)
        summary = extract_html_summary(html)
        text = extract_content_text(url, html)
        if not summary and text:
            summary = compact_text(text, limit=200)
        status = "success"
        if not text:
            status = "empty"
        return FetchResult(
            url=url,
            domain=extract_domain(url),
            content_title=title,
            content_summary=summary,
            content_text=text,
            fetch_status=status,
            fetch_error="",
            content_source="html_fallback",
        )
    except (HTTPError, URLError, socket.timeout) as error:
        return FetchResult(
            url=url,
            domain=extract_domain(url),
            content_title="",
            content_summary="",
            content_text="",
            fetch_status="failed",
            fetch_error=str(error),
            content_source="html_fallback",
        )


def normalize_request_url(url: str) -> str:
    """对请求 URL 做最小规范化，避免中文路径导致请求失败。"""
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/%:@")
    query = quote(unquote(parts.query), safe="=&/%:@?;+,-._~")
    fragment = quote(unquote(parts.fragment), safe="=&/%:@?;+,-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def decode_html(payload: bytes, charset: str | None) -> str:
    """按多种常见编码顺序解码 HTML。"""
    candidates = [charset, "utf-8", "gb18030", "gbk", "latin-1"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return payload.decode(candidate)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def extract_html_title(html: str) -> str:
    """从 HTML 中提取页面标题。"""
    title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if not title_match:
        return ""
    return compact_text(title_match.group(1), limit=200)


def extract_html_summary(html: str) -> str:
    """从 HTML 的 meta 信息中提取摘要。"""
    for pattern in (
        r'<meta[^>]+name="description"[^>]+content="([^"]*)"',
        r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
    ):
        match = re.search(pattern, html, re.I)
        if match:
            return compact_text(unquote_yaml_scalar(match.group(1)), limit=400)
    return ""


def extract_content_text(url: str, html: str) -> str:
    """Route HTML extraction through site-specific logic when available."""
    domain = extract_domain(url)
    if domain == "www.c114.com.cn" or domain.endswith(".c114.com.cn"):
        specialized = extract_c114_article_text(html)
        if specialized:
            return specialized
    return extract_visible_text(html)


def extract_c114_article_text(html: str) -> str:
    """Extract正文 from C114 pages using the article container before generic fallback."""
    match = re.search(
        r'<div class="article_text">\s*<div class="text" id="text1">\s*(.*?)\s*</div>\s*</div>',
        html,
        re.I | re.S,
    )
    if not match:
        return ""
    body_html = match.group(1)
    body_html = re.sub(r"<script.*?</script>", " ", body_html, flags=re.I | re.S)
    body_html = re.sub(r"<style.*?</style>", " ", body_html, flags=re.I | re.S)
    body_html = re.sub(r"<[^>]+>", " ", body_html)
    text = compact_text(unquote_yaml_scalar(body_html), limit=12000)
    text = strip_c114_shell_sections(text)
    return compact_text(text, limit=12000)


def strip_c114_shell_sections(text: str) -> str:
    """去掉 C114 页面外壳、版权和分享等非正文片段。"""
    stop_markers = [
        "免责声明",
        "相关链接",
        "热门文章",
        "最新视频",
        "为您推荐",
        "C114简介",
        "联系我们",
        "网站地图",
        "Copyright",
        "举报电话",
        "用户注销",
    ]
    cleaned = text
    for marker in stop_markers:
        index = cleaned.find(marker)
        if index != -1:
            cleaned = cleaned[:index]
    return cleaned


def extract_visible_text(html: str) -> str:
    """使用通用可见文本解析器提取正文。"""
    cleaned = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    parser = VisibleTextParser()
    parser.feed(cleaned)
    return parser.get_text()


def normalize_compare_text(value: str) -> str:
    """归一化标题文本，便于做相似度比较。"""
    lowered = value.lower()
    return re.sub(r"[\W_]+", "", lowered)


def title_similarity(left: str, right: str) -> float:
    """计算两个标题的相似度。"""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_aliyun_published_at(value: str) -> str:
    """把阿里云返回的发布时间归一化为 YYYY-MM-DD。"""
    if not value:
        return ""
    text = value.strip()
    if len(text) >= 10:
        return text[:10]
    return text


def published_date_close(reference: str, candidate: str, max_days: int = 3) -> bool:
    """判断两个日期是否在允许误差范围内。"""
    if not reference or not candidate:
        return False
    try:
        reference_day = date.fromisoformat(reference[:10])
        candidate_day = date.fromisoformat(candidate[:10])
    except ValueError:
        return False
    return abs((reference_day - candidate_day).days) <= max_days


def render_content_yaml(payload: ContentWorkflowPayload) -> str:
    """Render the step 4 YAML contract used by downstream analysis steps."""
    lines = [
        f"report_date: '{escape_yaml_scalar(payload.report_date)}'",
        f"input_path: '{escape_yaml_scalar(str(payload.input_path))}'",
        f"generated_at: '{escape_yaml_scalar(payload.generated_at)}'",
        "categories:",
    ]
    for category in payload.categories:
        lines.append(f"  - topic: '{escape_yaml_scalar(category.topic)}'")
        lines.append("    items:")
        for item in category.items:
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml_scalar(item.original_title)}'",
                    f"        topic: '{escape_yaml_scalar(item.topic)}'",
                    f"        channel: '{escape_yaml_scalar(item.channel)}'",
                    f"        original_url: '{escape_yaml_scalar(item.original_url)}'",
                    f"        original_published_at: '{escape_yaml_scalar(item.original_published_at)}'",
                    "        original_content:",
                ]
            )
            lines.extend(render_fetch_block(item.original_content, indent="          "))
            lines.append("        selected_contents:")
            for selected in item.selected_contents:
                lines.extend(
                    [
                        f"          - query: '{escape_yaml_scalar(selected.query)}'",
                        f"            query_type: '{escape_yaml_scalar(selected.query_type)}'",
                        f"            url: '{escape_yaml_scalar(selected.url)}'",
                        f"            domain: '{escape_yaml_scalar(selected.domain)}'",
                        f"            result_title: '{escape_yaml_scalar(selected.result_title)}'",
                        f"            published_at: '{escape_yaml_scalar(selected.published_at)}'",
                        f"            content_title: '{escape_yaml_scalar(selected.content_title)}'",
                        f"            content_summary: '{escape_yaml_scalar(selected.content_summary)}'",
                        f"            content_text: '{escape_yaml_scalar(selected.content_text)}'",
                        f"            content_source: '{escape_yaml_scalar(selected.content_source)}'",
                        f"            fetch_status: '{escape_yaml_scalar(selected.fetch_status)}'",
                        f"            fetch_error: '{escape_yaml_scalar(selected.fetch_error)}'",
                    ]
                )
    return "\n".join(lines)


def render_fetch_block(result: FetchResult, indent: str) -> list[str]:
    """把单条抓取结果渲染成 YAML 片段。"""
    return [
        f"{indent}url: '{escape_yaml_scalar(result.url)}'",
        f"{indent}domain: '{escape_yaml_scalar(result.domain)}'",
        f"{indent}content_title: '{escape_yaml_scalar(result.content_title)}'",
        f"{indent}content_summary: '{escape_yaml_scalar(result.content_summary)}'",
        f"{indent}content_text: '{escape_yaml_scalar(result.content_text)}'",
        f"{indent}content_source: '{escape_yaml_scalar(result.content_source)}'",
        f"{indent}fetch_status: '{escape_yaml_scalar(result.fetch_status)}'",
        f"{indent}fetch_error: '{escape_yaml_scalar(result.fetch_error)}'",
    ]


def save_content_results(output_path: Path, payload: ContentWorkflowPayload) -> None:
    """把 step 4 结果写入 YAML 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_content_yaml(payload), encoding="utf-8")


def compact_text(value: str, limit: int) -> str:
    """压缩空白并截断过长文本。"""
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit].strip()
