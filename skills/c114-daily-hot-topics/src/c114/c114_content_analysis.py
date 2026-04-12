"""C114 第 5/6 步正文分析与主题简报模块。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .c114_content import load_search_results_yaml
from .c114_intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    layer_issues_name,
    load_daily_articles_from_csv,
    step_4_content_name,
    step_5_content_analysis_name,
    step_6_brief_name,
)
from .c114_search import load_search_checklist_yaml, parse_yaml_value
from .checkpoint import StepCheckpointStore
from .llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    coerce_json_object_payload,
    complete_json_with_postprocess_retry,
    load_prompt_text,
    normalize_string_list,
    run_parallel_ordered,
)
from .settings import AppPaths, resolve_override_path

SKILL_ROOT = Path(__file__).resolve().parents[2]
CONTENT_ANALYSIS_PROMPT_PATH = SKILL_ROOT / "prompts" / "content-analysis-agent.md"
BRIEF_PROMPT_PATH = SKILL_ROOT / "prompts" / "brief-agent.md"
REQUIRED_ANALYSIS_LIST_FIELDS = ("core_points",)
OPTIONAL_ANALYSIS_LIST_FIELDS = (
    "new_facts",
    "entities",
    "signals",
    "risk_or_uncertainty",
    "layer_notes",
)
STEP5_TOPIC_BATCH_ITEM_LIMIT = 4


@dataclass(frozen=True)
class ContentDocument:
    """表示一份已抓取的正文文档。"""
    url: str
    domain: str
    title: str
    summary: str
    text: str
    source: str
    status: str
    error: str


@dataclass(frozen=True)
class SelectedDocument:
    """表示一条补充链接及其对应的正文文档。"""
    query: str
    query_type: str
    url: str
    domain: str
    result_title: str
    published_at: str
    document: ContentDocument


@dataclass(frozen=True)
class ContentAnalysisDraft:
    """表示 step 5 单篇文章的分析草稿。"""
    summary: str
    core_points: list[str]
    new_facts: list[str]
    entities: list[str]
    signals: list[str]
    risk_or_uncertainty: list[str]
    why_it_matters: str
    layer_notes: list[str]


@dataclass(frozen=True)
class ContentAnalysisItem:
    """表示 step 5 中单篇文章的完整分析单元。"""
    original_title: str
    topic: str
    channel: str
    original_url: str
    original_published_at: str
    original_content: ContentDocument
    selected_contents: list[SelectedDocument]
    analysis: ContentAnalysisDraft


@dataclass(frozen=True)
class ContentAnalysisSection:
    """表示按主题聚合后的 step 5 分析分组。"""
    topic: str
    items: list[ContentAnalysisItem]


@dataclass(frozen=True)
class ContentAnalysisInput:
    """表示 step 5/6 使用的整体输入载荷。"""
    report_date: str
    input_path: Path
    generated_at: str
    categories: list[ContentAnalysisSection]


@dataclass(frozen=True)
class ContentAnalysisOutputPaths:
    """表示 step 5、step 6 与问题汇总文件的输出路径。"""
    input_path: Path
    analysis_output: Path
    brief_output: Path
    issues_output: Path


@dataclass(frozen=True)
class BriefSectionDraft:
    """表示 step 6 某个主题生成后的章节草稿。"""
    topic: str
    core_judgment: str
    incremental_info: str
    industry_impact: str
    followups: list[str]


def resolve_content_analysis_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    output_override: str | None = None,
    issues_output_override: str | None = None,
) -> ContentAnalysisOutputPaths:
    """Resolve the canonical step 4 input and step 5/6/issue output paths."""
    run_dir: Path | None = None
    if input_override:
        input_path = resolve_override_path(paths.project_root, input_override)
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            input_path = (run_dir / step_4_content_name(report_date)).resolve()
        else:
            input_path = (c114_reports_root(paths.reports_dir) / step_4_content_name(report_date)).resolve()

    if output_override:
        analysis_output = resolve_override_path(paths.project_root, output_override)
    else:
        base_dir = input_path.parent if (input_override or run_dir) else c114_reports_root(paths.reports_dir)
        analysis_output = (base_dir / step_5_content_analysis_name(report_date)).resolve()

    if issues_output_override:
        issues_output = resolve_override_path(paths.project_root, issues_output_override)
    else:
        base_dir = input_path.parent if (input_override or run_dir) else c114_reports_root(paths.reports_dir)
        issues_output = (base_dir / layer_issues_name(report_date)).resolve()

    if input_override or run_dir:
        base_dir = input_path.parent
    else:
        base_dir = c114_reports_root(paths.reports_dir)
    brief_output = (base_dir / step_6_brief_name(report_date)).resolve()

    return ContentAnalysisOutputPaths(
        input_path=input_path,
        analysis_output=analysis_output,
        brief_output=brief_output,
        issues_output=issues_output,
    )


def load_content_analysis_inputs(input_path: Path) -> ContentAnalysisInput:
    """Parse the step 4 YAML into the structure consumed by step 5 and step 6."""
    report_date = ""
    generated_at = ""
    categories: list[ContentAnalysisSection] = []
    current_topic = ""
    current_items: list[ContentAnalysisItem] = []
    current_item: dict[str, Any] | None = None
    current_selected: dict[str, Any] | None = None
    current_block = ""
    current_analysis_list_key = ""

    def finalize_selected() -> None:
        nonlocal current_selected
        if current_item is None or current_selected is None:
            return
        selected_contents = current_item.setdefault("selected_contents", [])
        assert isinstance(selected_contents, list)
        selected_contents.append(
            SelectedDocument(
                query=str(current_selected.get("query", "")),
                query_type=str(current_selected.get("query_type", "")),
                url=str(current_selected.get("url", "")),
                domain=str(current_selected.get("domain", "")),
                result_title=str(current_selected.get("result_title", "")),
                published_at=str(current_selected.get("published_at", "")),
                document=ContentDocument(
                    url=str(current_selected.get("url", "")),
                    domain=str(current_selected.get("domain", "")),
                    title=str(current_selected.get("content_title", "") or current_selected.get("title", "")),
                    summary=str(
                        current_selected.get("content_summary", "") or current_selected.get("summary", "")
                    ),
                    text=str(current_selected.get("content_text", "") or current_selected.get("text", "")),
                    source=str(
                        current_selected.get("content_source", "") or current_selected.get("source", "")
                    ),
                    status=str(current_selected.get("fetch_status", "") or current_selected.get("status", "")),
                    error=str(current_selected.get("fetch_error", "") or current_selected.get("error", "")),
                ),
            )
        )
        current_selected = None

    def finalize_item() -> None:
        nonlocal current_item, current_analysis_list_key
        finalize_selected()
        if current_item is None:
            return
        current_items.append(
            ContentAnalysisItem(
                original_title=str(current_item.get("original_title", "")),
                topic=str(current_item.get("topic", "")),
                channel=str(current_item.get("channel", "")),
                original_url=str(current_item.get("original_url", "")),
                original_published_at=str(current_item.get("original_published_at", "")),
                original_content=ContentDocument(
                    url=str(current_item.get("original_content.url", "")),
                    domain=str(current_item.get("original_content.domain", "")),
                    title=str(
                        current_item.get("original_content.content_title", "")
                        or current_item.get("original_content.title", "")
                    ),
                    summary=str(
                        current_item.get("original_content.content_summary", "")
                        or current_item.get("original_content.summary", "")
                    ),
                    text=str(
                        current_item.get("original_content.content_text", "")
                        or current_item.get("original_content.text", "")
                    ),
                    source=str(
                        current_item.get("original_content.content_source", "")
                        or current_item.get("original_content.source", "")
                    ),
                    status=str(
                        current_item.get("original_content.fetch_status", "")
                        or current_item.get("original_content.status", "")
                    ),
                    error=str(
                        current_item.get("original_content.fetch_error", "")
                        or current_item.get("original_content.error", "")
                    ),
                ),
                selected_contents=list(current_item.get("selected_contents", [])),
                analysis=ContentAnalysisDraft(
                    summary=str(current_item.get("analysis.summary", "")),
                    core_points=list(current_item.get("analysis.core_points", [])),
                    new_facts=list(current_item.get("analysis.new_facts", [])),
                    entities=list(current_item.get("analysis.entities", [])),
                    signals=list(current_item.get("analysis.signals", [])),
                    risk_or_uncertainty=list(current_item.get("analysis.risk_or_uncertainty", [])),
                    why_it_matters=str(current_item.get("analysis.why_it_matters", "")),
                    layer_notes=list(current_item.get("analysis.layer_notes", [])),
                ),
            )
        )
        current_item = None
        current_analysis_list_key = ""

    def finalize_topic() -> None:
        nonlocal current_items
        finalize_item()
        if current_topic:
            categories.append(ContentAnalysisSection(topic=current_topic, items=current_items))
        current_items = []

    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("report_date: "):
            report_date = parse_yaml_value(line)
            continue
        if line.startswith("generated_at: "):
            generated_at = parse_yaml_value(line)
            continue
        if line.startswith("  - topic: "):
            finalize_topic()
            current_topic = parse_yaml_value(line)
            current_block = ""
            continue
        if line.startswith("      - original_title: "):
            finalize_item()
            current_item = {"original_title": parse_yaml_value(line), "selected_contents": []}
            current_block = ""
            continue
        if current_item is None:
            continue
        if line.startswith("        topic: "):
            current_item["topic"] = parse_yaml_value(line)
        elif line.startswith("        channel: "):
            current_item["channel"] = parse_yaml_value(line)
        elif line.startswith("        original_url: "):
            current_item["original_url"] = parse_yaml_value(line)
        elif line.startswith("        original_published_at: "):
            current_item["original_published_at"] = parse_yaml_value(line)
        elif line.startswith("        original_content:"):
            finalize_selected()
            current_block = "original_content"
        elif line.startswith("        selected_contents:"):
            finalize_selected()
            current_block = "selected_contents"
            current_analysis_list_key = ""
        elif line.startswith("        analysis:"):
            finalize_selected()
            current_block = "analysis"
            current_analysis_list_key = ""
        elif current_block == "original_content" and line.startswith("          "):
            key = line.strip().split(":", 1)[0]
            current_item[f"original_content.{key}"] = parse_yaml_value(line.strip())
        elif current_block == "selected_contents" and line.startswith("          - query: "):
            finalize_selected()
            current_selected = {"query": parse_yaml_value(line)}
        elif current_block == "selected_contents" and current_selected is not None and line.startswith("            "):
            key = line.strip().split(":", 1)[0]
            current_selected[key] = parse_yaml_value(line.strip())
        elif current_block == "analysis" and current_analysis_list_key and line.startswith("            - "):
            analysis_values = current_item.setdefault(f"analysis.{current_analysis_list_key}", [])
            assert isinstance(analysis_values, list)
            analysis_values.append(line.strip()[2:].strip().strip("'"))
        elif current_block == "analysis" and line.startswith("          "):
            stripped = line.strip()
            key = stripped.split(":", 1)[0]
            if stripped.endswith(":"):
                current_item[f"analysis.{key}"] = []
                current_analysis_list_key = key
            else:
                current_item[f"analysis.{key}"] = parse_yaml_value(stripped)
                current_analysis_list_key = ""

    finalize_topic()
    return ContentAnalysisInput(
        report_date=report_date,
        input_path=input_path,
        generated_at=generated_at,
        categories=categories,
    )


def render_content_analysis_yaml(payload: ContentAnalysisInput) -> str:
    """Render the step 5 YAML template or filled analysis payload."""
    lines = [
        f"report_date: '{payload.report_date}'",
        f"input_path: '{payload.input_path}'",
        f"prompt_path: '{CONTENT_ANALYSIS_PROMPT_PATH}'",
        "instructions: 'analysis 由 skill 内置模型读取 prompt_path 后自动填写；每条先读 original_content，再结合 selected_contents 提炼结论。'",
        "categories:",
    ]
    for category in payload.categories:
        lines.extend([f"  - topic: '{escape_yaml(category.topic)}'", "    items:"])
        for item in category.items:
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml(item.original_title)}'",
                    f"        topic: '{escape_yaml(item.topic)}'",
                    f"        channel: '{escape_yaml(item.channel)}'",
                    f"        original_url: '{escape_yaml(item.original_url)}'",
                    f"        original_published_at: '{escape_yaml(item.original_published_at)}'",
                    "        original_content:",
                    f"          title: '{escape_yaml(item.original_content.title)}'",
                    f"          summary: '{escape_yaml(item.original_content.summary)}'",
                    f"          text: '{escape_yaml(item.original_content.text)}'",
                    f"          source: '{escape_yaml(item.original_content.source)}'",
                    f"          status: '{escape_yaml(item.original_content.status)}'",
                    "        selected_contents:",
                ]
            )
            for selected in item.selected_contents:
                lines.extend(
                    [
                        f"          - query: '{escape_yaml(selected.query)}'",
                        f"            query_type: '{escape_yaml(selected.query_type)}'",
                        f"            url: '{escape_yaml(selected.url)}'",
                        f"            title: '{escape_yaml(selected.document.title)}'",
                        f"            summary: '{escape_yaml(selected.document.summary)}'",
                        f"            text: '{escape_yaml(selected.document.text)}'",
                        f"            source: '{escape_yaml(selected.document.source)}'",
                        f"            status: '{escape_yaml(selected.document.status)}'",
                    ]
                )
            lines.extend(
                [
                    "        analysis:",
                    f"          summary: '{escape_yaml(item.analysis.summary)}'",
                ]
            )
            lines.extend(render_analysis_list("core_points", item.analysis.core_points, indent="          "))
            lines.extend(render_analysis_list("new_facts", item.analysis.new_facts, indent="          "))
            lines.extend(render_analysis_list("entities", item.analysis.entities, indent="          "))
            lines.extend(render_analysis_list("signals", item.analysis.signals, indent="          "))
            lines.extend(
                render_analysis_list(
                    "risk_or_uncertainty",
                    item.analysis.risk_or_uncertainty,
                    indent="          ",
                )
            )
            lines.extend(
                [
                    f"          why_it_matters: '{escape_yaml(item.analysis.why_it_matters)}'",
                ]
            )
            lines.extend(render_analysis_list("layer_notes", item.analysis.layer_notes, indent="          "))
    return "\n".join(lines)


def render_analysis_list(name: str, values: list[str], indent: str) -> list[str]:
    """把分析字段列表渲染成指定缩进的 YAML 片段。"""
    lines = [f"{indent}{name}:"]
    if values:
        lines.extend(f"{indent}  - '{escape_yaml(value)}'" for value in values)
    return lines


def save_content_analysis_yaml(output_path: Path, payload: ContentAnalysisInput) -> None:
    """把 step 5 正文分析结果写入 YAML 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_content_analysis_yaml(payload), encoding="utf-8")


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
    if mode == "per_item":
        return _auto_complete_content_analysis_per_item(payload, llm_client, system_prompt, checkpoint_store=checkpoint_store)
    return _auto_complete_content_analysis_per_topic(
        payload,
        llm_client,
        system_prompt,
        max_attempts=max(1, batch_retry_attempts),
        checkpoint_store=checkpoint_store,
    )


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
            try:
                batch_result = complete_json_with_postprocess_retry(
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
                    default_max_attempts=max_attempts,
                )
            except Exception as error:
                if checkpoint_store is not None:
                    checkpoint_store.record_entry(
                        entry_id=entry_id,
                        status="postprocess_error" if isinstance(error, StructuredLLMError) else "error",
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        request_context={"topic": category.topic, "batch_index": batch_index},
                        error={"message": str(error)},
                    )
                raise
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
    for raw_item in raw_items:
        raw_object = coerce_json_object_payload(raw_item, "step 5 主题分析 item")
        original_title = str(raw_object.get("original_title", "")).strip()
        if not original_title:
            raise StructuredLLMError("step 5 主题分析 item 缺少 original_title。")
        if original_title in response_items_by_title:
            raise StructuredLLMError(f"step 5 主题分析结果中 original_title 重复：{original_title}")
        response_items_by_title[original_title] = normalize_content_analysis_draft(raw_object)

    completed_items: list[ContentAnalysisItem] = []
    for item in category.items:
        analysis = response_items_by_title.get(item.original_title)
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


