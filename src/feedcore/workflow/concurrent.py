from __future__ import annotations

import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from feedcore.brief_validation import compact_four_dimension_brief
from feedcore.brief_taxonomy import classify_topic
from feedcore.models import (
    Article,
    ArticleFourDimRecord,
    ArticleTextRecord,
    FeedItemRecord,
    ResearchScoreRecord,
    RssSourceRecord,
    TypeCollectionRecord,
    TypeFourDimRecord,
    TypePlanRecord,
    TypeProposalRecord,
)
from feedcore.rss import parse_feed
from feedcore.workflow.run_context import RunContext
from feedcore.workflow.reading_topics import (
    ReadingCategoryRecord,
    ReadingEventRecord,
    ReadingTopicRecord,
    build_reading_topic_groups,
    collapse_type_dims_for_type_brief,
    render_reading_topic_brief_html,
    render_reading_topic_brief_markdown,
)
from feedcore.workflow.research_gate import (
    build_default_keep_score,
    enforce_research_value_heuristics,
    is_research_keep,
    parse_research_score,
    should_pass_through_research_pending,
)
from feedcore.workflow.steps import (
    ArticleClient,
    RssClient,
    SummaryClient,
    Translator,
    detect_language,
    parse_four_dimensions,
    select_rss_sources,
)
from feedcore.workflow.types import build_type_plan_prompt, parse_type_plan, validate_type_plan


@dataclass(frozen=True)
class ConcurrentWorkflowClients:
    rss: RssClient
    article: ArticleClient
    summary: SummaryClient
    translator: Translator | None = None


@dataclass(frozen=True)
class ConcurrentWorkflowResult:
    run_id: str
    run_dir: Path
    brief_md: Path
    brief_html: Path


@dataclass(frozen=True)
class RssTaskResult:
    rss_id: str
    source: RssSourceRecord
    feed_items: list[FeedItemRecord]
    text_records: list[ArticleTextRecord]
    low_quality_records: list[ArticleTextRecord]
    article_dims: list[ArticleFourDimRecord]
    article_ids: dict[int, str]
    rss_ids: dict[int, str]


@dataclass(frozen=True)
class RssFeedResult:
    rss_id: str
    source: RssSourceRecord
    feed_items: list[FeedItemRecord]


def run_concurrent_workflow(
    *,
    rss_urls: list[str],
    output_dir: Path,
    clients: ConcurrentWorkflowClients,
    parse_feed_fn: Callable[[str, str], list[Article]] = parse_feed,
    default_categories: dict[str, str] | None = None,
    run_id: str | None = None,
    quick_sample_size: int | None = None,
    max_articles: int | None = None,
    rss_concurrency: int = 10,
    article_fetch_concurrency: int = 5,
    model_concurrency: int = 6,
    type_classification_concurrency: int = 2,
    type_synthesis_concurrency: int = 2,
) -> ConcurrentWorkflowResult:
    ctx = RunContext.create(output_dir=output_dir, run_id=run_id)
    (ctx.run_dir / "rss_tasks").mkdir(exist_ok=True)
    (ctx.run_dir / "type_collections").mkdir(exist_ok=True)

    sources = _assign_rss_ids(
        select_rss_sources(rss_urls, default_categories=default_categories, max_sources=quick_sample_size)
    )
    ctx.write_json("step1_rss_sources.json", [_source_dict(source) for source in sources])
    ctx.log_step("step1_rss_sources", input_count=len(rss_urls), output_count=len(sources), message="selected RSS sources")

    model_semaphore = threading.Semaphore(max(1, model_concurrency))
    log_lock = threading.Lock()
    feed_results = _fetch_rss_feeds(
        ctx=ctx,
        sources=sources,
        clients=clients,
        parse_feed_fn=parse_feed_fn,
        max_articles=max_articles,
        rss_concurrency=rss_concurrency,
    )
    feed_items = _collect_distinct_feed_items(feed_results, max_articles=max_articles)
    feed_results = _restrict_feed_results_to_selected_items(feed_results, feed_items)
    source_by_rss_id = {result.rss_id: result.source for result in feed_results}
    ctx.write_json("step2_feed_items.json", [item.to_dict() for item in feed_items])
    ctx.log_step(
        "step2_fetch_feeds",
        input_count=len(sources),
        output_count=len(feed_items),
        message="completed RSS feed fetch",
    )
    text_records = _fetch_all_text_records(
        ctx=ctx,
        feed_results=feed_results,
        clients=clients,
        article_fetch_concurrency=article_fetch_concurrency,
    )
    text_by_rss_id = _group_by_rss_id(text_records)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_articles_text.json",
            [item.to_dict() for item in text_by_rss_id.get(result.rss_id, [])],
        )

    analyzable_records, low_quality_records = _split_all_quality_records(ctx, text_records)
    research_scores = _score_all_research_records(
        ctx=ctx,
        source_by_rss_id=source_by_rss_id,
        text_records=analyzable_records,
        summary_client=clients.summary,
        model_semaphore=model_semaphore,
        log_lock=log_lock,
        model_concurrency=model_concurrency,
    )
    research_scores_by_rss_id = _group_research_scores_by_rss_id(research_scores)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_research_scores.json",
            [_research_score_dict(item) for item in research_scores_by_rss_id.get(result.rss_id, [])],
        )
    research_kept_records, filtered_by_research_score = _split_research_records(research_scores, analyzable_records)
    article_dims, article_ids, rss_ids = _analyze_all_articles(
        ctx=ctx,
        source_by_rss_id=source_by_rss_id,
        text_records=research_kept_records,
        summary_client=clients.summary,
        model_semaphore=model_semaphore,
        log_lock=log_lock,
        model_concurrency=model_concurrency,
    )
    dims_by_rss_id = _group_dims_by_rss_id(article_dims, rss_ids)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_article_four_dims.json",
            [_article_dim_dict(item, article_ids, rss_ids) for item in dims_by_rss_id.get(result.rss_id, [])],
        )

    ctx.write_json("step3_article_contents.json", [_text_checkpoint(item) for item in text_records])
    ctx.log_step(
        "step3_fetch_and_extract_text",
        input_count=len(feed_items),
        output_count=len(text_records),
        skipped_count=sum(1 for item in text_records if item.fetch_error or item.extract_error or item.translation_error),
        message="fetched article HTML in memory and extracted text",
    )
    ctx.write_json("step4_research_scores.json", [_research_score_dict(item) for item in research_scores])
    ctx.write_json(
        "filtered_by_research_score.json",
        [_research_score_dict(item) for item in filtered_by_research_score],
    )
    ctx.write_json("low_quality_articles.json", [_low_quality_checkpoint(item) for item in low_quality_records])
    ctx.log_step(
        "step4_research_scores",
        input_count=len(analyzable_records),
        output_count=len(research_scores),
        skipped_count=len(filtered_by_research_score),
        message="scored research value before article four-dimension analysis",
    )
    ctx.write_json("step4_article_four_dims.json", [_article_dim_dict(item, article_ids, rss_ids) for item in article_dims])
    ctx.log_step(
        "step4_article_four_dims",
        input_count=len(research_kept_records),
        output_count=len(article_dims),
        skipped_count=0,
        message="generated per-RSS article four dimensions",
    )

    type_plan = _plan_types(
        clients.summary,
        article_dims,
        article_ids,
        model_semaphore,
        ctx,
        log_lock,
        type_classification_concurrency=type_classification_concurrency,
    )
    type_collections, validated_plan = validate_type_plan(article_dims, type_plan, article_ids, rss_ids)
    type_collections = _merge_same_named_type_collections(type_collections)
    ctx.write_json("step5_type_plan.json", validated_plan.to_dict())
    ctx.log_step(
        "step5_type_classification",
        input_count=len(article_dims),
        output_count=len(validated_plan.types),
        skipped_count=len(validated_plan.validation_errors),
        message="planned short article types",
    )

    _write_type_collections(ctx, type_collections, article_ids, rss_ids)
    ctx.write_json("step6_grouped_types.json", [collection.to_dict() for collection in type_collections])
    ctx.log_step("step6_grouped_types", input_count=len(article_dims), output_count=len(type_collections), message="wrote same-type JSON collections")

    brief_type_collections = _filter_type_collections_for_brief(type_collections, research_scores)
    ctx.write_json("filtered_type_collections.json", [collection.to_dict() for collection in brief_type_collections])
    ctx.log_step(
        "step6_filter_type_collections",
        input_count=len(type_collections),
        output_count=len(brief_type_collections),
        skipped_count=max(len(type_collections) - len(brief_type_collections), 0),
        message="filtered low-value edge collections before type synthesis",
    )

    type_dims = _synthesize_types(
        clients.summary,
        brief_type_collections,
        model_semaphore,
        ctx,
        log_lock,
        type_synthesis_concurrency=type_synthesis_concurrency,
    )
    ctx.write_json("step7_type_four_dims.json", [item.to_dict() for item in type_dims])
    ctx.log_step("step7_type_four_dims", input_count=len(type_collections), output_count=len(type_dims), message="synthesized type-level four dimensions")

    step8_type_dims = collapse_type_dims_for_type_brief(type_dims, max_topics_per_category=5)
    ctx.write_json("step8_collapsed_type_four_dims.json", [item.to_dict() for item in step8_type_dims])

    step8_md = ctx.run_dir / "step8_brief.md"
    step8_md.write_text(render_type_brief_markdown(step8_type_dims), encoding="utf-8")
    step8_html = ctx.run_dir / "step8_brief.html"
    step8_html.write_text(render_brief_html(step8_md.read_text(encoding="utf-8")), encoding="utf-8")
    ctx.log_step(
        "step8_render_type_brief",
        input_count=len(type_dims),
        output_count=len(step8_type_dims),
        message="rendered collapsed traceable type brief",
    )

    reading_categories = build_reading_topic_groups(type_dims, max_topics_per_category=5)
    reading_categories = _synthesize_reading_events(
        clients.summary,
        reading_categories,
        model_semaphore,
        ctx,
        log_lock,
    )
    reading_categories = _summarize_reading_category_intros(
        clients.summary,
        reading_categories,
        model_semaphore,
        ctx,
        log_lock,
    )
    ctx.write_json("step9_reading_topic_groups.json", [item.to_dict() for item in reading_categories])
    report_date = _default_report_date(ctx.run_id)
    # v11: 从 step4_research_scores.json 加载 url→score 索引,reading_topics 渲染时
    # 按 score 排序后每个 H3 子主题取 top 10 source。这样 brief 里展示的就是
    # 高分(高 research value)文章,不再受 LLM 主观排序影响。
    url_scores = _load_url_score_lookup(ctx.run_dir)
    step10_md = ctx.run_dir / "step10_brief.md"
    step10_md.write_text(
        render_reading_topic_brief_markdown(reading_categories, report_date=report_date, url_scores=url_scores),
        encoding="utf-8",
    )
    step10_html = ctx.run_dir / "step10_brief.html"
    step10_html.write_text(
        render_reading_topic_brief_html(reading_categories, report_date=report_date, url_scores=url_scores),
        encoding="utf-8",
    )
    brief_md = ctx.run_dir / "brief.md"
    brief_md.write_text(step10_md.read_text(encoding="utf-8"), encoding="utf-8")
    brief_html = ctx.run_dir / "brief.html"
    brief_html.write_text(step10_html.read_text(encoding="utf-8"), encoding="utf-8")
    ctx.log_step("step9_reading_topic_groups", input_count=len(type_dims), output_count=len(reading_categories), message="grouped fine types into reading topics")
    ctx.log_step("step10_render_brief", input_count=len(reading_categories), output_count=2, message="rendered final reading brief")
    ctx.write_execution_log()
    return ConcurrentWorkflowResult(ctx.run_id, ctx.run_dir, brief_md, brief_html)


