"""36Kr brief email renderer.

This renderer keeps all content on one page instead of hiding it behind tabs.
Raw topic and activity items come from the step-1 JSON output, while step-6
theme analysis cards stay visible in dedicated homepage sections.

与 C114 无关：C114 Step6 HTML 使用 ``utils.tools.output.email.render_c114_brief_email``，
本模块仅用于 36kr 流水线（``render_kr36_brief_email``）。
"""

from __future__ import annotations

import csv
import html as _html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from utils.tools.analysis.yaml_io import load_content_analysis_inputs
from utils.tools.output.email import (
    RenderedEmail,
    normalize_block_lines,
    split_metric_line,
)

INFO_SUBCATEGORIES = (
    "36氪独家",
    "深氪",
    "后浪白皮书",
    "行业日报",
    "Long China 50",
    "投资派",
    "新青年观察",
    "KRLab",
    "AI协同创新中心",
    "数智前瞻",
)
BUCKET_TOPIC = "专题"
BUCKET_ACTIVITY = "活动"
BUCKET_INFO = "资讯"
# 活动区默认最多展示条数；已结束场次于展示前筛除（见 _is_kr36_activity_ended）。
KR36_ACTIVITY_VISIBLE_MAX = 8
# 专题/资讯 栏头「描述」在邮件 HTML 中控制在约此字数内（与 Step5 汇总统一下再截断）。
KR36_TOPIC_INFO_SECTION_BLURB_MAX_CHARS = 200
FOOTER_DISCLAIMER = "本报告由系统自动生成，仅供参考，不构成投资建议。"
PLACEHOLDER_LINK_TEXT = {"无", "暂无", "日期未知"}

# 「运行摘要」中 `- 键: 值` 的键名分流：详情加粗、分栏引导、其余作为运行数据小字
_SUMMARY_LEAD_KEYS = frozenset({"详情总结", "详情", "概览", "要点", "简报要点", "总览"})
_SUMMARY_INTRO_TOPIC = frozenset({"专题", "专题总结"})
_SUMMARY_INTRO_ACTIVITY = frozenset({"活动", "活动总结"})
_SUMMARY_INTRO_INFO = frozenset({"资讯", "资讯总结", "资讯概览"})


def _partition_summary_items(
    summary_items: list[tuple[str, str]],
) -> tuple[str, dict[str, str], list[tuple[str, str]]]:
    """拆成：顶部「详情」段落、分栏引导语、其余运行数据。"""

    lead_chunks: list[str] = []
    intros: dict[str, str] = {"topic": "", "activity": "", "info": ""}
    run_stats: list[tuple[str, str]] = []
    for key, value in summary_items:
        k = (key or "").strip()
        v = (value or "").strip()
        if not k and not v:
            continue
        if k in _SUMMARY_LEAD_KEYS and v:
            lead_chunks.append(v)
        elif k in _SUMMARY_INTRO_TOPIC and v:
            intros["topic"] = v
        elif k in _SUMMARY_INTRO_ACTIVITY and v:
            intros["activity"] = v
        elif k in _SUMMARY_INTRO_INFO and v:
            intros["info"] = v
        else:
            run_stats.append((k, v))
    lead = " ".join(lead_chunks).strip()
    return lead, intros, run_stats


def _kr36_build_plain_text(
    *,
    title: str,
    lead: str,
    bucket_blocks: list[dict[str, object]],
    omit_source_links: bool = False,
) -> str:
    """纯文本版：固定三大栏目（专题/活动/资讯）+ 总述 + 编号观点 + 源地址。"""

    lines: list[str] = [title]
    if str(lead or "").strip():
        lines.extend(["", str(lead).strip()])
    for block in bucket_blocks:
        bucket_title = str(block.get("title") or "").strip()
        summary = str(block.get("summary") or "").strip()
        points = block.get("points", [])
        if not bucket_title:
            continue
        lines.extend(["", bucket_title])
        if summary:
            for ln in str(summary).splitlines():
                t = str(ln).strip()
                if t:
                    lines.append(f" {t}")
        _sources = block.get("sources", [])
        activity_omit_block = bool(
            bucket_title == BUCKET_ACTIVITY
            and omit_source_links
            and isinstance(_sources, list)
            and bool(_sources)
        )
        show_points_block = bucket_title == BUCKET_INFO or (
            omit_source_links
            and bucket_title in (BUCKET_TOPIC, BUCKET_ACTIVITY)
            and not activity_omit_block
        )
        if show_points_block:
            if isinstance(points, list) and points:
                for index, item in enumerate(points, start=1):
                    if not isinstance(item, tuple) or len(item) != 2:
                        continue
                    topic_title = str(item[0]).strip() or "待补充主题"
                    viewpoint = str(item[1]).strip() or "暂无可提炼观点，待补充。"
                    lines.append(f" {index}) {topic_title} 的观点：{viewpoint}")
            elif bucket_title == BUCKET_INFO:
                lines.append(" 1) 暂无可提炼观点：待补充。")
        sources = block.get("sources", [])
        if isinstance(sources, list) and sources:
            lines.append(" 活动信息" if activity_omit_block else " 源地址")
            for index, source in enumerate(sources, start=1):
                if not isinstance(source, dict):
                    continue
                if bucket_title == BUCKET_ACTIVITY:
                    if activity_omit_block:
                        lines.append(
                            " "
                            f"{index}) 名称：{str(source.get('title') or '待补充')}；"
                            f"时间：{str(source.get('time') or '待补充')}；"
                            f"地点：{str(source.get('city') or '待补充')}；"
                            f"主题：{str(source.get('theme') or '待补充')}；"
                            f"状态：{str(source.get('status') or '待补充')}；"
                            f"倒计时：{str(source.get('countdown') or '待补充')}。"
                        )
                    else:
                        lines.append(
                            " "
                            f"{index}) 名称：{str(source.get('title') or '待补充')}；"
                            f"时间：{str(source.get('time') or '待补充')}；"
                            f"地点：{str(source.get('city') or '待补充')}；"
                            f"主题：{str(source.get('theme') or '待补充')}；"
                            f"状态：{str(source.get('status') or '待补充')}；"
                            f"倒计时：{str(source.get('countdown') or '待补充')}；"
                            f"链接：{str(source.get('url') or '待补充')}"
                        )
                else:
                    lines.append(
                        " "
                        f"{index}) {str(source.get('title') or '待补充')} | "
                        f"{str(source.get('url') or '待补充')}"
                    )
    body = "\n".join(lines).strip()
    if FOOTER_DISCLAIMER:
        body = f"{body}\n\n{FOOTER_DISCLAIMER}"
    return body