def render_brief_markdown(payload: ContentAnalysisInput) -> str:
    """Render the step 6 brief from the current content-analysis payload."""
    search_payload = None
    if payload.input_path.exists():
        search_results_path = infer_related_step3_path(payload.input_path)
        if search_results_path.exists():
            search_payload = load_search_results_yaml(search_results_path)
    summary = build_brief_runtime_summary(payload, search_payload)
    lines = [f"# {infer_brief_title(payload.input_path)}（{payload.report_date}）", "", "## 运行摘要", ""]
    lines.extend(
        [
            f"- 原始文章数：{summary['original_articles']}",
            f"- 补充链接数：{summary['external_links']}",
            f"- strong（强保留）：{summary['strong']}",
            f"- weak（弱保留）：{summary['weak']}",
            f"- drop（丢弃）：{summary['drop']}",
            f"- pending（待审）：{summary['pending']}",
            f"- 正文抓取成功数：{summary['content_success']}",
            f"- HTML fallback（HTML 回退）使用数：{summary['html_fallback']}",
        ]
    )
    for category in payload.categories:
        lines.extend(
            [
                "",
                f"## {category.topic}",
                "",
                "### 核心判断",
                "",
                "_本段应由程序内置模型自动生成；若仍看到此提示，说明 step 6 未按正式流程执行。_",
                "",
                "### 增量信息",
                "",
                "_本段应由程序内置模型自动生成；若仍看到此提示，说明 step 6 未按正式流程执行。_",
                "",
                "### 产业/公司影响",
                "",
                "_本段应由程序内置模型自动生成；若仍看到此提示，说明 step 6 未按正式流程执行。_",
                "",
                "### 需要继续跟踪的点",
                "",
                "_本段应由程序内置模型自动生成；若仍看到此提示，说明 step 6 未按正式流程执行。_",
                "",
                "### 源地址",
                "",
            ]
        )
        for item in category.items:
            lines.append(format_brief_link_line(item.original_title, item.original_published_at, item.original_url))
        lines.extend(["", "### 补充地址", ""])
        if category.items and any(item.selected_contents for item in category.items):
            for item in category.items:
                for selected in item.selected_contents:
                    title = selected.document.title or selected.result_title or selected.url
                    lines.append(format_brief_link_line(title, selected.published_at, selected.url))
        else:
            lines.append("- 无")
    return "\n".join(lines)


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
                    "只返回 JSON 对象，包含：核心判断、增量信息、产业/公司影响、需要继续跟踪的点。"
                    "其中前三个字段是字符串，最后一个字段是字符串列表。\n\n"
                    f"{json.dumps(build_brief_prompt_payload(category), ensure_ascii=False, indent=2)}"
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


