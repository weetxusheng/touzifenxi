"""C114 step 3 搜索 provider 与自动路由。"""

from __future__ import annotations

import json
import os
import socket
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .types import SearchQuery
from .utils import compact_text, normalize_url


def _urlopen_with_tls(request: Request, timeout: float) -> Any:
    """Open URL with certifi CA bundle when available, fallback to system default."""

    if str(os.getenv("TOUZIFENXI_INSECURE_SSL", "")).strip().lower() in {"1", "true", "yes", "on"}:
        insecure_context = ssl.create_default_context()
        insecure_context.check_hostname = False
        insecure_context.verify_mode = ssl.CERT_NONE
        return urlopen(request, timeout=timeout, context=insecure_context)

    try:
        import certifi  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return urlopen(request, timeout=timeout)
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    return urlopen(request, timeout=timeout, context=ssl_context)


class TavilyClient:
    def __init__(self, api_key: str, timeout: float = 20.0, base_url: str = "https://api.tavily.com") -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
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
            with _urlopen_with_tls(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Tavily 请求失败: {error.code} {detail}") from error
        except ssl.SSLError as error:  # pragma: no cover
            raise RuntimeError(f"Tavily SSL 证书校验失败: {error}") from error
        except URLError as error:  # pragma: no cover
            raise RuntimeError(f"Tavily 请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover
            raise RuntimeError("Tavily 请求超时。") from error


class MetasoClient:
    def __init__(self, api_key: str, timeout: float = 20.0, base_url: str = "https://metaso.cn/api/v1") -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
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
            with _urlopen_with_tls(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Metaso 请求失败: {error.code} {detail}") from error
        except ssl.SSLError as error:  # pragma: no cover
            raise RuntimeError(f"Metaso SSL 证书校验失败: {error}") from error
        except URLError as error:  # pragma: no cover
            raise RuntimeError(f"Metaso 请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover
            raise RuntimeError("Metaso 请求超时。") from error


class BaiduSearchClient:
    def __init__(
        self,
        api_key: str,
        timeout: float = 20.0,
        base_url: str = "https://qianfan.baidubce.com/v2/ai_search/web_search",
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = base_url

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        payload = {
            "messages": [{"content": query.value, "role": "user"}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": max_results}],
            "search_recency_filter": "year",
        }
        response = self._post_json(payload)
        return response.get("references", [])

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            with _urlopen_with_tls(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:  # pragma: no cover
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"百度搜索请求失败: {error.code} {detail}") from error
        except ssl.SSLError as error:  # pragma: no cover
            raise RuntimeError(f"百度搜索 SSL 证书校验失败: {error}") from error
        except URLError as error:  # pragma: no cover
            raise RuntimeError(f"百度搜索请求失败: {error.reason}") from error
        except socket.timeout as error:  # pragma: no cover
            raise RuntimeError("百度搜索请求超时。") from error


class GooglePlaywrightClient:
    def __init__(self, timeout: float = 20.0, headless: bool = True) -> None:
        self.timeout = timeout
        self.headless = headless

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as error:  # pragma: no cover
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
        except PlaywrightTimeoutError as error:  # pragma: no cover
            raise RuntimeError("Google Playwright 搜索超时。") from error
        except Exception as error:  # pragma: no cover
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
    def __init__(
        self,
        tavily_client: TavilyClient | None,
        metaso_client: MetasoClient | None,
        baidu_client: BaiduSearchClient | None,
        google_client: GooglePlaywrightClient | None = None,
        provider_mode: str = "auto",
        aliyun_iqs_client: Any | None = None,
    ) -> None:
        self.tavily_client = tavily_client
        self.metaso_client = metaso_client
        self.baidu_client = baidu_client
        self.google_client = google_client or GooglePlaywrightClient()
        self.provider_mode = provider_mode
        self.aliyun_iqs_client = aliyun_iqs_client

    def search(self, query: SearchQuery, max_results: int) -> list[dict[str, Any]]:
        _, results = self.search_with_provider(query, max_results)
        return results

    def _try_aliyun_iqs_search(self, query: SearchQuery, max_results: int) -> tuple[str, list[dict[str, Any]]] | None:
        """将阿里云 IQS unified 结果转成与 Tavily 默认归一化分支兼容的 dict 列表。"""
        if self.aliyun_iqs_client is None:
            return None
        documents = self.aliyun_iqs_client.search(query.value)
        normalized: list[dict[str, Any]] = []
        for doc in documents[:max_results]:
            url = str(getattr(doc, "link", "") or "").strip()
            title = compact_text(str(getattr(doc, "title", "") or ""), limit=160)
            if not url or not title:
                continue
            body = str(getattr(doc, "main_text", "") or "")
            rich = str(getattr(doc, "rich_main_body", "") or "")
            snippet = compact_text(body or rich, limit=400)
            published_at = str(getattr(doc, "published_at", "") or "")
            normalized.append(
                {
                    "url": url,
                    "title": title,
                    "snippet": snippet,
                    "published_at": published_at,
                    "score": 0.5,
                }
            )
        return ("aliyun_iqs", normalized) if normalized else None

    def _raise_no_search_provider(self, query: SearchQuery, max_results: int) -> tuple[str, list[dict[str, Any]]]:
        pair = self._try_aliyun_iqs_search(query, max_results)
        if pair is not None:
            return pair
        raise RuntimeError("未配置可用搜索 provider，无法执行 step 3 搜索。")

    def search_with_provider(self, query: SearchQuery, max_results: int) -> tuple[str, list[dict[str, Any]]]:
        provider = choose_search_provider(query, forced_provider=self.provider_mode)
        if provider == "auto":
            pair = self._try_aliyun_iqs_search(query, max_results)
            if pair is not None:
                return pair
        if provider == "aliyun_iqs":
            pair = self._try_aliyun_iqs_search(query, max_results)
            if pair is None:
                raise RuntimeError("未配置 aliyun_iqs_api_key 或 IQS 无结果，无法执行 step 3 搜索。")
            return pair
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
                    pair = self._try_aliyun_iqs_search(query, max_results)
                    if pair is not None:
                        return pair
                    raise
            if self.metaso_client is not None:
                return "metaso", self.metaso_client.search(query, max_results)
            if self.tavily_client is not None:
                return "tavily", self.tavily_client.search(query, max_results)
            return self._raise_no_search_provider(query, max_results)
        if provider == "metaso":
            if self.metaso_client is not None:
                return "metaso", self.metaso_client.search(query, max_results)
            if self.baidu_client is not None:
                return "baidu", self.baidu_client.search(query, max_results)
            if self.tavily_client is not None:
                return "tavily", self.tavily_client.search(query, max_results)
            return self._raise_no_search_provider(query, max_results)
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
                        pair = self._try_aliyun_iqs_search(query, max_results)
                        if pair is not None:
                            return pair
                        raise
                if self.baidu_client is not None:
                    return "baidu", self.baidu_client.search(query, max_results)
                pair = self._try_aliyun_iqs_search(query, max_results)
                if pair is not None:
                    return pair
                raise
        if self.metaso_client is not None:
            return "metaso", self.metaso_client.search(query, max_results)
        if self.baidu_client is not None:
            return "baidu", self.baidu_client.search(query, max_results)
        return self._raise_no_search_provider(query, max_results)

    def extract(self, urls: list[str], query: str) -> dict[str, str]:
        if self.tavily_client is None:
            return {}
        return self.tavily_client.extract(urls, query)


def _build_aliyun_iqs_client_from_runtime(runtime_config: Any) -> Any | None:
    key = str(getattr(runtime_config, "aliyun_iqs_api_key", "") or "").strip()
    if not key:
        return None
    from utils.tools.content.fetch import AliyunIQSClient

    return AliyunIQSClient(
        api_key=key,
        timeout=float(getattr(runtime_config, "aliyun_timeout_seconds", 45.0)),
        max_retries=int(getattr(runtime_config, "aliyun_max_retries", 2)),
        retry_backoff_seconds=float(getattr(runtime_config, "aliyun_retry_backoff_seconds", 0.5)),
    )


def build_auto_search_client(runtime_config: Any, provider_mode: str = "auto") -> AutoSearchClient:
    timeout = runtime_config.request_timeout_seconds
    tavily_client = TavilyClient(runtime_config.tavily_api_key, timeout=timeout) if runtime_config.tavily_api_key else None
    metaso_client = MetasoClient(runtime_config.metaso_api_key, timeout=timeout) if runtime_config.metaso_api_key else None
    baidu_client = BaiduSearchClient(runtime_config.baidu_api_key, timeout=timeout) if runtime_config.baidu_api_key else None
    aliyun_client = _build_aliyun_iqs_client_from_runtime(runtime_config)
    if tavily_client is None and metaso_client is None and baidu_client is None and aliyun_client is None:
        raise RuntimeError(
            "未配置任何 Step 3 搜索能力：请至少配置 tavily / metaso / baidu 之一，或配置 aliyun_iqs_api_key 使用阿里云 IQS 统一搜索。"
        )
    return AutoSearchClient(
        tavily_client=tavily_client,
        metaso_client=metaso_client,
        baidu_client=baidu_client,
        provider_mode=provider_mode,
        aliyun_iqs_client=aliyun_client,
    )


def choose_search_provider(query: SearchQuery, forced_provider: str = "auto") -> str:
    """``auto`` 在 ``AutoSearchClient`` 内优先走阿里云 IQS（有 key 且能出结果时），否则回退 Tavily/Metaso/Baidu 链。"""
    forced = str(forced_provider or "auto").strip().lower()
    if forced in {"tavily", "metaso", "baidu", "google", "aliyun_iqs"}:
        return forced
    if forced in {"", "auto"}:
        return "auto"
    return "tavily"


def search_with_provider(client: Any, query: SearchQuery, max_results: int) -> tuple[str, list[dict[str, Any]]]:
    if hasattr(client, "search_with_provider"):
        return client.search_with_provider(query, max_results)
    return "tavily", client.search(query, max_results)
