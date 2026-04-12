from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from .models import RawArticleDetail, RawArticleRef, StandardArticle


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
        """默认的无状态拉取闭环：列表 -> 详情 -> 标准文章。"""

        refs = self.fetch_listing(report_date)
        return [self.normalize_article(self.fetch_article(ref)) for ref in refs]

    def default_source_config(self) -> dict[str, object]:
        """返回当前站点的默认抓取配置。"""

        return {}

    def derive_source_bucket(self, raw: RawArticleDetail) -> str:
        """派生站点来源桶。"""

        return raw.source_bucket or raw.channel or ""

    def derive_channel(self, raw: RawArticleDetail) -> str:
        """派生站点频道。"""

        return raw.channel or raw.source_bucket or ""
