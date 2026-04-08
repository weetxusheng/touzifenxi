"""C114 第 3 步外部搜索与补充链接精筛模块。"""

from __future__ import annotations

import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .c114_intelligence import (
    ArticleAnalysis,
    SearchChecklistItem,
    autofill_search_checklist_items,
    c114_reports_root,
    find_latest_search_run_directory,
    provider_stats_name,
    step_2_checklist_name,
    step_3_results_name,
)
from .llm import MiniMaxChatClient, StructuredLLMError, begin_llm_step, load_prompt_text, run_parallel_ordered
from .settings import AppPaths, load_c114_runtime_config

SKILL_ROOT = Path(__file__).resolve().parents[2]
SEARCH_CONFIG_PATH = SKILL_ROOT / "config" / "search_domains.json"
SEARCH_REVIEW_PROMPT_PATH = SKILL_ROOT / "prompts" / "search-review-agent.md"
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.+-]{1,30}|[\u4e00-\u9fff]{2,16}")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[-_/](?P<month>\d{1,2})[-_/](?P<day>\d{1,2})"),
    re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"),
)


@dataclass(frozen=True)
class SearchArticleInput:
    """表示 step 3 中单篇文章的搜索输入。"""
    topic: str
    channel: str
    original_title: str
    original_url: str
    original_published_at: str
    keywords: list[str]


@dataclass(frozen=True)
class SearchQuery:
    """表示一条实际要发给搜索 provider 的查询。"""
    query_type: str
    value: str


@dataclass(frozen=True)
class SearchResult:
    """表示归一化后的单条搜索结果。"""
    query: str
    query_type: str
    result_title: str
    url: str
    domain: str
    published_at: str
    snippet: str
    score: float
    is_official: bool
    source_tier: str
    matched_terms: list[str]
    extract_text: str
    extract_status: str
    review_status: str = "pending"
    keep_level: str = ""
    review_reason: str = ""
    relevance_note: str = ""
    value_type: str = ""


@dataclass(frozen=True)
class QueryResultBucket:
    """表示某条 query 对应的一组搜索结果桶。"""
    query: str
    query_type: str
    provider: str
    results: list[SearchResult]


@dataclass(frozen=True)
class ArticleSearchPayload:
    """表示单篇文章在 step 3 的完整搜索结果。"""
    topic: str
    channel: str
    original_title: str
    original_url: str
    original_published_at: str
    queries: list[QueryResultBucket]
    search_results: list[SearchResult]
    selected_results: list[SearchResult]


@dataclass(frozen=True)
class SearchCategoryPayload:
    """表示按主题分组后的 step 3 输出。"""
    topic: str
    items: list[ArticleSearchPayload]


@dataclass(frozen=True)
class SearchWorkflowPayload:
    """表示 step 3 的整体输出载荷。"""
    report_date: str
    provider: str
    input_path: Path
    generated_at: str
    categories: list[SearchCategoryPayload]


@dataclass(frozen=True)
class SearchOutputPaths:
    """表示 step 3 输入与输出文件路径。"""
    input_path: Path
    output_path: Path
    provider: str

    @staticmethod
    def require_api_key(project_root: Path | None = None) -> str:
        """Return a Tavily API key or fail with a user-facing configuration error."""

        root = project_root or Path(__file__).resolve().parents[2]
        api_key = load_c114_runtime_config(root).tavily_api_key
        if not api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY，无法执行 C114 搜索层。")
        return api_key

    @staticmethod
    def require_metaso_api_key(project_root: Path | None = None) -> str:
        """读取 Metaso key，缺失时抛出可读错误。"""
        root = project_root or Path(__file__).resolve().parents[2]
        api_key = load_c114_runtime_config(root).metaso_api_key
        if not api_key:
            raise RuntimeError("未配置 METASO_API_KEY，无法执行中文搜索层。")
        return api_key

    @staticmethod
    def require_baidu_api_key(project_root: Path | None = None) -> str:
        """读取百度搜索 key，缺失时抛出可读错误。"""
        root = project_root or Path(__file__).resolve().parents[2]
        api_key = load_c114_runtime_config(root).baidu_api_key
        if not api_key:
            raise RuntimeError("未配置 BAIDU_API_KEY，无法执行百度搜索层。")
        return api_key


