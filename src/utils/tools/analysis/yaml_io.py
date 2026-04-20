"""Step 5 输入输出 YAML 解析与路径解析。

本模块负责 step 4 正文 YAML 到 step 5 数据模型的转换，以及 step 5 YAML 渲染。
它不调用 LLM，确保文件格式变更不会影响模型请求编排。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ..brief.render import escape_yaml
from ..facades.intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    layer_issues_name,
    step_4_content_name,
    step_5_content_analysis_name,
    step_6_brief_name,
)
from c114.runtime.settings import AppPaths, resolve_override_path
from ..search.workflow import parse_yaml_value
from .models import (
    CONTENT_ANALYSIS_PROMPT_PATH,
    ContentAnalysisDraft,
    ContentAnalysisInput,
    ContentAnalysisItem,
    ContentAnalysisOutputPaths,
    ContentAnalysisSection,
    ContentDocument,
    SelectedDocument,
)


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
