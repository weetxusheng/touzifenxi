"""Build synthetic step payloads from step1 analyses for 36kr."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from utils.tools.analysis.models import ArticleAnalysis
from utils.tools.search.types import ArticleSearchPayload, SearchCategoryPayload, SearchWorkflowPayload

PROVIDER_KR36_NATIVE = "36kr_no_c114_search"


def build_synthetic_workflow_from_analyses(
    analyses: list[ArticleAnalysis],
    report_date: str,
    *,
    input_path: Path,
) -> SearchWorkflowPayload:
    """Group by topic and keep only original links, without external search."""

    topic_order: list[str] = []
    by_topic: dict[str, list[ArticleSearchPayload]] = {}
    for a in analyses:
        t = a.topic
        if t not in by_topic:
            topic_order.append(t)
            by_topic[t] = []
        by_topic[t].append(
            ArticleSearchPayload(
                topic=t,
                channel=a.channel_name,
                original_title=a.title,
                original_url=a.url,
                original_published_at=a.publish_date,
                queries=[],
                search_results=[],
                selected_results=[],
            )
        )
    categories = [SearchCategoryPayload(topic=tp, items=by_topic[tp]) for tp in topic_order]
    total = sum(len(c.items) for c in categories)
    if total != len(analyses):
        raise ValueError(
            f"构造抓取计划时文章条数 {total} 与步骤1 analyses 条数 {len(analyses)} 不一致（应一一对应）。"
        )
    return SearchWorkflowPayload(
        report_date=report_date,
        provider=PROVIDER_KR36_NATIVE,
        input_path=input_path,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )

