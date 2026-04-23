"""Step 6 简报生成编排。

本模块负责从 step 5 分析结果生成分 topic 简报，并将章节级结果写入 checkpoint。
Markdown 细节委托给 brief 包，LLM 重试和结构化解析仍走 llm 公共层。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..analysis.models import BRIEF_PROMPT_PATH, BriefSectionDraft, ContentAnalysisInput, ContentAnalysisSection
from ..analysis.yaml_io import load_content_analysis_inputs
from ..brief.markdown import render_brief_markdown, render_generated_brief_markdown
from ..brief.render import render_layer_issues_yaml
from ..facades.content import load_search_results_yaml
from ..facades.intelligence import load_daily_articles_from_csv
from utils.tools.llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    coerce_json_object_payload,
    complete_json_with_postprocess_retry,
    load_prompt_text,
    normalize_string_list,
    run_parallel_ordered,
)
from utils.tools.runtime.checkpoint import StepCheckpointStore
from ..search.workflow import load_search_checklist_yaml


def generate_brief_markdown(
    payload: ContentAnalysisInput,
    llm_client: MiniMaxChatClient,
    prompt_path: Path = BRIEF_PROMPT_PATH,
    checkpoint_store: StepCheckpointStore | None = None,
) -> str:
    """Generate the final step 6 brief via the fixed MiniMax model."""

    section_drafts = auto_complete_brief_sections(
        payload,
        llm_client,
        prompt_path=prompt_path,
        checkpoint_store=checkpoint_store,
    )
    return render_generated_brief_markdown(payload, section_drafts)


def auto_complete_brief_sections(
    payload: ContentAnalysisInput,
    llm_client: MiniMaxChatClient,
    prompt_path: Path = BRIEF_PROMPT_PATH,
    *,
    checkpoint_store: StepCheckpointStore | None = None,
) -> list[BriefSectionDraft]:
    """Generate structured step 6 section drafts in parallel before rendering."""

    begin_llm_step(llm_client, "step_6")
    system_prompt = load_prompt_text(prompt_path)
    drafts_by_topic: dict[str, BriefSectionDraft] = {}

    configured_attempts = getattr(llm_client, "max_attempts_for_retry_class", None)
    max_rounds = 1
    if callable(configured_attempts):
        max_rounds = max(
            1,
            int(configured_attempts("infra", default=max_rounds)),
            int(configured_attempts("postprocess", default=max_rounds)),
        )

    def complete_category(category: ContentAnalysisSection) -> tuple[str, BriefSectionDraft | None, Exception | None]:
        entry_id = build_step6_topic_entry_id(category.topic)
        if checkpoint_store is not None:
            cached = checkpoint_store.get_result(entry_id)
            if isinstance(cached, dict):
                draft = brief_section_draft_from_dict(cached)
                drafts_by_topic[category.topic] = draft
                return category.topic, draft, None
        try:
            section_text = complete_json_with_postprocess_retry(
                llm_client=llm_client,
                system_prompt=system_prompt,
                user_prompt=(
                    "请基于下面这个主题下的 step 5 分析结果，生成行业研究员简报的四个章节。"
                    "若某条条目含 topic_fulltext_excerpt（专题/视频已落盘全文或 ASR 转写），请优先据其归纳；"
                    "并在「核心判断」中对专题/视频子项用「1）提炼后的标题：总结的看点」按序编号（详见系统提示）。"
                    "只返回 JSON 对象，包含：核心判断、增量信息、产业/公司影响、需要继续跟踪的点。"
                    "其中前三个字段是字符串，最后一个字段是字符串列表。\n\n"
                    f"{json.dumps(build_brief_prompt_payload(category, payload.topic_fulltext_excerpts_by_url), ensure_ascii=False, indent=2)}"
                ),
                normalize_response=normalize_brief_sections,
                response_label=f"step 6 主题 {category.topic} 简报结果",
            )
        except Exception as error:
            if checkpoint_store is not None:
                checkpoint_store.record_entry(
                    entry_id=entry_id,
                    status="postprocess_error" if isinstance(error, StructuredLLMError) else "error",
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context={"topic": category.topic},
                    error={"message": str(error)},
                )
            return category.topic, None, error
        draft = BriefSectionDraft(
            topic=category.topic,
            core_judgment=section_text["核心判断"],
            incremental_info=section_text["增量信息"],
            industry_impact=section_text["产业/公司影响"],
            followups=section_text["需要继续跟踪的点"],
        )
        if checkpoint_store is not None:
            checkpoint_store.record_entry(
                entry_id=entry_id,
                status="success",
                provider=getattr(llm_client, "current_provider_name", "") or "",
                request_context={"topic": category.topic},
                result=brief_section_draft_to_dict(draft),
            )
        drafts_by_topic[category.topic] = draft
        return category.topic, draft, None

    pending_categories = [
        category
        for category in payload.categories
        if category.topic not in drafts_by_topic
    ]
    last_errors_by_topic: dict[str, Exception] = {}

    for _ in range(max_rounds):
        if not pending_categories:
            break
        round_results = run_parallel_ordered(pending_categories, complete_category)
        next_pending: list[ContentAnalysisSection] = []
        for category, (_, draft, error) in zip(pending_categories, round_results):
            if draft is not None:
                drafts_by_topic[category.topic] = draft
                continue
            if error is not None:
                last_errors_by_topic[category.topic] = error
            next_pending.append(category)
        pending_categories = next_pending

    if pending_categories:
        details = "；".join(
            f"{category.topic}: {last_errors_by_topic.get(category.topic, StructuredLLMError('未知错误'))}"
            for category in pending_categories
        )
        raise StructuredLLMError(f"step 6 仍有 {len(pending_categories)} 个主题未完成：{details}")

    return [drafts_by_topic[category.topic] for category in payload.categories if category.topic in drafts_by_topic]


def build_step6_topic_entry_id(topic: str) -> str:
    return f"topic::{topic}"


def brief_section_draft_to_dict(draft: BriefSectionDraft) -> dict[str, Any]:
    return asdict(draft)


def brief_section_draft_from_dict(payload: dict[str, Any]) -> BriefSectionDraft:
    return BriefSectionDraft(
        topic=str(payload.get("topic", "")),
        core_judgment=str(payload.get("core_judgment", "")),
        incremental_info=str(payload.get("incremental_info", "")),
        industry_impact=str(payload.get("industry_impact", "")),
        followups=[str(item) for item in payload.get("followups") or []],
    )

def _normalize_brief_lookup_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    return u.rstrip("/")


# step5 可能写入较长专题全文；简报侧单独再截断，控制 step6 token
BRIEF_TOPIC_FULLTEXT_PROMPT_MAX_CHARS = 16_000


def build_brief_prompt_payload(
    category: ContentAnalysisSection, topic_fulltext_excerpts_by_url: dict[str, str] | None = None
) -> dict[str, Any]:
    """构造发给模型的 step 6 单主题简报输入。"""
    by_url = topic_fulltext_excerpts_by_url or {}
    items: list[dict[str, Any]] = []
    for item in category.items:
        row: dict[str, Any] = {
            "original_title": item.original_title,
            "summary": item.analysis.summary,
            "core_points": item.analysis.core_points[:3],
        }
        key = _normalize_brief_lookup_url(item.original_url)
        if key and key in by_url:
            blob = by_url[key]
            if len(blob) > BRIEF_TOPIC_FULLTEXT_PROMPT_MAX_CHARS:
                blob = blob[:BRIEF_TOPIC_FULLTEXT_PROMPT_MAX_CHARS] + "\n... [为简报截断]"
            row["topic_fulltext_excerpt"] = blob
        items.append(row)
    return {
        "topic": category.topic,
        "items": items,
    }


def normalize_brief_sections(payload: Any) -> dict[str, Any]:
    """校验并规范化模型返回的 step 6 主题章节。"""
    payload = coerce_json_object_payload(payload, "step 6 返回")
    required_text_fields = ("核心判断", "增量信息", "产业/公司影响")
    normalized: dict[str, Any] = {}
    for field_name in required_text_fields:
        value = str(payload.get(field_name, "")).strip()
        if not value:
            raise StructuredLLMError(f"step 6 缺少 {field_name}。")
        normalized[field_name] = value
    normalized_followups = normalize_string_list(payload.get("需要继续跟踪的点"))
    if not normalized_followups:
        raise StructuredLLMError("step 6 的需要继续跟踪的点不能为空。")
    normalized["需要继续跟踪的点"] = normalized_followups
    return normalized


def save_brief_markdown(output_path: Path, payload: ContentAnalysisInput) -> None:
    """把 step 6 简报写入 Markdown 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_brief_markdown(payload), encoding="utf-8")


def generate_layer_issues(
    report_date: str,
    raw_csv_path: Path | None = None,
    checklist_path: Path | None = None,
    search_results_path: Path | None = None,
    content_path: Path | None = None,
    keyword_count: int = 1,
) -> dict[str, list[dict[str, Any]]]:
    """Collect the most important workflow issues from the completed step outputs."""
    issues: dict[str, list[dict[str, Any]]] = {}

    if raw_csv_path and raw_csv_path.exists():
        rows = load_daily_articles_from_csv(raw_csv_path.read_text(encoding="utf-8"), report_date)
        raw_issues: list[dict[str, Any]] = []
        missing_keywords = [row.title for row in rows if not row.keywords]
        missing_summary = [row.title for row in rows if not row.summary]
        if missing_keywords:
            raw_issues.append(
                {"code": "missing_keywords", "count": len(missing_keywords), "examples": missing_keywords[:5]}
            )
        if missing_summary:
            raw_issues.append(
                {"code": "missing_summary", "count": len(missing_summary), "examples": missing_summary[:5]}
            )
        issues["raw_fetch"] = raw_issues

    if checklist_path and checklist_path.exists():
        _yaml_date, items = load_search_checklist_yaml(checklist_path)
        checklist_issues: list[dict[str, Any]] = []
        incomplete = [item.original_title for item in items if len(item.keywords) < keyword_count]
        if incomplete:
            checklist_issues.append({"code": "keywords_unfilled", "count": len(incomplete), "examples": incomplete[:5]})
        issues["search_checklist"] = checklist_issues

    if search_results_path and search_results_path.exists():
        payload = load_search_results_yaml(search_results_path)
        search_issues: list[dict[str, Any]] = []
        empty_selected: list[str] = []
        low_quality_domains: list[str] = []
        filtered_domains: list[str] = []
        keep_level_distribution = {"strong": 0, "weak": 0, "drop": 0, "pending": 0}
        for category in payload.categories:
            for item in category.items:
                if not item.selected_results:
                    empty_selected.append(item.original_title)
                for selected in item.selected_results:
                    keep_level = selected.keep_level if selected.keep_level in {"strong", "weak", "drop"} else "pending"
                    keep_level_distribution[keep_level] += 1
                    if selected.source_tier == "blocked":
                        filtered_domains.append(selected.domain)
                        continue
                    if not selected.is_official and selected.source_tier == "normal":
                        low_quality_domains.append(selected.domain)
        if empty_selected:
            search_issues.append(
                {"code": "selected_results_empty", "count": len(empty_selected), "examples": empty_selected[:5]}
            )
        if low_quality_domains:
            search_issues.append(
                {
                    "code": "selected_normal_sources",
                    "count": len(low_quality_domains),
                    "examples": low_quality_domains[:5],
                }
            )
        if filtered_domains:
            search_issues.append(
                {
                    "code": "filtered_selected_domains",
                    "count": len(filtered_domains),
                    "examples": filtered_domains[:5],
                }
            )
        if any(keep_level_distribution.values()):
            search_issues.append(
                {
                    "code": "keep_level_distribution",
                    "count": sum(keep_level_distribution.values()),
                    "examples": [
                        f"strong={keep_level_distribution['strong']}",
                        f"weak={keep_level_distribution['weak']}",
                        f"drop={keep_level_distribution['drop']}",
                        f"pending={keep_level_distribution['pending']}",
                    ],
                }
            )
        issues["search_results"] = search_issues

    if content_path and content_path.exists():
        payload = load_content_analysis_inputs(content_path)
        content_issues: list[dict[str, Any]] = []
        fallback_titles: list[str] = []
        failed_titles: list[str] = []
        for category in payload.categories:
            for item in category.items:
                if item.original_content.source == "html_fallback":
                    fallback_titles.append(item.original_title)
                if item.original_content.status in {"failed", "empty"}:
                    failed_titles.append(item.original_title)
                for selected in item.selected_contents:
                    if selected.document.source == "html_fallback":
                        fallback_titles.append(selected.result_title or item.original_title)
                    if selected.document.status in {"failed", "empty"}:
                        failed_titles.append(selected.result_title or item.original_title)
        if fallback_titles:
            content_issues.append(
                {"code": "html_fallback_used", "count": len(fallback_titles), "examples": fallback_titles[:5]}
            )
        if failed_titles:
            content_issues.append(
                {"code": "content_fetch_failed_or_empty", "count": len(failed_titles), "examples": failed_titles[:5]}
            )
        issues["content_fetch"] = content_issues

    return issues

def save_layer_issues_yaml(output_path: Path, report_date: str, issues: dict[str, list[dict[str, Any]]]) -> None:
    """把问题汇总写入 YAML 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_layer_issues_yaml(report_date, issues), encoding="utf-8")