def run_concurrent_workflow_from_prefetched(
    *,
    output_dir: Path,
    clients: ConcurrentWorkflowClients,
    rss_sources: list[dict[str, object]],
    feed_items: list[dict[str, object]],
    article_contents: list[dict[str, object]],
    run_id: str | None = None,
    model_concurrency: int = 6,
    type_classification_concurrency: int = 2,
    type_synthesis_concurrency: int = 2,
) -> ConcurrentWorkflowResult:
    """Continue the local workflow from a remote fetcher's step1/2/3 artifact."""
    ctx = RunContext.create(output_dir=output_dir, run_id=run_id)
    (ctx.run_dir / "rss_tasks").mkdir(exist_ok=True)
    (ctx.run_dir / "type_collections").mkdir(exist_ok=True)

    sources = [_rss_source_from_checkpoint(item, index) for index, item in enumerate(rss_sources, start=1)]
    feed_item_records = [_feed_item_from_checkpoint(item) for item in feed_items]
    text_records = [_article_text_from_checkpoint(item) for item in article_contents]
    feed_by_rss_id = _group_feed_items_by_rss_id(feed_item_records, text_records)
    feed_results = [
        RssFeedResult(getattr(source, "rss_id", f"rss_{index:03d}"), source, feed_by_rss_id.get(getattr(source, "rss_id", ""), []))
        for index, source in enumerate(sources, start=1)
    ]

    ctx.write_json("step1_rss_sources.json", [_source_dict(source) for source in sources])
    ctx.log_step(
        "step1_rss_sources",
        input_count=len(rss_sources),
        output_count=len(sources),
        message="imported prefetched RSS sources",
    )
    ctx.write_json("step2_feed_items.json", [item.to_dict() for item in feed_item_records])
    ctx.log_step(
        "step2_fetch_feeds",
        input_count=len(sources),
        output_count=len(feed_item_records),
        message="imported prefetched feed items",
    )
    for result in feed_results:
        ctx.write_json(f"rss_tasks/{result.rss_id}_feed_items.json", [item.to_dict() for item in result.feed_items])

    return _continue_concurrent_workflow_after_step3(
        ctx=ctx,
        clients=clients,
        feed_results=feed_results,
        feed_items=feed_item_records,
        text_records=text_records,
        model_concurrency=model_concurrency,
        type_classification_concurrency=type_classification_concurrency,
        type_synthesis_concurrency=type_synthesis_concurrency,
    )


def _continue_concurrent_workflow_after_step3(
    *,
    ctx: RunContext,
    clients: ConcurrentWorkflowClients,
    feed_results: list[RssFeedResult],
    feed_items: list[FeedItemRecord],
    text_records: list[ArticleTextRecord],
    model_concurrency: int,
    type_classification_concurrency: int,
    type_synthesis_concurrency: int,
) -> ConcurrentWorkflowResult:
    model_semaphore = threading.Semaphore(max(1, model_concurrency))
    log_lock = threading.Lock()
    source_by_rss_id = {result.rss_id: result.source for result in feed_results}
    text_by_rss_id = _group_by_rss_id(text_records)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_articles_text.json",
            [item.to_dict() for item in text_by_rss_id.get(result.rss_id, [])],
        )

    analyzable_records, low_quality_records = _split_all_quality_records(ctx, text_records)
    research_scores = _score_all_research_records(
        ctx=ctx,
        source_by_rss_id=source_by_rss_id,
        text_records=analyzable_records,
        summary_client=clients.summary,
        model_semaphore=model_semaphore,
        log_lock=log_lock,
        model_concurrency=model_concurrency,
    )
    research_scores_by_rss_id = _group_research_scores_by_rss_id(research_scores)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_research_scores.json",
            [_research_score_dict(item) for item in research_scores_by_rss_id.get(result.rss_id, [])],
        )
    research_kept_records, filtered_by_research_score = _split_research_records(research_scores, analyzable_records)
    article_dims, article_ids, rss_ids = _analyze_all_articles(
        ctx=ctx,
        source_by_rss_id=source_by_rss_id,
        text_records=research_kept_records,
        summary_client=clients.summary,
        model_semaphore=model_semaphore,
        log_lock=log_lock,
        model_concurrency=model_concurrency,
    )
    dims_by_rss_id = _group_dims_by_rss_id(article_dims, rss_ids)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_article_four_dims.json",
            [_article_dim_dict(item, article_ids, rss_ids) for item in dims_by_rss_id.get(result.rss_id, [])],
        )

    ctx.write_json("step3_article_contents.json", [_text_checkpoint(item) for item in text_records])
    ctx.log_step(
        "step3_fetch_and_extract_text",
        input_count=len(feed_items),
        output_count=len(text_records),
        skipped_count=sum(1 for item in text_records if item.fetch_error or item.extract_error or item.translation_error),
        message="imported prefetched article text",
    )
    ctx.write_json("step4_research_scores.json", [_research_score_dict(item) for item in research_scores])
    ctx.write_json(
        "filtered_by_research_score.json",
        [_research_score_dict(item) for item in filtered_by_research_score],
    )
    ctx.write_json("low_quality_articles.json", [_low_quality_checkpoint(item) for item in low_quality_records])
    ctx.log_step(
        "step4_research_scores",
        input_count=len(analyzable_records),
        output_count=len(research_scores),
        skipped_count=len(filtered_by_research_score),
        message="scored research value before article four-dimension analysis",
    )
    ctx.write_json("step4_article_four_dims.json", [_article_dim_dict(item, article_ids, rss_ids) for item in article_dims])
    ctx.log_step(
        "step4_article_four_dims",
        input_count=len(research_kept_records),
        output_count=len(article_dims),
        skipped_count=0,
        message="generated per-RSS article four dimensions",
    )

    type_plan = _plan_types(
        clients.summary,
        article_dims,
        article_ids,
        model_semaphore,
        ctx,
        log_lock,
        type_classification_concurrency=type_classification_concurrency,
    )
    type_collections, validated_plan = validate_type_plan(article_dims, type_plan, article_ids, rss_ids)
    type_collections = _merge_same_named_type_collections(type_collections)
    ctx.write_json("step5_type_plan.json", validated_plan.to_dict())
    ctx.log_step(
        "step5_type_classification",
        input_count=len(article_dims),
        output_count=len(validated_plan.types),
        skipped_count=len(validated_plan.validation_errors),
        message="planned short article types",
    )

    _write_type_collections(ctx, type_collections, article_ids, rss_ids)
    ctx.write_json("step6_grouped_types.json", [collection.to_dict() for collection in type_collections])
    ctx.log_step(
        "step6_grouped_types",
        input_count=len(article_dims),
        output_count=len(type_collections),
        message="wrote same-type JSON collections",
    )

    brief_type_collections = _filter_type_collections_for_brief(type_collections, research_scores)
    ctx.write_json("filtered_type_collections.json", [collection.to_dict() for collection in brief_type_collections])
    ctx.log_step(
        "step6_filter_type_collections",
        input_count=len(type_collections),
        output_count=len(brief_type_collections),
        skipped_count=max(len(type_collections) - len(brief_type_collections), 0),
        message="filtered low-value edge collections before type synthesis",
    )

    type_dims = _synthesize_types(
        clients.summary,
        brief_type_collections,
        model_semaphore,
        ctx,
        log_lock,
        type_synthesis_concurrency=type_synthesis_concurrency,
    )
    ctx.write_json("step7_type_four_dims.json", [item.to_dict() for item in type_dims])
    ctx.log_step(
        "step7_type_four_dims",
        input_count=len(type_collections),
        output_count=len(type_dims),
        message="synthesized type-level four dimensions",
    )

    step8_type_dims = collapse_type_dims_for_type_brief(type_dims, max_topics_per_category=5)
    ctx.write_json("step8_collapsed_type_four_dims.json", [item.to_dict() for item in step8_type_dims])

    step8_md = ctx.run_dir / "step8_brief.md"
    step8_md.write_text(render_type_brief_markdown(step8_type_dims), encoding="utf-8")
    step8_html = ctx.run_dir / "step8_brief.html"
    step8_html.write_text(render_brief_html(step8_md.read_text(encoding="utf-8")), encoding="utf-8")
    ctx.log_step(
        "step8_render_type_brief",
        input_count=len(type_dims),
        output_count=len(step8_type_dims),
        message="rendered collapsed traceable type brief",
    )

    reading_categories = build_reading_topic_groups(type_dims, max_topics_per_category=5)
    reading_categories = _synthesize_reading_events(
        clients.summary,
        reading_categories,
        model_semaphore,
        ctx,
        log_lock,
    )
    reading_categories = _summarize_reading_category_intros(
        clients.summary,
        reading_categories,
        model_semaphore,
        ctx,
        log_lock,
    )
    ctx.write_json("step9_reading_topic_groups.json", [item.to_dict() for item in reading_categories])
    report_date = _default_report_date(ctx.run_id)
    url_scores = _load_url_score_lookup(ctx.run_dir)
    step10_md = ctx.run_dir / "step10_brief.md"
    step10_md.write_text(
        render_reading_topic_brief_markdown(reading_categories, report_date=report_date, url_scores=url_scores),
        encoding="utf-8",
    )
    step10_html = ctx.run_dir / "step10_brief.html"
    step10_html.write_text(
        render_reading_topic_brief_html(reading_categories, report_date=report_date, url_scores=url_scores),
        encoding="utf-8",
    )
    brief_md = ctx.run_dir / "brief.md"
    brief_md.write_text(step10_md.read_text(encoding="utf-8"), encoding="utf-8")
    brief_html = ctx.run_dir / "brief.html"
    brief_html.write_text(step10_html.read_text(encoding="utf-8"), encoding="utf-8")
    ctx.log_step(
        "step9_reading_topic_groups",
        input_count=len(type_dims),
        output_count=len(reading_categories),
        message="grouped fine types into reading topics",
    )
    ctx.log_step("step10_render_brief", input_count=len(reading_categories), output_count=2, message="rendered final reading brief")
    ctx.write_execution_log()
    return ConcurrentWorkflowResult(ctx.run_id, ctx.run_dir, brief_md, brief_html)