class TavilyClient:
    """Minimal Tavily HTTP client used to avoid an extra SDK dependency."""

    def __init__(self, api_key: str, timeout: float = 20.0, base_url: str = "https://api.tavily.com") -> None:
        """保存 Tavily 调用所需的基础配置。"""
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        """调用 Tavily 搜索接口。"""
        payload = {
            "query": query.value,
            "topic": "general",
            "search_depth": "basic",
            "time_range": "week",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        response = self._post_json("/search", payload)
        return response.get("results", [])

    def extract(self, urls: list[str], query: str) -> dict[str, str]:
        """调用 Tavily extract 接口补正文摘要。"""
        if not urls:
            return {}
        payload = {
            "urls": urls,
            "extract_depth": "advanced",
            "include_images": False,
            "format": "markdown",
            "query": query,
        }
        response = self._post_json("/extract", payload)
        items = response.get("results", [])
        extracted: dict[str, str] = {}
        for item in items:
            url = item.get("url", "")
            text = item.get("raw_content") or item.get("content") or ""
            if url and text:
                extracted[normalize_url(url)] = compact_text(str(text), limit=4000)
        return extracted

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向 Tavily 某个 JSON 接口发送请求。"""
        request = Request(
            url=f"{self.base_url}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover - network-dependent
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Tavily 请求失败: {error.code} {detail}") from error
        except URLError as error:  # pragma: no cover - network-dependent
            raise RuntimeError(f"Tavily 请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover - network-dependent
            raise RuntimeError("Tavily 请求超时。") from error


class MetasoClient:
    """Minimal Metaso HTTP client for Chinese web search."""

    def __init__(self, api_key: str, timeout: float = 20.0, base_url: str = "https://metaso.cn/api/v1") -> None:
        """保存 Metaso 调用所需的基础配置。"""
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        """调用 Metaso 搜索接口。"""
        payload = {
            "q": query.value,
            "scope": "webpage",
            "includeSummary": False,
            "size": str(max_results),
            "includeRawContent": False,
            "conciseSnippet": False,
        }
        response = self._post_json("/search", payload)
        return response.get("webpages", [])

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向 Metaso 某个 JSON 接口发送请求。"""
        request = Request(
            url=f"{self.base_url}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover - network-dependent
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Metaso 请求失败: {error.code} {detail}") from error
        except URLError as error:  # pragma: no cover - network-dependent
            raise RuntimeError(f"Metaso 请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover - network-dependent
            raise RuntimeError("Metaso 请求超时。") from error


class BaiduSearchClient:
    """Minimal Baidu Qianfan web-search client."""

    def __init__(
        self,
        api_key: str,
        timeout: float = 20.0,
        base_url: str = "https://qianfan.baidubce.com/v2/ai_search/web_search",
    ) -> None:
        """保存百度搜索调用所需的基础配置。"""
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        """调用百度千帆网页搜索接口。"""
        payload = {
            "messages": [{"content": query.value, "role": "user"}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": max_results}],
            "search_recency_filter": "year",
        }
        response = self._post_json(payload)
        return response.get("references", [])

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """向百度搜索接口发送 JSON 请求。"""
        request = Request(
            url=self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover - network-dependent
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"百度搜索请求失败: {error.code} {detail}") from error
        except URLError as error:  # pragma: no cover - network-dependent
            raise RuntimeError(f"百度搜索请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover - network-dependent
            raise RuntimeError("百度搜索请求超时。") from error


class GooglePlaywrightClient:
    """Experimental browser-search provider used only as a last-resort fallback."""

    def __init__(self, timeout: float = 20.0, headless: bool = True) -> None:
        """保存 Google Playwright 搜索所需的浏览器参数。"""
        self.timeout = timeout
        self.headless = headless

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        """通过 Playwright 模拟浏览器抓取 Google 首页搜索结果。"""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as error:  # pragma: no cover - environment-dependent
            raise RuntimeError("未安装 Python Playwright，无法执行 Google 浏览器搜索。") from error

        search_url = f"https://www.google.com/search?hl=zh-CN&num={max(10, max_results)}&q={quote_plus(query.value)}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    locale="zh-CN",
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/123.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1440, "height": 1200},
                )
                page = context.new_page()
                page.goto(search_url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000))
                self._maybe_accept_google_consent(page)
                page.wait_for_timeout(1200)
                items = page.evaluate(
                    """
                    () => {
                      const roots = Array.from(document.querySelectorAll('#search .g, #search .MjjYud'));
                      const results = [];
                      for (const root of roots) {
                        const link = root.querySelector('a[href]');
                        const title = root.querySelector('h3');
                        if (!link || !title) continue;
                        const snippetNode =
                          root.querySelector('.VwiC3b') ||
                          root.querySelector('.yXK7lf') ||
                          root.querySelector('.s3v9rd') ||
                          root.querySelector('[data-sncf] span');
                        results.push({
                          title: (title.textContent || '').trim(),
                          url: link.href || '',
                          snippet: ((snippetNode && snippetNode.textContent) || '').trim(),
                        });
                      }
                      return results;
                    }
                    """
                )
                context.close()
                browser.close()
        except PlaywrightTimeoutError as error:  # pragma: no cover - network-dependent
            raise RuntimeError("Google Playwright 搜索超时。") from error
        except Exception as error:  # pragma: no cover - network-dependent
            raise RuntimeError(f"Google Playwright 搜索失败: {error}") from error

        normalized: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in items or []:
            url = str(item.get("url") or "").strip()
            title = compact_text(str(item.get("title") or ""), limit=160)
            if not url or not title:
                continue
            normalized_url = normalize_url(url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            normalized.append(
                {
                    "url": url,
                    "title": title,
                    "snippet": compact_text(str(item.get("snippet") or ""), limit=400),
                    "published_at": "",
                    "score": 0.5,
                }
            )
            if len(normalized) >= max_results:
                break
        return normalized

    def _maybe_accept_google_consent(self, page: Any) -> None:
        """尝试自动接受 Google 的同意弹窗。"""
        for label in ("接受全部", "全部接受", "Accept all", "I agree"):
            try:
                locator = page.get_by_role("button", name=label)
                if locator.count() > 0:
                    locator.first.click(timeout=1500)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue


class AutoSearchClient:
    """Route each query to the configured default provider and explicit fallbacks."""

    def __init__(
        self,
        tavily_client: TavilyClient | None,
        metaso_client: MetasoClient | None,
        baidu_client: BaiduSearchClient | None,
        google_client: GooglePlaywrightClient | None = None,
        provider_mode: str = "auto",
    ) -> None:
        """保存自动路由搜索所需的 provider 客户端。"""
        self.tavily_client = tavily_client
        self.metaso_client = metaso_client
        self.baidu_client = baidu_client
        self.google_client = google_client or GooglePlaywrightClient()
        self.provider_mode = provider_mode

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        """执行搜索并只返回结果列表。"""
        _, results = self.search_with_provider(query, max_results)
        return results

    def search_with_provider(self, query: SearchQuery, max_results: int) -> tuple[str, list[dict[str, Any]]]:
        """按当前策略选择 provider，并返回 provider 名称与结果。"""
        provider = choose_search_provider(query, forced_provider=self.provider_mode)
        if provider == "google":
            return "google", self.google_client.search(query, max_results)
        if provider == "baidu":
            if self.baidu_client is not None:
                try:
                    return "baidu", self.baidu_client.search(query, max_results)
                except RuntimeError:
                    if self.metaso_client is not None:
                        return "metaso", self.metaso_client.search(query, max_results)
                    if self.tavily_client is not None:
                        return "tavily", self.tavily_client.search(query, max_results)
                    raise
            if self.metaso_client is not None:
                return "metaso", self.metaso_client.search(query, max_results)
            if self.tavily_client is not None:
                return "tavily", self.tavily_client.search(query, max_results)
            raise RuntimeError("未配置可用搜索 provider，无法执行 step 3 搜索。")
        if provider == "metaso":
            if self.metaso_client is not None:
                return "metaso", self.metaso_client.search(query, max_results)
            if self.baidu_client is not None:
                return "baidu", self.baidu_client.search(query, max_results)
            if self.tavily_client is not None:
                return "tavily", self.tavily_client.search(query, max_results)
            raise RuntimeError("未配置可用搜索 provider，无法执行 step 3 搜索。")
        if self.tavily_client is not None:
            try:
                return "tavily", self.tavily_client.search(query, max_results)
            except RuntimeError:
                if self.metaso_client is not None:
                    try:
                        return "metaso", self.metaso_client.search(query, max_results)
                    except RuntimeError:
                        if self.baidu_client is not None:
                            return "baidu", self.baidu_client.search(query, max_results)
                        raise
                if self.baidu_client is not None:
                    return "baidu", self.baidu_client.search(query, max_results)
                raise
        if self.metaso_client is not None:
            return "metaso", self.metaso_client.search(query, max_results)
        if self.baidu_client is not None:
            return "baidu", self.baidu_client.search(query, max_results)
        raise RuntimeError("未配置可用搜索 provider，无法执行 step 3 搜索。")

    def extract(self, urls: list[str], query: str) -> dict[str, str]:
        """对已选中的链接批量补充 extract 文本。"""
        if self.tavily_client is None:
            return {}
        return self.tavily_client.extract(urls, query)


def build_auto_search_client(runtime_config: Any, provider_mode: str = "auto") -> AutoSearchClient:
    """Build an auto-search client from whichever provider keys are actually configured."""

    timeout = runtime_config.request_timeout_seconds
    tavily_client = TavilyClient(runtime_config.tavily_api_key, timeout=timeout) if runtime_config.tavily_api_key else None
    metaso_client = MetasoClient(runtime_config.metaso_api_key, timeout=timeout) if runtime_config.metaso_api_key else None
    baidu_client = (
        BaiduSearchClient(runtime_config.baidu_api_key, timeout=timeout) if runtime_config.baidu_api_key else None
    )
    if tavily_client is None and metaso_client is None and baidu_client is None:
        raise RuntimeError("未配置任何搜索 provider key，无法执行 step 3 搜索。")
    return AutoSearchClient(
        tavily_client=tavily_client,
        metaso_client=metaso_client,
        baidu_client=baidu_client,
        provider_mode=provider_mode,
    )


def resolve_search_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    output_override: str | None = None,
    provider: str = "auto",
) -> SearchOutputPaths:
    """Resolve the canonical step 2 input and step 3 output paths for one run."""

    run_dir: Path | None = None
    if input_override:
        input_path = (paths.project_root / input_override).resolve()
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            input_path = (run_dir / step_2_checklist_name(report_date)).resolve()
        else:
            input_path = (c114_reports_root(paths.reports_dir) / step_2_checklist_name(report_date)).resolve()

    if output_override:
        output_path = (paths.project_root / output_override).resolve()
    else:
        if input_override or run_dir:
            output_path = (input_path.parent / step_3_results_name(report_date)).resolve()
        else:
            output_path = (c114_reports_root(paths.reports_dir) / step_3_results_name(report_date)).resolve()
    return SearchOutputPaths(input_path=input_path, output_path=output_path, provider=provider)


def load_search_checklist_yaml(input_path: Path) -> tuple[str, list[SearchArticleInput]]:
    """Parse the step 2 checklist YAML into search-ready article records."""

    report_date = ""
    current_topic = ""
    current_item: dict[str, Any] | None = None
    items: list[SearchArticleInput] = []

    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line.startswith("report_date: "):
            report_date = parse_yaml_value(line)
        elif line.startswith("  - topic: "):
            if current_item:
                items.append(build_article_input(current_topic, current_item))
                current_item = None
            current_topic = parse_yaml_value(line)
        elif line.startswith("      - original_title: "):
            if current_item:
                items.append(build_article_input(current_topic, current_item))
            current_item = {
                "original_title": parse_yaml_value(line),
                "channel": "",
                "url": "",
                "original_published_at": "",
                "keywords": [],
            }
        elif line.startswith("        channel: ") and current_item is not None:
            current_item["channel"] = parse_yaml_value(line)
        elif line.startswith("        url: ") and current_item is not None:
            current_item["url"] = parse_yaml_value(line)
        elif line.startswith("        original_published_at: ") and current_item is not None:
            current_item["original_published_at"] = parse_yaml_value(line)
        elif line.startswith("          - ") and current_item is not None:
            current_item["keywords"].append(parse_yaml_list_item(line))
    if current_item:
        items.append(build_article_input(current_topic, current_item))
    return report_date, items


def validate_search_checklist_items(items: list[SearchArticleInput]) -> None:
    """Reject step 2 inputs that do not yet contain two model-generated keywords."""

    incomplete = [item for item in items if len(item.keywords) != 2]
    if not incomplete:
        return
    details = "；".join(f"《{item.original_title}》当前为 {len(item.keywords)} 组" for item in incomplete[:5])
    if len(incomplete) > 5:
        details = f"{details}；其余 {len(incomplete) - 5} 条未展开"
    raise ValueError("搜索清单 YAML 中存在未补全 keywords 的条目。请先完成 step 2 自动关键词生成后再运行 c114-search。"
        f" {details}")


def ensure_search_checklist_keywords(
    items: list[SearchArticleInput],
    llm_client: MiniMaxChatClient | None,
) -> list[SearchArticleInput]:
    """Auto-complete missing step 2 keywords before step 3 starts."""

    incomplete = [item for item in items if len(item.keywords) != 2]
    if not incomplete:
        return items
    if llm_client is None:
        validate_search_checklist_items(items)
        return items

    checklist_items = [
        SearchChecklistItem(
            report_date="",
            channel_name=item.channel,
            title=item.original_title,
            topic=item.topic,
            search_queries=list(item.keywords),
            publish_date=item.original_published_at,
            url=item.original_url,
        )
        for item in items
    ]
    analyses = [
        ArticleAnalysis(
            report_date="",
            channel_key="",
            channel_name=item.channel,
            title=item.original_title,
            publish_date=item.original_published_at,
            source_keywords=[],
            normalized_keywords=[],
            entities=[],
            signals=[],
            topic=item.topic,
            core_summary="",
            followup_queries=[],
            url=item.original_url,
        )
        for item in items
    ]
    completed = autofill_search_checklist_items(checklist_items, analyses, llm_client)
    return [
        SearchArticleInput(
            topic=item.topic,
            channel=item.channel_name,
            original_title=item.title,
            original_url=item.url,
            original_published_at=item.publish_date,
            keywords=list(item.search_queries),
        )
        for item in completed
    ]


def choose_search_provider(query: SearchQuery, forced_provider: str = "auto") -> str:
    """Resolve the provider name for one query under the current routing policy."""

    if forced_provider in {"tavily", "metaso", "baidu", "google"}:
        return forced_provider
    return "tavily"


def contains_chinese(value: str) -> bool:
    return bool(CHINESE_RE.search(value))


def build_article_input(topic: str, payload: dict[str, Any]) -> SearchArticleInput:
    return SearchArticleInput(
        topic=topic,
        channel=payload["channel"],
        original_title=payload["original_title"],
        original_url=payload["url"],
        original_published_at=str(payload.get("original_published_at", "")),
        keywords=list(payload["keywords"]),
    )


def build_search_queries(article: SearchArticleInput) -> list[SearchQuery]:
    if len(article.keywords) != 2:
        raise ValueError(
            f"搜索清单中的关键词数量必须为 2，当前《{article.original_title}》为 {len(article.keywords)}。"
        )
    return [
        SearchQuery(query_type="title", value=article.original_title),
        SearchQuery(query_type="keyword", value=article.keywords[0]),
        SearchQuery(query_type="keyword", value=article.keywords[1]),
    ]


def load_domain_config(config_path: Path = SEARCH_CONFIG_PATH) -> dict[str, list[str]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        "official_domains": payload.get("official_domains", []),
        "blocked_domains": payload.get("blocked_domains", []),
    }


def classify_domain(domain: str, domain_config: dict[str, list[str]]) -> tuple[bool, str]:
    normalized = domain.lower().lstrip(".")
    is_official = domain_matches(normalized, domain_config.get("official_domains", []))
    is_blocked = domain_matches(normalized, domain_config.get("blocked_domains", []))
    if is_blocked:
        return False, "blocked"
    if is_official:
        return True, "official"
    return False, "normal"


def domain_matches(domain: str, suffixes: list[str]) -> bool:
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes)


def run_search_workflow(
    input_path: Path,
    report_date: str,
    per_query_limit: int,
    per_article_limit: int,
    extract_limit: int,
    provider_name: str = "auto",
    client: Any | None = None,
    llm_client: MiniMaxChatClient | None = None,
    generated_at: str | None = None,
) -> SearchWorkflowPayload:
    """Execute step 3 end-to-end for one report date and produce normalized results."""

    yaml_report_date, articles = load_search_checklist_yaml(input_path)
    if yaml_report_date and yaml_report_date != report_date:
        raise ValueError(f"输入搜索清单日期为 {yaml_report_date}，与命令日期 {report_date} 不一致。")
    articles = ensure_search_checklist_keywords(articles, llm_client)
    validate_search_checklist_items(articles)

    domain_config = load_domain_config()
    runtime_config = load_c114_runtime_config(Path(__file__).resolve().parents[2])
    search_client = client or build_auto_search_client(
        runtime_config=runtime_config,
        provider_mode=provider_name,
    )
    grouped: dict[str, list[ArticleSearchPayload]] = {}
    report_day = date.fromisoformat(report_date)
    article_workers = min(8, max(1, len(articles)))
    with ThreadPoolExecutor(max_workers=article_workers) as executor:
        future_pairs = [
            (
                article,
                executor.submit(
                    search_article,
                    article=article,
                    report_date=report_day,
                    per_query_limit=per_query_limit,
                    per_article_limit=per_article_limit,
                    extract_limit=extract_limit,
                    client=search_client,
                    domain_config=domain_config,
                    recent_days=runtime_config.search_recent_days,
                ),
            )
            for article in articles
        ]
        topic_payloads: dict[str, list[tuple[int, ArticleSearchPayload]]] = {}
        for index, (article, future) in enumerate(future_pairs):
            payload = future.result()
            topic_payloads.setdefault(article.topic, []).append((index, payload))
        for topic, ordered_payloads in topic_payloads.items():
            grouped[topic] = [payload for _, payload in sorted(ordered_payloads, key=lambda item: item[0])]

    categories = [
        SearchCategoryPayload(topic=topic, items=grouped[topic]) for topic in sorted(grouped, key=sort_topic_key)
    ]
    payload = SearchWorkflowPayload(
        report_date=report_date,
        provider=provider_name,
        input_path=input_path,
        generated_at=generated_at or datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )
    if llm_client is not None:
        payload = auto_review_search_payload(payload, llm_client)
    return payload


def search_article(
    article: SearchArticleInput,
    report_date: date,
    per_query_limit: int,
    per_article_limit: int,
    extract_limit: int,
    client: Any,
    domain_config: dict[str, list[str]],
    recent_days: int,
) -> ArticleSearchPayload:
    """Search one article's title and keyword queries, then rank and enrich results."""

    queries = build_search_queries(article)
    buckets: list[QueryResultBucket] = []
    raw_results: list[SearchResult] = []
    max_workers = min(3, max(1, len(queries)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_pairs = [
            (
                query,
                executor.submit(
                    search_query_bucket,
                    client,
                    query,
                    per_query_limit,
                    article.original_title,
                    domain_config,
                    report_date,
                    recent_days,
                ),
            )
            for query in queries
        ]
        bucket_map: dict[tuple[str, str], QueryResultBucket] = {}
        for query, future in future_pairs:
            bucket = future.result()
            bucket_map[(query.query_type, query.value)] = bucket
        for query in queries:
            bucket = bucket_map[(query.query_type, query.value)]
            buckets.append(bucket)
            raw_results.extend(bucket.results)

    deduped = unique_search_results(
        filter_recent_results(raw_results, report_date=report_date, max_age_days=recent_days)
    )
    ranked = rank_search_results(deduped, report_date=report_date, original_title=article.original_title)
    search_results = ranked[:per_article_limit]
    selected_results = enrich_selected_results(
        search_results=search_results,
        article=article,
        extract_limit=extract_limit,
        client=client,
    )
    return ArticleSearchPayload(
        topic=article.topic,
        channel=article.channel,
        original_title=article.original_title,
        original_url=article.original_url,
        original_published_at=article.original_published_at,
        queries=buckets,
        search_results=search_results,
        selected_results=selected_results,
    )


def search_query_bucket(
    client: Any,
    query: SearchQuery,
    per_query_limit: int,
    original_title: str,
    domain_config: dict[str, list[str]],
    report_date: date,
    recent_days: int,
) -> QueryResultBucket:
    provider_name, raw_provider_results = search_with_provider(client, query, per_query_limit)
    normalized_results = [
        result
        for result in normalize_search_results(
            raw_provider_results,
            query=query,
            original_title=original_title,
            domain_config=domain_config,
            provider_name=provider_name,
        )
        if not result_is_blocked(result, domain_config)
    ]
    recent_results = filter_recent_results(normalized_results, report_date=report_date, max_age_days=recent_days)
    return QueryResultBucket(
        query=query.value,
        query_type=query.query_type,
        provider=provider_name,
        results=recent_results,
    )


def normalize_search_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
    provider_name: str = "tavily",
) -> list[SearchResult]:
    if provider_name == "metaso":
        return normalize_metaso_results(
            raw_results, query=query, original_title=original_title, domain_config=domain_config
        )
    if provider_name == "baidu":
        return normalize_baidu_results(
            raw_results, query=query, original_title=original_title, domain_config=domain_config
        )
    if provider_name == "google":
        return normalize_google_results(
            raw_results, query=query, original_title=original_title, domain_config=domain_config
        )

    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("content") or item.get("snippet") or ""), limit=400)
        published_at = normalize_published_at(item.get("published_date") or item.get("published_at") or "")
        if not published_at:
            published_at = infer_published_at(url, title, snippet)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at=published_at,
                snippet=snippet,
                score=float(item.get("score") or 0.0),
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def normalize_metaso_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
) -> list[SearchResult]:
    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("link") or item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("snippet") or ""), limit=400)
        published_at = normalize_published_at(item.get("date") or item.get("published_at") or "")
        if not published_at:
            published_at = infer_published_at(url, title, snippet)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at=published_at,
                snippet=snippet,
                score=normalize_metaso_score(item.get("score")),
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def normalize_metaso_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
    return mapping.get(text, 0.5)


