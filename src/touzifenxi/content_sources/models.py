from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawArticleRef:
    """站点列表页上的文章引用。"""

    source_site: str
    article_id: str
    title: str
    url: str
    published_at: str = ""
    channel: str = ""
    source_bucket: str = ""
    summary: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RawArticleDetail:
    """站点详情页或详情接口的原始文章。"""

    source_site: str
    article_id: str
    title: str
    url: str
    published_at: str = ""
    author: str = ""
    channel: str = ""
    source_bucket: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    content_text: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class StandardArticle:
    """进入共享分析流水线前的标准文章结构。"""

    source_site: str
    source_bucket: str
    channel: str
    article_id: str
    title: str
    url: str
    published_at: str
    author: str
    tags: list[str]
    keywords: list[str]
    summary: str
    content_text: str
    metadata: dict[str, object] = field(default_factory=dict)
