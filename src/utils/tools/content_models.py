from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


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


class ContentSourceAdapter(ABC):
    """站点适配器公共接口。"""

    source_site: str

    @abstractmethod
    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        """抓取某一天的候选文章列表。"""

    @abstractmethod
    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        """抓取单篇文章详情。"""

    @abstractmethod
    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle:
        """把站点详情归一化为统一文章结构。"""

    def fetch_standard_articles(self, report_date: date) -> list[StandardArticle]:
        refs = self.fetch_listing(report_date)
        return [self.normalize_article(self.fetch_article(ref)) for ref in refs]

    def default_source_config(self) -> dict[str, object]:
        return {}

    def derive_source_bucket(self, raw: RawArticleDetail) -> str:
        return raw.source_bucket or raw.channel or ""

    def derive_channel(self, raw: RawArticleDetail) -> str:
        return raw.channel or raw.source_bucket or ""
