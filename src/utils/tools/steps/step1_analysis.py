"""Step 1 单篇文章结构化分析编排。

本模块负责把原始 CSV 文章转成结构化 ArticleAnalysis，并汇总成 topic brief。
它只使用本地规则，不调用 LLM；模型参与的二次 topic grouping 放在 step1_5。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..analysis.csv_io import load_daily_articles_from_csv
from ..analysis.models import ArticleAnalysis, RawArticleRecord, TopicBrief
from ..analysis.normalization import (
    build_core_summary,
    build_followup_queries,
    classify_signals,
    extract_entities,
    infer_topic,
    normalize_keywords,
)


def analyze_article(article: RawArticleRecord) -> ArticleAnalysis:
    """Build a structured article analysis from one raw record."""

    normalized = normalize_keywords(article.keywords, article.title, article.summary)
    topic = infer_topic(
        normalized,
        article.channel_name,
        article.title,
        article.summary,
        url=article.url,
    )
    entities = extract_entities(normalized, article.title, article.summary)
    signals = classify_signals(article.title, article.summary)
    return ArticleAnalysis(
        report_date=article.report_date,
        channel_key=article.channel_key,
        channel_name=article.channel_name,
        title=article.title,
        publish_date=article.publish_date,
        source_keywords=article.keywords,
        normalized_keywords=normalized,
        entities=entities,
        signals=signals,
        topic=topic,
        core_summary=build_core_summary(topic, signals, entities, article.title),
        followup_queries=build_followup_queries(topic, entities, normalized, signals),
        url=article.url,
    )


def build_topic_briefs(analyses: list[ArticleAnalysis]) -> list[TopicBrief]:
    """Group article analyses into topic-level rollups for downstream reporting."""

    grouped: dict[str, list[ArticleAnalysis]] = {}
    for analysis in analyses:
        grouped.setdefault(analysis.topic, []).append(analysis)
    briefs: list[TopicBrief] = []
    for topic, items in grouped.items():
        keyword_counter: Counter[str] = Counter()
        entity_counter: Counter[str] = Counter()
        signal_counter: Counter[str] = Counter()
        query_order: list[str] = []
        channels: list[str] = []
        titles: list[str] = []
        for item in items:
            keyword_counter.update(item.normalized_keywords)
            entity_counter.update(item.entities)
            signal_counter.update(item.signals)
            titles.append(item.title)
            if item.channel_name not in channels:
                channels.append(item.channel_name)
            for query in item.followup_queries:
                if query not in query_order:
                    query_order.append(query)
        summary = f"{topic}共涉及{len(items)}篇文章，重点信号为{'、'.join(signal for signal, _ in signal_counter.most_common(3))}。"
        briefs.append(
            TopicBrief(
                topic=topic,
                article_count=len(items),
                channels=channels,
                keywords=[keyword for keyword, _ in keyword_counter.most_common(6)],
                entities=[entity for entity, _ in entity_counter.most_common(5)],
                signals=[signal for signal, _ in signal_counter.most_common(4)],
                summary=summary,
                followup_queries=query_order[:6],
                titles=titles[:5],
            )
        )
    return briefs

def analyze_daily_articles(input_path: Path, report_date: str) -> tuple[list[ArticleAnalysis], list[TopicBrief]]:
    """Analyze one day's C114 CSV into per-article insights and grouped topic briefs."""

    csv_text = input_path.read_text(encoding="utf-8")
    articles = load_daily_articles_from_csv(csv_text, report_date)
    analyses = [analyze_article(article) for article in articles]
    briefs = build_topic_briefs(analyses)
    return analyses, briefs