def _synthesize_reading_events(
    summary_client: SummaryClient,
    reading_categories: list[ReadingCategoryRecord],
    model_semaphore: threading.Semaphore,
    ctx: RunContext,
    log_lock: threading.Lock,
) -> list[ReadingCategoryRecord]:
    if not hasattr(summary_client, "summarize_reading_event"):
        return reading_categories

    out: list[ReadingCategoryRecord] = []
    for category in reading_categories:
        topics: list[ReadingTopicRecord] = []
        for topic in category.topics:
            events: list[ReadingEventRecord] = []
            for event in topic.events:
                started = time.perf_counter()
                status = "ok"
                error = None
                raw = ""
                synthesized = event
                with model_semaphore:
                    try:
                        raw = summary_client.summarize_reading_event(
                            category.category_group,
                            topic.name,
                            event.name,
                            event.type_records,
                        )
                        synthesized = _parse_synthesized_reading_event(event, raw)
                    except Exception as exc:
                        status = "error"
                        error = str(exc)
                        synthesized = _mark_reading_event_synthesis_failed(event, error)
                _log_json(
                    ctx.run_dir / "logs" / "model_calls.log",
                    {
                        "task": "reading_event_synthesis",
                        "category": category.category_group,
                        "topic": topic.name,
                        "event": event.name,
                        "status": status,
                        "duration_ms": _duration_ms(started),
                        "input_records": len(event.type_records),
                        "output_chars": len(raw),
                        "error": error,
                    },
                    lock=log_lock,
                )
                events.append(synthesized)
            topics.append(
                ReadingTopicRecord(
                    name=topic.name,
                    type_records=topic.type_records,
                    category_group=topic.category_group,
                    summary=topic.summary,
                    key_points=topic.key_points,
                    watchout=topic.watchout,
                    source_type_names=topic.source_type_names,
                    events=events,
                )
            )
        out.append(ReadingCategoryRecord(category_group=category.category_group, intro=category.intro, topics=topics))
    return out


def _parse_synthesized_reading_event(event: ReadingEventRecord, raw: str) -> ReadingEventRecord:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return ReadingEventRecord(
        name=event.name,
        type_records=event.type_records,
        summary=event.summary,
        source_type_names=event.source_type_names,
        synthesized_facts=_clean_synthesized_items(data.get("facts")),
        synthesized_background=_clean_synthesized_items(data.get("background")),
        synthesized_impact=_clean_synthesized_items(data.get("impact")),
        synthesized_contradictions=_clean_synthesized_items(data.get("contradictions")),
        synthesis_attempted=True,
    )


def _mark_reading_event_synthesis_failed(event: ReadingEventRecord, error: str | None) -> ReadingEventRecord:
    return ReadingEventRecord(
        name=event.name,
        type_records=event.type_records,
        summary=event.summary,
        source_type_names=event.source_type_names,
        synthesis_attempted=True,
        synthesis_error=str(error or ""),
    )


def _clean_synthesized_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        clean = " ".join(str(item or "").split()).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
            break
    return out


def _summarize_reading_category_intros(
    summary_client: SummaryClient,
    reading_categories,
    model_semaphore: threading.Semaphore,
    ctx: RunContext,
    log_lock: threading.Lock,
):
    out = []
    for category in reading_categories:
        started = time.perf_counter()
        status = "ok"
        error = None
        intro = category.intro
        if hasattr(summary_client, "summarize_category_intro"):
            with model_semaphore:
                try:
                    intro = summary_client.summarize_category_intro(category.category_group, category.topics)
                    intro = _clean_category_intro(intro) or category.intro
                except Exception as exc:
                    status = "error"
                    error = str(exc)
        out.append(type(category)(category_group=category.category_group, intro=intro, topics=category.topics))
        _log_json(
            ctx.run_dir / "logs" / "model_calls.log",
            {
                "task": "category_intro",
                "category": category.category_group,
                "status": status,
                "duration_ms": _duration_ms(started),
                "input_topics": len(category.topics),
                "output_chars": len(intro),
                "error": error,
            },
            lock=log_lock,
        )
    return out


def _clean_category_intro(value: str) -> str:
    clean = " ".join(str(value or "").strip().split())
    clean = clean.strip("` \n")
    if clean.startswith(("概述：", "概述:")):
        clean = clean.split("：", 1)[-1] if "：" in clean else clean.split(":", 1)[-1]
    return clean.strip()


def _default_report_date(run_id: str) -> str:
    match = re.search(r"(\d{8})", run_id or "")
    if match:
        current = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone(timedelta(hours=8)))
    else:
        current = datetime.now(timezone(timedelta(hours=8)))
    return (current - timedelta(days=1)).strftime("%Y-%m-%d")


def _load_url_score_lookup(run_dir: Path) -> dict[str, int]:
    """从 step4_research_scores.json 加载 url → research_score 索引。

    给 step10 渲染层用,让 reading topic 的 source 列表按 score 排序后取 top-N。
    如果文件不存在或解析失败,返回空 dict (渲染层会 fallback 到原顺序)。
    """
    path = Path(run_dir) / "step4_research_scores.json"
    if not path.exists():
        return {}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, int] = {}
    for r in records or []:
        try:
            url = (r.get("article", {}) or {}).get("link", "")
            score = int(r.get("score", 0) or 0)
            if url:
                out[url.strip()] = score
        except Exception:
            continue
    return out


def _fetch_rss_feeds(
    *,
    ctx: RunContext,
    sources: list[RssSourceRecord],
    clients: ConcurrentWorkflowClients,
    parse_feed_fn: Callable[[str, str], list[Article]],
    max_articles: int | None,
    rss_concurrency: int,
) -> list[RssFeedResult]:
    if rss_concurrency <= 1:
        return [
            _fetch_one_rss_feed(ctx, source, clients, parse_feed_fn, max_articles)
            for source in sources
        ]

    results: list[RssFeedResult] = []
    with ThreadPoolExecutor(max_workers=max(1, rss_concurrency)) as executor:
        future_map = {
            executor.submit(_fetch_one_rss_feed, ctx, source, clients, parse_feed_fn, max_articles): source
            for source in sources
        }
        for future in as_completed(future_map):
            results.append(future.result())
    return sorted(results, key=lambda item: item.rss_id)


def _fetch_one_rss_feed(
    ctx: RunContext,
    source: RssSourceRecord,
    clients: ConcurrentWorkflowClients,
    parse_feed_fn: Callable[[str, str], list[Article]],
    max_articles: int | None,
) -> RssFeedResult:
    rss_id = getattr(source, "rss_id", "")
    log_path = ctx.run_dir / "logs" / f"{rss_id}.log"
    _log_json(log_path, {"rss_id": rss_id, "event": "rss_fetch_start", "url": source.url})
    started = time.perf_counter()
    feed_items: list[FeedItemRecord] = []
    try:
        feed_text = clients.rss.fetch(source.url)
        articles = parse_feed_fn(feed_text, source.url)
        if max_articles is not None and max_articles > 0:
            articles = articles[:max_articles]
        feed_items = [
            FeedItemRecord(article=article, default_category=source.default_category, source_label=source.label)
            for article in articles
            if article.link
        ]
        _log_json(
            log_path,
            {
                "rss_id": rss_id,
                "event": "rss_fetch_done",
                "status": "ok",
                "duration_ms": _duration_ms(started),
                "article_count": len(feed_items),
            },
        )
    except Exception as exc:
        _log_json(log_path, {"rss_id": rss_id, "event": "rss_fetch_done", "status": "error", "error": str(exc), "duration_ms": _duration_ms(started)})

    ctx.write_json(f"rss_tasks/{rss_id}_feed_items.json", [item.to_dict() for item in feed_items])
    return RssFeedResult(rss_id, source, feed_items)


def _fetch_all_text_records(
    *,
    ctx: RunContext,
    feed_results: list[RssFeedResult],
    clients: ConcurrentWorkflowClients,
    article_fetch_concurrency: int,
) -> list[ArticleTextRecord]:
    out: list[ArticleTextRecord] = []
    with ThreadPoolExecutor(max_workers=max(1, article_fetch_concurrency)) as executor:
        future_map = {}
        for result in feed_results:
            log_path = ctx.run_dir / "logs" / f"{result.rss_id}.log"
            for index, item in enumerate(result.feed_items, start=1):
                article_id = f"{result.rss_id.replace('_', '')}_a{index:03d}"
                future_map[executor.submit(_fetch_one_text, result.source, article_id, item, clients, log_path)] = article_id
        for future in as_completed(future_map):
            out.append(future.result())
    return sorted(out, key=lambda item: item.article_id)


def _collect_distinct_feed_items(
    feed_results: list[RssFeedResult],
    *,
    max_articles: int | None,
    max_per_source: int | None = None,
) -> list[FeedItemRecord]:
    seen_links: set[str] = set()
    out: list[FeedItemRecord] = []
    for result in feed_results:
        kept_for_source = 0
        for item in result.feed_items:
            if max_per_source is not None and max_per_source > 0 and kept_for_source >= max_per_source:
                break
            link = item.article.link
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            out.append(item)
            kept_for_source += 1
            if max_articles is not None and max_articles > 0 and len(out) >= max_articles:
                return out
    return out


