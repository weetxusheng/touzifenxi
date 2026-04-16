"""C114 第 3 步外部搜索与补充链接精筛模块。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..facades.intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    provider_stats_name,
    step_2_checklist_name,
    step_3_results_name,
)
from ..llm import MiniMaxChatClient
from ..runtime.checkpoint import StepCheckpointStore
from ..runtime.settings import AppPaths, load_c114_runtime_config, resolve_override_path
from .logging import SEARCH_TRACE_LOG_PREFIX, SearchTraceLogger, search_trace_log_name_for_step
from .providers import (
    AutoSearchClient,
    BaiduSearchClient,
    GooglePlaywrightClient,
    MetasoClient,
    TavilyClient,
    build_auto_search_client,
    choose_search_provider,
    search_with_provider,
)
from .review import (
    SEARCH_REVIEW_PROMPT_PATH,
    STEP3_TOPIC_BATCH_ITEM_LIMIT,
    apply_search_review_result,
    apply_search_review_topic_result,
    apply_search_review_topic_result_partial,
    auto_review_search_payload,
    build_search_review_prompt_payload,
    build_search_review_topic_payload,
    split_search_review_items_for_topic,
)
from .types import (
    ArticleSearchPayload,
    QueryResultBucket,
    SearchArticleInput,
    SearchCategoryPayload,
    SearchOutputPaths,
    SearchQuery,
    SearchResult,
    SearchWorkflowPayload,
)
from .utils import (
    classify_domain,
    compact_text,
    compute_matched_terms,
    domain_matches,
    enrich_selected_results,
    extract_domain,
    filter_recent_results,
    infer_published_at,
    load_domain_config,
    normalize_baidu_results,
    normalize_google_results,
    normalize_metaso_results,
    normalize_search_results,
    normalize_url,
    rank_search_results,
    result_is_blocked,
    select_results_for_extract,
    unique_search_results,
)
from .yaml_io import (
    build_review_view_name,
    build_search_queries,
    ensure_search_checklist_keywords,
    escape_yaml_scalar,
    load_search_checklist_yaml,
    parse_yaml_list_item,
    parse_yaml_value,
    render_provider_usage_stats,
    render_result_list,
    render_search_results_yaml,
    render_search_review_view,
    save_search_results,
    unquote_yaml_scalar,
    validate_search_checklist_items,
)

SKILL_ROOT = Path(__file__).resolve().parents[3]
SEARCH_CONFIG_PATH = SKILL_ROOT / "config" / "search_domains.json"


def resolve_search_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    output_override: str | None = None,
    provider: str = "auto",
) -> SearchOutputPaths:
    """Resolve the canonical step 2 input and step 3 output paths for one run."""

    run_dir: Path | None = None
    if input_override:
        input_path = resolve_override_path(paths.project_root, input_override)
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            input_path = (run_dir / step_2_checklist_name(report_date)).resolve()
        else:
            input_path = (c114_reports_root(paths.reports_dir) / step_2_checklist_name(report_date)).resolve()

    if output_override:
        output_path = resolve_override_path(paths.project_root, output_override)
    else:
        if input_override or run_dir:
            output_path = (input_path.parent / step_3_results_name(report_date)).resolve()
        else:
            output_path = (c114_reports_root(paths.reports_dir) / step_3_results_name(report_date)).resolve()
    return SearchOutputPaths(input_path=input_path, output_path=output_path, provider=provider)


def run_search_workflow(
    input_path: Path,
    report_date: str,
    per_query_limit: int,
    per_article_limit: int,
    extract_limit: int,
    provider_name: str = "auto",
    client: Any | None = None,
    llm_client: MiniMaxChatClient | None = None,
    generated_at: str | None = None,
    trace_logger: SearchTraceLogger | None = None,
    checkpoint_store: StepCheckpointStore | None = None,
) -> SearchWorkflowPayload:
    """Execute step 3 end-to-end for one report date and produce normalized results."""

    yaml_report_date, articles = load_search_checklist_yaml(input_path)
    if yaml_report_date and yaml_report_date != report_date:
        raise ValueError(f"输入搜索清单日期为 {yaml_report_date}，与命令日期 {report_date} 不一致。")
    runtime_config = load_c114_runtime_config(SKILL_ROOT)
    articles = ensure_search_checklist_keywords(
        articles,
        llm_client,
        keyword_count=runtime_config.search_keyword_count,
    )
    validate_search_checklist_items(articles, keyword_count=runtime_config.search_keyword_count)

    domain_config = load_domain_config(SEARCH_CONFIG_PATH)
    search_client = client or build_auto_search_client(runtime_config=runtime_config, provider_mode=provider_name)
    grouped: dict[str, list[ArticleSearchPayload]] = {}
    report_day = date.fromisoformat(report_date)
    article_workers = min(8, max(1, len(articles)))
    with ThreadPoolExecutor(max_workers=article_workers) as executor:
        future_pairs = [
            (
                article,
                executor.submit(
                    search_article,
                    article=article,
                    report_date=report_day,
                    per_query_limit=per_query_limit,
                    per_article_limit=per_article_limit,
                    extract_limit=extract_limit,
                    client=search_client,
                    domain_config=domain_config,
                    recent_days=runtime_config.search_recent_days,
                    keyword_count=runtime_config.search_keyword_count,
                    trace_logger=trace_logger,
                    checkpoint_store=checkpoint_store,
                ),
            )
            for article in articles
        ]
        topic_payloads: dict[str, list[tuple[int, ArticleSearchPayload]]] = {}
        for index, (article, future) in enumerate(future_pairs):
            payload = future.result()
            topic_payloads.setdefault(article.topic, []).append((index, payload))
        for topic, ordered_payloads in topic_payloads.items():
            grouped[topic] = [payload for _, payload in sorted(ordered_payloads, key=lambda item: item[0])]

    categories = [SearchCategoryPayload(topic=topic, items=grouped[topic]) for topic in grouped]
    payload = SearchWorkflowPayload(
        report_date=report_date,
        provider=provider_name,
        input_path=input_path,
        generated_at=generated_at or datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )
    if llm_client is not None:
        payload = auto_review_search_payload(payload, llm_client, checkpoint_store=checkpoint_store)
    return payload


def search_article(
    article: SearchArticleInput,
    report_date: date,
    per_query_limit: int,
    per_article_limit: int,
    extract_limit: int,
    client: Any,
    domain_config: dict[str, list[str]],
    recent_days: int,
    keyword_count: int = 1,
    trace_logger: SearchTraceLogger | None = None,
    checkpoint_store: StepCheckpointStore | None = None,
) -> ArticleSearchPayload:
    """Search one article's title and keyword queries, then rank and enrich results."""

    article_entry_id = build_step3_article_entry_id(article)
    if checkpoint_store is not None:
        cached_article = checkpoint_store.get_result(article_entry_id)
        if isinstance(cached_article, dict):
            return article_search_payload_from_dict(cached_article)

    queries = build_search_queries(article, keyword_count=keyword_count)
    buckets: list[QueryResultBucket] = []
    raw_results: list[SearchResult] = []
    max_workers = min(3, max(1, len(queries)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_pairs = []
        direct_bucket_map: dict[tuple[str, str], QueryResultBucket] = {}
        for query in queries:
            entry_id = build_step3_query_entry_id(article, query)
            if checkpoint_store is not None:
                cached_bucket = checkpoint_store.get_result(entry_id)
                if isinstance(cached_bucket, dict):
                    direct_bucket_map[(query.query_type, query.value)] = query_result_bucket_from_dict(cached_bucket)
                    continue
            future_pairs.append(
                (
                    query,
                    executor.submit(
                        search_query_bucket,
                        client,
                        query,
                        per_query_limit,
                        article.original_title,
                        domain_config,
                        report_date,
                        recent_days,
                        trace_logger,
                    ),
                )
            )
        bucket_map: dict[tuple[str, str], QueryResultBucket] = {}
        bucket_map.update(direct_bucket_map)
        for query, future in future_pairs:
            try:
                bucket = future.result()
            except Exception as error:
                if checkpoint_store is not None:
                    checkpoint_store.record_entry(
                        entry_id=build_step3_query_entry_id(article, query),
                        status="error",
                        source="search",
                        request_context=build_step3_query_request_context(article, query),
                        error={"message": str(error)},
                    )
                raise
            if checkpoint_store is not None:
                checkpoint_store.record_entry(
                    entry_id=build_step3_query_entry_id(article, query),
                    status="success",
                    provider=bucket.provider,
                    source="search",
                    request_context=build_step3_query_request_context(article, query),
                    result=query_result_bucket_to_dict(bucket),
                )
            bucket_map[(query.query_type, query.value)] = bucket
        for query in queries:
            bucket = bucket_map[(query.query_type, query.value)]
            buckets.append(bucket)
            raw_results.extend(bucket.results)

    deduped = unique_search_results(filter_recent_results(raw_results, report_date=report_date, max_age_days=recent_days))
    ranked = rank_search_results(deduped, report_date=report_date, original_title=article.original_title)
    search_results = ranked[:per_article_limit]
    selected_results = enrich_selected_results(
        search_results=search_results,
        article=article,
        extract_limit=extract_limit,
        client=client,
    )
    payload = ArticleSearchPayload(
        topic=article.topic,
        channel=article.channel,
        original_title=article.original_title,
        original_url=article.original_url,
        original_published_at=article.original_published_at,
        queries=buckets,
        search_results=search_results,
        selected_results=selected_results,
    )
    if checkpoint_store is not None:
        checkpoint_store.record_entry(
            entry_id=article_entry_id,
            status="success",
            source="search_article",
            request_context={
                "topic": article.topic,
                "channel": article.channel,
                "original_title": article.original_title,
                "original_url": article.original_url,
                "original_published_at": article.original_published_at,
            },
            result=article_search_payload_to_dict(payload),
        )
    return payload


def build_step3_query_entry_id(article: SearchArticleInput, query: SearchQuery) -> str:
    return f"query::{article.topic}::{article.original_title}::{query.query_type}::{query.value}"


def build_step3_article_entry_id(article: SearchArticleInput | ArticleSearchPayload) -> str:
    return f"article::{article.topic}::{article.original_title}"


def build_step3_query_request_context(article: SearchArticleInput, query: SearchQuery) -> dict[str, object]:
    return {
        "topic": article.topic,
        "channel": article.channel,
        "original_title": article.original_title,
        "original_url": article.original_url,
        "original_published_at": article.original_published_at,
        "query_type": query.query_type,
        "query": query.value,
    }


def query_result_bucket_to_dict(bucket: QueryResultBucket) -> dict[str, object]:
    return asdict(bucket)


def query_result_bucket_from_dict(payload: dict[str, Any]) -> QueryResultBucket:
    return QueryResultBucket(
        query=str(payload.get("query", "")),
        query_type=str(payload.get("query_type", "")),
        provider=str(payload.get("provider", "")),
        results=[search_result_from_dict(item) for item in payload.get("results") or [] if isinstance(item, dict)],
    )


def article_search_payload_to_dict(payload: ArticleSearchPayload) -> dict[str, object]:
    return asdict(payload)


def article_search_payload_from_dict(payload: dict[str, Any]) -> ArticleSearchPayload:
    return ArticleSearchPayload(
        topic=str(payload.get("topic", "")),
        channel=str(payload.get("channel", "")),
        original_title=str(payload.get("original_title", "")),
        original_url=str(payload.get("original_url", "")),
        original_published_at=str(payload.get("original_published_at", "")),
        queries=[
            query_result_bucket_from_dict(item)
            for item in payload.get("queries") or []
            if isinstance(item, dict)
        ],
        search_results=[
            search_result_from_dict(item)
            for item in payload.get("search_results") or []
            if isinstance(item, dict)
        ],
        selected_results=[
            search_result_from_dict(item)
            for item in payload.get("selected_results") or []
            if isinstance(item, dict)
        ],
    )


def search_result_from_dict(payload: dict[str, Any]) -> SearchResult:
    return SearchResult(
        query=str(payload.get("query", "")),
        query_type=str(payload.get("query_type", "")),
        result_title=str(payload.get("result_title", "")),
        url=str(payload.get("url", "")),
        domain=str(payload.get("domain", "")),
        published_at=str(payload.get("published_at", "")),
        snippet=str(payload.get("snippet", "")),
        score=float(payload.get("score", 0.0) or 0.0),
        is_official=bool(payload.get("is_official", False)),
        source_tier=str(payload.get("source_tier", "")),
        matched_terms=[str(item) for item in payload.get("matched_terms") or []],
        extract_text=str(payload.get("extract_text", "")),
        extract_status=str(payload.get("extract_status", "")),
        review_status=str(payload.get("review_status", "pending")),
        keep_level=str(payload.get("keep_level", "")),
        review_reason=str(payload.get("review_reason", "")),
        relevance_note=str(payload.get("relevance_note", "")),
        value_type=str(payload.get("value_type", "")),
    )


def search_query_bucket(
    client: Any,
    query: SearchQuery,
    per_query_limit: int,
    original_title: str,
    domain_config: dict[str, list[str]],
    report_date: date,
    recent_days: int,
    trace_logger: SearchTraceLogger | None = None,
) -> QueryResultBucket:
    started_at = time.perf_counter()
    provider_name = "unknown"
    request_id: str | None = None
    if trace_logger is not None:
        trace_logger.write_started(query=query, provider=provider_name)
        request_id = trace_logger.current_request_id()
    try:
        provider_name, raw_provider_results = search_with_provider(client, query, per_query_limit)
    except Exception as error:
        if trace_logger is not None:
            trace_logger.write_record(
                request_id=request_id,
                query=query,
                provider=provider_name,
                status="error",
                duration_ms=(time.perf_counter() - started_at) * 1000.0,
                error_message=str(error),
            )
        raise
    normalized_results = [
        result
        for result in normalize_search_results(
            raw_provider_results,
            query=query,
            original_title=original_title,
            domain_config=domain_config,
            provider_name=provider_name,
        )
        if not result_is_blocked(result, domain_config)
    ]
    recent_results = filter_recent_results(normalized_results, report_date=report_date, max_age_days=recent_days)
    if trace_logger is not None:
        trace_logger.write_record(
            request_id=request_id,
            query=query,
            provider=provider_name,
            status="success",
            duration_ms=(time.perf_counter() - started_at) * 1000.0,
            raw_result_count=len(raw_provider_results),
            result_count=len(recent_results),
            provider_results=raw_provider_results,
            retained_results=recent_results,
        )
    return QueryResultBucket(
        query=query.value,
        query_type=query.query_type,
        provider=provider_name,
        results=recent_results,
    )


__all__ = [
    "SEARCH_CONFIG_PATH",
    "SEARCH_REVIEW_PROMPT_PATH",
    "SEARCH_TRACE_LOG_PREFIX",
    "STEP3_TOPIC_BATCH_ITEM_LIMIT",
    "ArticleSearchPayload",
    "AutoSearchClient",
    "BaiduSearchClient",
    "GooglePlaywrightClient",
    "MetasoClient",
    "QueryResultBucket",
    "SearchArticleInput",
    "SearchCategoryPayload",
    "SearchOutputPaths",
    "SearchQuery",
    "SearchResult",
    "SearchTraceLogger",
    "SearchWorkflowPayload",
    "TavilyClient",
    "apply_search_review_result",
    "apply_search_review_topic_result",
    "apply_search_review_topic_result_partial",
    "auto_review_search_payload",
    "build_auto_search_client",
    "build_review_view_name",
    "build_search_queries",
    "build_search_review_prompt_payload",
    "build_search_review_topic_payload",
    "choose_search_provider",
    "classify_domain",
    "compact_text",
    "compute_matched_terms",
    "domain_matches",
    "enrich_selected_results",
    "escape_yaml_scalar",
    "extract_domain",
    "filter_recent_results",
    "infer_published_at",
    "load_domain_config",
    "load_search_checklist_yaml",
    "normalize_baidu_results",
    "normalize_google_results",
    "normalize_metaso_results",
    "normalize_search_results",
    "normalize_url",
    "parse_yaml_list_item",
    "parse_yaml_value",
    "provider_stats_name",
    "rank_search_results",
    "render_provider_usage_stats",
    "render_result_list",
    "render_search_results_yaml",
    "render_search_review_view",
    "resolve_search_output_paths",
    "result_is_blocked",
    "run_search_workflow",
    "save_search_results",
    "search_article",
    "search_query_bucket",
    "search_trace_log_name_for_step",
    "search_with_provider",
    "select_results_for_extract",
    "split_search_review_items_for_topic",
    "unquote_yaml_scalar",
    "unique_search_results",
    "validate_search_checklist_items",
]
