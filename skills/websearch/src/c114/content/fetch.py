"""Step 4 网络抓取与 provider 路由。

本模块负责阿里云 IQS 与 HTML fallback 的抓取策略、URL 级 checkpoint 和抓取结果标准化。
正文清洗逻辑委托给 content.html，workflow 编排放在 steps.step4_content_fetch。
"""

from __future__ import annotations

import json
import http.client
import socket
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ..runtime.checkpoint import StepCheckpointStore
from ..runtime.config import load_c114_runtime_config
from ..search.workflow import SearchResult, extract_domain, normalize_url
from .html import (
    compact_text,
    decode_html,
    extract_content_text,
    extract_html_summary,
    extract_html_title,
    normalize_aliyun_published_at,
    normalize_compare_text,
    published_date_close,
    title_similarity,
)
from .types import (
    AliyunSearchDocument,
    ArticleContentPayload,
    FetchResult,
    SearchContentArticleInput,
    SelectedContentPayload,
)

BLOCKED_CONTENT_DOMAINS = {
    "maxyic.com",
    "supplypost.com",
}

class AliyunIQSClient:
    """Minimal Aliyun IQS client used as the preferred正文提取 provider."""
    def __init__(
        self,
        api_key: str,
        timeout: float = 20.0,
        endpoint: str = "https://cloud-iqs.aliyuncs.com/search/unified",
        max_concurrency: int = 2,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        """保存阿里云 IQS 调用所需的基础配置。"""
        self.api_key = api_key
        self.timeout = timeout
        self.endpoint = endpoint
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._search_gate = threading.BoundedSemaphore(value=max(1, max_concurrency))
        self._search_http_rounds = 0

    @staticmethod
    def from_env(project_root: Path | None = None) -> AliyunIQSClient | None:
        """从本地运行配置中创建阿里云 IQS 客户端。"""
        root = project_root or Path(__file__).resolve().parents[2]
        runtime_config = load_c114_runtime_config(root)
        api_key = runtime_config.aliyun_iqs_api_key
        if not api_key:
            return None
        return AliyunIQSClient(
            api_key=api_key,
            timeout=runtime_config.aliyun_timeout_seconds,
            max_retries=runtime_config.aliyun_max_retries,
            retry_backoff_seconds=runtime_config.aliyun_retry_backoff_seconds,
        )

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
        last_error: RuntimeError | None = None
        for attempt in range(self.max_retries + 1):
            self._search_http_rounds += 1
            try:
                with self._search_gate:
                    with urlopen(request, timeout=self.timeout) as response:
                        body = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:  # pragma: no cover - network-dependent
                detail = error.read().decode("utf-8", errors="ignore")
                last_error = RuntimeError(f"阿里云 IQS 请求失败: {error.code} {detail}")
                if error.code not in {408, 429, 500, 502, 503, 504} or attempt >= self.max_retries:
                    raise last_error from error
            except URLError as error:  # pragma: no cover - network-dependent
                last_error = RuntimeError(f"阿里云 IQS 请求失败: {error.reason}")
                if attempt >= self.max_retries:
                    raise last_error from error
            except socket.timeout as error:  # pragma: no cover - network-dependent
                last_error = RuntimeError("阿里云 IQS 请求超时。")
                if attempt >= self.max_retries:
                    raise last_error from error
            time.sleep(self.retry_backoff_seconds * (attempt + 1))
        else:  # pragma: no cover - defensive
            raise last_error or RuntimeError("阿里云 IQS 请求失败。")

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

    def take_search_http_rounds(self) -> int:
        """返回自上次清零以来 IQS search() 的 HTTP 尝试次数（含重试），读完后清零。"""

        total = self._search_http_rounds
        self._search_http_rounds = 0
        return total


def fetch_article_contents(
    article: SearchContentArticleInput,
    report_date: str,
    fetcher: Callable[[str], FetchResult],
    cache: dict[str, FetchResult] | None = None,
    aliyun_client: AliyunIQSClient | None = None,
    allowed_keep_levels: tuple[str, ...] = ("strong", "weak"),
    checkpoint_store: StepCheckpointStore | None = None,
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
        checkpoint_store=checkpoint_store,
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
            checkpoint_store=checkpoint_store,
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
    checkpoint_store: StepCheckpointStore | None = None,
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
        checkpoint_store=checkpoint_store,
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
    checkpoint_store: StepCheckpointStore | None = None,
) -> FetchResult:
    """Fetch one URL with cache reuse and Aliyun-first / HTML-fallback routing."""
    normalized = normalize_url(url)
    if checkpoint_store is not None:
        cached_checkpoint = checkpoint_store.get_result(build_step4_checkpoint_entry_id(url))
        if isinstance(cached_checkpoint, dict):
            checkpoint_result = fetch_result_from_dict(cached_checkpoint)
            if checkpoint_store.is_success(build_step4_checkpoint_entry_id(url)):
                cache.setdefault(normalized, checkpoint_result)
                return checkpoint_result
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
        if checkpoint_store is not None:
            fetch_result = cache[normalized]
            checkpoint_store.record_entry(
                entry_id=build_step4_checkpoint_entry_id(url),
                status="success" if fetch_result.fetch_status == "success" else "error",
                source=fetch_result.content_source,
                request_context={
                    "url": url,
                    "query_text": query_text,
                    "target_title": target_title,
                    "report_date": report_date,
                },
                result=fetch_result_to_dict(fetch_result),
                error=(
                    {"message": fetch_result.fetch_error}
                    if fetch_result.fetch_error
                    else {}
                ),
            )
    return cache[normalized]


def build_step4_checkpoint_entry_id(url: str) -> str:
    """生成 step 4 按 URL 记录正文抓取状态的 checkpoint 键。"""

    return f"url::{normalize_url(url)}"


def fetch_result_to_dict(result: FetchResult) -> dict[str, str]:
    return {
        "url": result.url,
        "domain": result.domain,
        "content_title": result.content_title,
        "content_summary": result.content_summary,
        "content_text": result.content_text,
        "fetch_status": result.fetch_status,
        "fetch_error": result.fetch_error,
        "content_source": result.content_source,
    }


def fetch_result_from_dict(payload: dict[str, str]) -> FetchResult:
    return FetchResult(
        url=str(payload.get("url", "")),
        domain=str(payload.get("domain", "")),
        content_title=str(payload.get("content_title", "")),
        content_summary=str(payload.get("content_summary", "")),
        content_text=str(payload.get("content_text", "")),
        fetch_status=str(payload.get("fetch_status", "")),
        fetch_error=str(payload.get("fetch_error", "")),
        content_source=str(payload.get("content_source", "html_fallback")),
    )


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
    except (HTTPError, URLError, socket.timeout, TimeoutError, ConnectionResetError, http.client.RemoteDisconnected) as error:
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