def render_generated_brief_markdown(
    payload: ContentAnalysisInput,
    section_drafts: list[BriefSectionDraft],
) -> str:
    """Render the final step 6 markdown from precomputed section drafts."""

    search_payload = None
    if payload.input_path.exists():
        search_results_path = infer_related_step3_path(payload.input_path)
        if search_results_path.exists():
            search_payload = load_search_results_yaml(search_results_path)
    summary = build_brief_runtime_summary(payload, search_payload)
    lines = [f"# {infer_brief_title(payload.input_path)}（{payload.report_date}）", "", "## 运行摘要", ""]
    lines.extend(
        [
            f"- 原始文章数：{summary['original_articles']}",
            f"- 补充链接数：{summary['external_links']}",
            f"- strong（强保留）：{summary['strong']}",
            f"- weak（弱保留）：{summary['weak']}",
            f"- drop（丢弃）：{summary['drop']}",
            f"- pending（待审）：{summary['pending']}",
            f"- 正文抓取成功数：{summary['content_success']}",
            f"- HTML fallback（HTML 回退）使用数：{summary['html_fallback']}",
        ]
    )
    if len(payload.categories) != len(section_drafts):
        raise StructuredLLMError("step 6 主题数量与生成的简报章节数量不一致。")
    for category, section_text in zip(payload.categories, section_drafts):
        lines.extend(
            [
                "",
                f"## {category.topic}",
                "",
                "### 核心判断",
                "",
                section_text.core_judgment,
                "",
                "### 增量信息",
                "",
                section_text.incremental_info,
                "",
                "### 产业/公司影响",
                "",
                section_text.industry_impact,
                "",
                "### 需要继续跟踪的点",
                "",
            ]
        )
        for followup in section_text.followups:
            lines.append(f"- {followup}")
        lines.extend(["", "### 源地址", ""])
        for item in category.items:
            lines.append(format_brief_link_line(item.original_title, item.original_published_at, item.original_url))
        lines.extend(["", "### 补充地址", ""])
        supplement_count = 0
        for item in category.items:
            for selected in item.selected_contents:
                title = selected.document.title or selected.result_title or selected.url
                lines.append(format_brief_link_line(title, selected.published_at, selected.url))
                supplement_count += 1
        if supplement_count == 0:
            lines.append("- 无")
    return "\n".join(lines)


