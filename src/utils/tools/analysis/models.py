"""Step 5/6 正文分析共享数据模型。

这里放跨正文分析、简报生成和问题汇总共享的稳定结构。
模块不调用 LLM、不读写文件，避免数据契约和执行流程耦合。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
C114_ROOT = PROJECT_ROOT / "src" / "c114"
CONTENT_ANALYSIS_PROMPT_PATH = C114_ROOT / "prompts" / "content-analysis-agent.md"
BRIEF_PROMPT_PATH = C114_ROOT / "prompts" / "brief-agent.md"
REQUIRED_ANALYSIS_LIST_FIELDS = ("core_points",)
OPTIONAL_ANALYSIS_LIST_FIELDS = (
    "new_facts",
    "entities",
    "signals",
    "risk_or_uncertainty",
    "layer_notes",
)
STEP5_TOPIC_BATCH_ITEM_LIMIT = 4


@dataclass(frozen=True)
class ContentDocument:
    """表示一份已抓取的正文文档。"""
    url: str
    domain: str
    title: str
    summary: str
    text: str
    source: str
    status: str
    error: str


@dataclass(frozen=True)
class SelectedDocument:
    """表示一条补充链接及其对应的正文文档。"""
    query: str
    query_type: str
    url: str
    domain: str
    result_title: str
    published_at: str
    document: ContentDocument


@dataclass(frozen=True)
class ContentAnalysisDraft:
    """表示 step 5 单篇文章的分析草稿。"""
    summary: str
    core_points: list[str]
    new_facts: list[str]
    entities: list[str]
    signals: list[str]
    risk_or_uncertainty: list[str]
    why_it_matters: str
    layer_notes: list[str]


@dataclass(frozen=True)
class ContentAnalysisItem:
    """表示 step 5 中单篇文章的完整分析单元。"""
    original_title: str
    topic: str
    channel: str
    original_url: str
    original_published_at: str
    original_content: ContentDocument
    selected_contents: list[SelectedDocument]
    analysis: ContentAnalysisDraft


@dataclass(frozen=True)
class ContentAnalysisSection:
    """表示按主题聚合后的 step 5 分析分组。"""
    topic: str
    items: list[ContentAnalysisItem]


@dataclass(frozen=True)
class ContentAnalysisInput:
    """表示 step 5/6 使用的整体输入载荷。"""
    report_date: str
    input_path: Path
    generated_at: str
    categories: list[ContentAnalysisSection]
    # 可选：按原文 URL 索引的专题子项全文/转写摘录（来自 36kr kr36_topic_fulltext JSON）。
    # step5 会并入每条的 topic_fulltext_excerpt 供分析；step6 简报提示也会使用（构建时可再截断）。
    topic_fulltext_excerpts_by_url: dict[str, str] | None = None


@dataclass(frozen=True)
class ContentAnalysisOutputPaths:
    """表示 step 5、step 6 与问题汇总文件的输出路径。"""
    input_path: Path
    analysis_output: Path
    brief_output: Path
    issues_output: Path


@dataclass(frozen=True)
class BriefSectionDraft:
    """表示 step 6 某个主题生成后的章节草稿。"""
    topic: str
    core_judgment: str
    incremental_info: str
    industry_impact: str
    followups: list[str]
@dataclass(frozen=True)
class RawArticleRecord:
    """表示从原始 CSV 中读取的一条文章记录。"""
    report_date: str
    channel_key: str
    channel_name: str
    channel_hot_topics: list[str]
    title: str
    publish_date: str
    keywords: list[str]
    summary: str
    url: str


@dataclass(frozen=True)
class ArticleAnalysis:
    """表示单篇文章在 step 1 的结构化分析结果。"""
    report_date: str
    channel_key: str
    channel_name: str
    title: str
    publish_date: str
    source_keywords: list[str]
    normalized_keywords: list[str]
    entities: list[str]
    signals: list[str]
    topic: str
    core_summary: str
    followup_queries: list[str]
    url: str


@dataclass(frozen=True)
class TopicBrief:
    """表示按主题聚合后的简要概览。"""
    topic: str
    article_count: int
    channels: list[str]
    keywords: list[str]
    entities: list[str]
    signals: list[str]
    summary: str
    followup_queries: list[str]
    titles: list[str]


@dataclass(frozen=True)
class SearchChecklistItem:
    """表示 step 2 中一篇文章对应的一条检索清单项。"""
    report_date: str
    channel_name: str
    title: str
    topic: str
    search_queries: list[str]
    publish_date: str
    url: str


@dataclass(frozen=True)
class SearchChecklistSection:
    """表示 step 2 中按主题分组后的检索清单。"""
    topic: str
    items: list[SearchChecklistItem]


@dataclass(frozen=True)
class AnalysisOutputPaths:
    """表示 step 1/2 输出文件的路径集合。"""
    input_path: Path
    analysis_output: Path
    checklist_output: Path


TOPIC_GROUPING_CHECKPOINT_ENTRY_ID = "batch::all_articles"