def _restrict_feed_results_to_selected_items(
    feed_results: list[RssFeedResult],
    selected_items: list[FeedItemRecord],
) -> list[RssFeedResult]:
    selected_pairs = {
        (item.article.feed_url, item.article.link)
        for item in selected_items
        if item.article.link
    }
    out: list[RssFeedResult] = []
    for result in feed_results:
        kept = [
            item
            for item in result.feed_items
            if (item.article.feed_url, item.article.link) in selected_pairs
        ]
        out.append(RssFeedResult(result.rss_id, result.source, kept))
    return out


def _fetch_one_text(
    source: RssSourceRecord,
    article_id: str,
    item: FeedItemRecord,
    clients: ConcurrentWorkflowClients,
    log_path: Path,
) -> ArticleTextRecord:
    started = time.perf_counter()
    fetch_error = None
    translation_error = None
    try:
        text = clients.article.fetch_text(item.article.link)
    except Exception as exc:
        text = item.article.description or item.article.title
        fetch_error = str(exc)
    language = detect_language(text)
    translated = False
    if language == "en" and clients.translator is not None:
        try:
            text = clients.translator.translate_to_chinese(text)
            translated = True
        except Exception as exc:
            translation_error = str(exc)
    _log_json(
        log_path,
        {
            "rss_id": source.rss_id,
            "event": "article_text_extracted",
            "article_id": article_id,
            "url": item.article.link,
            "status": "error" if fetch_error else "ok",
            "duration_ms": _duration_ms(started),
            "text_chars": len(text or ""),
            "error": fetch_error or translation_error,
        },
    )
    return ArticleTextRecord(
        rss_id=source.rss_id,
        article_id=article_id,
        article=item.article,
        default_category=item.default_category,
        text=text,
        detected_language=language,
        translated=translated,
        fetch_error=fetch_error,
        translation_error=translation_error,
    )


def _split_quality_records(records: list[ArticleTextRecord], log_path: Path) -> tuple[list[ArticleTextRecord], list[ArticleTextRecord]]:
    analyzable: list[ArticleTextRecord] = []
    low_quality: list[ArticleTextRecord] = []
    for item in records:
        if _is_low_quality_text(item.text):
            low_quality.append(item)
            _log_json(
                log_path,
                {
                    "rss_id": item.rss_id,
                    "event": "article_quality_gate",
                    "article_id": item.article_id,
                    "status": "skipped",
                    "reason": "low_quality_text",
                    "text_chars": len(item.text or ""),
                },
            )
        else:
            analyzable.append(item)
    return analyzable, low_quality


def _split_all_quality_records(ctx: RunContext, records: list[ArticleTextRecord]) -> tuple[list[ArticleTextRecord], list[ArticleTextRecord]]:
    by_rss_id = _group_by_rss_id(records)
    analyzable: list[ArticleTextRecord] = []
    low_quality: list[ArticleTextRecord] = []
    for rss_id in sorted(by_rss_id):
        log_path = ctx.run_dir / "logs" / f"{rss_id}.log"
        rss_analyzable, rss_low_quality = _split_quality_records(by_rss_id[rss_id], log_path)
        analyzable.extend(rss_analyzable)
        low_quality.extend(rss_low_quality)
    return sorted(analyzable, key=lambda item: item.article_id), sorted(low_quality, key=lambda item: item.article_id)


def _is_low_quality_text(text: str) -> bool:
    clean = " ".join((text or "").split())
    cjk = sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff")
    ascii_letters = sum(1 for ch in clean if ch.isascii() and ch.isalpha())
    has_enough_content = cjk >= 80 or ascii_letters >= 220
    if not has_enough_content and len(clean) < 120:
        return True
    lowered = clean.casefold()
    boilerplate = (
        "播放中",
        "加载中",
        "正在加载",
        "enable javascript",
        "please enable javascript",
        "subscribe to continue",
        "access denied",
        "forbidden",
    )
    if any(token in lowered for token in boilerplate):
        meaningful = re.findall(r"[\w\u4e00-\u9fff]+", clean)
        return len(meaningful) < 80
    return False


def _score_all_research_records(
    *,
    ctx: RunContext,
    source_by_rss_id: dict[str, RssSourceRecord],
    text_records: list[ArticleTextRecord],
    summary_client: SummaryClient,
    model_semaphore: threading.Semaphore,
    log_lock: threading.Lock,
    model_concurrency: int,
) -> list[ResearchScoreRecord]:
    scores: list[ResearchScoreRecord] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(text_records) or 1, model_concurrency))) as executor:
        future_map = {}
        for item in text_records:
            source = source_by_rss_id.get(item.rss_id)
            if source is None:
                continue
            log_path = ctx.run_dir / "logs" / f"{item.rss_id}.log"
            future_map[
                executor.submit(
                    _score_one_research_article,
                    ctx,
                    source,
                    item,
                    summary_client,
                    model_semaphore,
                    log_lock,
                    log_path,
                )
            ] = item
        for future in as_completed(future_map):
            scores.append(future.result())
    return sorted(scores, key=lambda item: item.article_id)


def _score_one_research_article(
    ctx: RunContext,
    source: RssSourceRecord,
    item: ArticleTextRecord,
    summary_client: SummaryClient,
    model_semaphore: threading.Semaphore,
    log_lock: threading.Lock,
    log_path: Path,
) -> ResearchScoreRecord:
    started = time.perf_counter()
    status = "ok"
    error = None
    raw_output = ""

    if hasattr(summary_client, "score_research_article"):
        with model_semaphore:
            try:
                raw_result = summary_client.score_research_article(item.article, item.text, item.default_category)
                raw_output = (
                    raw_result
                    if isinstance(raw_result, str)
                    else json.dumps(raw_result, ensure_ascii=False)
                )
            except Exception as exc:
                status = "error"
                error = str(exc)
                record = ResearchScoreRecord(
                    rss_id=item.rss_id,
                    article_id=item.article_id,
                    article=item.article,
                    default_category=item.default_category,
                    score=0,
                    decision="pending",
                    reason="Research scoring failed.",
                    investment_relevance=0,
                    information_increment=0,
                    decision_value=0,
                    verifiability=0,
                    noise_penalty=0,
                    evidence=[],
                    tags=[],
                    validation_errors=[f"research scoring failed: {error}"],
                )
            else:
                record = parse_research_score(
                    raw_output,
                    rss_id=item.rss_id,
                    article_id=item.article_id,
                    article=item.article,
                    default_category=item.default_category,
                )
                record = enforce_research_value_heuristics(record, item.text)
    else:
        status = "fallback"
        record = build_default_keep_score(item)

    payload = {
        "task": "research_score",
        "rss_id": source.rss_id,
        "article_id": item.article_id,
        "model": getattr(summary_client, "model", ""),
        "status": status,
        "duration_ms": _duration_ms(started),
        "input_chars": len(item.text or ""),
        "output_chars": len(raw_output),
        "score": record.score,
        "decision": record.decision,
        "error": error,
    }
    _log_json(ctx.run_dir / "logs" / "model_calls.log", payload, lock=log_lock)
    _log_json(
        log_path,
        {
            "rss_id": source.rss_id,
            "event": "article_research_score",
            "article_id": item.article_id,
            "status": status,
            "decision": record.decision,
            "score": record.score,
            "duration_ms": payload["duration_ms"],
            "error": error,
        },
    )
    return record


def _split_research_records(
    scores: list[ResearchScoreRecord],
    text_records: list[ArticleTextRecord],
) -> tuple[list[ArticleTextRecord], list[ResearchScoreRecord]]:
    text_by_article_id = {item.article_id: item for item in text_records}
    kept: list[ArticleTextRecord] = []
    filtered: list[ResearchScoreRecord] = []
    for score in scores:
        article_text = text_by_article_id.get(score.article_id)
        if article_text is None:
            continue
        if is_research_keep(score) or should_pass_through_research_pending(score):
            kept.append(article_text)
        else:
            filtered.append(score)
    return sorted(kept, key=lambda item: item.article_id), sorted(filtered, key=lambda item: item.article_id)


def _analyze_all_articles(
    *,
    ctx: RunContext,
    source_by_rss_id: dict[str, RssSourceRecord],
    text_records: list[ArticleTextRecord],
    summary_client: SummaryClient,
    model_semaphore: threading.Semaphore,
    log_lock: threading.Lock,
    model_concurrency: int,
) -> tuple[list[ArticleFourDimRecord], dict[int, str], dict[int, str]]:
    dims: list[ArticleFourDimRecord] = []
    article_ids: dict[int, str] = {}
    rss_ids: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(len(text_records) or 1, model_concurrency))) as executor:
        future_map = {}
        for item in text_records:
            source = source_by_rss_id.get(item.rss_id)
            if source is None:
                continue
            log_path = ctx.run_dir / "logs" / f"{item.rss_id}.log"
            future_map[
                executor.submit(_analyze_one_article, ctx, source, item, summary_client, model_semaphore, log_lock, log_path)
            ] = item
        for future in as_completed(future_map):
            article_id, record = future.result()
            dims.append(record)
            article_ids[id(record)] = article_id
            rss_ids[id(record)] = future_map[future].rss_id
    return sorted(dims, key=lambda item: article_ids[id(item)]), article_ids, rss_ids


def _analyze_one_article(
    ctx: RunContext,
    source: RssSourceRecord,
    item: ArticleTextRecord,
    summary_client: SummaryClient,
    model_semaphore: threading.Semaphore,
    log_lock: threading.Lock,
    log_path: Path,
) -> tuple[str, ArticleFourDimRecord]:
    started = time.perf_counter()
    status = "ok"
    error = None
    output_chars = 0
    summary_input = _build_article_summary_input(item.article, item.text)
    with model_semaphore:
        try:
            brief = summary_client.summarize_article(item.article, summary_input)
        except Exception as exc:
            brief = _model_failure_brief(exc)
            status = "error"
            error = str(exc)
    if status == "ok":
        brief = compact_four_dimension_brief(brief, item.text)
    output_chars = len(brief)
    dims = _prune_four_dimension_items(parse_four_dimensions(brief))
    classified = classify_topic(item.article, item.text)
    final_category = classified if classified != "社会与其它" else item.default_category
    record = ArticleFourDimRecord(
        article=item.article,
        default_category=item.default_category,
        final_category=final_category,
        facts=dims["facts"],
        background=dims["background"],
        impact=dims["impact"],
        contradictions=dims["contradictions"],
        brief=brief,
    )
    payload = {
        "task": "article_four_dims",
        "rss_id": source.rss_id,
        "article_id": item.article_id,
        "model": getattr(summary_client, "model", ""),
        "status": status,
        "duration_ms": _duration_ms(started),
        "input_chars": len(item.text or ""),
        "summary_input_chars": len(summary_input),
        "output_chars": output_chars,
        "error": error,
    }
    _log_json(ctx.run_dir / "logs" / "model_calls.log", payload, lock=log_lock)
    _log_json(log_path, {"rss_id": source.rss_id, "event": "article_four_dims_done", "article_id": item.article_id, "status": status, "duration_ms": payload["duration_ms"], "error": error})
    return item.article_id, record


_ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT = 2200
_ARTICLE_SUMMARY_INPUT_LEAD_SENTENCES = 4
_ARTICLE_SUMMARY_INPUT_MIN_SENTENCES = 6
_ARTICLE_SUMMARY_INPUT_MAX_SENTENCES = 12
_ARTICLE_SUMMARY_INPUT_PRIORITY_TOKENS = (
    "visit",
    "summit",
    "meeting",
    "talk",
    "trade",
    "tariff",
    "market",
    "access",
    "approval",
    "policy",
    "chip",
    "ceo",
    "ai",
    "访华",
    "会晤",
    "峰会",
    "贸易",
    "关税",
    "市场准入",
    "审批",
    "芯片",
    "高管",
    "人工智能",
    "访问",
)
_ARTICLE_SUMMARY_INPUT_CONTRA_TOKENS = (
    "but",
    "however",
    "yet",
    "still",
    "uncertain",
    "risk",
    "tension",
    "dispute",
    "fragile",
    "pending",
    "但",
    "然而",
    "不过",
    "仍",
    "尚未",
    "风险",
    "分歧",
    "争议",
    "脆弱",
    "不确定",
)
_LOW_VALUE_DIMENSION_TOKENS = (
    "挥舞",
    "高喊",
    "欢迎欢迎",
    "国旗",
    "暂停开放",
    "安保",
    "禁止拍照",
    "迎接",
    "欢迎仪式",
    "青年",
    "礼宾",
    "crowd",
    "waving",
    "welcome",
    "security",
    "closed",
    "photo",
)
_HIGH_VALUE_DIMENSION_TOKENS = (
    "关税",
    "贸易",
    "委员会",
    "市场",
    "准入",
    "审批",
    "芯片",
    "出口",
    "制裁",
    "协议",
    "会晤",
    "峰会",
    "访华",
    "访问",
    "谈判",
    "会谈",
    "监管",
    "法案",
    "投资",
    "财报",
    "业绩",
    "approve",
    "approval",
    "tariff",
    "trade",
    "committee",
    "market",
    "chip",
    "export",
    "summit",
    "meeting",
    "policy",
    "regulation",
)


def _build_article_summary_input(article: Article, text: str) -> str:
    clean = (text or "").strip()
    if len(clean) <= _ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT:
        return clean

    paragraphs = [
        paragraph
        for paragraph in (part.strip() for part in re.split(r"\n\s*\n+", clean))
        if paragraph and not _is_summary_noise_paragraph(paragraph)
    ]
    if not paragraphs:
        return _truncate_article_summary_input(clean, max_chars=_ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT)

    sentence_records = _article_summary_sentence_records(paragraphs)
    if not sentence_records:
        return _truncate_article_summary_input(clean, max_chars=_ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT)
    lead_text = " ".join(item["text"] for item in sentence_records[:_ARTICLE_SUMMARY_INPUT_LEAD_SENTENCES])
    anchor_terms = _summary_anchor_terms(f"{article.title}\n{lead_text}")

    selected_indices: set[int] = set()
    total_chars = 0
    for index, item in enumerate(sentence_records):
        if index >= _ARTICLE_SUMMARY_INPUT_LEAD_SENTENCES:
            break
        if total_chars and total_chars + len(item["text"]) > _ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT:
            break
        selected_indices.add(index)
        total_chars += len(item["text"])

    scored: list[tuple[int, int]] = []
    for index, item in enumerate(sentence_records):
        if index in selected_indices:
            continue
        score = _article_summary_sentence_score(
            item["text"],
            anchor_terms=anchor_terms,
            sentence_index=index,
            paragraph_index=item["paragraph_index"],
        )
        if score <= 0:
            continue
        scored.append((score, index))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    for score, index in scored:
        if len(selected_indices) >= _ARTICLE_SUMMARY_INPUT_MAX_SENTENCES:
            break
        sentence = sentence_records[index]["text"]
        if total_chars and total_chars + len(sentence) > _ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT:
            continue
        overlap_count = _summary_overlap_count(sentence, anchor_terms)
        if len(selected_indices) >= _ARTICLE_SUMMARY_INPUT_MIN_SENTENCES and overlap_count < 2 and score < 6:
            continue
        selected_indices.add(index)
        total_chars += len(sentence)

    for index, item in enumerate(sentence_records):
        if len(selected_indices) >= _ARTICLE_SUMMARY_INPUT_MIN_SENTENCES:
            break
        if index in selected_indices:
            continue
        if total_chars and total_chars + len(item["text"]) > _ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT:
            break
        selected_indices.add(index)
        total_chars += len(item["text"])

    selected = [item["text"] for index, item in enumerate(sentence_records) if index in selected_indices]
    excerpt = "\n\n".join(selected).strip()
    return excerpt or _truncate_article_summary_input(clean, max_chars=_ARTICLE_SUMMARY_INPUT_FULL_TEXT_LIMIT)


def _is_summary_noise_paragraph(paragraph: str) -> bool:
    lowered = paragraph.casefold()
    if "getty images" in lowered or "image source" in lowered:
        return True
    if "图像来源" in paragraph or "圖片來源" in paragraph:
        return True
    if lowered.startswith("end of "):
        return True
    return False


def _article_summary_sentence_records(paragraphs: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        for sentence in _split_summary_sentences(paragraph):
            clean = " ".join(sentence.split()).strip()
            if len(clean) < 20:
                continue
            records.append({"text": clean, "paragraph_index": paragraph_index})
    return records


def _split_summary_sentences(text: str) -> list[str]:
    normalized = re.sub(r"(?<=\.)\s+(?=[A-Z0-9\"'])", "\n", text.strip())
    normalized = re.sub(r"([。！？!?；;])", r"\1\n", normalized)
    out: list[str] = []
    for part in normalized.splitlines():
        clean = part.strip()
        if clean:
            out.append(clean)
    return out


def _summary_anchor_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,}", text or ""):
        if token.isascii():
            lowered = token.casefold()
            if lowered not in {"the", "and", "for", "with", "from", "that", "this", "into"}:
                terms.add(lowered)
            continue
        if len(token) <= 4:
            terms.add(token)
            continue
        for width in (2, 3):
            for start in range(0, len(token) - width + 1):
                terms.add(token[start : start + width])
    return terms


def _summary_overlap_count(text: str, anchor_terms: set[str]) -> int:
    if not anchor_terms:
        return 0
    return len(_summary_anchor_terms(text) & anchor_terms)


def _article_summary_sentence_score(
    text: str,
    *,
    anchor_terms: set[str],
    sentence_index: int,
    paragraph_index: int,
) -> int:
    lowered = text.casefold()
    overlap_count = _summary_overlap_count(text, anchor_terms)
    number_bonus = 2 if re.search(r"\d", text) else 0
    priority_bonus = sum(token in lowered for token in _ARTICLE_SUMMARY_INPUT_PRIORITY_TOKENS) * 2
    contra_bonus = sum(token in lowered for token in _ARTICLE_SUMMARY_INPUT_CONTRA_TOKENS)
    early_bonus = max(0, 6 - sentence_index) + max(0, 4 - paragraph_index)
    quote_penalty = 3 if any(mark in text for mark in ('"', "“", "”")) and overlap_count < 2 else 0
    return overlap_count * 4 + number_bonus + priority_bonus + contra_bonus + early_bonus - quote_penalty


def _prune_four_dimension_items(dims: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        "facts": _prune_dimension_items("facts", dims.get("facts", [])),
        "background": _prune_dimension_items("background", dims.get("background", [])),
        "impact": _prune_dimension_items("impact", dims.get("impact", [])),
        "contradictions": _prune_dimension_items("contradictions", dims.get("contradictions", [])),
    }


def _prune_dimension_items(section: str, items: list[str]) -> list[str]:
    cleaned = [item for item in _unique(items) if str(item or "").strip()]
    if len(cleaned) <= 1:
        return cleaned

    scores = [_dimension_item_value_score(section, item) for item in cleaned]
    strong_indices = [index for index, score in enumerate(scores) if score >= 3]
    if strong_indices:
        keep_indices = set(strong_indices)
        for index, score in enumerate(scores):
            if index in keep_indices or score < 0:
                continue
            keep_indices.add(index)
            if len(keep_indices) >= min(len(cleaned), 4):
                break
    else:
        non_negative = [index for index, score in enumerate(scores) if score >= 0]
        keep_indices = set(non_negative or [max(range(len(cleaned)), key=lambda idx: scores[idx])])

    pruned = [item for index, item in enumerate(cleaned) if index in keep_indices]
    return pruned or cleaned[:1]


def _dimension_item_value_score(section: str, text: str) -> int:
    lowered = str(text or "").casefold()
    high_hits = sum(token.casefold() in lowered for token in _HIGH_VALUE_DIMENSION_TOKENS)
    low_hits = sum(token.casefold() in lowered for token in _LOW_VALUE_DIMENSION_TOKENS)
    number_bonus = 1 if re.search(r"\d", lowered) and high_hits > 0 else 0
    risk_terms = ("风险", "分歧", "争议", "不确定", "risk", "dispute", "uncertain")
    section_bonus = 1 if section == "contradictions" and any(token in lowered for token in risk_terms) else 0
    return high_hits * 3 + number_bonus + section_bonus - low_hits * 4