def _extract_subsection_items(section: dict[str, Any], subtitle: str) -> list[str]:
    subsection_map = section.get("subsections", {})
    if not isinstance(subsection_map, dict):
        return []
    raw_items = subsection_map.get(subtitle, [])
    if not isinstance(raw_items, list):
        return []
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _normalize_sentence(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    if normalized[-1] not in "。！？!?":
        normalized += "。"
    return normalized


def _trim_text(text: str, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip("，,；;。.!?！？") + "…"


def _first_complete_sentence(text: str, max_chars: int = 140) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    matched = re.match(r"^(.+?[。！？!?])", normalized)
    if matched:
        sentence = matched.group(1).strip()
        if len(sentence) <= max_chars:
            return sentence
    if len(normalized) <= max_chars:
        return normalized
    return _trim_text(normalized, max_chars)


def _extract_section_viewpoint(section: dict[str, Any]) -> str:
    for subtitle in ("核心判断", "增量信息", "产业/公司影响", "产品/公司影响"):
        items = _extract_subsection_items(section, subtitle)
        if items:
            return _normalize_sentence(" ".join(items))
    return "暂无明确观点，待补充。"


def _rich_topic_section_narrative(section: dict[str, Any], *, max_chars: int = 400) -> str:
    """专题主题卡：拼合多子块为一段，比单句首句更详。"""
    parts: list[str] = []
    for subtitle in ("核心判断", "增量信息", "产业/公司影响", "产品/公司影响"):
        items = _extract_subsection_items(section, subtitle)
        if items:
            parts.append(" ".join(str(x).strip() for x in items if str(x).strip()))
    if not parts:
        return _trim_text(_extract_section_viewpoint(section), max_chars)
    merged = _normalize_sentence(" ".join(parts))
    return _trim_text(merged, max_chars) if len(merged) > max_chars else merged


def _build_bucket_summary(
    *,
    bucket_title: str,
    sections: list[dict[str, Any]],
    intro_text: str,
    raw_article_count: int,
    activity_sources: list[dict[str, str]] | None = None,
) -> str:
    intro = _normalize_sentence(intro_text)
    if bucket_title == BUCKET_TOPIC:
        snippets: list[str] = []
        for section in sections:
            sentence = _rich_topic_section_narrative(section, max_chars=400).rstrip("。")
            if sentence and sentence not in snippets:
                snippets.append(sentence)
            if len(snippets) >= 8:
                break
        prefix = f"今日专题共覆盖{len(sections)}个主题、共{raw_article_count}条专题来源。"
        detail = ""
        if snippets:
            detail = "围绕内容信号综合来看，各主题要点包括：" + "；".join(snippets) + "。"
        if intro and detail:
            return _normalize_sentence(f"{intro} {prefix}{detail}")
        if intro:
            return _normalize_sentence(f"{intro} {prefix}")
        if detail:
            return _normalize_sentence(prefix + detail)
        if raw_article_count > 0:
            return _normalize_sentence(prefix + "当前专题页面以索引型内容为主，后续可结合正文继续深化。")
        return "今日专题暂无可用内容，待补充。"

    if bucket_title == BUCKET_ACTIVITY:
        normalized_sources = [item for item in (activity_sources or []) if isinstance(item, dict)]
        phase_groups: dict[str, list[dict[str, str]]] = {"待开始": [], "进行中": [], "已结束": []}

        def infer_phase(countdown_text: str) -> str:
            text = str(countdown_text or "").strip()
            if not text:
                return "待开始"
            if "已结束" in text or "结束" in text:
                return "已结束"
            if any(keyword in text for keyword in ("进行中", "报名中", "直播中", "展出中")):
                return "进行中"
            if any(keyword in text for keyword in ("后开始", "待开始", "即将开始", "未开始", "倒计时")):
                return "待开始"
            return "待开始"

        for source in normalized_sources:
            phase = infer_phase(str(source.get("countdown") or ""))
            phase_groups[phase].append(source)

        pending_count = len(phase_groups["待开始"])
        ongoing_count = len(phase_groups["进行中"])
        ended_count = len(phase_groups["已结束"])
        total_count = len(normalized_sources) if normalized_sources else raw_article_count

        def sample_titles(items: list[dict[str, str]], limit: int = 2) -> str:
            titles: list[str] = []
            for item in items:
                title = str(item.get("title") or "").strip()
                if title and title not in titles:
                    titles.append(title)
                if len(titles) >= limit:
                    break
            return "、".join(titles)

        city_counter = Counter(
            str(item.get("city") or "").strip()
            for item in normalized_sources
            if str(item.get("city") or "").strip() and str(item.get("city") or "").strip() not in {"待补充", "未知"}
        )
        city_hint = ""
        if city_counter:
            top_cities = "、".join(f"{city}({count}场)" for city, count in city_counter.most_common(3))
            city_hint = f"地点分布上，{top_cities}活动占比更高。"

        base = (
            f"今日活动共覆盖{len(sections)}个主题，整理到{total_count}场活动。"
            f"按状态看：待开始{pending_count}场、进行中{ongoing_count}场、已结束{ended_count}场。"
        )
        details: list[str] = []
        pending_titles = sample_titles(phase_groups["待开始"])
        ongoing_titles = sample_titles(phase_groups["进行中"])
        ended_titles = sample_titles(phase_groups["已结束"])
        if pending_titles:
            details.append(f"待开始活动主要有：{pending_titles}。")
        if ongoing_titles:
            details.append(f"进行中活动主要有：{ongoing_titles}。")
        if ended_titles:
            details.append(f"已结束活动主要有：{ended_titles}。")
        if city_hint:
            details.append(city_hint)

        detail_text = " ".join(details).strip()
        if intro and detail_text:
            return _normalize_sentence(f"{intro} {base}{detail_text}")
        if intro:
            return _normalize_sentence(f"{intro} {base}")
        if detail_text:
            return _normalize_sentence(base + detail_text)
        return _normalize_sentence(base)

    if intro:
        return intro
    if sections:
        snippets: list[str] = []
        for section in sections:
            sentence = _trim_text(_extract_section_viewpoint(section), 56).rstrip("。")
            if sentence and sentence not in snippets:
                snippets.append(sentence)
            if len(snippets) >= 2:
                break
        if snippets:
            return f"今日{bucket_title}共覆盖{len(sections)}个主题，核心关注：{'；'.join(snippets)}。"
        return f"今日{bucket_title}共覆盖{len(sections)}个主题。"
    if raw_article_count > 0:
        return f"今日{bucket_title}共抓取{raw_article_count}条原始内容，正文分析待补充。"
    return f"今日{bucket_title}暂无可用内容，待补充。"


def _build_bucket_points(
    *,
    sections: list[dict[str, Any]],
    fallback_articles: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    points: list[tuple[str, str]] = []
    for section in sections:
        topic_title = str(section.get("title") or "").strip() or "待补充主题"
        viewpoint = _trim_text(_extract_section_viewpoint(section), 180)
        if viewpoint:
            points.append((topic_title, viewpoint))
    if points:
        return points
    for article in fallback_articles[:5]:
        title = str(article.get("title") or "").strip()
        if title:
            points.append((title, "该条目为原始抓取信息，正文分析待补充。"))
    return points


def _build_section_url_viewpoint_map(sections: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for section in sections:
        viewpoint = _trim_text(_extract_section_viewpoint(section), 180)
        if not viewpoint:
            continue
        for url in _extract_section_urls(section):
            normalized = _normalize_url_for_match(url)
            if normalized and normalized not in mapping:
                mapping[normalized] = viewpoint
    return mapping


def _build_bucket_points_by_source(
    *,
    sources: list[dict[str, str]],
    article_viewpoint_map: dict[str, str],
    section_url_viewpoint_map: dict[str, str],
) -> list[tuple[str, str]]:
    points: list[tuple[str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or "").strip() or "待补充主题"
        url = _normalize_url_for_match(str(source.get("url") or ""))
        viewpoint = ""
        if url:
            viewpoint = str(article_viewpoint_map.get(url) or "").strip()
            if not viewpoint:
                viewpoint = str(section_url_viewpoint_map.get(url) or "").strip()
        if not viewpoint:
            viewpoint = "该条目正文分析待补充。"
        points.append((title, _trim_text(_normalize_sentence(viewpoint), 180)))
    return points


def _parse_source_line(item: str) -> tuple[str, str, str]:
    cleaned = str(item or "").strip()
    if not cleaned:
        return "", "", ""
    matched = re.match(r"^(.+?)\s*\|\s*([^|]+?)\s*\|\s*(https?://\S+)$", cleaned)
    if matched:
        return matched.group(1).strip(), matched.group(2).strip(), matched.group(3).strip()
    matched = re.match(r"^(.+?)\s*\|\s*(https?://\S+)$", cleaned)
    if matched:
        return matched.group(1).strip(), "", matched.group(2).strip()
    url = _extract_url(cleaned)
    if not url:
        return cleaned, "", ""
    title = cleaned.replace(url, "").strip(" |")
    return title or url, "", url


def _build_activity_source_entry(
    *,
    title: str,
    published_at: str,
    url: str,
    article: dict[str, Any] | None,
) -> dict[str, str]:
    metadata = article.get("metadata", {}) if isinstance(article, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    status = str(metadata.get("activity_status") or "").strip()
    start_label = str(metadata.get("start_label") or "").strip()
    if status and start_label and status == start_label:
        display_countdown = ""
    else:
        display_countdown = start_label
    return {
        "title": title or str((article or {}).get("title") or "活动"),
        "time": str(metadata.get("activity_time_range") or published_at or "待补充"),
        "city": str(metadata.get("activity_city") or "待补充"),
        "theme": str(metadata.get("activity_theme") or "待补充"),
        "status": status or "待补充",
        "countdown": display_countdown or "待补充",
        "url": url,
    }


def _collect_bucket_sources(
    *,
    bucket_title: str,
    sections: list[dict[str, Any]],
    fallback_articles: list[dict[str, Any]],
    url_bucket_map: dict[str, str],
    url_article_map: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def append_entry(raw_title: str, published_at: str, url: str) -> None:
        raw_url = str(url or "").strip()
        normalized_url = _normalize_url_for_match(raw_url)
        if not normalized_url or normalized_url in seen_urls:
            return
        detected_bucket = (
            url_bucket_map.get(raw_url)
            or url_bucket_map.get(normalized_url)
            or _infer_bucket_from_url(raw_url)
            or _infer_bucket_from_url(normalized_url)
        )
        if detected_bucket and detected_bucket != bucket_title:
            return
        article = url_article_map.get(raw_url) or url_article_map.get(normalized_url) or {}
        if bucket_title == BUCKET_ACTIVITY:
            entry = _build_activity_source_entry(
                title=raw_title or str(article.get("title") or ""),
                published_at=published_at,
                url=raw_url,
                article=article,
            )
        else:
            entry = {
                "title": raw_title or str(article.get("title") or normalized_url),
                "url": raw_url,
            }
        sources.append(entry)
        seen_urls.add(normalized_url)

    for section in sections:
        source_items = _extract_subsection_items(section, "源地址")
        for item in source_items:
            parsed_title, published_at, url = _parse_source_line(item)
            append_entry(parsed_title, published_at, url)

    for article in fallback_articles:
        url = str(article.get("url") or "").strip()
        if not url:
            continue
        append_entry(str(article.get("title") or "").strip(), str(article.get("published_at") or "").strip(), url)

    return sources


def _html_escape_section_summary(text: str) -> str:
    """栏头总结：将换行渲染为 <br>，供专题一句 + 领域归纳 等多句拼接使用。"""
    t = (text or "").strip()
    if not t:
        return '<p class="section-summary"></p>'
    if "\n" in t:
        body = "<br>".join(_html.escape(line.strip()) for line in t.splitlines() if str(line).strip())
        return f'<p class="section-summary">{body}</p>'
    return f'<p class="section-summary">{_html.escape(t)}</p>'


def _render_bucket_section_html(
    *,
    title: str,
    summary: str,
    points: list[tuple[str, str]],
    sources: list[dict[str, str]],
    show_topic_activity_viewpoints: bool = False,
) -> str:
    activity_omit_detail = bool(
        title == BUCKET_ACTIVITY and show_topic_activity_viewpoints and bool(sources)
    )

    viewpoints_html = ""
    if not activity_omit_detail and (
        title == BUCKET_INFO
        or (show_topic_activity_viewpoints and title in (BUCKET_TOPIC, BUCKET_ACTIVITY) and points)
    ):
        items_html = "".join(
            "<li>"
            f'<span class="point-topic">{_html.escape(topic)} 的观点：</span>'
            f"{_html.escape(viewpoint)}"
            "</li>"
            for topic, viewpoint in points
        )
        if items_html:
            viewpoints_html = f'<ol class="viewpoints">{items_html}</ol>'
    elif not activity_omit_detail and title == BUCKET_INFO and not points:
        viewpoints_html = (
            '<ol class="viewpoints">'
            '<li><span class="point-topic">暂无可提炼观点：</span>待补充。</li>'
            "</ol>"
        )

    if title == BUCKET_ACTIVITY and sources:
        if activity_omit_detail:
            source_items_html = "".join(
                '<article class="activity-card activity-card-omit">'
                f'<h4 class="activity-card-title activity-card-title-omit">{_html.escape(str(item.get("title") or "待补充"))}</h4>'
                '<p class="activity-field"><span class="activity-field-label">时间：</span>'
                f'{_html.escape(str(item.get("time") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">地点：</span>'
                f'{_html.escape(str(item.get("city") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">主题：</span>'
                f'{_html.escape(str(item.get("theme") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">状态：</span>'
                f'{_html.escape(str(item.get("status") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">倒计时：</span>'
                f'{_html.escape(str(item.get("countdown") or "待补充"))}</p>'
                "</article>"
                for item in sources
            )
        else:
            source_items_html = "".join(
                "<article class=\"activity-card\">"
                f'<h4 class="activity-card-title"><a href="{_html.escape(str(item.get("url") or "#"), quote=True)}" target="_blank" rel="noopener">{_html.escape(str(item.get("title") or "待补充"))}</a></h4>'
                '<p class="activity-field"><span class="activity-field-label">时间：</span>'
                f'{_html.escape(str(item.get("time") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">地点：</span>'
                f'{_html.escape(str(item.get("city") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">主题：</span>'
                f'{_html.escape(str(item.get("theme") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">状态：</span>'
                f'{_html.escape(str(item.get("status") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">倒计时：</span>'
                f'{_html.escape(str(item.get("countdown") or "待补充"))}</p>'
                "</article>"
                for item in sources
            )
    else:
        source_items_html = "".join(
            "<li>"
            f'<a href="{_html.escape(str(item.get("url") or "#"), quote=True)}" target="_blank" rel="noopener">'
            f'{_html.escape(str(item.get("title") or "待补充"))}</a>'
            "</li>"
            for item in sources
        )
    if not source_items_html:
        source_block_html = ""
    elif title == BUCKET_ACTIVITY:
        source_block_html = f'<div class="activity-cards">{source_items_html}</div>'
    else:
        source_block_html = f'<ol class="sources">{source_items_html}</ol>'

    if activity_omit_detail and source_block_html:
        block_title = "活动信息"
    elif source_block_html:
        block_title = "源地址"
    else:
        block_title = ""
    source_title_html = f'<h3 class="source-title">{_html.escape(block_title)}</h3>{source_block_html}' if block_title else ""

    return (
        '<section class="brief-section">'
        f'<h2 class="section-title">{_html.escape(title)}</h2>'
        f"{_html_escape_section_summary(summary)}"
        f"{viewpoints_html}"
        f"{source_title_html}"
        "</section>"
    )


def _derive_date_from_path(path: Path) -> str:
    """Extract a YYYY-MM-DD date from a step output filename."""

    matched = re.search(r"(\d{8})", path.stem)
    if matched:
        compact = matched.group(1)
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return datetime.now().strftime("%Y-%m-%d")


def _load_articles(step6_path: Path) -> list[dict[str, Any]]:
    """Load raw articles from the matching kr36_hot_topics JSON payload."""

    date_suffix = _derive_date_from_path(step6_path).replace("-", "")
    candidates = (
        step6_path.parent / f"kr36_hot_topics_{date_suffix}.json",
        step6_path.parent / "kr36_hot_topics.json",
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("articles"), list):
            return [item for item in payload["articles"] if isinstance(item, dict)]
    return []


def _normalize_url_for_match(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw.rstrip("/")
    path = parts.path.rstrip("/")
    if not path:
        path = "/"
    normalized = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    return normalized


def _build_step5_path_candidates(step6_path: Path) -> list[Path]:
    stem = step6_path.stem
    candidates: list[Path] = []
    replacements = (
        ("step_6_brief", "step_5_content_analysis"),
        ("step4_brief", "step3_analysis"),
        ("step_4_brief", "step_3_analysis"),
    )
    for old, new in replacements:
        if old in stem:
            candidates.append(step6_path.with_name(stem.replace(old, new) + ".yaml"))
    date_token = re.search(r"(\d{8})", stem)
    if date_token:
        token = date_token.group(1)
        candidates.extend(
            [
                step6_path.parent / f"kr36_step_5_content_analysis_{token}.yaml",
                step6_path.parent / f"kr36_step3_analysis_{token}.yaml",
            ]
        )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.is_absolute() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _polish_step5_viewpoint_display(text: str) -> str:
    """
    将「提炼标题：…；内容要点：…」规范为「标题：正文」前置一句，便于阅读时不重复栏目里的标题行。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    if re.search(r"内容要点\s*[：:]", t):
        for splitter in (r"\s*[；;]\s*内容要点\s*[：:]\s*", r"\s*内容要点\s*[：:]\s*"):
            parts = re.split(splitter, t, maxsplit=1)
            if len(parts) == 2:
                head = re.sub(r"^\s*提炼标题\s*[：:]\s*", "", parts[0].strip()).strip()
                body = parts[1].strip()
                if head and body:
                    return f"{head}：{body}"
    only = re.match(r"^\s*提炼标题\s*[：:]\s*(.+)$", t, re.DOTALL)
    if only and "内容要点" not in t:
        return str(only.group(1)).strip()
    return t


def _compose_item_viewpoint(summary: str, core_points: list[str]) -> str:
    summary_text = str(summary or "").strip()
    points: list[str] = []
    for point in core_points:
        text = str(point or "").strip()
        if text and text not in points:
            points.append(text)

    low_signal_hints = (
        "这是一篇",
        "这是一条",
        "该内容为",
        "该页面",
        "专题索引",
        "导航页",
        "属于",
        "文章",
        "报道",
    )
    is_low_signal_summary = bool(summary_text) and any(hint in summary_text for hint in low_signal_hints)

    def _finish(s: str) -> str:
        return _normalize_sentence(_polish_step5_viewpoint_display(s))

    if summary_text and not is_low_signal_summary:
        if points and points[0] not in summary_text:
            return _finish(f"{summary_text}；{points[0]}")
        return _finish(summary_text)

    if points:
        merged = "；".join(points[:2])
        if summary_text and summary_text not in merged and not is_low_signal_summary:
            merged = f"{summary_text}；{merged}"
        return _finish(merged)

    if summary_text:
        return _finish(summary_text)
    return ""


def _load_article_viewpoint_map(step6_path: Path | None) -> dict[str, str]:
    if step6_path is None:
        return {}
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.exists():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        viewpoints: dict[str, str] = {}
        for category in payload.categories:
            for item in category.items:
                viewpoint = _compose_item_viewpoint(
                    item.analysis.summary,
                    list(item.analysis.core_points),
                )
                url = _normalize_url_for_match(item.original_url)
                if viewpoint and url:
                    viewpoints[url] = _trim_text(viewpoint, 180)
                for selected in item.selected_contents:
                    selected_url = _normalize_url_for_match(selected.url)
                    if selected_url and selected_url not in viewpoints:
                        selected_summary = str(selected.document.summary or "").strip()
                        if selected_summary:
                            polished = _normalize_sentence(
                                _polish_step5_viewpoint_display(str(selected_summary))
                            )
                            viewpoints[selected_url] = _trim_text(polished, 180)
        return viewpoints
    return {}


def _step5_item_has_original_text(item: Any) -> bool:
    """用于资讯：主正文 `original_content.text` 为空或明显失败时视为无原文，不展示该条。"""
    try:
        doc = getattr(item, "original_content", None)
        if doc is None:
            return True
        text = (getattr(doc, "text", None) or "").strip()
        if text:
            return True
        st = (getattr(doc, "status", None) or "").strip().lower()
        if st and any(
            x in st
            for x in (
                "fail",
                "error",
                "empty",
                "no_content",
                "no content",
                "block",
                "risk",
                "kr36_risk",
            )
        ):
            return False
    except Exception:
        return True
    return False


def _section_blurb_cap(text: str, max_chars: int = KR36_TOPIC_INFO_SECTION_BLURB_MAX_CHARS) -> str:
    """专题/资讯栏头描述等：统一按字数截断（保留句读）。"""
    t = (text or "").strip()
    if not t:
        return ""
    return _trim_text(t, max(24, max_chars))


def _synthesize_step5_topic_rollup_from_all_topic_items(step6_path: Path | None) -> str:
    """
    按 step5 中每个 `category`（专题线）下所有「栏目=专题」子项，拼成一段总述，
    覆盖该专题下全部文章的分析要点，用于栏头 `section-summary`；返回前已压到约 200 字内。
    """
    if step6_path is None:
        return ""
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.is_file():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        parts: list[str] = []
        for section in payload.categories:
            t_items = [it for it in section.items if _channel_to_email_bucket(it.channel) == BUCKET_TOPIC]
            if not t_items:
                continue
            tname = (section.topic or "").strip() or "专题"
            n = len(t_items)
            blurbs: list[str] = []
            for it in t_items:
                v = _compose_item_viewpoint(it.analysis.summary, list(it.analysis.core_points))
                text_in = (str(v) if v and str(v).strip() else str(it.analysis.summary or "")).strip()
                v = _polish_step5_viewpoint_display(text_in) if text_in else ""
                if v:
                    v = _trim_text(_normalize_sentence(v), 120)
                if v:
                    blurbs.append(v)
            if not blurbs:
                continue
            body = "；".join(blurbs)
            body = _trim_text(_normalize_sentence(body), 300)
            parts.append(f"「{tname}」{n} 条：{body}")
        if not parts:
            return ""
        merged = _normalize_sentence(" ".join(parts))
        return _section_blurb_cap(merged)
    return ""


def _synthesize_step5_info_rollup_from_all_info_items(step6_path: Path | None) -> str:
    """
    与专题汇总同构：每档 category 下所有「栏目=资讯」且有效原文子项，拼栏头总述，约 200 字内。
    """
    if step6_path is None:
        return ""
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.is_file():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        parts: list[str] = []
        for section in payload.categories:
            i_items = [
                it
                for it in section.items
                if _channel_to_email_bucket(it.channel) == BUCKET_INFO and _step5_item_has_original_text(it)
            ]
            if not i_items:
                continue
            tname = (section.topic or "").strip() or "资讯"
            n = len(i_items)
            blurbs: list[str] = []
            for it in i_items:
                v = _compose_item_viewpoint(it.analysis.summary, list(it.analysis.core_points))
                text_in = (str(v) if v and str(v).strip() else str(it.analysis.summary or "")).strip()
                v = _polish_step5_viewpoint_display(text_in) if text_in else ""
                if v:
                    v = _trim_text(_normalize_sentence(v), 120)
                if v:
                    blurbs.append(v)
            if not blurbs:
                continue
            body = "；".join(blurbs)
            body = _trim_text(_normalize_sentence(body), 300)
            parts.append(f"「{tname}」{n} 条：{body}")
        if not parts:
            return ""
        merged = _normalize_sentence(" ".join(parts))
        return _section_blurb_cap(merged)
    return ""


def _load_step5_per_item_points_by_bucket(
    step6_path: Path | None,
    *,
    viewpoint_max_chars: int = 500,
    url_article_map: dict[str, dict[str, Any]] | None = None,
    max_activity_subitems: int = KR36_ACTIVITY_VISIBLE_MAX,
) -> dict[str, list[tuple[str, str]]] | None:
    """
    从 step5 分析结果按 `item.channel` 归入 专题/活动/资讯，每条一行 (展示标题, 观点)。

    与 Markdown 的 ``##`` 分栏无关，用于「不展示源地址」时把约 10 条子项逐条列观点；
    若同一栏内有多个 `category.topic`（多档专题），标题前会加 `【主题名】` 区分。
    活动子项会结合 hot_topics（``url_article_map``）筛掉已结束，并截断为最多
    ``max_activity_subitems`` 条。找不到 step5 文件时返回 None。
    """
    if step6_path is None:
        return None
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.is_file():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        raw: dict[str, list[tuple[str, str, str]]] = {
            BUCKET_TOPIC: [],
            BUCKET_ACTIVITY: [],
            BUCKET_INFO: [],
        }
        umap = url_article_map or {}
        for category in payload.categories:
            g = (category.topic or "").strip()
            for item in category.items:
                b = _channel_to_email_bucket(item.channel)
                t = (item.original_title or "").strip() or "（未命名）"
                v = _compose_item_viewpoint(item.analysis.summary, list(item.analysis.core_points))
                if not (v and str(v).strip()):
                    v = str(item.analysis.summary or "").strip() or "（本条目暂无可生成观点。）"
                v = _trim_text(_normalize_sentence(str(v)), viewpoint_max_chars)
                if b == BUCKET_ACTIVITY and umap:
                    u = (item.original_url or "").strip()
                    if u:
                        art = umap.get(u) or umap.get(_normalize_url_for_match(u) or "")
                        if art and not _include_kr36_activity_in_feed(art):
                            continue
                if b == BUCKET_INFO and not _step5_item_has_original_text(item):
                    continue
                raw[b].append((g, t, v))
        if max_activity_subitems and raw[BUCKET_ACTIVITY]:
            raw[BUCKET_ACTIVITY] = raw[BUCKET_ACTIVITY][: max(0, max_activity_subitems)]
        out: dict[str, list[tuple[str, str]]] = {}
        for bucket, rows in raw.items():
            if not rows:
                out[bucket] = []
                continue
            groups = {g for g, _, _ in rows if g}
            multi_group = len(groups) > 1
            flat: list[tuple[str, str]] = []
            for g, t, v in rows:
                if multi_group and g:
                    label = f"【{g}】{t}"
                else:
                    label = t
                flat.append((label, v))
            out[bucket] = flat
        return out
    return None


def _load_step5_activity_omit_enriched(
    step6_path: Path | None,
    url_article_map: dict[str, dict[str, Any]],
    *,
    viewpoint_max_chars: int = 500,
    max_items: int = KR36_ACTIVITY_VISIBLE_MAX,
) -> list[dict[str, str]] | None:
    """
    从 step5 取活动子项，与 hot 对齐后带齐「主题/时间/地点/状态/倒计时」+ 观点，仅「进行中+待开始」。
    无可用 step5 时返回 None；有文件但无子项时返回「[]」。
    """
    if step6_path is None or not url_article_map:
        return None
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.is_file():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        raw: list[tuple[str, str, str, str]] = []
        for category in payload.categories:
            g = (category.topic or "").strip()
            for item in category.items:
                if _channel_to_email_bucket(item.channel) != BUCKET_ACTIVITY:
                    continue
                u = (item.original_url or "").strip()
                art: dict[str, Any] | None = None
                if u:
                    art = url_article_map.get(u) or url_article_map.get(_normalize_url_for_match(u) or "")
                    if art and not _include_kr36_activity_in_feed(art):
                        continue
                t = (item.original_title or "").strip() or "（未命名）"
                v = _compose_item_viewpoint(item.analysis.summary, list(item.analysis.core_points))
                if not (v and str(v).strip()):
                    v = str(item.analysis.summary or "").strip() or "（本条目暂无可生成观点。）"
                v = _trim_text(_normalize_sentence(str(v)), viewpoint_max_chars)
                raw.append((g, t, v, u))
        if max_items and raw:
            raw = raw[: max(0, max_items)]
        if not raw:
            return []
        groups = {g for g, _, _, _ in raw if g}
        multi = len(groups) > 1
        out: list[dict[str, str]] = []
        for g, t, v, u in raw:
            label = f"【{g}】{t}" if (multi and g) else t
            article: dict[str, Any] | None
            if u:
                got = url_article_map.get(u) or url_article_map.get(_normalize_url_for_match(u) or "")
                article = got if isinstance(got, dict) else None
            else:
                article = None
            published_at = str((article or {}).get("published_at") or "") if article else ""
            base = _build_activity_source_entry(
                title=label,
                published_at=published_at,
                url=u,
                article=article,
            )
            base["viewpoint"] = v
            base["title"] = label
            out.append(base)
        return out
    return None


def _build_omit_per_item_list_summary(
    *,
    intro_text: str,
    bucket_title: str,
    n_subitems: int,
) -> str:
    """不展示源地址、且用 step5 逐条列观点时，栏头一句总结 + 条数说明。"""
    if n_subitems <= 0:
        return ""
    label = "专题子项" if bucket_title == BUCKET_TOPIC else ("活动子项" if bucket_title == BUCKET_ACTIVITY else "资讯条目")
    intro = (intro_text or "").strip()
    if intro:
        return _normalize_sentence(f"{intro} 本栏共 {n_subitems} 条{label}，下为逐条观点。")
    return _normalize_sentence(f"本栏共 {n_subitems} 条{label}，下为逐条观点。")


def _infer_bucket_from_url(url: str) -> str | None:
    normalized = str(url or "").strip()
    if not normalized:
        return None
    if re.search(r"^https?://(?:www\.)?36kr\.com/search/articles/", normalized):
        return BUCKET_INFO
    if re.search(r"^https?://(?:www\.)?36kr\.com/topics/", normalized):
        return BUCKET_TOPIC
    if re.search(r"^https?://(?:www\.)?36kr\.com/(?:sign-up-activity|activity)/", normalized):
        return BUCKET_ACTIVITY
    return None


def _normalize_bucket(article: dict[str, Any]) -> str:
    bucket = str(article.get("source_bucket") or article.get("channel") or "").strip()
    if bucket == BUCKET_TOPIC:
        return BUCKET_TOPIC
    if bucket == BUCKET_ACTIVITY:
        return BUCKET_ACTIVITY
    inferred = _infer_bucket_from_url(str(article.get("url") or ""))
    if inferred:
        return inferred
    return BUCKET_INFO


def _filter_articles_by_bucket(articles: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    return [article for article in articles if _normalize_bucket(article) == bucket]


def _build_url_bucket_map(articles: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for article in articles:
        url = str(article.get("url") or "").strip()
        if url:
            bucket = _normalize_bucket(article)
            mapping[url] = bucket
            normalized = _normalize_url_for_match(url)
            if normalized:
                mapping[normalized] = bucket
    return mapping


def _build_url_article_map(articles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for article in articles:
        url = str(article.get("url") or "").strip()
        if url:
            mapping[url] = article
            normalized = _normalize_url_for_match(url)
            if normalized:
                mapping[normalized] = article
    return mapping


def _is_kr36_activity_ended(article: dict[str, Any]) -> bool:
    """依据 hot_topics 中 metadata 判断活动是否已结束。"""
    if not isinstance(article, dict):
        return False
    metadata = article.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    for key in ("activity_status", "start_label"):
        text = str(metadata.get(key) or "").strip()
        if "未结束" in text:
            return False
    for key in ("activity_status", "start_label"):
        text = str(metadata.get(key) or "").strip()
        if text and "已结束" in text:
            return True
    return False


def _infer_kr36_activity_phase(article: dict[str, Any] | None) -> str:
    """根据 metadata 粗分活动阶段：已结束 / 进行中 / 待开始。"""
    if not article or not isinstance(article, dict):
        return "待开始"
    if _is_kr36_activity_ended(article):
        return "已结束"
    metadata = article.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    status = str(metadata.get("activity_status") or "").strip()
    start_label = str(metadata.get("start_label") or "").strip()
    combined = f"{status} {start_label}"
    if "未结束" in combined:
        return "进行中"
    if "已结束" in combined:
        return "已结束"
    for keyword in ("进行中", "报名中", "直播中", "展出中", "进行"):
        if keyword in combined:
            return "进行中"
    for keyword in ("天后开始", "小时开始", "分后开始", "待开始", "即将开始", "未开始", "倒计时"):
        if keyword in combined:
            return "待开始"
    return "待开始"


def _include_kr36_activity_in_feed(article: dict[str, Any] | None) -> bool:
    """活动区/omit 子项：仅「进行中 + 待开始」；无源数据时保留。"""
    if not article or not isinstance(article, dict):
        return True
    return _infer_kr36_activity_phase(article) in ("进行中", "待开始")


def _filter_kr36_visible_activities(
    articles: list[dict[str, Any]], *, max_items: int = KR36_ACTIVITY_VISIBLE_MAX
) -> list[dict[str, Any]]:
    """筛除已结束及非进行/非待开始场，再截断为最多 max_items 场。"""
    selected = [a for a in articles if _include_kr36_activity_in_feed(a)]
    return selected[: max(0, max_items)] if max_items else selected


def _load_url_channel_map(step6_path: Path) -> dict[str, str]:
    """Load URL-to-channel mapping from the step-1 CSV output."""

    date_suffix = _derive_date_from_path(step6_path).replace("-", "")
    csv_path = step6_path.parent / f"kr36_step_1_analysis_{date_suffix}.csv"
    if not csv_path.exists():
        return {}

    mapping: dict[str, str] = {}
    try:
        with csv_path.open(encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                url = str(row.get("文章链接") or "").strip()
                channel = str(row.get("栏目") or "").strip()
                if url and channel:
                    mapping[url] = channel
    except Exception:
        return {}
    return mapping


def _parse_brief_markdown(markdown_text: str) -> tuple[str, list[tuple[str, str]], list[dict[str, Any]]]:
    """Parse step-6 markdown into title, stats, and topic sections."""

    title = "36Kr 主题简报"
    summary_items: list[tuple[str, str]] = []
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_subtitle = ""
    buffer: list[str] = []

    def flush_subsection() -> None:
        nonlocal buffer, current_subtitle, current_section
        if current_section is None or not current_subtitle:
            buffer = []
            return
        subsection_map = current_section.setdefault("subsections", {})
        assert isinstance(subsection_map, dict)
        subsection_map[current_subtitle] = normalize_block_lines(buffer)
        buffer = []

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if raw_line.startswith("# "):
            title = raw_line[2:].strip()
            continue
        if raw_line.startswith("## "):
            flush_subsection()
            current_subtitle = ""
            section_title = raw_line[3:].strip()
            if section_title == "运行摘要":
                current_section = None
            else:
                current_section = {"title": section_title, "subsections": {}}
                sections.append(current_section)
            continue
        if raw_line.startswith("### "):
            flush_subsection()
            current_subtitle = raw_line[4:].strip()
            continue
        if current_section is None:
            if raw_line.startswith("- "):
                key, value = split_metric_line(raw_line[2:].strip())
                if key:
                    summary_items.append((key, value))
            continue
        buffer.append(raw_line)

    flush_subsection()
    return title, summary_items, sections


def _title_trailing_colon(text: str) -> str:
    t = (text or "").rstrip()
    if not t:
        return t
    if t[-1] in "：:":
        return t
    return t + "："


def _anchor(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return slug or "section"


def _extract_url(text: str) -> str:
    matched = re.search(r"(https?://\S+)$", text.strip())
    return matched.group(1) if matched else ""


def _extract_section_urls(section: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    subsection_map = section.get("subsections", {})
    if not isinstance(subsection_map, dict):
        return urls
    for items in subsection_map.values():
        if not isinstance(items, list):
            continue
        for item in items:
            url = _extract_url(str(item))
            if url:
                urls.append(url)
    return urls


def _channel_to_email_bucket(channel: str) -> str:
    c = (channel or "").strip()
    if c == BUCKET_TOPIC:
        return BUCKET_TOPIC
    if c == BUCKET_ACTIVITY:
        return BUCKET_ACTIVITY
    return BUCKET_INFO


def _dominant_bucket_from_channels(channels: list[str]) -> str:
    if not channels:
        return BUCKET_INFO
    buckets = [_channel_to_email_bucket(c) for c in channels]
    counts = Counter(buckets)
    best_n = max(counts.values())
    winners = [b for b, n in counts.items() if n == best_n]
    for pref in (BUCKET_TOPIC, BUCKET_ACTIVITY, BUCKET_INFO):
        if pref in winners:
            return pref
    return BUCKET_INFO


def _load_group_topic_bucket_map(step6_path: Path | None) -> dict[str, str]:
    """
    从 step5 分析 YAML 为每个「聚合主题」(与 step6 ``##`` 标题一致) 得到 专题/活动/资讯
    多数票，供无源地址的简报 Markdown 做栏目分类。

    当 step6 不输出「源地址」时，_classify_section_bucket 无法从 URL 推断栏目；此前会全部
    落入「资讯」。
    """
    if step6_path is None:
        return {}
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.is_file():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        out: dict[str, str] = {}
        for category in payload.categories:
            t = str(category.topic or "").strip()
            if not t:
                continue
            chans = [item.channel for item in category.items]
            out[t] = _dominant_bucket_from_channels(chans)
        return out
    return {}


def _classify_section_bucket(
    section: dict[str, Any],
    url_bucket_map: dict[str, str],
    *,
    group_topic_bucket_map: dict[str, str] | None = None,
) -> str:
    section_urls = _extract_section_urls(section)
    if any(_infer_bucket_from_url(url) == BUCKET_INFO for url in section_urls):
        return BUCKET_INFO

    counts: Counter[str] = Counter()
    for url in section_urls:
        bucket = url_bucket_map.get(url) or _infer_bucket_from_url(url) or BUCKET_INFO
        counts[bucket] += 1
    if not counts:
        title = str(section.get("title") or "").strip()
        if group_topic_bucket_map and title in group_topic_bucket_map:
            return group_topic_bucket_map[title]
        return BUCKET_INFO
    max_count = max(counts.values())
    for bucket in (BUCKET_TOPIC, BUCKET_ACTIVITY, BUCKET_INFO):
        if counts[bucket] == max_count:
            return bucket
    return BUCKET_INFO


def _collect_present_info_channels(sections: list[dict[str, Any]], url_channel_map: dict[str, str]) -> list[str]:
    present: list[str] = []
    for section in sections:
        for url in _extract_section_urls(section):
            channel = str(url_channel_map.get(url, "")).strip()
            if channel in {BUCKET_TOPIC, BUCKET_ACTIVITY, BUCKET_INFO} or not channel:
                continue
            if channel not in present:
                present.append(channel)
    return [channel for channel in INFO_SUBCATEGORIES if channel in present] or present


def _collect_section_channel_labels(section: dict[str, Any], url_channel_map: dict[str, str]) -> list[str]:
    labels: list[str] = []
    for url in _extract_section_urls(section):
        channel = str(url_channel_map.get(url, "")).strip()
        if channel in {BUCKET_TOPIC, BUCKET_ACTIVITY, BUCKET_INFO} or not channel:
            continue
        if channel not in labels:
            labels.append(channel)
    return [channel for channel in INFO_SUBCATEGORIES if channel in labels] or labels


def _render_topic_link_item(article: dict[str, Any]) -> str:
    title = _html.escape(str(article.get("title") or "专题"))
    url = _html.escape(str(article.get("url") or "#"), quote=True)
    return (
        '<li class="topic-source-item">'
        f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
        "</li>"
    )


def _render_topic_source_block(articles: list[dict[str, Any]]) -> str:
    list_items = "".join(_render_topic_link_item(article) for article in articles)
    if not list_items:
        return ""
    return (
        '<div class="block topic-source-block">'
        '<h4 class="block-title">专题源地址</h4>'
        f'<ul class="link-list topic-source-list">{list_items}</ul>'
        "</div>"
    )


def _render_info_article_link(article: dict[str, Any]) -> str:
    title = _html.escape(str(article.get("title") or "资讯"))
    url = _html.escape(str(article.get("url") or "#"), quote=True)
    published_at = _html.escape(str(article.get("published_at") or ""))
    date_html = f'<span class="info-source-date">{published_at}</span>' if published_at else ""
    return (
        '<li class="info-source-item">'
        f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
        f"{date_html}"
        "</li>"
    )


def _render_info_channel_source_groups(articles: list[dict[str, Any]], url_channel_map: dict[str, str]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {channel: [] for channel in INFO_SUBCATEGORIES}
    for article in articles:
        if _normalize_bucket(article) != BUCKET_INFO:
            continue
        url = str(article.get("url") or "").strip()
        channel = str(article.get("channel") or "").strip()
        if channel not in grouped:
            channel = str(url_channel_map.get(url) or "").strip()
        if channel not in grouped:
            continue
        grouped[channel].append(article)

    blocks: list[str] = []
    for channel in INFO_SUBCATEGORIES:
        items = grouped[channel][:5]
        if not items:
            continue
        links = "".join(_render_info_article_link(article) for article in items)
        blocks.append(
            '<section class="info-channel-card">'
            f'<h3 class="info-channel-title">{_html.escape(channel)}</h3>'
            f'<ul class="link-list info-source-list">{links}</ul>'
            "</section>"
        )
    if not blocks:
        return ""
    return '<div class="info-channel-grid">' + "".join(blocks) + "</div>"


def _render_activity_source_item(article: dict[str, Any]) -> str:
    """活动与专题一致放入「源地址」列表；另标注状态与倒计时/进行中/已结束。"""
    metadata = article.get("metadata") or {}
    title = _html.escape(str(article.get("title") or "活动"))
    url = _html.escape(str(article.get("url") or "#"), quote=True)
    status = str(metadata.get("activity_status") or "").strip()
    start_label = str(metadata.get("start_label") or "").strip()
    # 状态与第二行文案相同（如均为「已结束」）时只保留状态徽标，避免重复标记
    if status and start_label and status == start_label:
        start_label = ""
    status_html = f'<span class="activity-status">{_html.escape(status)}</span>' if status else ""
    countdown_html = (
        f'<span class="activity-countdown">{_html.escape(start_label)}</span>' if start_label else ""
    )
    badges = ""
    if status_html or countdown_html:
        badges = f'<span class="activity-source-badges">{status_html}{countdown_html}</span>'
    return (
        '<li class="topic-source-item activity-source-item">'
        f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
        f"{badges}"
        "</li>"
    )


def _render_activity_source_block(articles: list[dict[str, Any]]) -> str:
    items = "".join(_render_activity_source_item(article) for article in articles)
    if not items:
        return ""
    return (
        '<div class="block topic-source-block">'
        '<h4 class="block-title">活动源地址</h4>'
        f'<ul class="link-list topic-source-list activity-source-list">{items}</ul>'
        "</div>"
    )


def _render_link_item(
    item: str,
    url_channel_map: dict[str, str],
    url_bucket_map: dict[str, str] | None = None,
    url_article_map: dict[str, dict[str, Any]] | None = None,
) -> str:
    cleaned = item.strip()
    if not cleaned or cleaned in PLACEHOLDER_LINK_TEXT:
        return ""

    matched = re.match(r"^(.+?)\s*\|\s*([^|]+?)\s*\|\s*(https?://\S+)$", cleaned)
    if matched:
        title = _html.escape(matched.group(1).strip())
        published_at = matched.group(2).strip()
        url = matched.group(3).strip()
        display_title = f"{title}（{_html.escape(published_at)}）" if published_at not in PLACEHOLDER_LINK_TEXT else title
    else:
        matched = re.match(r"^(.+?)\s*\|\s*(https?://\S+)$", cleaned)
        if not matched:
            return f"<li>{_html.escape(cleaned)}</li>"
        title = _html.escape(matched.group(1).strip())
        url = matched.group(2).strip()
        display_title = title

    channel = str(url_channel_map.get(url, "")).strip()
    if url_bucket_map and url_bucket_map.get(url) == BUCKET_TOPIC:
        return ""
    if url_bucket_map and url_bucket_map.get(url) == BUCKET_ACTIVITY:
        article = (url_article_map or {}).get(url, {})
        metadata = article.get("metadata") if isinstance(article, dict) else {}
        start_label = str((metadata or {}).get("start_label") or "").strip()
        if start_label:
            display_title = f"{title} | {_html.escape(start_label)}"
        else:
            display_title = title
    channel_badge = ""
    if channel and channel not in {BUCKET_TOPIC, BUCKET_ACTIVITY, BUCKET_INFO}:
        channel_badge = f'<span class="link-badge">{_html.escape(channel)}</span>'
    return (
        "<li>"
        f'{channel_badge}<a href="{_html.escape(url, quote=True)}" target="_blank" rel="noopener">{display_title}</a>'
        "</li>"
    )


def _render_section(
    section: dict[str, Any],
    url_channel_map: dict[str, str],
    *,
    skip_link_blocks: bool = False,
    url_bucket_map: dict[str, str] | None = None,
    url_article_map: dict[str, dict[str, Any]] | None = None,
) -> str:
    title = str(section.get("title") or "")
    subsection_map = section.get("subsections", {})
    if not title or not isinstance(subsection_map, dict):
        return ""

    badges = _collect_section_channel_labels(section, url_channel_map)
    badge_html = "".join(f'<span class="section-badge">{_html.escape(label)}</span>' for label in badges)
    badge_row = f'<div class="section-badges">{badge_html}</div>' if badge_html else ""

    blocks: list[str] = []
    for subtitle, items in subsection_map.items():
        if not isinstance(items, list) or not items:
            continue
        if subtitle in {"源地址", "补充地址"}:
            if skip_link_blocks:
                continue
            list_items = "".join(
                _render_link_item(str(item), url_channel_map, url_bucket_map, url_article_map)
                for item in items
            )
            if not list_items:
                continue
            body = f'<ul class="link-list">{list_items}</ul>'
        elif subtitle in {"增量信息", "需要继续跟踪的点"}:
            list_items = "".join(f"<li>{_html.escape(str(item))}</li>" for item in items if str(item).strip())
            if not list_items:
                continue
            body = f"<ul>{list_items}</ul>"
        else:
            text = " ".join(str(item).strip() for item in items if str(item).strip())
            if not text:
                continue
            block_class = "block-text indented-text" if subtitle in {"核心判断", "产业/公司影响", "产品/公司影响"} else "block-text"
            body = f'<p class="{block_class}">{_html.escape(text)}</p>'

        blocks.append(
            '<div class="block">'
            f'<h4 class="block-title">{_html.escape(str(subtitle))}</h4>'
            f"{body}"
            "</div>"
        )

    if not blocks:
        return ""

    title_line = _title_trailing_colon(title)
    return (
        f'<section class="analysis-card" id="{_anchor(title)}">'
        f'<div class="analysis-head"><h3 class="analysis-title">{_html.escape(title_line)}</h3>{badge_row}</div>'
        f"{''.join(blocks)}"
        "</section>"
    )


def _render_section_group(
    *,
    section_id: str,
    title: str,
    count_label: str,
    intro: str,
    body_html: str,
    extra_html: str = "",
    heading_trailing_colon: bool = True,
) -> str:
    intro_stripped = str(intro or "").strip()
    intro_html = (
        f'<p class="section-intro">{_html.escape(intro_stripped)}</p>' if intro_stripped else ""
    )
    colon_html = (
        '<span class="section-title-colon" aria-hidden="true">：</span>' if heading_trailing_colon else ""
    )
    count_stripped = str(count_label or "").strip()
    count_html = (
        f'<span class="section-count">{_html.escape(count_stripped)}</span>' if count_stripped else ""
    )
    return (
        f'<section class="home-section" id="{section_id}">'
        '<div class="section-header">'
        f'<h2 class="section-title section-title-kr">'
        f'<span class="section-title-label">{_html.escape(title)}</span>'
        f"{colon_html}"
        f"{count_html}"
        "</h2>"
        "</div>"
        f"{intro_html}"
        f"{extra_html}"
        f"{body_html}"
        "</section>"
    )


def render_kr36_brief_email(
    markdown_text: str,
    step6_path: Path | None = None,
    *,
    omit_source_links: bool = False,
) -> RenderedEmail:
    """Render the 36Kr brief into fixed 3-column text-first email template.

    When ``omit_source_links`` is True, do not render 源 address lists or use hot_topics
    fallback for links; 资讯 still shows each ``##`` 主题 via ``_build_bucket_points``.
    """

    title, summary_items, topic_sections = _parse_brief_markdown(markdown_text)
    lead_text, section_intros, _run_stats = _partition_summary_items(summary_items)

    raw_articles = _load_articles(step6_path) if step6_path else []
    url_bucket_map = _build_url_bucket_map(raw_articles)
    url_article_map = _build_url_article_map(raw_articles)
    article_viewpoint_map = _load_article_viewpoint_map(step6_path)
    topic_articles = _filter_articles_by_bucket(raw_articles, BUCKET_TOPIC)
    activity_articles = _filter_kr36_visible_activities(
        _filter_articles_by_bucket(raw_articles, BUCKET_ACTIVITY)
    )
    info_articles = _filter_articles_by_bucket(raw_articles, BUCKET_INFO)

    topic_analysis_sections: list[dict[str, Any]] = []
    activity_analysis_sections: list[dict[str, Any]] = []
    info_analysis_sections: list[dict[str, Any]] = []
    group_topic_bucket_map = _load_group_topic_bucket_map(step6_path)
    for section in topic_sections:
        bucket = _classify_section_bucket(
            section, url_bucket_map, group_topic_bucket_map=group_topic_bucket_map
        )
        if bucket == BUCKET_TOPIC:
            topic_analysis_sections.append(section)
        elif bucket == BUCKET_ACTIVITY:
            activity_analysis_sections.append(section)
        else:
            info_analysis_sections.append(section)

    if omit_source_links:
        topic_sources = []
        info_sources = []
    else:
        topic_sources = _collect_bucket_sources(
            bucket_title=BUCKET_TOPIC,
            sections=topic_analysis_sections,
            fallback_articles=topic_articles,
            url_bucket_map=url_bucket_map,
            url_article_map=url_article_map,
        )
        activity_sources = _collect_bucket_sources(
            bucket_title=BUCKET_ACTIVITY,
            sections=activity_analysis_sections,
            fallback_articles=activity_articles,
            url_bucket_map=url_bucket_map,
            url_article_map=url_article_map,
        )
        info_sources = _collect_bucket_sources(
            bucket_title=BUCKET_INFO,
            sections=info_analysis_sections,
            fallback_articles=info_articles,
            url_bucket_map=url_bucket_map,
            url_article_map=url_article_map,
        )

    section_url_topic = _build_section_url_viewpoint_map(topic_analysis_sections)
    section_url_activity = _build_section_url_viewpoint_map(activity_analysis_sections)
    section_url_info = _build_section_url_viewpoint_map(info_analysis_sections)

    per_item_by_bucket: dict[str, list[tuple[str, str]]] | None = None
    if omit_source_links:
        per_item_by_bucket = _load_step5_per_item_points_by_bucket(
            step6_path,
            url_article_map=url_article_map,
            max_activity_subitems=KR36_ACTIVITY_VISIBLE_MAX,
        )
        if per_item_by_bucket is not None:
            topic_points = per_item_by_bucket.get(BUCKET_TOPIC) or _build_bucket_points(
                sections=topic_analysis_sections, fallback_articles=[]
            )
            activity_points = per_item_by_bucket.get(BUCKET_ACTIVITY) or _build_bucket_points(
                sections=activity_analysis_sections, fallback_articles=[]
            )
            info_points = per_item_by_bucket.get(BUCKET_INFO) or _build_bucket_points(
                sections=info_analysis_sections, fallback_articles=[]
            )
        else:
            topic_points = _build_bucket_points(sections=topic_analysis_sections, fallback_articles=[])
            activity_points = _build_bucket_points(sections=activity_analysis_sections, fallback_articles=[])
            info_points = _build_bucket_points(sections=info_analysis_sections, fallback_articles=[])
    else:
        topic_points = _build_bucket_points_by_source(
            sources=topic_sources,
            article_viewpoint_map=article_viewpoint_map,
            section_url_viewpoint_map=section_url_topic,
        )
        activity_points = _build_bucket_points_by_source(
            sources=activity_sources,
            article_viewpoint_map=article_viewpoint_map,
            section_url_viewpoint_map=section_url_activity,
        )
        info_points = _build_bucket_points_by_source(
            sources=info_sources,
            article_viewpoint_map=article_viewpoint_map,
            section_url_viewpoint_map=section_url_info,
        )

    activity_omit_enriched: list[dict[str, str]] | None = None
    if omit_source_links and step6_path:
        activity_omit_enriched = _load_step5_activity_omit_enriched(
            step6_path, url_article_map
        )
    if activity_omit_enriched is not None:
        activity_points = []
    if omit_source_links:
        if activity_omit_enriched is not None:
            activity_sources = list(activity_omit_enriched)
        else:
            activity_sources = []

    activity_sources_synth = [
        _build_activity_source_entry(
            title=str(a.get("title") or "待补充"),
            published_at=str(a.get("published_at") or "").strip(),
            url=str(a.get("url") or "").strip(),
            article=a,
        )
        for a in activity_articles
    ]

    topic_rollup = _synthesize_step5_topic_rollup_from_all_topic_items(step6_path) if step6_path else ""
    if (topic_rollup or "").strip():
        tr = (topic_rollup or "").strip()
        if omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_TOPIC):
            topic_summary = tr
        else:
            intro_t = (section_intros.get("topic", "") or "").strip()
            topic_summary = _normalize_sentence(f"{intro_t} {tr}") if intro_t else tr
    elif omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_TOPIC):
        topic_synth = _build_bucket_summary(
            bucket_title=BUCKET_TOPIC,
            sections=topic_analysis_sections,
            intro_text="",
            raw_article_count=len(topic_articles),
        )
        topic_summary = (topic_synth or "").strip() or "今日专题暂无可用内容，待补充。"
    else:
        topic_summary = _build_bucket_summary(
            bucket_title=BUCKET_TOPIC,
            sections=topic_analysis_sections,
            intro_text=section_intros.get("topic", ""),
            raw_article_count=len(topic_articles),
        )
    topic_summary = _section_blurb_cap(topic_summary)

    if omit_source_links and activity_omit_enriched is not None:
        act_synth = _build_bucket_summary(
            bucket_title=BUCKET_ACTIVITY,
            sections=activity_analysis_sections,
            intro_text="",
            raw_article_count=len(activity_articles),
            activity_sources=activity_sources_synth,
        )
        activity_summary = (act_synth or "").strip() or "今日活动暂无可读场次，待补充。"
    elif omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_ACTIVITY):
        activity_summary = _build_bucket_summary(
            bucket_title=BUCKET_ACTIVITY,
            sections=activity_analysis_sections,
            intro_text=section_intros.get("activity", ""),
            raw_article_count=len(activity_articles),
            activity_sources=activity_sources_synth,
        )
    else:
        activity_summary = _build_bucket_summary(
            bucket_title=BUCKET_ACTIVITY,
            sections=activity_analysis_sections,
            intro_text=section_intros.get("activity", ""),
            raw_article_count=len(activity_articles),
            activity_sources=activity_sources,
        )

    info_rollup = _synthesize_step5_info_rollup_from_all_info_items(step6_path) if step6_path else ""
    if (info_rollup or "").strip():
        ir = (info_rollup or "").strip()
        if omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_INFO):
            info_summary = ir
        else:
            intro_i = (section_intros.get("info", "") or "").strip()
            info_summary = _normalize_sentence(f"{intro_i} {ir}") if intro_i else ir
    elif omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_INFO):
        info_summary = _build_omit_per_item_list_summary(
            intro_text=section_intros.get("info", ""),
            bucket_title=BUCKET_INFO,
            n_subitems=len(info_points),
        )
    else:
        info_summary = _build_bucket_summary(
            bucket_title=BUCKET_INFO,
            sections=info_analysis_sections,
            intro_text=section_intros.get("info", ""),
            raw_article_count=len(info_articles),
        )
    info_summary = _section_blurb_cap(info_summary)

    bucket_blocks: list[dict[str, object]] = [
        {
            "title": BUCKET_TOPIC,
            "summary": topic_summary,
            "points": topic_points,
            "sources": topic_sources,
        },
        {
            "title": BUCKET_ACTIVITY,
            "summary": activity_summary,
            "points": activity_points,
            "sources": activity_sources,
        },
        {
            "title": BUCKET_INFO,
            "summary": info_summary,
            "points": info_points,
            "sources": info_sources,
        },
    ]

    lead_html = f'<p class="lead">{_html.escape(lead_text.strip())}</p>' if lead_text.strip() else ""
    section_html = "".join(
        _render_bucket_section_html(
            title=str(block["title"]),
            summary=str(block["summary"]),
            points=block.get("points", []) if isinstance(block.get("points", []), list) else [],
            sources=block.get("sources", []) if isinstance(block.get("sources", []), list) else [],
            show_topic_activity_viewpoints=omit_source_links,
        )
        for block in bucket_blocks
    )

    html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{_html.escape(title)}</title>
    <style>
      *,*::before,*::after{{box-sizing:border-box;}}
      body{{
        margin:0;
        padding:0;
        background:#f4f6f8;
        color:#1f2d3d;
        font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",sans-serif;
      }}
      .wrapper{{max-width:960px;margin:0 auto;padding:28px 20px 40px;}}
      .header{{
        background:#ffffff;
        color:#102235;
        border-radius:18px;
        padding:28px 30px;
        box-shadow:0 10px 28px rgba(17, 34, 53, 0.06);
        border:1px solid rgba(16, 34, 53, 0.08);
      }}
      .title{{margin:0;font-size:28px;line-height:1.25;letter-spacing:-0.02em;}}
      .lead{{margin-top:10px;font-size:14px;line-height:1.8;color:#627384;}}
      .brief-section{{
        background:#ffffff;
        border-radius:18px;
        padding:24px 26px;
        margin-top:18px;
        box-shadow:0 10px 28px rgba(17, 34, 53, 0.06);
        border:1px solid rgba(16, 34, 53, 0.08);
      }}
      .section-title{{margin:0 0 12px;font-size:21px;color:#102235;letter-spacing:-0.01em;}}
      .section-summary{{margin:0 0 12px;font-size:14px;line-height:1.9;color:#314457;}}
      .viewpoints{{margin:0;padding-left:18px;}}
      .viewpoints li{{margin:8px 0;line-height:1.8;color:#314457;font-size:14px;}}
      .viewpoints-no-index{{padding-left:0;list-style:none;}}
      .point-topic{{font-weight:700;color:#2d4d69;}}
      .source-title{{margin:14px 0 8px;font-size:14px;font-weight:700;color:#2d4d69;}}
      .sources{{margin:0;padding-left:24px;}}
      .sources li{{margin:4px 0;line-height:1.8;color:#314457;font-size:14px;word-break:break-word;}}
      .sources a{{color:#0f5ea8;text-decoration:none;}}
      .sources a:hover{{text-decoration:underline;}}
      .source-label{{font-weight:700;color:#2d4d69;}}
      .activity-cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;}}
      .activity-card{{border:1px solid #e1e8ef;border-radius:12px;background:#f8fbfe;padding:12px;}}
      .activity-card-title{{margin:0 0 8px;font-size:14px;line-height:1.7;color:#102235;}}
      .activity-card-title a{{color:#0f5ea8;text-decoration:none;}}
      .activity-card-title a:hover{{text-decoration:underline;}}
      .activity-field{{margin:4px 0;font-size:13px;line-height:1.7;color:#314457;}}
      .activity-field-label{{display:inline-block;min-width:48px;font-weight:700;color:#2d4d69;}}
      .activity-card-omit .activity-field-label{{min-width:52px;}}
      .activity-card-title-omit{{margin:0 0 8px;font-size:15px;font-weight:700;color:#102235;}}
      .activity-card-empty{{display:flex;align-items:center;justify-content:center;min-height:64px;color:#666;}}
      .footer{{margin-top:24px;font-size:12px;color:#7a8897;text-align:center;}}
      @media (max-width:640px){{
        .wrapper{{padding:20px 12px 28px;}}
        .header{{padding:20px 18px;}}
        .title{{font-size:24px;}}
        .brief-section{{padding:18px;}}
      }}
    </style>
  </head>
  <body>
    <div class="wrapper">
      <header class="header">
        <h1 class="title">{_html.escape(title)}</h1>
        {lead_html}
      </header>
      {section_html}
      <div class="footer">{_html.escape(FOOTER_DISCLAIMER)}</div>
    </div>
  </body>
</html>
"""

    plain_text = _kr36_build_plain_text(
        title=title,
        lead=lead_text,
        bucket_blocks=bucket_blocks,
        omit_source_links=omit_source_links,
    )
    return RenderedEmail(subject=title, text=plain_text, html=html_body)