def normalize_baidu_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
) -> list[SearchResult]:
    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("snippet") or item.get("content") or ""), limit=400)
        published_at = normalize_published_at(item.get("date") or "")
        if not published_at:
            published_at = infer_published_at(url, title, snippet)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        score = float(item.get("rerank_score") or 0.0) + float(item.get("authority_score") or 0.0)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at=published_at,
                snippet=snippet,
                score=score,
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def normalize_google_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
) -> list[SearchResult]:
    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("snippet") or ""), limit=400)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at="",
                snippet=snippet,
                score=float(item.get("score") or 0.5),
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def search_with_provider(client: Any, query: SearchQuery, max_results: int) -> tuple[str, list[dict[str, Any]]]:
    if hasattr(client, "search_with_provider"):
        return client.search_with_provider(query, max_results)
    return "tavily", client.search(query, max_results)


def result_is_blocked(result: SearchResult, domain_config: dict[str, list[str]]) -> bool:
    return classify_domain(result.domain, domain_config)[1] == "blocked"


def unique_search_results(results: list[SearchResult]) -> list[SearchResult]:
    by_url: dict[str, SearchResult] = {}
    title_seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        normalized_url = normalize_url(result.url)
        normalized_title = normalize_title(result.result_title)
        if normalized_url in by_url or normalized_title in title_seen:
            continue
        by_url[normalized_url] = result
        title_seen.add(normalized_title)
        unique.append(result)
    return unique


