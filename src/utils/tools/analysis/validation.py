"""Step 5 模型返回结构校验与本地兼容层。

本模块负责把 LLM 返回的松散 JSON 规范化为内部数据模型。
它保留标题归一化兜底，但不负责请求重试和 checkpoint 落盘。
"""

from __future__ import annotations

from typing import Any

from utils.tools.llm import StructuredLLMError, coerce_json_object_payload, normalize_string_list
from .models import (
    OPTIONAL_ANALYSIS_LIST_FIELDS,
    REQUIRED_ANALYSIS_LIST_FIELDS,
    ContentAnalysisDraft,
    ContentAnalysisInput,
    ContentAnalysisItem,
    ContentAnalysisSection,
)
from .title_matching import normalize_analysis_title_key


def collect_missing_analysis_fields(payload: ContentAnalysisInput) -> list[dict[str, Any]]:
    """Report which step 5 analysis fields are still missing before step 6."""

    missing_items: list[dict[str, Any]] = []
    for category in payload.categories:
        for item in category.items:
            missing_fields: list[str] = []
            if not item.analysis.summary.strip():
                missing_fields.append("summary")
            for field_name in REQUIRED_ANALYSIS_LIST_FIELDS:
                values = getattr(item.analysis, field_name)
                if not any(str(value).strip() for value in values):
                    missing_fields.append(field_name)
            if missing_fields:
                missing_items.append(
                    {
                        "topic": item.topic,
                        "original_title": item.original_title,
                        "missing_fields": missing_fields,
                    }
                )
    return missing_items

def normalize_content_analysis_draft(payload: Any) -> ContentAnalysisDraft:
    """校验并规范化模型返回的 step 5 分析结果。"""
    payload = coerce_json_object_payload(payload, "step 5 分析结果")
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise StructuredLLMError("step 5 缺少 summary。")
    normalized_lists: dict[str, list[str]] = {}
    for field_name in REQUIRED_ANALYSIS_LIST_FIELDS:
        normalized = normalize_string_list(payload.get(field_name))
        if not normalized:
            raise StructuredLLMError(f"step 5 字段 {field_name} 不能为空。")
        normalized_lists[field_name] = normalized
    for field_name in OPTIONAL_ANALYSIS_LIST_FIELDS:
        normalized_lists[field_name] = normalize_string_list(payload.get(field_name))
    return ContentAnalysisDraft(
        summary=summary,
        core_points=normalized_lists["core_points"],
        new_facts=normalized_lists["new_facts"],
        entities=normalized_lists["entities"],
        signals=normalized_lists["signals"],
        risk_or_uncertainty=normalized_lists["risk_or_uncertainty"],
        why_it_matters=str(payload.get("why_it_matters", "")).strip(),
        layer_notes=normalized_lists["layer_notes"],
    )


def normalize_content_analysis_topic_response(
    payload: Any,
    category: ContentAnalysisSection,
) -> list[ContentAnalysisItem]:
    """把单主题批量返回结果映射回原始文章顺序。"""

    payload = coerce_json_object_payload(payload, "step 5 主题分析结果")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise StructuredLLMError("step 5 主题分析结果缺少非空 items 数组。")

    response_items_by_title: dict[str, ContentAnalysisDraft] = {}
    response_items_by_normalized_title: dict[str, tuple[str, ContentAnalysisDraft]] = {}
    for raw_item in raw_items:
        raw_object = coerce_json_object_payload(raw_item, "step 5 主题分析 item")
        original_title = str(raw_object.get("original_title", "")).strip()
        if not original_title:
            raise StructuredLLMError("step 5 主题分析 item 缺少 original_title。")
        if original_title in response_items_by_title:
            raise StructuredLLMError(f"step 5 主题分析结果中 original_title 重复：{original_title}")
        analysis = normalize_content_analysis_draft(raw_object)
        response_items_by_title[original_title] = analysis
        normalized_title = normalize_analysis_title_key(original_title)
        previous = response_items_by_normalized_title.get(normalized_title)
        if previous is not None and previous[0] != original_title:
            raise StructuredLLMError(f"step 5 主题分析结果中 original_title 归一化后重复：{original_title}")
        response_items_by_normalized_title[normalized_title] = (original_title, analysis)

    completed_items: list[ContentAnalysisItem] = []
    for item in category.items:
        # 模型经常会改掉标题里的空格或中英文标点；先精确匹配，失败后再用
        # 归一化 key 兜底，避免把可本地判断的格式偏差误判成业务缺失。
        analysis = response_items_by_title.get(item.original_title)
        if analysis is None:
            normalized_match = response_items_by_normalized_title.get(normalize_analysis_title_key(item.original_title))
            if normalized_match is not None:
                analysis = normalized_match[1]
        if analysis is None:
            raise StructuredLLMError(f"step 5 主题分析结果缺少 original_title：{item.original_title}")
        completed_items.append(
            ContentAnalysisItem(
                original_title=item.original_title,
                topic=item.topic,
                channel=item.channel,
                original_url=item.original_url,
                original_published_at=item.original_published_at,
                original_content=item.original_content,
                selected_contents=item.selected_contents,
                analysis=analysis,
            )
        )
    return completed_items