def infer_related_step3_path(step4_or_step5_input_path: Path) -> Path:
    """从 step 4 输入路径推断同目录下的 step 3 搜索结果路径。"""

    name = step4_or_step5_input_path.name
    if "_step_4_content_" in name:
        return step4_or_step5_input_path.with_name(name.replace("_step_4_content_", "_step_3_search_results_"))
    return step4_or_step5_input_path


def infer_brief_title(input_path: Path) -> str:
    """根据输入文件名前缀推断站点简报标题。"""

    name = input_path.name.lower()
    if name.startswith("infoq_"):
        return "InfoQ 主题简报"
    return "C114 主题简报"


def format_brief_link_line(title: str, published_at: str, url: str) -> str:
    """把 step 6 链接行统一格式化为标题、日期、链接。"""

    normalized_date = (published_at or "").strip() or "日期未知"
    return f"- {title} | {normalized_date} | {url}"


def build_brief_prompt_payload(category: ContentAnalysisSection) -> dict[str, Any]:
    """构造发给模型的 step 6 单主题简报输入。"""
    return {
        "topic": category.topic,
        "items": [
            {
                "original_title": item.original_title,
                "summary": item.analysis.summary,
                "core_points": item.analysis.core_points[:3],
            }
            for item in category.items
        ],
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
        incomplete = [item.original_title for item in items if len(item.keywords) != 2]
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


def render_layer_issues_yaml(report_date: str, issues: dict[str, list[dict[str, Any]]]) -> str:
    """把各层问题统计渲染成 YAML 文本。"""
    lines = [f"report_date: '{report_date}'", "layers:"]
    for layer, layer_issues in issues.items():
        lines.append(f"  {layer}:")
        if not layer_issues:
            lines.append("    issues: []")
            continue
        lines.append("    issues:")
        for issue in layer_issues:
            lines.append(f"      - code: '{escape_yaml(str(issue['code']))}'")
            lines.append(f"        count: {issue['count']}")
            lines.append("        examples:")
            for example in issue.get("examples", []):
                lines.append(f"          - '{escape_yaml(str(example))}'")
    return "\n".join(lines)


def save_layer_issues_yaml(output_path: Path, report_date: str, issues: dict[str, list[dict[str, Any]]]) -> None:
    """把问题汇总写入 YAML 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_layer_issues_yaml(report_date, issues), encoding="utf-8")


def escape_yaml(value: str) -> str:
    """转义 YAML 单引号标量中的单引号。"""
    return value.replace("'", "''")


def build_brief_runtime_summary(
    payload: ContentAnalysisInput,
    search_payload: Any | None,
) -> dict[str, int]:
    """Build the compact runtime summary shown at the top of the final brief."""
    summary = {
        "original_articles": sum(len(category.items) for category in payload.categories),
        "external_links": 0,
        "strong": 0,
        "weak": 0,
        "drop": 0,
        "pending": 0,
        "content_success": 0,
        "html_fallback": 0,
    }
    for category in payload.categories:
        for item in category.items:
            if item.original_content.status == "success":
                summary["content_success"] += 1
            if item.original_content.source == "html_fallback":
                summary["html_fallback"] += 1
            for selected in item.selected_contents:
                summary["external_links"] += 1
                if selected.document.status == "success":
                    summary["content_success"] += 1
                if selected.document.source == "html_fallback":
                    summary["html_fallback"] += 1

    if search_payload is not None:
        for category in search_payload.categories:
            for item in category.items:
                for selected in item.selected_results:
                    keep_level = selected.keep_level if selected.keep_level in {"strong", "weak", "drop"} else "pending"
                    summary[keep_level] += 1
    return summary