def rank_search_results(results: list[SearchResult], report_date: date, original_title: str) -> list[SearchResult]:
    return sorted(
        results,
        key=lambda item: (
            0 if item.is_official else 1,
            -len(item.matched_terms),
            recency_bucket(item.published_at, report_date),
            0 if item.query_type == "title" else 1,
            -item.score,
            normalize_title_distance(item.result_title, original_title),
            item.domain,
        ),
    )


def filter_recent_results(results: list[SearchResult], report_date: date, max_age_days: int) -> list[SearchResult]:
    """Keep only recent candidates according to the configured recency window."""

    filtered: list[SearchResult] = []
    for result in results:
        if not result.published_at:
            continue
        try:
            published_date = date.fromisoformat(result.published_at[:10])
        except ValueError:
            continue
        age_days = (report_date - published_date).days
        if 0 <= age_days <= max_age_days:
            filtered.append(result)
    return filtered


def infer_published_at(url: str, title: str, snippet: str) -> str:
    candidates = (url, title, snippet)
    for candidate in candidates:
        text = str(candidate)
        for pattern in DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                inferred = date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            return inferred.isoformat()
    return ""


def select_results_for_extract(results: list[SearchResult], extract_limit: int) -> list[SearchResult]:
    return results[:extract_limit]


def enrich_selected_results(
    search_results: list[SearchResult],
    article: SearchArticleInput,
    extract_limit: int,
    client: Any,
) -> list[SearchResult]:
    """Optionally attach Tavily extract text for the final selected URLs."""

    selected = [replace(item) for item in search_results]
    if not selected:
        return selected
    if not hasattr(client, "extract"):
        return selected
    to_extract = select_results_for_extract(selected, extract_limit)
    extracted_map: dict[str, str] = {}
    try:
        extracted_map = client.extract([item.url for item in to_extract], query=article.original_title)
    except Exception:
        for item in to_extract:
            index = selected.index(item)
            selected[index] = replace(selected[index], extract_status="failed")
        return selected

    for index, item in enumerate(selected):
        if index >= extract_limit:
            break
        extract_text = extracted_map.get(normalize_url(item.url), "")
        selected[index] = replace(
            item,
            extract_text=extract_text,
            extract_status="success" if extract_text else "failed",
        )
    return selected


