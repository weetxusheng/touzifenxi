"""Step 5 and Step 6 rendering helpers for the C114 skill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .c114_content import load_search_results_yaml
from .c114_intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    layer_issues_name,
    load_daily_articles_from_csv,
    step_3_results_name,
    step_4_content_name,
    step_5_content_analysis_name,
    step_6_brief_name,
)
from .c114_search import load_search_checklist_yaml, parse_yaml_value
from .settings import AppPaths, load_c114_runtime_config

SKILL_ROOT = Path(__file__).resolve().parents[2]
CONTENT_ANALYSIS_PROMPT_PATH = SKILL_ROOT / "prompts" / "content-analysis-agent.md"
BRIEF_PROMPT_PATH = SKILL_ROOT / "prompts" / "brief-agent.md"
REQUIRED_ANALYSIS_LIST_FIELDS = (
    "core_points",
    "new_facts",
    "entities",
    "signals",
    "risk_or_uncertainty",
    "layer_notes",
)


@dataclass(frozen=True)
class ContentDocument:
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
    query: str
    query_type: str
    url: str
    domain: str
    result_title: str
    published_at: str
    document: ContentDocument


@dataclass(frozen=True)
class ContentAnalysisDraft:
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
    original_title: str
    topic: str
    channel: str
    original_url: str
    original_content: ContentDocument
    selected_contents: list[SelectedDocument]
    analysis: ContentAnalysisDraft


@dataclass(frozen=True)
class ContentAnalysisSection:
    topic: str
    items: list[ContentAnalysisItem]


@dataclass(frozen=True)
class ContentAnalysisInput:
    report_date: str
    input_path: Path
    generated_at: str
    categories: list[ContentAnalysisSection]


@dataclass(frozen=True)
class ContentAnalysisOutputPaths:
    input_path: Path
    analysis_output: Path
    brief_output: Path
    issues_output: Path


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
        input_path = (paths.project_root / input_override).resolve()
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            input_path = (run_dir / step_4_content_name(report_date)).resolve()
        else:
            input_path = (c114_reports_root(paths.reports_dir) / step_4_content_name(report_date)).resolve()

    if output_override:
        analysis_output = (paths.project_root / output_override).resolve()
    else:
        base_dir = input_path.parent if (input_override or run_dir) else c114_reports_root(paths.reports_dir)
        analysis_output = (base_dir / step_5_content_analysis_name(report_date)).resolve()

    if issues_output_override:
        issues_output = (paths.project_root / issues_output_override).resolve()
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
        "instructions: 'analysis 必须由 agent 读取 prompt_path 后填写，不允许自行改提示词；每条先读 original_content，再结合 selected_contents 提炼结论。'",
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
    lines = [f"{indent}{name}:"]
    if values:
        lines.extend(f"{indent}  - '{escape_yaml(value)}'" for value in values)
    return lines


def save_content_analysis_yaml(output_path: Path, payload: ContentAnalysisInput) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_content_analysis_yaml(payload), encoding="utf-8")


def collect_missing_analysis_fields(payload: ContentAnalysisInput) -> list[dict[str, Any]]:
    """Report which step 5 analysis fields still need agent completion before step 6."""

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
            if not item.analysis.why_it_matters.strip():
                missing_fields.append("why_it_matters")
            if missing_fields:
                missing_items.append(
                    {
                        "topic": item.topic,
                        "original_title": item.original_title,
                        "missing_fields": missing_fields,
                    }
                )
    return missing_items


def render_brief_markdown(payload: ContentAnalysisInput) -> str:
    """Render the step 6 brief from the current content-analysis payload."""
    search_payload = None
    if payload.input_path.exists():
        search_results_path = payload.input_path
        step4_prefix = "c114_step_4_content_"
        if search_results_path.name.startswith(step4_prefix):
            report_day = date.fromisoformat(payload.report_date)
            search_results_path = search_results_path.with_name(step_3_results_name(report_day))
        if search_results_path.exists():
            search_payload = load_search_results_yaml(search_results_path)
    runtime_config = load_c114_runtime_config(Path(__file__).resolve().parents[2])
    summary = build_brief_runtime_summary(payload, search_payload)
    role_label = "资深研究员 agent" if runtime_config.brief_role == "senior_researcher" else runtime_config.brief_role
    lines = [f"# C114 主题简报（{payload.report_date}）", "", f"> 角色：{role_label}", "", "## 运行摘要", ""]
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
                "_待资深研究员 agent 补全：给出该主题最重要的判断。_",
                "",
                "### 增量信息",
                "",
                "_待资深研究员 agent 补全：仅写相对源稿新增的事实、数据或观点。_",
                "",
                "### 产业/公司影响",
                "",
                "_待资深研究员 agent 补全：说明对产业链、公司或竞争格局的潜在影响。_",
                "",
                "### 需要继续跟踪的点",
                "",
                "_待资深研究员 agent 补全：列出后续值得持续追踪的线索。_",
                "",
                "### 源地址",
                "",
            ]
        )
        for item in category.items:
            lines.append(f"- {item.original_title} | {item.original_url}")
        lines.extend(["", "### 补充地址", ""])
        if category.items and any(item.selected_contents for item in category.items):
            for item in category.items:
                for selected in item.selected_contents:
                    title = selected.document.title or selected.result_title or selected.url
                    lines.append(f"- {title} | {selected.url}")
        else:
            lines.append("- 无")
    return "\n".join(lines)


def save_brief_markdown(output_path: Path, payload: ContentAnalysisInput) -> None:
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_layer_issues_yaml(report_date, issues), encoding="utf-8")


def escape_yaml(value: str) -> str:
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
