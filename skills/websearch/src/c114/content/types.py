"""Step 4 正文抓取数据模型。

这些结构是 step 3 搜索结果进入 step 5 分析前的标准中间形态。
模块不负责网络请求和 YAML 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..search.workflow import SearchResult


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
