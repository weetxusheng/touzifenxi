"""C114 step 3 搜索流程使用的类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SearchArticleInput:
    topic: str
    channel: str
    original_title: str
    original_url: str
    original_published_at: str
    keywords: list[str]


@dataclass(frozen=True)
class SearchQuery:
    query_type: str
    value: str


@dataclass(frozen=True)
class SearchResult:
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
    query: str
    query_type: str
    provider: str
    results: list[SearchResult]


@dataclass(frozen=True)
class ArticleSearchPayload:
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
    topic: str
    items: list[ArticleSearchPayload]


@dataclass(frozen=True)
class SearchWorkflowPayload:
    report_date: str
    provider: str
    input_path: Path
    generated_at: str
    categories: list[SearchCategoryPayload]


@dataclass(frozen=True)
class SearchOutputPaths:
    input_path: Path
    output_path: Path
    provider: str

    @staticmethod
    def require_api_key(project_root: Path | None = None) -> str:
        from c114.runtime.config import load_c114_runtime_config

        root = project_root or Path(__file__).resolve().parents[4]
        api_key = load_c114_runtime_config(root).tavily_api_key
        if not api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY，无法执行 C114 搜索层。")
        return api_key

    @staticmethod
    def require_metaso_api_key(project_root: Path | None = None) -> str:
        from c114.runtime.config import load_c114_runtime_config

        root = project_root or Path(__file__).resolve().parents[4]
        api_key = load_c114_runtime_config(root).metaso_api_key
        if not api_key:
            raise RuntimeError("未配置 METASO_API_KEY，无法执行中文搜索层。")
        return api_key

    @staticmethod
    def require_baidu_api_key(project_root: Path | None = None) -> str:
        from c114.runtime.config import load_c114_runtime_config

        root = project_root or Path(__file__).resolve().parents[4]
        api_key = load_c114_runtime_config(root).baidu_api_key
        if not api_key:
            raise RuntimeError("未配置 BAIDU_API_KEY，无法执行百度搜索层。")
        return api_key