def auto_review_search_payload(payload: SearchWorkflowPayload, llm_client: MiniMaxChatClient) -> SearchWorkflowPayload:
    """Fill every selected_results.ai_review via the fixed MiniMax model."""

    begin_llm_step(llm_client, "step_3")
    system_prompt = load_prompt_text(SEARCH_REVIEW_PROMPT_PATH)
    reviewed_categories: list[SearchCategoryPayload] = []
    for category in payload.categories:
        reviewed_items: list[ArticleSearchPayload] = []
        for item in category.items:
            def review_one(result: SearchResult, item_snapshot: ArticleSearchPayload = item) -> SearchResult:
                response = llm_client.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=(
                        "请审查下面这条搜索补充结果，只返回 JSON 对象。\n"
                        "格式："
                        "{\"keep_level\":\"strong|weak|drop\",\"reason\":\"...\",\"relevance_note\":\"...\",\"value_type\":\"...\"}\n\n"
                        f"{json.dumps(build_search_review_prompt_payload(item_snapshot, result), ensure_ascii=False, indent=2)}"
                    ),
                )
                return apply_search_review_result(result, response)

            reviewed_results = run_parallel_ordered(item.selected_results, review_one)
            reviewed_items.append(
                ArticleSearchPayload(
                    topic=item.topic,
                    channel=item.channel,
                    original_title=item.original_title,
                    original_url=item.original_url,
                    original_published_at=item.original_published_at,
                    queries=item.queries,
                    search_results=item.search_results,
                    selected_results=reviewed_results,
                )
            )
        reviewed_categories.append(SearchCategoryPayload(topic=category.topic, items=reviewed_items))
    return SearchWorkflowPayload(
        report_date=payload.report_date,
        provider=payload.provider,
        input_path=payload.input_path,
        generated_at=payload.generated_at,
        categories=reviewed_categories,
    )