def _truncate_article_summary_input(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    prefix = text[:max_chars]
    candidates = [
        prefix.rfind("\n\n"),
        prefix.rfind("。"),
        prefix.rfind("！"),
        prefix.rfind("？"),
        prefix.rfind(". "),
        prefix.rfind("; "),
        prefix.rfind("；"),
        prefix.rfind("! "),
        prefix.rfind("? "),
    ]
    boundary = max(candidates)
    if boundary >= max_chars // 2:
        return prefix[: boundary + 1].strip()
    return prefix.strip()


def _plan_types(
    summary_client: SummaryClient,
    article_dims: list[ArticleFourDimRecord],
    article_ids: dict[int, str],
    model_semaphore: threading.Semaphore,
    ctx: RunContext,
    log_lock: threading.Lock,
    *,
    type_classification_concurrency: int = 2,
):
    if not article_dims:
        return parse_type_plan('{"types":[],"ungrouped":[]}')
    batches = _batch_records(article_dims, batch_size=10)
    plans = []
    with ThreadPoolExecutor(max_workers=max(1, type_classification_concurrency)) as executor:
        future_map = {
            executor.submit(
                _plan_type_batch,
                summary_client,
                batch,
                article_ids,
                model_semaphore,
                ctx,
                log_lock,
                batch_index,
            ): batch_index
            for batch_index, batch in enumerate(batches, start=1)
        }
        for future in as_completed(future_map):
            plans.append(future.result())
    return _merge_type_plans(plans)


def _plan_type_batch(
    summary_client: SummaryClient,
    article_dims: list[ArticleFourDimRecord],
    article_ids: dict[int, str],
    model_semaphore: threading.Semaphore,
    ctx: RunContext,
    log_lock: threading.Lock,
    batch_index: int,
):
    prompt = build_type_plan_prompt(article_dims, article_ids)
    started = time.perf_counter()
    status = "ok"
    error = None
    with model_semaphore:
        try:
            if hasattr(summary_client, "plan_types"):
                raw = summary_client.plan_types(prompt)
            else:
                raw = summary_client.chat(prompt, system_prompt="你是新闻分类编辑。只输出严格 JSON。")
        except Exception as exc:
            raw = "not json"
            status = "error"
            error = str(exc)
    _log_json(
        ctx.run_dir / "logs" / "model_calls.log",
        {
            "task": "type_classification",
            "batch": batch_index,
            "model": getattr(summary_client, "model", ""),
            "status": status,
            "duration_ms": _duration_ms(started),
            "input_chars": len(prompt),
            "output_chars": len(raw),
            "error": error,
        },
        lock=log_lock,
    )
    return parse_type_plan(raw)


def _write_type_collections(
    ctx: RunContext,
    type_collections: list[TypeCollectionRecord],
    article_ids: dict[int, str],
    rss_ids: dict[int, str],
) -> None:
    for index, collection in enumerate(type_collections, start=1):
        type_id = collection.type_id or f"type_{index:03d}"
        filename = f"{type_id}_{_safe_filename(collection.name)}.json"
        payload = collection.to_dict()
        for article in payload["articles"]:
            pass
        ctx.write_json(f"type_collections/{filename}", payload)
        _log_json(
            ctx.run_dir / "logs" / f"{type_id}_{_safe_filename(collection.name)}.log",
            {
                "task": "type_collection",
                "type_id": type_id,
                "type": collection.name,
                "article_count": len(collection.articles),
                "status": "ok",
            },
        )


def _merge_type_plans(plans: list[TypePlanRecord]) -> TypePlanRecord:
    proposals: list[TypeProposalRecord] = []
    ungrouped: list[dict[str, str]] = []
    errors: list[str] = []
    for plan in sorted(plans, key=lambda item: _first_article_id(item)):
        proposals.extend(plan.types)
        ungrouped.extend(plan.ungrouped)
        errors.extend(plan.validation_errors)
    return TypePlanRecord(types=proposals, ungrouped=ungrouped, validation_errors=errors)


def _merge_same_named_type_collections(collections: list[TypeCollectionRecord]) -> list[TypeCollectionRecord]:
    merged: dict[str, TypeCollectionRecord] = {}
    order: list[str] = []
    for collection in collections:
        key = _canonical_type_name(collection.name)
        if key not in merged:
            merged[key] = TypeCollectionRecord(
                type_id=collection.type_id,
                name=key,
                rationale=collection.rationale,
                articles=collection.articles,
                source_rss_ids=collection.source_rss_ids,
                validation_errors=collection.validation_errors,
            )
            order.append(key)
            continue
        existing = merged[key]
        merged[key] = TypeCollectionRecord(
            type_id=existing.type_id,
            name=key,
            rationale=_join_unique([existing.rationale, collection.rationale]),
            articles=[*existing.articles, *collection.articles],
            source_rss_ids=_unique([*existing.source_rss_ids, *collection.source_rss_ids]),
            validation_errors=[*existing.validation_errors, *collection.validation_errors],
        )
    return [merged[key] for key in order]


def _canonical_type_name(name: str) -> str:
    clean = "".join(str(name or "").split())
    groups = {
        "通胀与利率": ("通胀", "CPI", "PPI", "美联储", "加息", "降息", "利率"),
        "数字金融": ("支付转型", "代币化", "加密合规", "稳定币", "链上", "区块链"),
        "加密资产": ("比特币", "以太坊", "加密市场", "牛市", "价格预测", "市占率"),
        "公司财报": ("财报", "业绩", "营收", "利润"),
        "企业并购": ("并购", "收购", "报价", "要约"),
        "特朗普访华": ("特朗普访华", "特习", "访华", "中美峰会"),
        "地缘冲突": ("伊朗", "俄乌", "中东", "停火", "战争", "台湾问题"),
        "AI芯片": ("AI芯片", "芯片", "半导体", "GPU", "算力"),
        "模型发布": ("模型发布", "大模型", "OpenAI", "ChatGPT", "Sora"),
    }
    for canonical, tokens in groups.items():
        if any(token.casefold() in clean.casefold() for token in tokens):
            return canonical
    return clean or "待复核"


def _first_article_id(plan: TypePlanRecord) -> str:
    for proposal in plan.types:
        if proposal.article_ids:
            return proposal.article_ids[0]
    return "zzzz"


def _batch_records(records: list[ArticleFourDimRecord], *, batch_size: int) -> list[list[ArticleFourDimRecord]]:
    return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]


def _synthesize_types(
    summary_client: SummaryClient,
    type_collections: list[TypeCollectionRecord],
    model_semaphore: threading.Semaphore,
    ctx: RunContext,
    log_lock: threading.Lock,
    *,
    type_synthesis_concurrency: int,
) -> list[TypeFourDimRecord]:
    out: list[TypeFourDimRecord] = []
    with ThreadPoolExecutor(max_workers=max(1, type_synthesis_concurrency)) as executor:
        future_map = {
            executor.submit(_synthesize_one_type, summary_client, collection, model_semaphore, ctx, log_lock): collection
            for collection in type_collections
        }
        for future in as_completed(future_map):
            out.append(future.result())
    order = {collection.type_id: index for index, collection in enumerate(type_collections)}
    return sorted(out, key=lambda item: order.get(item.type_id, 9999))


def _synthesize_one_type(
    summary_client: SummaryClient,
    collection: TypeCollectionRecord,
    model_semaphore: threading.Semaphore,
    ctx: RunContext,
    log_lock: threading.Lock,
) -> TypeFourDimRecord:
    started = time.perf_counter()
    status = "ok"
    error = None
    with model_semaphore:
        try:
            if hasattr(summary_client, "summarize_type_collection"):
                brief = summary_client.summarize_type_collection(collection.name, collection.articles)
            else:
                brief = summary_client.chat(_build_type_synthesis_prompt(collection), system_prompt="你是新闻简报编辑。严格基于给定文章四要素整合同一类型。")
        except Exception as exc:
            brief = _merge_type_fallback(collection)
            status = "error"
            error = str(exc)
    dims = _prune_four_dimension_items(parse_four_dimensions(brief))
    record = TypeFourDimRecord(
        type_id=collection.type_id,
        name=collection.name,
        facts=dims["facts"],
        background=dims["background"],
        impact=dims["impact"],
        contradictions=dims["contradictions"],
        category_group=_category_group_for_collection(collection),
        source_links=_unique([item.article.link for item in collection.articles if item.article.link]),
        source_refs=_source_refs(collection.articles),
        error=error,
    )
    payload = {
        "task": "type_synthesis",
        "type_id": collection.type_id,
        "type": collection.name,
        "article_count": len(collection.articles),
        "status": status,
        "duration_ms": _duration_ms(started),
        "error": error,
    }
    _log_json(ctx.run_dir / "logs" / "model_calls.log", payload, lock=log_lock)
    _log_json(ctx.run_dir / "logs" / f"{collection.type_id}_{_safe_filename(collection.name)}.log", payload)
    return record


