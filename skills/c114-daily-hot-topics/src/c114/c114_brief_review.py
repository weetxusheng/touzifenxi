"""Step 7 quality review for the final brief."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .c114_content_analysis import load_content_analysis_inputs
from .c114_intelligence import (
    c114_reports_root,
    find_latest_search_run_directory,
    step_4_content_name,
    step_5_content_analysis_name,
    step_6_brief_name,
    step_7_brief_review_name,
)
from .llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    load_prompt_text,
    normalize_string_list,
    run_parallel_ordered,
)
from .settings import AppPaths, resolve_override_path

SKILL_ROOT = Path(__file__).resolve().parents[2]
BRIEF_REVIEW_PROMPT_PATH = SKILL_ROOT / "prompts" / "brief-review-agent.md"
PLACEHOLDER_MARKERS = (
    "_本段应由程序内置模型自动生成",
    "本段应由程序内置模型自动生成",
    "_待研究员",
    "_待资深研究员 agent 补全",
    "待资深研究员 agent 补全",
)
SECTION_NAMES = ("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点")
MARKDOWN_LINK_PATTERN = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)")
MARKET_LANGUAGE_MARKERS = ("主线", "估值", "资本市场", "景气", "映射标的", "主题炒作")
OVERREACH_MARKERS = ("将决定", "必然", "一定", "持续性估值支撑", "爆发", "显著改善", "真正的价值")
GENERIC_FOLLOWUP_MARKERS = ("持续跟踪", "继续关注", "后续进展", "相关进展", "动态变化")
VALID_REVIEW_ISSUE_TYPES = {
    "unsupported_claim",
    "overstatement",
    "missing_increment",
    "link_misgrouped",
    "topic_drift",
    "template_leftover",
    "other",
    "market_mismatch",
    "weak_judgment",
    "increment_not_new",
    "impact_overreach",
    "followup_too_generic",
}


@dataclass(frozen=True)
class BriefReviewOutputPaths:
    """封装 step 7 审查层需要读写的文件路径。"""

    brief_input_path: Path
    analysis_input_path: Path
    content_input_path: Path
    review_output_path: Path


@dataclass(frozen=True)
class BriefLink:
    """表示简报中的一条链接及其显示名称。"""

    name: str
    url: str


@dataclass(frozen=True)
class BriefTopicSection:
    """表示 step 6 中一个主题块的结构化内容。"""

    topic: str
    sections: dict[str, str]
    source_links: list[BriefLink]
    supplementary_links: list[BriefLink]


@dataclass(frozen=True)
class BriefReviewFinding:
    """表示审查报告中的单条问题记录。"""

    topic: str
    severity: str
    issue_type: str
    problem: str
    evidence: str
    suggestion: str


@dataclass(frozen=True)
class BriefReviewReport:
    """表示 step 7 最终输出的整份审查报告。"""

    report_date: str
    brief_path: Path
    analysis_path: Path
    review_prompt_path: Path
    overall_decision: str
    summary: str
    findings: list[BriefReviewFinding]
    strengths: list[str]


def resolve_brief_review_output_paths(
    paths: AppPaths,
    report_date: date,
    brief_input_override: str | None = None,
    analysis_input_override: str | None = None,
    content_input_override: str | None = None,
    output_override: str | None = None,
) -> BriefReviewOutputPaths:
    """Resolve the canonical paths needed by the step 7 review layer."""
    run_dir: Path | None = None
    if brief_input_override:
        brief_input_path = resolve_override_path(paths.project_root, brief_input_override)
    else:
        run_dir = find_latest_search_run_directory(paths.reports_dir, report_date)
        if run_dir:
            brief_input_path = (run_dir / step_6_brief_name(report_date)).resolve()
        else:
            brief_input_path = (c114_reports_root(paths.reports_dir) / step_6_brief_name(report_date)).resolve()

    base_dir = brief_input_path.parent if (brief_input_override or run_dir) else c114_reports_root(paths.reports_dir)
    analysis_input_path = (
        resolve_override_path(paths.project_root, analysis_input_override)
        if analysis_input_override
        else (base_dir / step_5_content_analysis_name(report_date)).resolve()
    )
    content_input_path = (
        resolve_override_path(paths.project_root, content_input_override)
        if content_input_override
        else (base_dir / step_4_content_name(report_date)).resolve()
    )
    review_output_path = (
        resolve_override_path(paths.project_root, output_override)
        if output_override
        else (base_dir / step_7_brief_review_name(report_date)).resolve()
    )
    return BriefReviewOutputPaths(
        brief_input_path=brief_input_path,
        analysis_input_path=analysis_input_path,
        content_input_path=content_input_path,
        review_output_path=review_output_path,
    )


def build_brief_review_report(
    report_date: str,
    brief_path: Path,
    analysis_path: Path,
    content_path: Path,
) -> BriefReviewReport:
    """Review a generated brief against the supporting step 5 evidence."""
    del content_path
    analysis_input = load_content_analysis_inputs(analysis_path)
    brief_sections = parse_brief_markdown(brief_path.read_text(encoding="utf-8"))
    findings: list[BriefReviewFinding] = []
    strengths: list[str] = []

    analysis_topics = {category.topic: category for category in analysis_input.categories}
    brief_topics = {section.topic: section for section in brief_sections}

    for missing_topic in sorted(set(analysis_topics) - set(brief_topics)):
        findings.append(
            BriefReviewFinding(
                topic=missing_topic,
                severity="high",
                issue_type="topic_drift",
                problem="研究员简报缺少该主题。",
                evidence=f"step_5 存在主题 {missing_topic}，但 step_6 未输出对应章节。",
                suggestion="补写该主题的完整简报块，并补齐源地址与补充地址。",
            )
        )

    for extra_topic in sorted(set(brief_topics) - set(analysis_topics)):
        findings.append(
            BriefReviewFinding(
                topic=extra_topic,
                severity="high",
                issue_type="topic_drift",
                problem="简报中出现了未在正文分析中定义的主题。",
                evidence=f"step_6 包含主题 {extra_topic}，但 step_5 中不存在该主题。",
                suggestion="检查主题是否误合并或误命名，确保与 step_5 保持一致。",
            )
        )

    for topic, section in brief_topics.items():
        category = analysis_topics.get(topic)
        if category is None:
            continue
        findings.extend(review_topic_placeholders(section))
        findings.extend(review_topic_links(section, category))
        findings.extend(review_topic_content(section, category))

        if not any(
            marker in section.sections.get(name, "") for name in SECTION_NAMES for marker in PLACEHOLDER_MARKERS
        ):
            strengths.append(f"{topic}：未发现模板占位语句。")
        expected_original_urls = {item.original_url for item in category.items}
        if expected_original_urls and all(link.url in expected_original_urls for link in section.source_links):
            strengths.append(f"{topic}：源地址分组与原始 C114 链接一致。")

    high_count = sum(1 for finding in findings if finding.severity == "high")
    medium_count = sum(1 for finding in findings if finding.severity == "medium")
    overall_decision = "revise" if high_count > 0 or medium_count > 1 else "pass"
    if findings:
        summary = (
            f"本轮审查发现 {len(findings)} 个问题，其中 high（严重）{high_count} 个、"
            f"medium（中等）{medium_count} 个。当前简报建议先修订再继续下游使用。"
        )
    else:
        summary = "本轮未发现需要阻断使用的明显问题，当前简报可直接进入下一步。"
    if not strengths:
        strengths.append("结构完整，已形成按主题聚合的研究员简报。")

    return BriefReviewReport(
        report_date=report_date,
        brief_path=brief_path,
        analysis_path=analysis_path,
        review_prompt_path=BRIEF_REVIEW_PROMPT_PATH,
        overall_decision=overall_decision,
        summary=summary,
        findings=findings,
        strengths=strengths,
    )


def build_brief_review_report_with_llm(
    report_date: str,
    brief_path: Path,
    analysis_path: Path,
    content_path: Path,
    llm_client: MiniMaxChatClient,
) -> BriefReviewReport:
    """Run the final step 7 audit via the fixed MiniMax model."""
    begin_llm_step(llm_client, "step_7")
    analysis_input = load_content_analysis_inputs(analysis_path)
    structural_report = build_brief_review_report(
        report_date=report_date,
        brief_path=brief_path,
        analysis_path=analysis_path,
        content_path=content_path,
    )
    system_prompt = load_prompt_text(BRIEF_REVIEW_PROMPT_PATH)
    brief_sections = parse_brief_markdown(brief_path.read_text(encoding="utf-8"))
    section_by_topic = {section.topic: section for section in brief_sections}
    analysis_by_topic = {category.topic: category for category in analysis_input.categories}
    shared_topics = [topic for topic in analysis_by_topic if topic in section_by_topic]

    def review_topic(topic: str) -> tuple[str, str, list[BriefReviewFinding], list[str]]:
        response = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=(
                "请只审查这一个主题的简报。只返回 JSON 对象，字段必须包含："
                "summary、findings、strengths。"
                "findings 中每条必须包含 topic、severity、issue_type、problem、evidence、suggestion。\n\n"
                f"{json.dumps(build_topic_review_prompt_payload(section_by_topic[topic], analysis_by_topic[topic]), ensure_ascii=False, indent=2)}"
            ),
        )
        normalized = normalize_topic_review_payload(response, default_topic=topic)
        return topic, normalized["summary"], normalized["findings"], normalized["strengths"]

    topic_reviews = run_parallel_ordered(shared_topics, review_topic)
    findings = list(structural_report.findings)
    strengths = list(structural_report.strengths)
    topic_summaries: list[str] = []
    for _, topic_summary, topic_findings, topic_strengths in topic_reviews:
        if topic_summary:
            topic_summaries.append(topic_summary)
        findings.extend(topic_findings)
        strengths.extend(topic_strengths)

    high_count = sum(1 for finding in findings if finding.severity == "high")
    medium_count = sum(1 for finding in findings if finding.severity == "medium")
    overall_decision = "revise" if high_count > 0 or medium_count > 1 else "pass"
    if findings:
        summary = (
            f"本轮审查发现 {len(findings)} 个问题，其中 high（严重）{high_count} 个、"
            f"medium（中等）{medium_count} 个。当前简报建议先修订再继续下游使用。"
        )
    else:
        summary = "本轮未发现需要阻断使用的明显问题，当前简报可直接进入下一步。"
    if topic_summaries:
        summary = summary + " " + " ".join(topic_summaries[:3])

    unique_strengths = list(dict.fromkeys(item for item in strengths if item))
    return BriefReviewReport(
        report_date=report_date,
        brief_path=brief_path,
        analysis_path=analysis_path,
        review_prompt_path=BRIEF_REVIEW_PROMPT_PATH,
        overall_decision=overall_decision,
        summary=summary,
        findings=findings,
        strengths=unique_strengths or ["结构完整，已形成按主题聚合的研究员简报。"],
    )


def build_brief_review_prompt_payload(brief_markdown: str, analysis_input: Any) -> dict[str, object]:
    """构造整包审查时发送给模型的输入载荷。"""
    return {
        "brief_markdown": brief_markdown,
        "analysis": {
            category.topic: [
                {
                    "original_title": item.original_title,
                    "summary": item.analysis.summary,
                    "new_facts": item.analysis.new_facts,
                    "signals": item.analysis.signals,
                    "risk_or_uncertainty": item.analysis.risk_or_uncertainty,
                    "why_it_matters": item.analysis.why_it_matters,
                }
                for item in category.items
            ]
            for category in analysis_input.categories
        },
    }


def build_topic_review_prompt_payload(section: BriefTopicSection, category: Any) -> dict[str, object]:
    """构造单主题审查时发送给模型的输入载荷。"""
    return {
        "topic": section.topic,
        "brief_section": {
            "sections": section.sections,
            "source_links": [{"name": link.name, "url": link.url} for link in section.source_links],
            "supplementary_links": [{"name": link.name, "url": link.url} for link in section.supplementary_links],
        },
        "analysis_items": [
            {
                "original_title": item.original_title,
                "summary": item.analysis.summary,
                "new_facts": item.analysis.new_facts,
                "signals": item.analysis.signals,
                "risk_or_uncertainty": item.analysis.risk_or_uncertainty,
                "why_it_matters": item.analysis.why_it_matters,
            }
            for item in category.items
        ],
    }


def normalize_brief_review_report(
    payload: object,
    *,
    report_date: str,
    brief_path: Path,
    analysis_path: Path,
) -> BriefReviewReport:
    """把整包 step 7 模型输出校验并转换为内部报告对象。"""
    if not isinstance(payload, dict):
        raise StructuredLLMError("step 7 返回不是 JSON 对象。")
    overall_decision = str(payload.get("overall_decision", "")).strip()
    if overall_decision not in {"pass", "revise"}:
        raise StructuredLLMError(f"step 7 overall_decision 非法：{overall_decision}")
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise StructuredLLMError("step 7 缺少 summary。")
    findings_payload = payload.get("findings")
    if not isinstance(findings_payload, list):
        raise StructuredLLMError("step 7 findings 必须为列表。")
    findings: list[BriefReviewFinding] = []
    for item in findings_payload:
        if not isinstance(item, dict):
            raise StructuredLLMError("step 7 finding 必须为对象。")
        severity = str(item.get("severity", "")).strip()
        issue_type = str(item.get("issue_type", "")).strip()
        if severity not in {"high", "medium", "low"}:
            raise StructuredLLMError(f"step 7 finding severity 非法：{severity}")
        if issue_type not in VALID_REVIEW_ISSUE_TYPES:
            raise StructuredLLMError(f"step 7 finding issue_type 非法：{issue_type}")
        findings.append(
            BriefReviewFinding(
                topic=str(item.get("topic", "")).strip(),
                severity=severity,
                issue_type=issue_type,
                problem=str(item.get("problem", "")).strip(),
                evidence=str(item.get("evidence", "")).strip(),
                suggestion=str(item.get("suggestion", "")).strip(),
            )
        )
    strengths = normalize_string_list(payload.get("strengths"))
    return BriefReviewReport(
        report_date=report_date,
        brief_path=brief_path,
        analysis_path=analysis_path,
        review_prompt_path=BRIEF_REVIEW_PROMPT_PATH,
        overall_decision=overall_decision,
        summary=summary,
        findings=findings,
        strengths=strengths or ["结构完整，已形成按主题聚合的研究员简报。"],
    )


def normalize_topic_review_payload(payload: object, *, default_topic: str) -> dict[str, object]:
    """把单主题审查结果校验并转换为内部可用结构。"""
    if not isinstance(payload, dict):
        raise StructuredLLMError("step 7 主题审查返回不是 JSON 对象。")
    summary = str(payload.get("summary", "")).strip()
    if not summary:
        raise StructuredLLMError("step 7 主题审查缺少 summary。")
    findings_payload = payload.get("findings")
    if not isinstance(findings_payload, list):
        raise StructuredLLMError("step 7 主题审查 findings 必须为列表。")
    normalized_findings: list[BriefReviewFinding] = []
    for item in findings_payload:
        if not isinstance(item, dict):
            raise StructuredLLMError("step 7 主题审查 finding 必须为对象。")
        severity = str(item.get("severity", "")).strip()
        issue_type = str(item.get("issue_type", "")).strip()
        if severity not in {"high", "medium", "low"}:
            raise StructuredLLMError(f"step 7 主题审查 severity 非法：{severity}")
        if issue_type not in VALID_REVIEW_ISSUE_TYPES:
            raise StructuredLLMError(f"step 7 主题审查 issue_type 非法：{issue_type}")
        normalized_findings.append(
            BriefReviewFinding(
                topic=str(item.get("topic", "")).strip() or default_topic,
                severity=severity,
                issue_type=issue_type,
                problem=str(item.get("problem", "")).strip(),
                evidence=str(item.get("evidence", "")).strip(),
                suggestion=str(item.get("suggestion", "")).strip(),
            )
        )
    strengths = normalize_string_list(payload.get("strengths"))
    return {"summary": summary, "findings": normalized_findings, "strengths": strengths}


def parse_brief_markdown(markdown_text: str) -> list[BriefTopicSection]:
    """Parse the step 6 Markdown into deterministic topic sections for review."""
    topics: list[BriefTopicSection] = []
    current_topic: str | None = None
    current_section: str | None = None
    section_buffers: dict[str, list[str]] = {}
    source_links: list[BriefLink] = []
    supplementary_links: list[BriefLink] = []

    def finalize_topic() -> None:
        nonlocal current_topic, current_section, section_buffers, source_links, supplementary_links
        if current_topic is None:
            return
        topics.append(
            BriefTopicSection(
                topic=current_topic,
                sections={name: "\n".join(lines).strip() for name, lines in section_buffers.items()},
                source_links=list(source_links),
                supplementary_links=list(supplementary_links),
            )
        )
        current_topic = None
        current_section = None
        section_buffers = {}
        source_links = []
        supplementary_links = []

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            title = line[3:].strip()
            if title == "运行摘要":
                current_section = None
                continue
            finalize_topic()
            current_topic = title
            continue
        if current_topic is None:
            continue
        if line.startswith("### "):
            current_section = line[4:].strip()
            section_buffers.setdefault(current_section, [])
            continue
        if current_section in {"源地址", "补充地址"} and line.startswith("- "):
            name, url = parse_markdown_link_line(line[2:])
            link = BriefLink(name=name, url=url)
            if current_section == "源地址":
                source_links.append(link)
            else:
                supplementary_links.append(link)
            continue
        if current_section is not None:
            section_buffers.setdefault(current_section, []).append(line)

    finalize_topic()
    return topics


def review_topic_placeholders(section: BriefTopicSection) -> list[BriefReviewFinding]:
    """Flag placeholder language that should never survive into a finished brief."""
    placeholder_sections = [
        name for name, content in section.sections.items() if any(marker in content for marker in PLACEHOLDER_MARKERS)
    ]
    if not placeholder_sections:
        return []
    return [
        BriefReviewFinding(
            topic=section.topic,
            severity="high",
            issue_type="template_leftover",
            problem="简报仍残留模板占位语句，说明研究员层尚未完成该主题的正式撰写。",
            evidence=f"发现占位语句的分节：{', '.join(placeholder_sections)}。",
            suggestion="删除占位语句，基于 step_5 证据补写对应分节的正式内容。",
        )
    ]


def review_topic_links(section: BriefTopicSection, category: object) -> list[BriefReviewFinding]:
    """Check whether source and supplementary links are grouped into the right buckets."""
    items = getattr(category, "items", [])
    original_urls = {item.original_url for item in items}
    selected_urls = {selected.url for item in items for selected in item.selected_contents}
    findings: list[BriefReviewFinding] = []

    wrong_source_links = [link for link in section.source_links if link.url not in original_urls]
    if wrong_source_links:
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="high",
                issue_type="link_misgrouped",
                problem="源地址分组混入了非原始 C114 链接。",
                evidence="；".join(f"{link.name} -> {link.url}" for link in wrong_source_links),
                suggestion="将这些链接移出“源地址”，只保留 step_5 中的 original_url。",
            )
        )

    wrong_supplementary_links = [
        link
        for link in section.supplementary_links
        if link.url in original_urls or (selected_urls and link.url not in selected_urls)
    ]
    if wrong_supplementary_links:
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="high",
                issue_type="link_misgrouped",
                problem="补充地址分组存在原始链接或未在 step_5 选中补充链接中的地址。",
                evidence="；".join(f"{link.name} -> {link.url}" for link in wrong_supplementary_links),
                suggestion="补充地址只能引用 step_5 中的 selected_contents.url，源稿链接应保留在“源地址”。",
            )
        )
    return findings


def review_topic_content(section: BriefTopicSection, category: object) -> list[BriefReviewFinding]:
    """Review market-facing content quality against the available supporting evidence."""
    items = getattr(category, "items", [])
    has_analysis_summary = any(getattr(item.analysis, "summary", "").strip() for item in items)
    has_why_it_matters = any(getattr(item.analysis, "why_it_matters", "").strip() for item in items)
    new_fact_count = sum(len(getattr(item.analysis, "new_facts", [])) for item in items)
    signal_count = sum(len(getattr(item.analysis, "signals", [])) for item in items)
    findings: list[BriefReviewFinding] = []

    core_judgment = section.sections.get("核心判断", "").strip()
    if core_judgment and not has_analysis_summary and not has_why_it_matters:
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="medium",
                issue_type="unsupported_claim",
                problem="核心判断已经写成正式研究判断，但 step_5 里没有对应的分析结论作为证据支撑。",
                evidence=(
                    f"step_6 核心判断写道：{truncate_text(core_judgment)}；"
                    "但 step_5 的 analysis.summary 与 analysis.why_it_matters 仍为空。"
                ),
                suggestion="先在 step_5 补出 summary/why_it_matters，再把核心判断收敛到这些证据可以支持的范围。",
            )
        )
    elif core_judgment and contains_any(core_judgment, MARKET_LANGUAGE_MARKERS) and signal_count == 0:
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="medium",
                issue_type="market_mismatch",
                problem="核心判断已经上升到市场主线或资本市场口径，但缺少足够的产业信号支撑。",
                evidence=(
                    f"核心判断包含市场化表述：{truncate_text(core_judgment)}；"
                    "但 step_5 的 analysis.signals 为空。"
                ),
                suggestion="把判断改回产业事实层，或在 step_5 中补充能支撑市场判断的具体信号。",
            )
        )

    increment_section = section.sections.get("增量信息", "").strip()
    increment_lines = extract_bullet_lines(increment_section)
    if increment_lines and new_fact_count == 0:
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="medium",
                issue_type="increment_not_new",
                problem="增量信息已经写成条目，但 step_5 没有沉淀对应的新事实清单，当前难以核对哪些是真增量。",
                evidence=(
                    f"step_6 增量信息共 {len(increment_lines)} 条；"
                    "但 step_5 的 analysis.new_facts 为空。"
                ),
                suggestion="先在 step_5 的 new_facts 中逐条沉淀新增事实，再把增量信息严格对齐到这些条目。",
            )
        )

    impact_section = section.sections.get("产业/公司影响", "").strip()
    if impact_section and contains_any(impact_section, OVERREACH_MARKERS + MARKET_LANGUAGE_MARKERS):
        if not has_why_it_matters and signal_count == 0:
            findings.append(
                BriefReviewFinding(
                    topic=section.topic,
                    severity="medium",
                    issue_type="impact_overreach",
                    problem="产业/公司影响已经写到竞争格局、估值或盈利层面，但 step_5 还没有给出足够的影响链条证据。",
                    evidence=(
                        f"step_6 产业/公司影响写道：{truncate_text(impact_section)}；"
                        "但 step_5 的 analysis.why_it_matters 与 analysis.signals 为空。"
                    ),
                    suggestion="将影响判断收窄到正文和补充材料能直接支持的层面，避免把方向性信号直接写成市场结论。",
                )
            )

    followup_section = section.sections.get("需要继续跟踪的点", "").strip()
    followup_lines = extract_bullet_lines(followup_section)
    if not followup_lines:
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="medium",
                issue_type="followup_too_generic",
                problem="需要继续跟踪的点没有拆成可执行条目，后续很难直接转成跟踪动作。",
                evidence="step_6 的该分节没有 bullet 条目。",
                suggestion="把跟踪点改写成 2 到 4 条可执行、可检索、可验证的跟踪事项。",
            )
        )
    elif all(is_generic_followup(line) for line in followup_lines):
        findings.append(
            BriefReviewFinding(
                topic=section.topic,
                severity="low",
                issue_type="followup_too_generic",
                problem="跟踪点存在，但表述偏泛，缺少具体公司、事件或指标。",
                evidence="；".join(followup_lines[:3]),
                suggestion="在跟踪点里加入具体主体、时间窗口、订单/政策/试点/收入等可验证信号。",
            )
        )

    return findings


def parse_markdown_link_line(content: str) -> tuple[str, str]:
    """从一行简报链接文本中提取标题和 URL。"""
    markdown_link = MARKDOWN_LINK_PATTERN.search(content)
    if markdown_link:
        url = markdown_link.group("url").strip()
        name = content[: markdown_link.start()].rstrip()
        if name.endswith("|"):
            name = name[:-1].rstrip()
        return name.strip(), url
    if " | " in content:
        name, url = content.rsplit(" | ", 1)
        return name.strip(), url.strip()
    return content.strip(), ""


def extract_bullet_lines(section_text: str) -> list[str]:
    """提取一个分节中的 bullet 列表内容。"""
    lines: list[str] = []
    for raw_line in section_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
    return lines


def contains_any(content: str, markers: tuple[str, ...] | list[str]) -> bool:
    """判断文本是否包含任一给定标记词。"""
    return any(marker in content for marker in markers)


def is_generic_followup(line: str) -> bool:
    """判断跟踪点是否过于空泛，缺少可执行信息。"""
    if contains_any(line, GENERIC_FOLLOWUP_MARKERS):
        return True
    return len(line) < 14


def truncate_text(value: str, limit: int = 90) -> str:
    """把长文本截断成适合写入审查证据的长度。"""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def render_brief_review_yaml(report: BriefReviewReport) -> str:
    """Render the step 7 YAML report for downstream revision or audit work."""
    lines = [
        f"report_date: '{escape_yaml(report.report_date)}'",
        f"brief_path: '{escape_yaml(str(report.brief_path))}'",
        f"analysis_path: '{escape_yaml(str(report.analysis_path))}'",
        f"review_prompt_path: '{escape_yaml(str(report.review_prompt_path))}'",
        f"overall_decision: '{escape_yaml(report.overall_decision)}'",
        f"summary: '{escape_yaml(report.summary)}'",
        "findings:",
    ]
    if report.findings:
        for finding in report.findings:
            lines.extend(
                [
                    f"  - topic: '{escape_yaml(finding.topic)}'",
                    f"    severity: '{escape_yaml(finding.severity)}'",
                    f"    issue_type: '{escape_yaml(finding.issue_type)}'",
                    f"    problem: '{escape_yaml(finding.problem)}'",
                    f"    evidence: '{escape_yaml(finding.evidence)}'",
                    f"    suggestion: '{escape_yaml(finding.suggestion)}'",
                ]
            )
    else:
        lines.append("  []")
    lines.append("strengths:")
    if report.strengths:
        for strength in report.strengths:
            lines.append(f"  - '{escape_yaml(strength)}'")
    else:
        lines.append("  []")
    return "\n".join(lines)


def save_brief_review_yaml(output_path: Path, report: BriefReviewReport) -> None:
    """把审查报告 YAML 写入目标文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_brief_review_yaml(report), encoding="utf-8")


def escape_yaml(value: str) -> str:
    """转义 YAML 单引号字符串中的特殊字符。"""
    return value.replace("'", "''")