def build_search_review_prompt_payload(item: ArticleSearchPayload, result: SearchResult) -> dict[str, Any]:
    return {
        "topic": item.topic,
        "channel": item.channel,
        "original_title": item.original_title,
        "original_url": item.original_url,
        "original_published_at": item.original_published_at,
        "result": {
            "query": result.query,
            "query_type": result.query_type,
            "result_title": result.result_title,
            "url": result.url,
            "domain": result.domain,
            "published_at": result.published_at,
            "snippet": result.snippet,
            "score": result.score,
            "is_official": result.is_official,
            "source_tier": result.source_tier,
            "matched_terms": result.matched_terms,
            "extract_status": result.extract_status,
            "extract_text": result.extract_text,
        },
    }


def apply_search_review_result(result: SearchResult, payload: Any) -> SearchResult:
    if not isinstance(payload, dict):
        raise StructuredLLMError("step 3 审查结果不是 JSON 对象。")
    keep_level = str(payload.get("keep_level", "")).strip()
    if keep_level not in {"strong", "weak", "drop"}:
        raise StructuredLLMError(f"step 3 返回了非法 keep_level：{keep_level}")
    reason = str(payload.get("reason", "")).strip()
    relevance_note = str(payload.get("relevance_note", "")).strip()
    value_type = str(payload.get("value_type", "")).strip()
    if not (reason and relevance_note and value_type):
        raise StructuredLLMError("step 3 审查结果缺少 reason、relevance_note 或 value_type。")
    return replace(
        result,
        review_status="reviewed",
        keep_level=keep_level,
        review_reason=reason,
        relevance_note=relevance_note,
        value_type=value_type,
    )


