from __future__ import annotations

from collections import Counter
from datetime import date
from typing import TYPE_CHECKING

from .base import ContentSourceAdapter
from .models import RawArticleDetail, RawArticleRef, StandardArticle

if TYPE_CHECKING:
    from c114.c114_hot_topics import ChannelDailyReport


class C114SourceAdapter(ContentSourceAdapter):
    """C114 站点适配器。"""

    source_site = "c114"

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        from c114.c114_hot_topics import collect_daily_report

        reports = collect_daily_report(report_date=report_date)
        refs: list[RawArticleRef] = []
        for article in self.standard_articles_from_reports(report_date, reports):
            refs.append(
                RawArticleRef(
                    source_site=self.source_site,
                    article_id=article.article_id,
                    title=article.title,
                    url=article.url,
                    published_at=article.published_at,
                    channel=article.channel,
                    source_bucket=article.source_bucket,
                    summary=article.summary,
                    metadata=dict(article.metadata),
                )
            )
        return refs

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        keywords = [str(item) for item in ref.metadata.get("keywords") or []]
        tags = [str(item) for item in ref.metadata.get("tags") or []]
        return RawArticleDetail(
            source_site=self.source_site,
            article_id=ref.article_id,
            title=ref.title,
            url=ref.url,
            published_at=ref.published_at,
            author=str(ref.metadata.get("author", "")),
            channel=ref.channel,
            source_bucket=ref.source_bucket,
            tags=tags,
            summary=ref.summary,
            content_text=str(ref.metadata.get("content_text", "")),
            metadata={**ref.metadata, "keywords": keywords},
        )

    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle:
        keywords = [str(item) for item in raw.metadata.get("keywords") or []]
        return StandardArticle(
            source_site=self.source_site,
            source_bucket=raw.source_bucket,
            channel=raw.channel,
            article_id=raw.article_id,
            title=raw.title,
            url=raw.url,
            published_at=raw.published_at,
            author=raw.author,
            tags=list(raw.tags),
            keywords=keywords,
            summary=raw.summary,
            content_text=raw.content_text,
            metadata=dict(raw.metadata),
        )

    def standard_articles_from_reports(
        self,
        report_date: date,
        reports: list["ChannelDailyReport"],
    ) -> list[StandardArticle]:
        articles: list[StandardArticle] = []
        for report in reports:
            hot_tags = [keyword for keyword, _ in report.hot_topics]
            for article in report.articles:
                published_at = article.publish_date.isoformat() if article.publish_date else report_date.isoformat()
                metadata = {
                    "channel_key": report.channel_key,
                    "channel_name": report.channel_name,
                    "channel_url": report.channel_url,
                    "keywords": list(article.keywords),
                    "tags": list(hot_tags),
                    "article_count": report.article_count,
                }
                raw = RawArticleDetail(
                    source_site=self.source_site,
                    article_id=f"{self.source_site}:{report.channel_key}:{article.url}",
                    title=article.title,
                    url=article.url,
                    published_at=published_at,
                    author="",
                    channel=report.channel_name,
                    source_bucket=report.channel_name,
                    tags=list(hot_tags),
                    summary=article.summary,
                    content_text="",
                    metadata=metadata,
                )
                articles.append(self.normalize_article(raw))
        return articles

    def default_source_config(self) -> dict[str, object]:
        return {
            "channels": ["home", "quantum", "satellite", "la", "ai"],
            "candidate_limit": 30,
            "timeout": 20.0,
        }


def summarize_keywords_for_c114_articles(articles: list[StandardArticle], limit: int = 10) -> list[tuple[str, int]]:
    """为 C114 标准文章生成热点词概览。"""

    counter: Counter[str] = Counter()
    for article in articles:
        for token in article.keywords or article.tags:
            cleaned = str(token).strip()
            if cleaned:
                counter[cleaned] += 1
    return counter.most_common(limit)
