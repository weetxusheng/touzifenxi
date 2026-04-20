"""Step 5 正文分析编排。

本模块负责按配置读取正文分析输入、调用 LLM、写入 checkpoint，并把成功结果回填成 step 5 数据模型。
纯文件解析、标题匹配和 Markdown 渲染已经下沉到 analysis/brief，避免 step 编排继续变成大杂烩。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..analysis.models import (
    CONTENT_ANALYSIS_PROMPT_PATH,
    STEP5_TOPIC_BATCH_ITEM_LIMIT,
    ContentAnalysisDraft,
    ContentAnalysisInput,
    ContentAnalysisItem,
    ContentAnalysisSection,
    ContentDocument,
    SelectedDocument,
)
from ..analysis.validation import normalize_content_analysis_draft, normalize_content_analysis_topic_response
from utils.tools.llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    complete_json_with_postprocess_retry,
    load_prompt_text,
    run_parallel_ordered,
)
from utils.tools.runtime.checkpoint import StepCheckpointStore


def auto_complete_content_analysis(
    payload: ContentAnalysisInput,
    llm_client: MiniMaxChatClient,
    prompt_path: Path = CONTENT_ANALYSIS_PROMPT_PATH,
    *,
    mode: str = "per_topic",
    batch_retry_attempts: int = 3,
    checkpoint_store: StepCheckpointStore | None = None,
) -> ContentAnalysisInput:
    """按配置调用大模型补全 step 5 分析结果。"""

    begin_llm_step(llm_client, "step_5")
    system_prompt = load_prompt_text(prompt_path)
    completed_payload: ContentAnalysisInput
    if mode == "per_item":
        completed_payload = _auto_complete_content_analysis_per_item(
            payload,
            llm_client,
            system_prompt,
            checkpoint_store=checkpoint_store,
        )
    else:
        completed_payload = _auto_complete_content_analysis_per_topic(
            payload,
            llm_client,
            system_prompt,
            max_attempts=max(1, batch_retry_attempts),
            checkpoint_store=checkpoint_store,
        )
    return sanitize_step5_link_candidates(completed_payload)


def _auto_complete_content_analysis_per_item(
    payload: ContentAnalysisInput,
    llm_client: MiniMaxChatClient,
    system_prompt: str,
    *,
    checkpoint_store: StepCheckpointStore | None = None,
) -> ContentAnalysisInput:
    """按单篇文章调用模型补全 step 5。"""

    completed_categories: list[ContentAnalysisSection] = []
    for category in payload.categories:
        def complete_item(item: ContentAnalysisItem) -> ContentAnalysisItem:
            entry_id = build_step5_item_entry_id(item)
            if checkpoint_store is not None:
                cached = checkpoint_store.get_result(entry_id)
                if isinstance(cached, dict):
                    return content_analysis_item_from_dict(cached)
            response = llm_client.complete_json(
                system_prompt=system_prompt,
                user_prompt=(
                    "请基于下面的原文正文和补充正文，完成 step 5 正文分析。"
                    "只返回 JSON 对象，至少包含：summary、core_points。"
                    "如果你有足够把握，也可以额外返回：new_facts、entities、signals、"
                    "risk_or_uncertainty、why_it_matters、layer_notes。\n\n"
                    f"{json.dumps(build_content_analysis_prompt_payload(item), ensure_ascii=False, indent=2)}"
                ),
            )
            try:
                analysis = normalize_content_analysis_draft(response)
            except Exception as error:  # noqa: BLE001
                record_postprocess_error = getattr(llm_client, "record_postprocess_error", None)
                if callable(record_postprocess_error):
                    record_postprocess_error(error=error, response_payload=response)
                if checkpoint_store is not None:
                    checkpoint_store.record_entry(
                        entry_id=entry_id,
                        status="postprocess_error" if isinstance(error, StructuredLLMError) else "error",
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        request_context={"topic": item.topic, "original_title": item.original_title},
                        error={"message": str(error)},
                    )
                raise
            completed_item = ContentAnalysisItem(
                original_title=item.original_title,
                topic=item.topic,
                channel=item.channel,
                original_url=item.original_url,
                original_published_at=item.original_published_at,
                original_content=item.original_content,
                selected_contents=item.selected_contents,
                analysis=analysis,
            )
            if checkpoint_store is not None:
                checkpoint_store.record_entry(
                    entry_id=entry_id,
                    status="success",
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context={"topic": item.topic, "original_title": item.original_title},
                    result=content_analysis_item_to_dict(completed_item),
                )
            return completed_item

        completed_items = run_parallel_ordered(category.items, complete_item)
        completed_categories.append(ContentAnalysisSection(topic=category.topic, items=completed_items))
    return ContentAnalysisInput(
        report_date=payload.report_date,
        input_path=payload.input_path,
        generated_at=payload.generated_at,
        categories=completed_categories,
    )


def _auto_complete_content_analysis_per_topic(
    payload: ContentAnalysisInput,
    llm_client: MiniMaxChatClient,
    system_prompt: str,
    *,
    max_attempts: int,
    checkpoint_store: StepCheckpointStore | None = None,
) -> ContentAnalysisInput:
    """按主题批量调用模型补全 step 5。"""

    completed_categories: list[ContentAnalysisSection] = []
    for category in payload.categories:
        completed_items: list[ContentAnalysisItem] = []
        for batch_index, item_batch in enumerate(split_content_analysis_items_for_topic(category.items), start=1):
            batch_category = ContentAnalysisSection(topic=category.topic, items=item_batch)
            entry_id = build_step5_batch_entry_id(category.topic, batch_index)
            if checkpoint_store is not None:
                cached = checkpoint_store.get_result(entry_id)
                if isinstance(cached, dict):
                    completed_items.extend(content_analysis_items_from_batch_dict(cached))
                    continue
            topic_payload = build_content_analysis_topic_prompt_payload(batch_category)
            current_batch = batch_category
            batch_result = _complete_content_analysis_batch_until_success(
                llm_client=llm_client,
                system_prompt=system_prompt,
                topic_payload=topic_payload,
                category=category,
                batch_index=batch_index,
                current_batch=current_batch,
                entry_id=entry_id,
                max_attempts=max_attempts,
                checkpoint_store=checkpoint_store,
            )
            if checkpoint_store is not None:
                checkpoint_store.record_entry(
                    entry_id=entry_id,
                    status="success",
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context={"topic": category.topic, "batch_index": batch_index},
                    result=content_analysis_batch_to_dict(category.topic, batch_result),
                )
            completed_items.extend(batch_result)
        completed_categories.append(ContentAnalysisSection(topic=category.topic, items=completed_items))

    return ContentAnalysisInput(
        report_date=payload.report_date,
        input_path=payload.input_path,
        generated_at=payload.generated_at,
        categories=completed_categories,
    )


def _complete_content_analysis_batch_until_success(
    *,
    llm_client: MiniMaxChatClient,
    system_prompt: str,
    topic_payload: dict[str, Any],
    category: ContentAnalysisSection,
    batch_index: int,
    current_batch: ContentAnalysisSection,
    entry_id: str,
    max_attempts: int,
    checkpoint_store: StepCheckpointStore | None,
) -> list[ContentAnalysisItem]:
    """补齐 step 5 单个 topic batch，失败会落 checkpoint 后继续重试。

    这里把“请求级重试”和“业务级补齐”拆开：LLM client 仍负责接口超时、
    provider fallback 等请求层问题；本循环负责把未满足本地结构约束的 batch
    留在 checkpoint 里，并在当前 run 内继续补到成功或达到明确上限。
    """

    last_error: Exception | None = None
    for attempt_index in range(max(1, max_attempts)):
        try:
            return complete_json_with_postprocess_retry(
                llm_client=llm_client,
                system_prompt=system_prompt,
                user_prompt=(
                    "请基于下面同一主题下的多篇原文正文和补充正文，完成 step 5 正文分析。"
                    "只返回一个 JSON 对象，格式为："
                    '{"topic":"...","items":[{"original_title":"...","summary":"...","core_points":["..."]}]}。'
                    "items 中必须覆盖输入里的全部 original_title，且不要遗漏。"
                    "如果你有足够把握，也可以在每个 item 里额外返回：new_facts、entities、signals、"
                    "risk_or_uncertainty、why_it_matters、layer_notes。\n\n"
                    f"{json.dumps(topic_payload, ensure_ascii=False, indent=2)}"
                ),
                normalize_response=lambda response, batch=current_batch: normalize_content_analysis_topic_response(response, batch),
                response_label=f"step 5 主题 {category.topic} 分析结果",
                default_max_attempts=1,
            )
        except Exception as error:  # noqa: BLE001
            last_error = error
            if checkpoint_store is not None:
                checkpoint_store.record_entry(
                    entry_id=entry_id,
                    status="postprocess_error" if isinstance(error, StructuredLLMError) else "error",
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context={
                        "topic": category.topic,
                        "batch_index": batch_index,
                        "completion_attempt": attempt_index + 1,
                    },
                    error={"message": str(error)},
                )
    raise last_error or StructuredLLMError(f"step 5 主题 {category.topic} 分析结果补齐失败。")


def build_content_analysis_prompt_payload(item: ContentAnalysisItem) -> dict[str, Any]:
    """构造发给模型的 step 5 单篇文章分析输入。"""
    return {
        "topic": item.topic,
        "channel": item.channel,
        "original_title": item.original_title,
        "original_url": item.original_url,
        "original_content": {
            "title": item.original_content.title,
            "text": prepare_step5_prompt_text(
                item.original_content.text,
                source=item.original_content.source,
            ),
            "source": item.original_content.source,
        },
        "selected_contents": [
            {
                "query": selected.query,
                "query_type": selected.query_type,
                "url": selected.url,
                "result_title": selected.result_title,
                "published_at": selected.published_at,
                "title": selected.document.title,
                "text": prepare_step5_prompt_text(
                    selected.document.text,
                    source=selected.document.source,
                ),
                "source": selected.document.source,
            }
            for selected in item.selected_contents
        ],
    }


def build_step5_item_entry_id(item: ContentAnalysisItem) -> str:
    return f"item::{item.topic}::{item.original_title}"


def build_step5_batch_entry_id(topic: str, batch_index: int) -> str:
    return f"batch::{topic}::{batch_index}"


def content_analysis_item_to_dict(item: ContentAnalysisItem) -> dict[str, Any]:
    return asdict(item)


def content_analysis_item_from_dict(payload: dict[str, Any]) -> ContentAnalysisItem:
    original_content = payload.get("original_content") or {}
    selected_contents = payload.get("selected_contents") or []
    analysis = payload.get("analysis") or {}
    return ContentAnalysisItem(
        original_title=str(payload.get("original_title", "")),
        topic=str(payload.get("topic", "")),
        channel=str(payload.get("channel", "")),
        original_url=str(payload.get("original_url", "")),
        original_published_at=str(payload.get("original_published_at", "")),
        original_content=ContentDocument(
            url=str(original_content.get("url", "")),
            domain=str(original_content.get("domain", "")),
            title=str(original_content.get("title", "")),
            summary=str(original_content.get("summary", "")),
            text=str(original_content.get("text", "")),
            source=str(original_content.get("source", "")),
            status=str(original_content.get("status", "")),
            error=str(original_content.get("error", "")),
        ),
        selected_contents=[
            SelectedDocument(
                query=str(item_payload.get("query", "")),
                query_type=str(item_payload.get("query_type", "")),
                url=str(item_payload.get("url", "")),
                domain=str(item_payload.get("domain", "")),
                result_title=str(item_payload.get("result_title", "")),
                published_at=str(item_payload.get("published_at", "")),
                document=ContentDocument(
                    url=str((item_payload.get("document") or {}).get("url", item_payload.get("url", ""))),
                    domain=str((item_payload.get("document") or {}).get("domain", item_payload.get("domain", ""))),
                    title=str((item_payload.get("document") or {}).get("title", "")),
                    summary=str((item_payload.get("document") or {}).get("summary", "")),
                    text=str((item_payload.get("document") or {}).get("text", "")),
                    source=str((item_payload.get("document") or {}).get("source", "")),
                    status=str((item_payload.get("document") or {}).get("status", "")),
                    error=str((item_payload.get("document") or {}).get("error", "")),
                ),
            )
            for item_payload in selected_contents
            if isinstance(item_payload, dict)
        ],
        analysis=ContentAnalysisDraft(
            summary=str(analysis.get("summary", "")),
            core_points=[str(item) for item in analysis.get("core_points") or []],
            new_facts=[str(item) for item in analysis.get("new_facts") or []],
            entities=[str(item) for item in analysis.get("entities") or []],
            signals=[str(item) for item in analysis.get("signals") or []],
            risk_or_uncertainty=[str(item) for item in analysis.get("risk_or_uncertainty") or []],
            why_it_matters=str(analysis.get("why_it_matters", "")),
            layer_notes=[str(item) for item in analysis.get("layer_notes") or []],
        ),
    )


def content_analysis_batch_to_dict(topic: str, items: list[ContentAnalysisItem]) -> dict[str, Any]:
    return {
        "topic": topic,
        "items": [content_analysis_item_to_dict(item) for item in items],
    }


def content_analysis_items_from_batch_dict(payload: dict[str, Any]) -> list[ContentAnalysisItem]:
    return [
        content_analysis_item_from_dict(item)
        for item in payload.get("items") or []
        if isinstance(item, dict)
    ]


def build_content_analysis_topic_prompt_payload(category: ContentAnalysisSection) -> dict[str, Any]:
    """构造发给模型的 step 5 单主题批量分析输入。"""

    topic_items: list[dict[str, Any]] = []
    for item in category.items:
        item_payload = build_content_analysis_prompt_payload(item)
        topic_items.append(
            {
                "original_title": item.original_title,
                "original_url": item.original_url,
                "channel": item.channel,
                "original_content": item_payload["original_content"],
                "selected_contents": item_payload["selected_contents"],
            }
        )
    return {
        "topic": category.topic,
        "items": topic_items,
    }


def split_content_analysis_items_for_topic(
    items: list[ContentAnalysisItem],
    *,
    item_limit: int = STEP5_TOPIC_BATCH_ITEM_LIMIT,
) -> list[list[ContentAnalysisItem]]:
    """将大 topic 按固定篇数拆成小批，避免单次请求过大。"""

    normalized_limit = max(1, int(item_limit))
    if len(items) <= normalized_limit:
        return [list(items)]
    return [
        items[index : index + normalized_limit]
        for index in range(0, len(items), normalized_limit)
    ]


def prepare_step5_prompt_text(text: str, *, source: str) -> str:
    """按正文来源压缩 step 5 发送给模型的正文内容。"""

    normalized = str(text).strip()
    if not normalized:
        return ""
    if source == "html_fallback":
        return truncate_html_fallback_prompt_text(normalized)
    return normalized


def truncate_html_fallback_prompt_text(text: str) -> str:
    """对 HTML fallback 正文只保留前后关键片段，减少噪音与长度。"""

    normalized = text.strip()
    if not normalized:
        return ""
    edge_limit = html_fallback_edge_limit(normalized)
    if len(normalized) <= edge_limit * 2:
        return normalized
    head = normalized[:edge_limit].strip()
    tail = normalized[-edge_limit:].strip()
    return f"{head}\n...\n{tail}".strip()


def html_fallback_edge_limit(text: str) -> int:
    """根据正文语言倾向决定 HTML fallback 前后保留长度。"""

    if not text:
        return 100
    ascii_count = sum(1 for char in text if char.isascii() and not char.isspace())
    non_ascii_count = sum(1 for char in text if not char.isascii() and not char.isspace())
    return 200 if ascii_count > non_ascii_count else 100


MOJIBAKE_MARKERS = ("�", "ï¿½", "&amp;#x", "&#x", "&#xfffd;", "Ã", "â€", "ðŸ")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
ENCODED_ENTITY_RE = re.compile(r"(?:&#x[0-9a-fA-F]+;|&#\d+;|&amp;#x[0-9a-fA-F]+;)")


def sanitize_step5_link_candidates(payload: ContentAnalysisInput) -> ContentAnalysisInput:
    """在 step 5 阶段清理源地址/补充地址：去重并过滤乱码项。"""

    sanitized_categories: list[ContentAnalysisSection] = []
    for category in payload.categories:
        seen_source_urls: set[str] = set()
        seen_supplement_urls: set[str] = set()
        sanitized_items: list[ContentAnalysisItem] = []
        for item in category.items:
            normalized_title = sanitize_link_text(item.original_title)
            normalized_published_at = sanitize_link_text(item.original_published_at)
            normalized_original_url = normalize_link_url(item.original_url)
            keep_source = (
                bool(normalized_original_url)
                and not is_probably_garbled(item.original_title)
                and normalized_original_url not in seen_source_urls
            )
            if keep_source:
                seen_source_urls.add(normalized_original_url)
            else:
                normalized_original_url = ""

            sanitized_selected_contents: list[SelectedDocument] = []
            for selected in item.selected_contents:
                normalized_selected_url = normalize_link_url(selected.url)
                resolved_title = selected.document.title or selected.result_title
                if (
                    not normalized_selected_url
                    or is_probably_garbled(resolved_title)
                    or normalized_selected_url in seen_source_urls
                    or normalized_selected_url in seen_supplement_urls
                ):
                    continue
                seen_supplement_urls.add(normalized_selected_url)
                sanitized_selected_contents.append(
                    SelectedDocument(
                        query=selected.query,
                        query_type=selected.query_type,
                        url=normalized_selected_url,
                        domain=selected.domain,
                        result_title=sanitize_link_text(selected.result_title),
                        published_at=sanitize_link_text(selected.published_at),
                        document=ContentDocument(
                            url=normalized_selected_url,
                            domain=selected.document.domain,
                            title=sanitize_link_text(selected.document.title),
                            summary=selected.document.summary,
                            text=selected.document.text,
                            source=selected.document.source,
                            status=selected.document.status,
                            error=selected.document.error,
                        ),
                    )
                )

            sanitized_items.append(
                ContentAnalysisItem(
                    original_title=normalized_title,
                    topic=item.topic,
                    channel=item.channel,
                    original_url=normalized_original_url,
                    original_published_at=normalized_published_at,
                    original_content=item.original_content,
                    selected_contents=sanitized_selected_contents,
                    analysis=item.analysis,
                )
            )
        sanitized_categories.append(ContentAnalysisSection(topic=category.topic, items=sanitized_items))
    return ContentAnalysisInput(
        report_date=payload.report_date,
        input_path=payload.input_path,
        generated_at=payload.generated_at,
        categories=sanitized_categories,
    )


def sanitize_link_text(value: str) -> str:
    """清理链接标题/日期中的不可见字符与常见乱码占位符。"""

    text = str(value or "").replace("\uFFFD", "").replace("ï¿½", "")
    text = CONTROL_CHARS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_link_url(value: str) -> str:
    """标准化 URL，并只保留 http(s) 链接。"""

    normalized = str(value or "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return ""
    return normalized


def is_probably_garbled(value: str) -> bool:
    """判断文本是否包含明显乱码特征。"""

    text = str(value or "").strip()
    if not text:
        return True
    # 出现高密度 HTML 实体编码通常意味着标题源已乱码。
    if len(ENCODED_ENTITY_RE.findall(text)) >= 3:
        return True
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in MOJIBAKE_MARKERS)