def render_type_brief_markdown(type_dims: list[TypeFourDimRecord]) -> str:
    lines = ["# 国际新闻简报", ""]
    for group_name, items in _group_type_dims_by_category(type_dims):
        lines.extend([f"## {group_name}", ""])
        for item in items:
            lines.extend(
                [
                    f"### {item.name}",
                    "",
                    "#### 事实",
                    *_bullet_lines(item.facts),
                    "",
                    "#### 背景",
                    *_bullet_lines(item.background),
                    "",
                    "#### 产生的影响",
                    *_bullet_lines(item.impact),
                    "",
                    "#### 反面观点 / 数据矛盾点",
                    *_bullet_lines(item.contradictions),
                    "",
                ]
            )
            source_lines = _source_markdown_lines(item)
            if source_lines:
                lines.extend(["来源：", *source_lines, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_type_brief_markdown_flat(type_dims: list[TypeFourDimRecord]) -> str:
    lines = ["# 国际新闻简报", ""]
    for item in type_dims:
        lines.extend(
            [
                f"## {item.name}",
                "",
                "### 事实",
                *_bullet_lines(item.facts),
                "",
                "### 背景",
                *_bullet_lines(item.background),
                "",
                "### 产生的影响",
                *_bullet_lines(item.impact),
                "",
                "### 反面观点 / 数据矛盾点",
                *_bullet_lines(item.contradictions),
                "",
            ]
        )
        source_lines = _source_markdown_lines(item)
        if source_lines:
            lines.extend(["来源：", *source_lines, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_brief_html(markdown: str) -> str:
    body_lines: list[str] = []
    nav_items: list[tuple[str, str]] = []
    in_list = False
    in_section = False
    in_entry = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body_lines.append("</ul>")
            in_list = False

    def close_section() -> None:
        nonlocal in_section
        close_list()
        if in_section:
            body_lines.append("</div>")
            body_lines.append("</section>")
            in_section = False

    def close_entry() -> None:
        nonlocal in_entry
        close_section()
        if in_entry:
            body_lines.append("</article>")
            in_entry = False

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            close_entry()
            body_lines.append(f"<h1>{html.escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            close_entry()
            category_name = line[3:].strip()
            category_id = _anchor_id(category_name)
            nav_items.append((category_name, category_id))
            body_lines.append(f'<h2 class="category-title" id="{category_id}">{html.escape(category_name)}</h2>')
        elif line.startswith("### "):
            close_section()
            in_entry = True
            body_lines.append('<article class="brief-entry">')
            body_lines.append(f'<h3 class="entry-title">{html.escape(line[4:].strip())}</h3>')
        elif line.startswith("#### "):
            close_section()
            title = html.escape(line[4:].strip())
            body_lines.append('<section class="factor-section">')
            body_lines.append(f'<h3 class="factor-title">{title}</h3>')
            body_lines.append('<div class="factor-box">')
            in_section = True
        elif line.startswith("- "):
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{_render_inline_markdown(line[2:].strip())}</li>")
        else:
            close_list()
            if line == "来源：":
                close_section()
                body_lines.append('<section class="factor-section source-section">')
                body_lines.append('<h3 class="factor-title">来源</h3>')
                body_lines.append('<div class="factor-box source-box">')
                in_section = True
            else:
                body_lines.append(f"<p>{_render_inline_markdown(line)}</p>")
    close_entry()
    nav_html = _render_category_nav(nav_items)
    if nav_html:
        for index, line in enumerate(body_lines):
            if line.startswith("<h1>"):
                body_lines.insert(index + 1, nav_html)
                break
        else:
            body_lines.insert(0, nav_html)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>国际新闻简报</title>",
            "  <style>",
            "    :root{color-scheme:light;--text:#071832;--muted:#5b677a;--line:#d8dee8;--accent:#8fa0b7;--box:#fbfcfe;--box-line:#dfe5ee;--link:#235a9f;}",
            "    *{box-sizing:border-box}",
            "    body{font-family:Arial,'Microsoft YaHei','PingFang SC',sans-serif;max-width:900px;margin:26px auto 40px;padding:0 18px;line-height:1.8;color:var(--text);background:#fff;font-size:16px;}",
            "    h1{font-size:28px;line-height:1.25;margin:0 0 22px;font-weight:800;}",
            "    html{scroll-behavior:smooth;}",
            "    .category-nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 24px;padding:10px 0 14px;border-bottom:1px solid var(--line);}",
            "    .category-nav a{display:inline-flex;align-items:center;min-height:32px;padding:4px 12px;border:1px solid var(--box-line);border-radius:6px;background:#fff;color:var(--text);font-size:14px;font-weight:700;text-decoration:none;}",
            "    .category-nav a:hover{border-color:var(--accent);color:var(--link);}",
            "    .category-title{font-size:24px;line-height:1.3;margin:34px 0 18px;padding:12px 0 10px;border-top:2px solid var(--line);border-bottom:1px solid var(--line);font-weight:800;letter-spacing:0;}",
            "    .category-title{scroll-margin-top:18px;}",
            "    .brief-entry{margin:0 0 30px;}",
            "    .entry-title{font-size:22px;line-height:1.35;margin:0 0 18px;padding:0 0 14px;border-bottom:2px solid var(--line);font-weight:800;letter-spacing:0;}",
            "    .factor-section{margin:18px 0;}",
            "    .factor-title{font-size:18px;line-height:1.35;margin:0 0 10px;padding-left:12px;border-left:3px solid var(--accent);font-weight:800;letter-spacing:0;}",
            "    .factor-box{border:1px solid var(--box-line);border-radius:7px;background:var(--box);padding:14px 18px;}",
            "    .factor-box ul{margin:0;padding-left:20px;list-style:disc;}",
            "    .factor-box li{margin:0;color:var(--text);}",
            "    .factor-box li+li{margin-top:8px;}",
            "    .factor-box p{margin:0;}",
            "    .source-box ul{list-style:disc;padding-left:20px;}",
            "    .source-box li{margin:3px 0;color:var(--muted);}",
            "    a{color:var(--link);text-decoration:none;border-bottom:1px solid rgba(35,90,159,.25);}",
            "    a:hover{border-bottom-color:currentColor;}",
            "    @media (max-width:640px){body{padding:0 14px;font-size:15px}.entry-title{font-size:20px}.factor-title{font-size:17px}.factor-box{padding:12px 14px}}",
            "  </style>",
            "</head>",
            "<body>",
            *body_lines,
            "</body>",
            "</html>",
            "",
        ]
    )


def _assign_rss_ids(sources: list[RssSourceRecord]) -> list[RssSourceRecord]:
    out = []
    for index, source in enumerate(sources, start=1):
        source_id = f"rss_{index:03d}"
        out.append(
            RssSourceRecord(
                url=source.url,
                label=source.label,
                default_category=source.default_category,
                selected_reason=source.selected_reason,
                status=source.status,
            )
        )
        object.__setattr__(out[-1], "rss_id", source_id)
    return out


def _rss_source_from_checkpoint(data: dict[str, object], index: int) -> RssSourceRecord:
    source = RssSourceRecord(
        url=str(data.get("url") or ""),
        label=str(data.get("label") or ""),
        default_category=str(data.get("default_category") or "社会与其它"),
        selected_reason=str(data.get("selected_reason") or ""),
        status=str(data.get("status") or "selected"),
    )
    object.__setattr__(source, "rss_id", str(data.get("rss_id") or f"rss_{index:03d}"))
    return source


def _feed_item_from_checkpoint(data: dict[str, object]) -> FeedItemRecord:
    return FeedItemRecord(
        article=_article_from_checkpoint(data.get("article")),
        default_category=str(data.get("default_category") or "社会与其它"),
        source_label=str(data.get("source_label") or ""),
        status=str(data.get("status") or "queued"),
        skip_reason=str(data.get("skip_reason") or ""),
    )


def _article_text_from_checkpoint(data: dict[str, object]) -> ArticleTextRecord:
    article = _article_from_checkpoint(data.get("article"))
    stored_html_url = str(data.get("stored_html_url") or "").strip()
    if stored_html_url:
        article = Article(
            title=article.title,
            link=stored_html_url,
            pub_date=article.pub_date,
            description=article.description,
            source=article.source,
            feed_url=article.feed_url,
        )
    return ArticleTextRecord(
        rss_id=str(data.get("rss_id") or ""),
        article_id=str(data.get("article_id") or ""),
        article=article,
        default_category=str(data.get("default_category") or "社会与其它"),
        text=str(data.get("text") or ""),
        detected_language=str(data.get("detected_language") or "unknown"),
        translated=bool(data.get("translated", False)),
        fetch_error=_optional_checkpoint_str(data.get("fetch_error")),
        extract_error=_optional_checkpoint_str(data.get("extract_error")),
        translation_error=_optional_checkpoint_str(data.get("translation_error")),
    )


def _article_from_checkpoint(value: object) -> Article:
    data = value if isinstance(value, dict) else {}
    return Article(
        title=str(data.get("title") or ""),
        link=str(data.get("link") or ""),
        pub_date=str(data.get("pub_date") or ""),
        description=str(data.get("description") or ""),
        source=str(data.get("source") or ""),
        feed_url=str(data.get("feed_url") or ""),
    )


def _optional_checkpoint_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _group_feed_items_by_rss_id(
    feed_items: list[FeedItemRecord],
    text_records: list[ArticleTextRecord],
) -> dict[str, list[FeedItemRecord]]:
    rss_id_by_link = {item.article.link: item.rss_id for item in text_records}
    grouped: dict[str, list[FeedItemRecord]] = {}
    for item in feed_items:
        rss_id = rss_id_by_link.get(item.article.link)
        if rss_id:
            grouped.setdefault(rss_id, []).append(item)
    return grouped


def _source_dict(source: RssSourceRecord) -> dict[str, object]:
    data = source.to_dict()
    data["rss_id"] = getattr(source, "rss_id", "")
    return data


def _text_checkpoint(item: ArticleTextRecord) -> dict[str, object]:
    return item.to_dict()


def _research_score_dict(item: ResearchScoreRecord) -> dict[str, object]:
    return item.to_dict()


def _low_quality_checkpoint(item: ArticleTextRecord) -> dict[str, object]:
    data = item.to_dict()
    data["quality_error"] = "low_quality_text"
    return data


def _article_dim_dict(item: ArticleFourDimRecord, article_ids: dict[int, str], rss_ids: dict[int, str]) -> dict[str, object]:
    data = item.to_dict()
    data["article_id"] = article_ids.get(id(item), "")
    data["rss_id"] = rss_ids.get(id(item), "")
    return data


def _group_by_rss_id(records: list[ArticleTextRecord]) -> dict[str, list[ArticleTextRecord]]:
    grouped: dict[str, list[ArticleTextRecord]] = {}
    for item in records:
        grouped.setdefault(item.rss_id, []).append(item)
    for items in grouped.values():
        items.sort(key=lambda item: item.article_id)
    return grouped


def _group_research_scores_by_rss_id(records: list[ResearchScoreRecord]) -> dict[str, list[ResearchScoreRecord]]:
    grouped: dict[str, list[ResearchScoreRecord]] = {}
    for item in records:
        grouped.setdefault(item.rss_id, []).append(item)
    for items in grouped.values():
        items.sort(key=lambda item: item.article_id)
    return grouped


def _group_dims_by_rss_id(
    records: list[ArticleFourDimRecord], rss_ids: dict[int, str]
) -> dict[str, list[ArticleFourDimRecord]]:
    grouped: dict[str, list[ArticleFourDimRecord]] = {}
    for item in records:
        grouped.setdefault(rss_ids.get(id(item), ""), []).append(item)
    grouped.pop("", None)
    return grouped


def _model_failure_brief(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return "\n".join(
        [
            "#### 事实",
            f"- 模型生成失败：{message}",
            "",
            "#### 背景",
            "- 该条文章已抓取，但模型未能完成四要素归纳。",
            "",
            "#### 产生的影响",
            "- 当前自动简报无法使用该条文章形成可靠结论。",
            "",
            "#### 反面观点 / 数据矛盾点",
            "- 该条文章未完成模型归纳，需人工复核。",
        ]
    )


def _build_type_synthesis_prompt(collection: TypeCollectionRecord) -> str:
    records = []
    for item in collection.articles:
        records.append(
            {
                "title": item.article.title,
                "url": item.article.link,
                "facts": item.facts,
                "background": item.background,
                "impact": item.impact,
                "contradictions": item.contradictions,
            }
        )
    return f"""请整合类型「{collection.name}」下文章，输出四要素。

要求只基于输入，不要借用其它类型信息。

{json.dumps(records, ensure_ascii=False, indent=2)}

#### 事实
- ...

#### 背景
- ...

#### 产生的影响
- ...

#### 反面观点 / 数据矛盾点
- ..."""


def _merge_type_fallback(collection: TypeCollectionRecord) -> str:
    return "\n".join(
        [
            "#### 事实",
            *_bullet_lines(_unique([v for item in collection.articles for v in item.facts])),
            "",
            "#### 背景",
            *_bullet_lines(_unique([v for item in collection.articles for v in item.background])),
            "",
            "#### 产生的影响",
            *_bullet_lines(_unique([v for item in collection.articles for v in item.impact])),
            "",
            "#### 反面观点 / 数据矛盾点",
            *_bullet_lines(_unique([v for item in collection.articles for v in item.contradictions])),
        ]
    )


def _source_refs(articles: list[ArticleFourDimRecord]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in articles:
        url = (item.article.link or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = " ".join((item.article.title or "").split())
        source = " ".join((item.article.source or "").split())
        refs.append(
            {
                "title": title or source or url,
                "url": url,
                "source": source,
            }
        )
    return refs


FIXED_CATEGORY_GROUPS = ["国际形式", "人工智能与科技", "金融市场与宏观", "财经信息"]

_TYPE_COLLECTION_HARD_SIGNAL_TOKENS = (
    "贸易",
    "关税",
    "谈判",
    "机制",
    "审批",
    "采购",
    "订单",
    "出口",
    "芯片",
    "算力",
    "半导体",
    "人工智能",
    "AI",
    "财报",
    "营收",
    "利润",
    "融资",
    "投资",
    "通胀",
    "利率",
    "汇率",
    "美联储",
    "股市",
    "比特币",
    "加密",
    "伊朗",
    "乌克兰",
    "台湾",
    "战争",
    "冲突",
    "制裁",
    "特习",
    "访华",
    "中美",
)

_TYPE_COLLECTION_EDGE_SIGNAL_TOKENS = (
    "着装",
    "穿着",
    "服装",
    "礼宾",
    "机场",
    "专机",
    "迎接",
    "抵达",
    "行程",
    "花絮",
    "影片",
    "风景",
    "社交媒体",
    "热议",
    "火爆全网",
    "趣味",
    "异见",
    "宗教",
    "人权",
    "牧师",
    "拘留",
    "符号",
)


def _category_group_for_collection(collection: TypeCollectionRecord) -> str:
    name_group = _category_group_from_text(collection.name)
    if name_group:
        return name_group

    votes: dict[str, int] = {}
    for item in collection.articles:
        group = _normalize_category_group(item.final_category) or _normalize_category_group(item.default_category)
        if group:
            votes[group] = votes.get(group, 0) + 1
    if votes:
        return max(FIXED_CATEGORY_GROUPS, key=lambda name: (votes.get(name, 0), -FIXED_CATEGORY_GROUPS.index(name)))

    rationale_group = _category_group_from_text(collection.rationale)
    if rationale_group:
        return rationale_group

    blob = " ".join(
        [
            *[item.article.title for item in collection.articles],
            *[value for item in collection.articles for value in item.facts[:3]],
            *[item.article.source for item in collection.articles],
        ]
    )
    evidence_group = _category_group_from_text(blob)
    if evidence_group:
        return evidence_group
    return "财经信息"


def _filter_type_collections_for_brief(
    type_collections: list[TypeCollectionRecord],
    research_scores: list[ResearchScoreRecord],
) -> list[TypeCollectionRecord]:
    score_by_link = {
        (score.article.link or "").strip(): score
        for score in research_scores
        if (score.article.link or "").strip()
    }
    return [collection for collection in type_collections if _should_keep_type_collection_for_brief(collection, score_by_link)]


def _should_keep_type_collection_for_brief(
    collection: TypeCollectionRecord,
    score_by_link: dict[str, ResearchScoreRecord],
) -> bool:
    linked_scores = [
        score_by_link.get((item.article.link or "").strip())
        for item in collection.articles
        if (item.article.link or "").strip()
    ]
    linked_scores = [item for item in linked_scores if item is not None]
    if any(item.decision == "keep" for item in linked_scores):
        return True

    signal_score = _type_collection_signal_score(collection, _TYPE_COLLECTION_HARD_SIGNAL_TOKENS)
    edge_score = _type_collection_signal_score(collection, _TYPE_COLLECTION_EDGE_SIGNAL_TOKENS)
    max_score = max((item.score for item in linked_scores), default=0)

    if signal_score >= 2:
        return True
    if max_score >= 60 and edge_score <= 1:
        return True
    return False


def _type_collection_signal_score(collection: TypeCollectionRecord, tokens: tuple[str, ...]) -> int:
    blob = " ".join(
        [
            collection.name,
            collection.rationale,
            *[item.article.title for item in collection.articles],
            *[value for item in collection.articles for value in item.facts[:2]],
            *[value for item in collection.articles for value in item.background[:1]],
            *[value for item in collection.articles for value in item.impact[:1]],
        ]
    ).casefold()
    return sum(1 for token in tokens if token.casefold() in blob)


def _normalize_category_group(value: str) -> str | None:
    text = str(value or "")
    if not text:
        return None
    if any(token in text for token in ("国际", "地缘", "外交", "战争", "冲突", "中东", "伊朗", "乌克兰", "台湾")):
        return "国际形式"
    if any(token in text for token in ("人工智能", "科技", "AI", "芯片", "算力", "半导体", "机器人", "大模型")):
        return "人工智能与科技"
    if any(token in text for token in ("金融市场", "宏观", "通胀", "利率", "美联储", "股市", "债券", "油价", "比特币", "加密")):
        return "金融市场与宏观"
    if any(token in text for token in ("财经", "财报", "公司", "产业", "营收", "利润", "并购", "投资", "支付")):
        return "财经信息"
    return None


def _category_group_from_text(text: str) -> str:
    lowered = text.casefold()
    priority_keywords = [
        ("国际形式", ("特朗普访华", "特习", "中美", "外交", "战争", "冲突", "中东", "伊朗", "乌克兰", "台湾", "trump", "summit", "iran", "war")),
        ("人工智能与科技", ("人工智能", "科技", "ai芯片", "芯片", "算力", "半导体", "机器人", "大模型", "openai", "nvidia", "robot")),
        ("金融市场与宏观", ("金融市场", "宏观", "通胀", "利率", "美联储", "股市", "债券", "油价", "比特币", "加密", "inflation", "fed", "bitcoin")),
        ("财经信息", ("财经", "财报", "公司", "产业", "营收", "利润", "并购", "投资", "支付", "earnings", "revenue", "payment")),
    ]
    for group, tokens in priority_keywords:
        if any(token.casefold() in lowered for token in tokens):
            return group
    return ""


def _group_type_dims_by_category(type_dims: list[TypeFourDimRecord]) -> list[tuple[str, list[TypeFourDimRecord]]]:
    grouped: dict[str, list[TypeFourDimRecord]] = {name: [] for name in FIXED_CATEGORY_GROUPS}
    for item in type_dims:
        group = _normalize_category_group(item.category_group) or _category_group_from_text(item.name)
        grouped[group].append(item)
    return [(name, grouped[name]) for name in FIXED_CATEGORY_GROUPS if grouped[name]]


def _source_markdown_lines(item: TypeFourDimRecord) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for ref in item.source_refs:
        url = str(ref.get("url", "")).strip()
        if not url or url in seen:
            continue
        title = str(ref.get("title", "") or ref.get("source", "") or url).strip()
        if _is_polluted_source_title(title):
            seen.add(url)
            continue
        seen.add(url)
        lines.append(f"- [{_escape_markdown_link_text(title)}]({url})")
    for url in item.source_links:
        clean_url = str(url or "").strip()
        if clean_url and clean_url not in seen:
            seen.add(clean_url)
            lines.append(f"- {clean_url}")
    return lines


def _is_polluted_source_title(title: str) -> bool:
    clean = " ".join(str(title or "").split())
    if not clean:
        return True
    lowered = clean.casefold()
    if lowered.startswith(("http://", "https://")):
        return True
    if "news.google.com/rss/articles" in lowered and ("[" in clean or "(" in clean or ")" in clean):
        return True
    if "excerpt" in lowered and ("[" in clean or "(" in clean):
        return True
    if clean.startswith("[\\[") or clean.startswith("[[") or clean.startswith("!["):
        return True
    if re.search(r"!?\[[^\]]+\]\([^)]*https?://[^)]*\)", clean):
        return True
    if clean.count("http://") + clean.count("https://") > 0:
        return True
    return False


def _render_inline_markdown(text: str) -> str:
    result: list[str] = []
    cursor = 0
    pattern = re.compile(r"\[((?:\\.|[^\]])+)\]\((https?://[^)\s]+)\)")
    for match in pattern.finditer(text):
        result.append(html.escape(text[cursor : match.start()]))
        label = html.escape(_unescape_markdown_link_text(match.group(1)))
        url = html.escape(match.group(2), quote=True)
        result.append(f'<a href="{url}">{label}</a>')
        cursor = match.end()
    result.append(html.escape(text[cursor:]))
    return "".join(result)


def _unescape_markdown_link_text(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _render_category_nav(nav_items: list[tuple[str, str]]) -> str:
    if not nav_items:
        return ""
    links = [
        f'<a href="#{html.escape(anchor, quote=True)}">{html.escape(name)}</a>'
        for name, anchor in _unique_nav_items(nav_items)
    ]
    return '<nav class="category-nav">' + "".join(links) + "</nav>"


def _unique_nav_items(nav_items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, anchor in nav_items:
        if anchor in seen:
            continue
        seen.add(anchor)
        out.append((name, anchor))
    return out


def _anchor_id(value: str) -> str:
    clean = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value).strip("-")
    return f"category-{clean or 'section'}"


def _escape_markdown_link_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _bullet_lines(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values if value] or ["- 未形成可追溯要点。"]


def _log_json(path: Path, payload: dict[str, object], lock: threading.Lock | None = None) -> None:
    payload = {"time": datetime.now(timezone.utc).isoformat(), **payload}
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    if lock is None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _safe_filename(value: str) -> str:
    clean = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value).strip("_")
    return clean[:40] or "type"


def _join_unique(values: list[str]) -> str:
    return "; ".join(_unique(values))


def _unique(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        clean = " ".join(str(value).split())
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out