def render_search_results_yaml(payload: SearchWorkflowPayload) -> str:
    """Render the full step 3 YAML contract, including pending AI review fields."""

    lines = [
        f"report_date: '{payload.report_date}'",
        f"provider: '{escape_yaml_scalar(payload.provider)}'",
        f"input_path: '{escape_yaml_scalar(str(payload.input_path))}'",
        f"review_prompt_path: '{escape_yaml_scalar(str(SEARCH_REVIEW_PROMPT_PATH))}'",
        "review_instructions:",
        "  - 'skill 内置模型会读取 review_prompt_path 指向的提示词文件。'",
        "  - '仅对 selected_results 下各条结果生成 ai_review 字段。'",
        "  - '必须逐条填写所有 selected_results；不得留空、不得跳过、不得只填一部分。'",
        "  - 'ai_review.status 固定填写 reviewed。'",
        "  - 'ai_review.keep_level 只能填写 strong、weak、drop 三档。'",
        "  - '不得改写原标题、原链接、搜索结果元数据。'",
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
                    f"        channel: '{escape_yaml_scalar(item.channel)}'",
                    f"        original_url: '{escape_yaml_scalar(item.original_url)}'",
                    f"        original_published_at: '{escape_yaml_scalar(item.original_published_at)}'",
                    "        queries:",
                ]
            )
            for query in item.queries:
                lines.extend(
                    [
                        f"          - query: '{escape_yaml_scalar(query.query)}'",
                        f"            query_type: '{escape_yaml_scalar(query.query_type)}'",
                        f"            provider: '{escape_yaml_scalar(query.provider)}'",
                        "            results:",
                    ]
                )
                lines.extend(render_result_list(query.results, indent="              "))
            lines.append("        search_results:")
            lines.extend(render_result_list(item.search_results, indent="          "))
            lines.append("        selected_results:")
            lines.extend(render_result_list(item.selected_results, indent="          ", include_ai_review=True))
    return "\n".join(lines)


def render_result_list(results: list[SearchResult], indent: str, *, include_ai_review: bool = False) -> list[str]:
    lines: list[str] = []
    for result in results:
        lines.extend(
            [
                f"{indent}- query: '{escape_yaml_scalar(result.query)}'",
                f"{indent}  query_type: '{escape_yaml_scalar(result.query_type)}'",
                f"{indent}  result_title: '{escape_yaml_scalar(result.result_title)}'",
                f"{indent}  url: '{escape_yaml_scalar(result.url)}'",
                f"{indent}  domain: '{escape_yaml_scalar(result.domain)}'",
                f"{indent}  published_at: '{escape_yaml_scalar(result.published_at)}'",
                f"{indent}  snippet: '{escape_yaml_scalar(result.snippet)}'",
                f"{indent}  score: {result.score:.4f}",
                f"{indent}  is_official: {'true' if result.is_official else 'false'}",
                f"{indent}  source_tier: '{escape_yaml_scalar(result.source_tier)}'",
                f"{indent}  extract_status: '{escape_yaml_scalar(result.extract_status)}'",
                f"{indent}  extract_text: '{escape_yaml_scalar(result.extract_text)}'",
                f"{indent}  matched_terms:",
            ]
        )
        for term in result.matched_terms:
            lines.append(f"{indent}    - '{escape_yaml_scalar(term)}'")
        if include_ai_review:
            lines.extend(
                [
                    f"{indent}  ai_review:",
                    f"{indent}    status: '{escape_yaml_scalar(result.review_status)}'",
                    f"{indent}    keep_level: '{escape_yaml_scalar(result.keep_level)}'",
                    f"{indent}    reason: '{escape_yaml_scalar(result.review_reason)}'",
                    f"{indent}    relevance_note: '{escape_yaml_scalar(result.relevance_note)}'",
                    f"{indent}    value_type: '{escape_yaml_scalar(result.value_type)}'",
                ]
            )
    return lines


def save_search_results(output_path: Path, payload: SearchWorkflowPayload) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_search_results_yaml(payload), encoding="utf-8")
    stats_path = output_path.parent / provider_stats_name(date.fromisoformat(payload.report_date))
    stats_path.write_text(render_provider_usage_stats(payload), encoding="utf-8")
    review_view_path = output_path.parent / build_review_view_name(payload.report_date)
    review_view_path.write_text(render_search_review_view(payload), encoding="utf-8")


def build_review_view_name(report_date: str) -> str:
    return f"c114_search_review_view_{report_date.replace('-', '')}.yaml"


