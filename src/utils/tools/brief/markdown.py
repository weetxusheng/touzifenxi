"""Step 6 Markdown 渲染。

本模块只负责把 step 5 分析结果和 step 6 章节草稿渲染成 Markdown。
LLM 章节生成、checkpoint 和问题汇总由 steps.step6_brief 负责。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..analysis.models import BriefSectionDraft, ContentAnalysisInput
from ..facades.content import load_search_results_yaml
from utils.tools.llm import StructuredLLMError
from .links import format_brief_link_line, infer_brief_title, infer_related_step3_path


def _payload_is_kr36_report(payload: ContentAnalysisInput) -> bool:
    p = str(getattr(payload, "input_path", "") or "")
    return "kr36" in p.lower()


def format_kr36_crawl_week_range_label(report_date_iso: str) -> str:
    """
    36Kr 简报主标题内日期，与 ``source_adapter.resolve_kr36_biweekly_date_window`` 完全对齐：

    - **周三（weekday=2）** → 上周六 ～ 本周三（4 天窗口）
    - **周六（weekday=5）** → 本周三 ～ 本周六（4 天窗口）
    - **其他日期**          → 上一完整自然周（兜底，用于一次性/临时跑）

    例如：周三 Apr 29 → 「2026-04-25 至 2026-04-29」
          周六 May 2  → 「2026-04-29 至 2026-05-02」
    """
    s = (report_date_iso or "").strip()
    if len(s) < 10:
        return s or "日期未知"
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        return s
    wd = d.weekday()
    if wd == 2:  # 周三 → 上周六 ～ 本周三
        last_sat = d - timedelta(days=4)
        return f"{last_sat.isoformat()} 至 {d.isoformat()}"
    if wd == 5:  # 周六 → 本周三 ～ 本周六
        last_wed = d - timedelta(days=3)
        return f"{last_wed.isoformat()} 至 {d.isoformat()}"
    # 兜底：上一完整自然周
    current_week_monday = d - timedelta(days=wd)
    previous_week_sunday = current_week_monday - timedelta(days=1)
    previous_week_monday = previous_week_sunday - timedelta(days=6)
    if previous_week_monday == previous_week_sunday:
        return previous_week_monday.isoformat()
    return f"{previous_week_monday.isoformat()} 至 {previous_week_sunday.isoformat()}"


def _brief_title_date_phrase(payload: ContentAnalysisInput) -> str:
    raw = (payload.report_date or "").strip() or "日期未知"
    if not _payload_is_kr36_report(payload):
        return raw
    return format_kr36_crawl_week_range_label(raw)


def render_brief_markdown(payload: ContentAnalysisInput) -> str:
    """Render the step 6 brief from the current content-analysis payload."""
    search_payload = None
    if payload.input_path.exists():
        search_results_path = infer_related_step3_path(payload.input_path)
        if search_results_path.exists():
            search_payload = load_search_results_yaml(search_results_path)
    summary = build_brief_runtime_summary(payload, search_payload)
    lines = [f"# {infer_brief_title(payload.input_path)}（{_brief_title_date_phrase(payload)}）", "", "## 运行摘要", ""]
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
            ]
        )
        if not _payload_is_kr36_report(payload):
            lines.extend(
                [
                    "",
                    "### 源地址",
                    "",
                ]
            )
            for item in category.items:
                if not item.original_url.strip():
                    continue
                lines.append(
                    format_brief_link_line(
                        item.original_title, item.original_published_at, item.original_url,
                        include_date="/topics/" not in item.original_url,
                    )
                )
            lines.extend(["", "### 补充地址", ""])
            if category.items and any(item.selected_contents for item in category.items):
                for item in category.items:
                    for selected in item.selected_contents:
                        if not selected.url.strip():
                            continue
                        title = selected.document.title or selected.result_title or selected.url
                        lines.append(format_brief_link_line(title, selected.published_at, selected.url))
            else:
                lines.append("- 无")
    return "\n".join(lines)

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
    lines = [f"# {infer_brief_title(payload.input_path)}（{_brief_title_date_phrase(payload)}）", "", "## 运行摘要", ""]
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
        if not _payload_is_kr36_report(payload):
            lines.extend(["", "### 源地址", ""])
            for item in category.items:
                if not item.original_url.strip():
                    continue
                lines.append(format_brief_link_line(
                    item.original_title, item.original_published_at, item.original_url,
                    include_date="/topics/" not in item.original_url,
                ))
            lines.extend(["", "### 补充地址", ""])
            supplement_count = 0
            for item in category.items:
                for selected in item.selected_contents:
                    if not selected.url.strip():
                        continue
                    title = selected.document.title or selected.result_title or selected.url
                    lines.append(format_brief_link_line(title, selected.published_at, selected.url))
                    supplement_count += 1
            if supplement_count == 0:
                lines.append("- 无")
    return "\n".join(lines)

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
