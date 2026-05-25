"""Deterministic chip-theme topic grouping (replaces LLM clustering for chip)."""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Iterable

from utils.tools.content_models import StandardArticle
from utils.tools.facades.intelligence import ArticleAnalysis, TopicBrief
from utils.tools.research.chip_themes import (
    CHIP_THEMES,
    classify_chip_themes,
    classify_chip_themes_with_filter,
)

OTHER_TOPIC = "其他动态"
NON_SEMI_TOPIC = "非半导体内容"


def group_analyses_by_chip_themes(
    analyses: Iterable[ArticleAnalysis],
) -> tuple[list[ArticleAnalysis], list[TopicBrief]]:
    """Assign each ArticleAnalysis to its first-matched chip theme; build TopicBriefs.

    Topic 分桶规则：
    1) 用 classify_chip_themes_with_filter 同时拿到 (themes, is_semiconductor)
    2) is_semiconductor=False → topic=NON_SEMI_TOPIC（非半导体内容）
    3) is_semiconductor=True 且命中主题 → topic=themes[0]（字典顺序首位）
    4) is_semiconductor=True 但未命中主题 → topic=OTHER_TOPIC
    """

    grouped: dict[str, list[ArticleAnalysis]] = defaultdict(list)
    new_analyses: list[ArticleAnalysis] = []

    for analysis in analyses:
        fake = _build_fake_standard_article(analysis)
        themes, is_semi = classify_chip_themes_with_filter(fake)
        if not is_semi:
            topic = NON_SEMI_TOPIC
        elif themes:
            topic = themes[0]
        else:
            topic = OTHER_TOPIC
        new = dataclasses.replace(analysis, topic=topic)
        new_analyses.append(new)
        grouped[topic].append(new)

    # 输出桶顺序：CHIP_THEMES 字典顺序 → OTHER_TOPIC → NON_SEMI_TOPIC
    topic_order = list(CHIP_THEMES.keys()) + [OTHER_TOPIC, NON_SEMI_TOPIC]
    briefs: list[TopicBrief] = []
    for topic in topic_order:
        bucket = grouped.get(topic) or []
        if not bucket:
            continue
        briefs.append(_build_brief(topic, bucket))
    return new_analyses, briefs


def _build_fake_standard_article(analysis: ArticleAnalysis) -> StandardArticle:
    """Adapt ArticleAnalysis 字段到 StandardArticle，用于复用 classify_chip_themes。"""

    return StandardArticle(
        source_site="chip",
        source_bucket=analysis.channel_key,
        channel=analysis.channel_key,
        article_id=analysis.url or analysis.title,
        title=analysis.title,
        url=analysis.url,
        published_at=analysis.publish_date,
        author="",
        tags=[],
        keywords=list(analysis.normalized_keywords or []) + list(analysis.source_keywords or []),
        summary=analysis.core_summary,
        content_text=" ".join(analysis.signals or []),
        metadata={},
    )


def _classify_from_analysis(analysis: ArticleAnalysis) -> list[str]:
    """Backward-compatible wrapper（保留旧名以防有人引用）。"""

    return classify_chip_themes(_build_fake_standard_article(analysis))


def _build_brief(topic: str, bucket: list[ArticleAnalysis]) -> TopicBrief:
    titles: list[str] = []
    channels: list[str] = []
    keywords: list[str] = []
    entities: list[str] = []
    signals: list[str] = []
    followup_queries: list[str] = []
    seen_channels: set[str] = set()
    seen_kw: set[str] = set()
    seen_ent: set[str] = set()
    seen_sig: set[str] = set()
    seen_fu: set[str] = set()

    for a in bucket:
        titles.append(a.title)
        if a.channel_key and a.channel_key not in seen_channels:
            seen_channels.add(a.channel_key)
            channels.append(a.channel_key)
        for kw in (a.normalized_keywords or []):
            if kw and kw not in seen_kw:
                seen_kw.add(kw)
                keywords.append(kw)
        for ent in (a.entities or []):
            if ent and ent not in seen_ent:
                seen_ent.add(ent)
                entities.append(ent)
        for sig in (a.signals or []):
            if sig and sig not in seen_sig:
                seen_sig.add(sig)
                signals.append(sig)
        for fu in (a.followup_queries or []):
            if fu and fu not in seen_fu:
                seen_fu.add(fu)
                followup_queries.append(fu)

    summary = f"{topic}：{len(bucket)} 条信号"

    return TopicBrief(
        topic=topic,
        article_count=len(bucket),
        channels=channels,
        keywords=keywords,
        entities=entities,
        signals=signals,
        summary=summary,
        followup_queries=followup_queries,
        titles=titles,
    )