def render_search_review_view(payload: SearchWorkflowPayload) -> str:
    """Render a compact step 3 review view grouped by keep-level decisions."""

    counts = {"strong": 0, "weak": 0, "drop": 0, "pending": 0}
    lines = [
        f"report_date: '{payload.report_date}'",
        f"input_path: '{escape_yaml_scalar(str(payload.input_path))}'",
        "categories:",
    ]
    for category in payload.categories:
        lines.append(f"  - topic: '{escape_yaml_scalar(category.topic)}'")
        lines.append("    items:")
        for item in category.items:
            grouped = {"strong": [], "weak": [], "drop": [], "pending": []}
            for result in item.selected_results:
                level = result.keep_level if result.keep_level in {"strong", "weak", "drop"} else "pending"
                counts[level] += 1
                grouped[level].append(result)
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml_scalar(item.original_title)}'",
                    f"        original_url: '{escape_yaml_scalar(item.original_url)}'",
                    "        review_groups:",
                ]
            )
            for level in ("strong", "weak", "drop", "pending"):
                lines.append(f"          {level}:")
                for result in grouped[level]:
                    lines.extend(
                        [
                            f"            - title: '{escape_yaml_scalar(result.result_title)}'",
                            f"              url: '{escape_yaml_scalar(result.url)}'",
                            f"              reason: '{escape_yaml_scalar(result.review_reason)}'",
                            f"              value_type: '{escape_yaml_scalar(result.value_type)}'",
                        ]
                    )
    lines.extend(
        [
            "summary:",
            f"  strong: {counts['strong']}",
            f"  weak: {counts['weak']}",
            f"  drop: {counts['drop']}",
            f"  pending: {counts['pending']}",
        ]
    )
    return "\n".join(lines)


def render_provider_usage_stats(payload: SearchWorkflowPayload) -> str:
    """Summarize provider usage so expensive search calls remain observable."""

    query_provider_counts: dict[str, int] = {}
    total_queries = 0
    selected_articles = 0
    selected_results_total = 0
    extract_success = 0
    extract_failed = 0

    for category in payload.categories:
        for item in category.items:
            for query in item.queries:
                total_queries += 1
                query_provider_counts[query.provider] = query_provider_counts.get(query.provider, 0) + 1
            if item.selected_results:
                selected_articles += 1
            selected_results_total += len(item.selected_results)
            for result in item.selected_results:
                if result.extract_status == "success":
                    extract_success += 1
                elif result.extract_status == "failed":
                    extract_failed += 1

    extract_calls = selected_articles
    total_calls = sum(query_provider_counts.values()) + extract_calls
    lines = [
        f"# C114 {payload.report_date} Provider 用量统计",
        "",
        f"- 搜索模式：`{payload.provider}`",
        f"- 输入清单：`{payload.input_path}`",
        f"- query 总数：`{total_queries}`",
        f"- provider 总调用次数：`{total_calls}`",
        "",
        "## Query Search（搜索）调用",
        "",
    ]
    for provider_name in ("tavily", "baidu", "metaso", "google"):
        count = query_provider_counts.get(provider_name, 0)
        lines.append(f"- {provider_name.title()}: `{count}`")
    lines.extend(
        [
            "",
            "## Tavily Extract（补充正文摘要）调用",
            "",
            f"- Tavily extract 调用次数：`{extract_calls}`",
            f"- extract success（成功 URL 数）：`{extract_success}`",
            f"- extract failed（失败 URL 数）：`{extract_failed}`",
            "",
            "## 说明",
            "",
            "- `Query Search` 按每个 query 实际落到的 provider 统计。",
            "- `Tavily Extract` 目前统一由 Tavily 执行，每篇有 `selected_results` 的文章触发一次。",
            "- `provider 总调用次数 = Query Search 总调用 + Tavily extract 调用`。",
        ]
    )
    return "\n".join(lines)


def parse_yaml_value(line: str) -> str:
    value = line.split(":", 1)[1].strip()
    return unquote_yaml_scalar(value)


def parse_yaml_list_item(line: str) -> str:
    return unquote_yaml_scalar(line.split("- ", 1)[1].strip())


def unquote_yaml_scalar(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("'") and stripped.endswith("'"):
        return stripped[1:-1].replace("''", "'")
    return stripped


def escape_yaml_scalar(value: str) -> str:
    return value.replace("'", "''")


def extract_domain(url: str) -> str:
    return urlsplit(url).netloc.lower()


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def normalize_title(title: str) -> str:
    return compact_text(title, limit=200).lower()


def normalize_published_at(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    candidates = [text, text[:19], text[:10]]
    for candidate in candidates:
        for parser in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(candidate, parser).strftime("%Y-%m-%d")
            except ValueError:
                continue
    if len(text) >= 10:
        return text[:10]
    return text


def recency_bucket(published_at: str, report_date: date) -> int:
    if not published_at:
        return 2
    try:
        delta = abs((report_date - date.fromisoformat(published_at[:10])).days)
    except ValueError:
        return 2
    return 0 if delta <= 3 else 1


def tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(value):
        cleaned = token.strip()
        if len(cleaned) < 2:
            continue
        if cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def compute_matched_terms(original_title: str, query: str, result_title: str, snippet: str) -> list[str]:
    text = f"{result_title} {snippet}"
    candidates = tokenize(original_title) + tokenize(query)
    matched: list[str] = []
    for token in candidates:
        if token in text and token not in matched:
            matched.append(token)
    return matched[:8]


def normalize_title_distance(result_title: str, original_title: str) -> int:
    return abs(len(result_title) - len(original_title))


def compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit].strip()


def sort_topic_key(topic: str) -> tuple[int, str]:
    priority = {
        "AI与算力": 0,
        "卫星互联网与商业航天": 1,
        "量子技术": 2,
        "6G与下一代通信": 3,
        "低空经济": 4,
    }
    return (priority.get(topic, 99), topic)
