"""C114 step 3 ai_review 的批量审查与回填逻辑。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    coerce_json_object_payload,
    complete_json_with_postprocess_retry,
    load_prompt_text,
)
from ..runtime.checkpoint import StepCheckpointStore
from .types import ArticleSearchPayload, SearchCategoryPayload, SearchResult, SearchWorkflowPayload

SKILL_ROOT = Path(__file__).resolve().parents[3]
SEARCH_REVIEW_PROMPT_PATH = SKILL_ROOT / "prompts" / "search-review-agent.md"
STEP3_TOPIC_BATCH_ITEM_LIMIT = 4


def auto_review_search_payload(
    payload: SearchWorkflowPayload,
    llm_client: MiniMaxChatClient,
    *,
    checkpoint_store: StepCheckpointStore | None = None,
) -> SearchWorkflowPayload:
    begin_llm_step(llm_client, "step_3")
    system_prompt = load_prompt_text(SEARCH_REVIEW_PROMPT_PATH)
    reviewed_categories: list[SearchCategoryPayload] = []
    for category in payload.categories:
        if not any(item.selected_results for item in category.items):
            reviewed_categories.append(category)
            continue
        pending_items = []
        reviewed_items_by_title: dict[str, ArticleSearchPayload] = {}
        for item in category.items:
            if checkpoint_store is not None:
                cached = checkpoint_store.get_result(build_step3_review_article_entry_id(item))
                if isinstance(cached, dict):
                    reviewed_items_by_title[item.original_title] = _article_search_payload_from_dict(cached)
                    continue
                hydrated_item = _apply_cached_single_review_results(item, checkpoint_store)
                if _is_reviewed_article_payload(hydrated_item):
                    checkpoint_store.record_entry(
                        entry_id=build_step3_review_article_entry_id(hydrated_item),
                        status="success",
                        provider="",
                        source="ai_review_article",
                        request_context={"topic": hydrated_item.topic, "original_title": hydrated_item.original_title},
                        result=_article_search_payload_to_dict(hydrated_item),
                    )
                    reviewed_items_by_title[item.original_title] = hydrated_item
                    continue
                item = hydrated_item
            pending_items.append(item)
        for batch_index, batch_items in enumerate(split_search_review_items_for_topic(pending_items), start=1):
            batch_category = SearchCategoryPayload(topic=category.topic, items=batch_items)
            batch_entry_id = build_step3_review_batch_entry_id(category.topic, batch_index)
            try:
                batch_reviewed_items, missing_targets = complete_search_review_batch_with_retry(
                    llm_client=llm_client,
                    system_prompt=system_prompt,
                    category=batch_category,
                    items=batch_items,
                )
                if checkpoint_store is not None:
                    checkpoint_store.record_entry(
                        entry_id=batch_entry_id,
                        status="success",
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        source="ai_review_batch",
                        request_context={"topic": category.topic, "batch_index": batch_index},
                        result={"original_titles": [item.original_title for item in batch_items]},
                    )
                _record_batch_review_successes(
                    batch_reviewed_items=batch_reviewed_items,
                    missing_targets=missing_targets,
                    checkpoint_store=checkpoint_store,
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                )
            except Exception as error:
                if checkpoint_store is not None:
                    status = "postprocess_error" if isinstance(error, StructuredLLMError) else "error"
                    checkpoint_store.record_entry(
                        entry_id=batch_entry_id,
                        status=status,
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        source="ai_review_batch",
                        request_context={"topic": category.topic, "batch_index": batch_index},
                        error={"message": str(error)},
                    )
                raise
            for item_index, result_index, original_item, original_result in missing_targets:
                single_entry_id = build_step3_review_single_entry_id(original_item, original_result)
                try:
                    reviewed_result = complete_search_review_single_with_retry(
                        llm_client=llm_client,
                        system_prompt=system_prompt,
                        item=original_item,
                        result=original_result,
                    )
                    if checkpoint_store is not None:
                        checkpoint_store.record_entry(
                            entry_id=single_entry_id,
                            status="success",
                            provider=getattr(llm_client, "current_provider_name", "") or "",
                            source="ai_review_single",
                            request_context={
                                "topic": original_item.topic,
                                "original_title": original_item.original_title,
                                "url": original_result.url,
                            },
                            result=as_review_result_dict(reviewed_result),
                        )
                except Exception as error:
                    if checkpoint_store is not None:
                        status = "postprocess_error" if isinstance(error, StructuredLLMError) else "error"
                        checkpoint_store.record_entry(
                            entry_id=single_entry_id,
                            status=status,
                            provider=getattr(llm_client, "current_provider_name", "") or "",
                            source="ai_review_single",
                            request_context={
                                "topic": original_item.topic,
                                "original_title": original_item.original_title,
                                "url": original_result.url,
                            },
                            error={"message": str(error)},
                        )
                    raise
                item_snapshot = batch_reviewed_items[item_index]
                selected_results = list(item_snapshot.selected_results)
                selected_results[result_index] = reviewed_result
                updated_item = replace(item_snapshot, selected_results=selected_results)
                batch_reviewed_items[item_index] = updated_item
                if checkpoint_store is not None and _is_reviewed_article_payload(updated_item):
                    checkpoint_store.record_entry(
                        entry_id=build_step3_review_article_entry_id(updated_item),
                        status="success",
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        source="ai_review_article",
                        request_context={"topic": updated_item.topic, "original_title": updated_item.original_title},
                        result=_article_search_payload_to_dict(updated_item),
                    )
            for reviewed_item in batch_reviewed_items:
                if checkpoint_store is not None and _is_reviewed_article_payload(reviewed_item):
                    checkpoint_store.record_entry(
                        entry_id=build_step3_review_article_entry_id(reviewed_item),
                        status="success",
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        source="ai_review_article",
                        request_context={"topic": reviewed_item.topic, "original_title": reviewed_item.original_title},
                        result=_article_search_payload_to_dict(reviewed_item),
                    )
                reviewed_items_by_title[reviewed_item.original_title] = reviewed_item
        reviewed_categories.append(
            SearchCategoryPayload(
                topic=category.topic,
                items=[reviewed_items_by_title.get(item.original_title, item) for item in category.items],
            )
        )
    return SearchWorkflowPayload(
        report_date=payload.report_date,
        provider=payload.provider,
        input_path=payload.input_path,
        generated_at=payload.generated_at,
        categories=reviewed_categories,
    )


def build_search_review_prompt_payload(item: ArticleSearchPayload, result: SearchResult) -> dict[str, Any]:
    return {
        "topic": item.topic,
        "channel": item.channel,
        "original_title": item.original_title,
        "original_url": item.original_url,
        "original_published_at": item.original_published_at,
        "result": {
            "query": result.query,
            "query_type": result.query_type,
            "result_title": result.result_title,
            "url": result.url,
            "domain": result.domain,
            "published_at": result.published_at,
            "snippet": result.snippet,
            "score": result.score,
            "is_official": result.is_official,
            "source_tier": result.source_tier,
            "matched_terms": result.matched_terms,
            "extract_status": result.extract_status,
            "extract_text": result.extract_text,
        },
    }


def build_search_review_topic_payload(category: SearchCategoryPayload) -> dict[str, Any]:
    return {
        "topic": category.topic,
        "items": [
            {
                "original_title": item.original_title,
                "original_published_at": item.original_published_at,
                "results": [
                    {
                        "url": result.url,
                        "result_title": result.result_title,
                        "published_at": result.published_at,
                        "snippet": result.snippet,
                        "matched_terms": result.matched_terms,
                    }
                    for result in item.selected_results
                ],
            }
            for item in category.items
            if item.selected_results
        ],
    }


def split_search_review_items_for_topic(
    items: list[ArticleSearchPayload],
    *,
    batch_item_limit: int = STEP3_TOPIC_BATCH_ITEM_LIMIT,
) -> list[list[ArticleSearchPayload]]:
    normalized_items = [item for item in items if item.selected_results]
    if not normalized_items:
        return []
    limit = max(1, int(batch_item_limit))
    return [normalized_items[index : index + limit] for index in range(0, len(normalized_items), limit)]


def complete_search_review_batch_with_retry(
    *,
    llm_client: MiniMaxChatClient,
    system_prompt: str,
    category: SearchCategoryPayload,
    items: list[ArticleSearchPayload],
    max_attempts: int = 1,
) -> tuple[list[ArticleSearchPayload], list[tuple[int, int, ArticleSearchPayload, SearchResult]]]:
    user_prompt = (
        "请审查下面同一 topic 下的多条搜索补充结果，只返回 JSON 对象。\n"
        "格式："
        "{\"items\":[{\"original_title\":\"标题\",\"results\":[{\"url\":\"...\",\"keep_level\":\"strong|weak|drop\","
        "\"reason\":\"...\",\"relevance_note\":\"...\",\"value_type\":\"...\"}]}]}\n\n"
        f"{json.dumps(build_search_review_topic_payload(category), ensure_ascii=False, indent=2)}"
    )
    return complete_json_with_postprocess_retry(
        llm_client=llm_client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        normalize_response=lambda payload: apply_search_review_topic_result_partial(items, payload),
        response_label="step 3 批量审查结果",
        default_max_attempts=max_attempts,
    )


def complete_search_review_single_with_retry(
    *,
    llm_client: MiniMaxChatClient,
    system_prompt: str,
    item: ArticleSearchPayload,
    result: SearchResult,
    max_attempts: int = 1,
) -> SearchResult:
    user_prompt = (
        "请审查下面这条搜索补充结果，只返回 JSON 对象。\n"
        "格式："
        "{\"keep_level\":\"strong|weak|drop\",\"reason\":\"...\",\"relevance_note\":\"...\",\"value_type\":\"...\"}\n\n"
        f"{json.dumps(build_search_review_prompt_payload(item, result), ensure_ascii=False, indent=2)}"
    )
    return complete_json_with_postprocess_retry(
        llm_client=llm_client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        normalize_response=lambda payload: apply_search_review_result(result, payload),
        response_label="step 3 单条审查结果",
        default_max_attempts=max_attempts,
    )


def apply_search_review_result(result: SearchResult, payload: Any) -> SearchResult:
    payload = coerce_json_object_payload(payload, "step 3 审查结果")
    if "items" in payload and isinstance(payload.get("items"), list):
        raw_items = payload.get("items") or []
        if len(raw_items) == 1 and isinstance(raw_items[0], dict):
            raw_results = raw_items[0].get("results")
            if isinstance(raw_results, list) and len(raw_results) == 1 and isinstance(raw_results[0], dict):
                payload = raw_results[0]
    keep_level = str(payload.get("keep_level", "")).strip()
    if keep_level not in {"strong", "weak", "drop"}:
        raise StructuredLLMError(f"step 3 返回了非法 keep_level：{keep_level}")
    reason = str(payload.get("reason", "")).strip()
    relevance_note = str(payload.get("relevance_note", "")).strip()
    value_type = str(payload.get("value_type", "")).strip()
    if not (reason and relevance_note and value_type):
        raise StructuredLLMError("step 3 审查结果缺少 reason、relevance_note 或 value_type。")
    return replace(
        result,
        review_status="reviewed",
        keep_level=keep_level,
        review_reason=reason,
        relevance_note=relevance_note,
        value_type=value_type,
    )


def build_step3_review_batch_entry_id(topic: str, batch_index: int) -> str:
    return f"review_batch::{topic}::{batch_index}"


def build_step3_review_article_entry_id(item: ArticleSearchPayload) -> str:
    return f"review_article::{item.topic}::{item.original_title}"


def build_step3_review_single_entry_id(item: ArticleSearchPayload, result: SearchResult) -> str:
    return f"review_single::{item.topic}::{item.original_title}::{result.url}"


def as_review_result_dict(result: SearchResult) -> dict[str, Any]:
    return {
        "url": result.url,
        "keep_level": result.keep_level,
        "reason": result.review_reason,
        "relevance_note": result.relevance_note,
        "value_type": result.value_type,
    }


def _is_reviewed_search_result(result: SearchResult) -> bool:
    return (
        result.review_status == "reviewed"
        and result.keep_level in {"strong", "weak", "drop"}
        and bool(result.review_reason and result.relevance_note and result.value_type)
    )


def _is_reviewed_article_payload(item: ArticleSearchPayload) -> bool:
    return bool(item.selected_results) and all(_is_reviewed_search_result(result) for result in item.selected_results)


def _apply_cached_single_review_results(
    item: ArticleSearchPayload,
    checkpoint_store: StepCheckpointStore,
) -> ArticleSearchPayload:
    selected_results: list[SearchResult] = []
    changed = False
    for result in item.selected_results:
        cached = checkpoint_store.get_result(build_step3_review_single_entry_id(item, result))
        if isinstance(cached, dict):
            selected_results.append(apply_search_review_result(result, cached))
            changed = True
        else:
            selected_results.append(result)
    if not changed:
        return item
    return replace(item, selected_results=selected_results)


def _record_batch_review_successes(
    *,
    batch_reviewed_items: list[ArticleSearchPayload],
    missing_targets: list[tuple[int, int, ArticleSearchPayload, SearchResult]],
    checkpoint_store: StepCheckpointStore | None,
    provider: str,
) -> None:
    if checkpoint_store is None:
        return
    missing_index_pairs = {(item_index, result_index) for item_index, result_index, _, _ in missing_targets}
    for item_index, reviewed_item in enumerate(batch_reviewed_items):
        for result_index, reviewed_result in enumerate(reviewed_item.selected_results):
            if not _is_reviewed_search_result(reviewed_result):
                continue
            if (item_index, result_index) in missing_index_pairs:
                continue
            checkpoint_store.record_entry(
                entry_id=build_step3_review_single_entry_id(reviewed_item, reviewed_result),
                status="success",
                provider=provider,
                source="ai_review_single",
                request_context={
                    "topic": reviewed_item.topic,
                    "original_title": reviewed_item.original_title,
                    "url": reviewed_result.url,
                },
                result=as_review_result_dict(reviewed_result),
            )
        if _is_reviewed_article_payload(reviewed_item):
            checkpoint_store.record_entry(
                entry_id=build_step3_review_article_entry_id(reviewed_item),
                status="success",
                provider=provider,
                source="ai_review_article",
                request_context={"topic": reviewed_item.topic, "original_title": reviewed_item.original_title},
                result=_article_search_payload_to_dict(reviewed_item),
            )


def _article_search_payload_to_dict(payload: ArticleSearchPayload) -> dict[str, Any]:
    from .workflow import article_search_payload_to_dict

    return article_search_payload_to_dict(payload)


def _article_search_payload_from_dict(payload: dict[str, Any]) -> ArticleSearchPayload:
    from .workflow import article_search_payload_from_dict

    return article_search_payload_from_dict(payload)


def apply_search_review_topic_result(items: list[ArticleSearchPayload], payload: Any) -> list[ArticleSearchPayload]:
    reviewed_items, missing_targets = apply_search_review_topic_result_partial(items, payload)
    if missing_targets:
        _, _, original_item, original_result = missing_targets[0]
        raise StructuredLLMError(
            f"step 3 批量审查结果缺少《{original_item.original_title}》下链接 {original_result.url} 的结果。"
        )
    return reviewed_items


def apply_search_review_topic_result_partial(
    items: list[ArticleSearchPayload],
    payload: Any,
) -> tuple[list[ArticleSearchPayload], list[tuple[int, int, ArticleSearchPayload, SearchResult]]]:
    payload = coerce_json_object_payload(payload, "step 3 批量审查结果")
    if "items" not in payload:
        total_selected = sum(len(item.selected_results) for item in items)
        if len(items) == 1 and total_selected == 1:
            only_item = items[0]
            reviewed_result = apply_search_review_result(only_item.selected_results[0], payload)
            return ([replace(only_item, selected_results=[reviewed_result])], [])
        raise StructuredLLMError("step 3 批量审查结果缺少 items 列表。")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise StructuredLLMError("step 3 批量审查结果缺少 items 列表。")

    article_map: dict[str, dict[str, Any]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        raw_title = raw_item.get("original_title")
        raw_results = raw_item.get("results")
        if not isinstance(raw_title, str) or not isinstance(raw_results, list):
            continue
        title = raw_title.strip()
        if not title or title in article_map:
            continue
        result_map: dict[str, Any] = {}
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            raw_url = raw_result.get("url")
            if not isinstance(raw_url, str):
                continue
            url = raw_url.strip()
            if url and url not in result_map:
                result_map[url] = raw_result
        article_map[title] = result_map

    reviewed_items: list[ArticleSearchPayload] = []
    missing_targets: list[tuple[int, int, ArticleSearchPayload, SearchResult]] = []
    for item_index, item in enumerate(items):
        if not item.selected_results:
            reviewed_items.append(item)
            continue
        result_map = article_map.get(item.original_title) or {}
        reviewed_results: list[SearchResult] = []
        for result_index, result in enumerate(item.selected_results):
            review_payload = result_map.get(result.url)
            if review_payload is None:
                missing_targets.append((item_index, result_index, item, result))
                reviewed_results.append(result)
                continue
            reviewed_results.append(apply_search_review_result(result, review_payload))
        reviewed_items.append(replace(item, selected_results=reviewed_results))
    return reviewed_items, missing_targets
