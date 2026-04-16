"""Step 4 输入输出 YAML 解析与渲染。

本模块负责读取 step 3 搜索结果 YAML，并把正文抓取结果渲染成 step 4 YAML。
它不负责网络请求和抓取策略。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..facades.intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    step_3_results_name,
    step_4_content_name,
)
from ..runtime.settings import AppPaths, resolve_override_path
from ..search.workflow import SearchResult, escape_yaml_scalar, parse_yaml_list_item, parse_yaml_value
from .types import (
    ContentWorkflowPayload,
    FetchOutputPaths,
    FetchResult,
    SearchContentArticleInput,
    SearchContentCategoryInput,
    SearchResultsInputPayload,
)


def resolve_content_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    output_override: str | None = None,
) -> FetchOutputPaths:
    """Resolve the canonical step 3 input and step 4 output paths."""
    run_dir: Path | None = None
    if input_override:
        input_path = resolve_override_path(paths.project_root, input_override)
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            input_path = (run_dir / step_3_results_name(report_date)).resolve()
        else:
            input_path = (c114_reports_root(paths.reports_dir) / step_3_results_name(report_date)).resolve()

    if output_override:
        output_path = resolve_override_path(paths.project_root, output_override)
    else:
        if input_override or run_dir:
            output_path = (input_path.parent / step_4_content_name(report_date)).resolve()
        else:
            output_path = (c114_reports_root(paths.reports_dir) / step_4_content_name(report_date)).resolve()
    return FetchOutputPaths(input_path=input_path, output_path=output_path)


def load_search_results_yaml(input_path: Path) -> SearchResultsInputPayload:
    """Parse the step 3 YAML into the normalized payload consumed by step 4."""
    report_date = ""
    generated_at = ""
    categories: list[SearchContentCategoryInput] = []
    current_topic = ""
    current_items: list[SearchContentArticleInput] = []
    current_item: dict[str, object] | None = None
    current_selected: dict[str, object] | None = None
    current_section = ""
    matched_terms_mode = False

    def finalize_selected() -> None:
        nonlocal current_selected
        if current_item is None or current_selected is None:
            return
        selected_results = current_item.setdefault("selected_results", [])
        assert isinstance(selected_results, list)
        selected_results.append(
            SearchResult(
                query=str(current_selected.get("query", "")),
                query_type=str(current_selected.get("query_type", "")),
                result_title=str(current_selected.get("result_title", "")),
                url=str(current_selected.get("url", "")),
                domain=str(current_selected.get("domain", "")),
                published_at=str(current_selected.get("published_at", "")),
                snippet=str(current_selected.get("snippet", "")),
                score=float(current_selected.get("score", 0.0)),
                is_official=bool(current_selected.get("is_official", False)),
                source_tier=str(current_selected.get("source_tier", "normal")),
                matched_terms=list(current_selected.get("matched_terms", [])),
                extract_text=str(current_selected.get("extract_text", "")),
                extract_status=str(current_selected.get("extract_status", "")),
                review_status=str(current_selected.get("review_status", "pending")),
                keep_level=str(current_selected.get("keep_level", "")),
                review_reason=str(current_selected.get("review_reason", "")),
                relevance_note=str(current_selected.get("relevance_note", "")),
                value_type=str(current_selected.get("value_type", "")),
            )
        )
        current_selected = None

    def finalize_item() -> None:
        nonlocal current_item
        finalize_selected()
        if current_item is None:
            return
        current_items.append(
            SearchContentArticleInput(
                topic=current_topic,
                channel=str(current_item.get("channel", "")),
                original_title=str(current_item.get("original_title", "")),
                original_url=str(current_item.get("original_url", "")),
                original_published_at=str(current_item.get("original_published_at", "")),
                selected_results=list(current_item.get("selected_results", [])),
            )
        )
        current_item = None

    def finalize_topic() -> None:
        nonlocal current_items
        finalize_item()
        if current_topic:
            categories.append(SearchContentCategoryInput(topic=current_topic, items=current_items))
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
            current_section = ""
            matched_terms_mode = False
            continue
        if line.startswith("      - original_title: "):
            finalize_item()
            current_item = {
                "original_title": parse_yaml_value(line),
                "channel": "",
                "original_url": "",
                "original_published_at": "",
                "selected_results": [],
            }
            current_section = ""
            matched_terms_mode = False
            continue
        if current_item is None:
            continue
        if line.startswith("        channel: "):
            current_item["channel"] = parse_yaml_value(line)
        elif line.startswith("        original_url: "):
            current_item["original_url"] = parse_yaml_value(line)
        elif line.startswith("        original_published_at: "):
            current_item["original_published_at"] = parse_yaml_value(line)
        elif line.startswith("        selected_results:"):
            finalize_selected()
            current_section = "selected_results"
            matched_terms_mode = False
        elif line.startswith("        search_results:") or line.startswith("        queries:"):
            finalize_selected()
            current_section = "ignore"
            matched_terms_mode = False
        elif current_section == "selected_results" and line.startswith("          - query: "):
            finalize_selected()
            current_selected = {
                "query": parse_yaml_value(line),
                "query_type": "",
                "result_title": "",
                "url": "",
                "domain": "",
                "published_at": "",
                "snippet": "",
                "score": 0.0,
                "is_official": False,
                "source_tier": "normal",
                "extract_status": "",
                "extract_text": "",
                "matched_terms": [],
                "review_status": "pending",
                "keep_level": "",
                "review_reason": "",
                "relevance_note": "",
                "value_type": "",
            }
            matched_terms_mode = False
        elif current_section == "selected_results" and current_selected is not None:
            if line.startswith("            query_type: "):
                current_selected["query_type"] = parse_yaml_value(line)
            elif line.startswith("            result_title: "):
                current_selected["result_title"] = parse_yaml_value(line)
            elif line.startswith("            url: "):
                current_selected["url"] = parse_yaml_value(line)
            elif line.startswith("            domain: "):
                current_selected["domain"] = parse_yaml_value(line)
            elif line.startswith("            published_at: "):
                current_selected["published_at"] = parse_yaml_value(line)
            elif line.startswith("            snippet: "):
                current_selected["snippet"] = parse_yaml_value(line)
            elif line.startswith("            score: "):
                current_selected["score"] = float(line.split(":", 1)[1].strip())
            elif line.startswith("            is_official: "):
                current_selected["is_official"] = line.endswith("true")
            elif line.startswith("            source_tier: "):
                current_selected["source_tier"] = parse_yaml_value(line)
            elif line.startswith("            is_trusted_media: "):
                current_selected["source_tier"] = "normal"
            elif line.startswith("            extract_status: "):
                current_selected["extract_status"] = parse_yaml_value(line)
            elif line.startswith("            extract_text: "):
                current_selected["extract_text"] = parse_yaml_value(line)
            elif line.startswith("            ai_review:"):
                matched_terms_mode = False
            elif line.startswith("              status: "):
                current_selected["review_status"] = parse_yaml_value(line)
            elif line.startswith("              keep_level: "):
                current_selected["keep_level"] = parse_yaml_value(line)
            elif line.startswith("              keep: "):
                current_selected["keep_level"] = "strong" if parse_yaml_value(line) == "true" else ""
            elif line.startswith("              reason: "):
                current_selected["review_reason"] = parse_yaml_value(line)
            elif line.startswith("              relevance_note: "):
                current_selected["relevance_note"] = parse_yaml_value(line)
            elif line.startswith("              value_type: "):
                current_selected["value_type"] = parse_yaml_value(line)
            elif line.startswith("            matched_terms:"):
                matched_terms_mode = True
            elif matched_terms_mode and line.startswith("              - "):
                matched_terms = current_selected.setdefault("matched_terms", [])
                assert isinstance(matched_terms, list)
                matched_terms.append(parse_yaml_list_item(line))
            else:
                matched_terms_mode = False

    finalize_topic()
    return SearchResultsInputPayload(
        report_date=report_date,
        input_path=input_path,
        generated_at=generated_at,
        categories=categories,
    )

def render_content_yaml(payload: ContentWorkflowPayload) -> str:
    """Render the step 4 YAML contract used by downstream analysis steps."""
    lines = [
        f"report_date: '{escape_yaml_scalar(payload.report_date)}'",
        f"input_path: '{escape_yaml_scalar(str(payload.input_path))}'",
        f"generated_at: '{escape_yaml_scalar(payload.generated_at)}'",
        "categories:",
    ]
    for category in payload.categories:
        lines.append(f"  - topic: '{escape_yaml_scalar(category.topic)}'")
        lines.append("    items:")
        for item in category.items:
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml_scalar(item.original_title)}'",
                    f"        topic: '{escape_yaml_scalar(item.topic)}'",
                    f"        channel: '{escape_yaml_scalar(item.channel)}'",
                    f"        original_url: '{escape_yaml_scalar(item.original_url)}'",
                    f"        original_published_at: '{escape_yaml_scalar(item.original_published_at)}'",
                    "        original_content:",
                ]
            )
            lines.extend(render_fetch_block(item.original_content, indent="          "))
            lines.append("        selected_contents:")
            for selected in item.selected_contents:
                lines.extend(
                    [
                        f"          - query: '{escape_yaml_scalar(selected.query)}'",
                        f"            query_type: '{escape_yaml_scalar(selected.query_type)}'",
                        f"            url: '{escape_yaml_scalar(selected.url)}'",
                        f"            domain: '{escape_yaml_scalar(selected.domain)}'",
                        f"            result_title: '{escape_yaml_scalar(selected.result_title)}'",
                        f"            published_at: '{escape_yaml_scalar(selected.published_at)}'",
                        f"            content_title: '{escape_yaml_scalar(selected.content_title)}'",
                        f"            content_summary: '{escape_yaml_scalar(selected.content_summary)}'",
                        f"            content_text: '{escape_yaml_scalar(selected.content_text)}'",
                        f"            content_source: '{escape_yaml_scalar(selected.content_source)}'",
                        f"            fetch_status: '{escape_yaml_scalar(selected.fetch_status)}'",
                        f"            fetch_error: '{escape_yaml_scalar(selected.fetch_error)}'",
                    ]
                )
    return "\n".join(lines)


def render_fetch_block(result: FetchResult, indent: str) -> list[str]:
    """把单条抓取结果渲染成 YAML 片段。"""
    return [
        f"{indent}url: '{escape_yaml_scalar(result.url)}'",
        f"{indent}domain: '{escape_yaml_scalar(result.domain)}'",
        f"{indent}content_title: '{escape_yaml_scalar(result.content_title)}'",
        f"{indent}content_summary: '{escape_yaml_scalar(result.content_summary)}'",
        f"{indent}content_text: '{escape_yaml_scalar(result.content_text)}'",
        f"{indent}content_source: '{escape_yaml_scalar(result.content_source)}'",
        f"{indent}fetch_status: '{escape_yaml_scalar(result.fetch_status)}'",
        f"{indent}fetch_error: '{escape_yaml_scalar(result.fetch_error)}'",
    ]


def save_content_results(output_path: Path, payload: ContentWorkflowPayload) -> None:
    """把 step 4 结果写入 YAML 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_content_yaml(payload), encoding="utf-8")
