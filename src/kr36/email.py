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

from utils.tools.output.email import (
    RenderedEmail,
    format_plain_text_item,
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
    run_stats: list[tuple[str, str]],
    topics: list[dict[str, object]],
) -> str:
    """纯文本版：标题 + 详情总结 + 运行数据行 + 各主题块（与 HTML 信息层级一致）。"""

    lines: list[str] = [title]
    if str(lead or "").strip():
        lines.extend(["", str(lead).strip()])
    for sk, sv in run_stats:
        if sk or sv:
            lines.append(f"- {sk}：{sv}" if sk else f"- {sv}")
    for topic in topics:
        topic_title = str(topic["title"])
        lines.extend(["", topic_title])
        subsections = topic.get("subsections", {})
        assert isinstance(subsections, dict)
        for subtitle, items in subsections.items():
            lines.append(f"{subtitle}")
            for item in items:
                rendered = format_plain_text_item(str(item))
                if rendered:
                    lines.append(rendered)
    body = "\n".join(lines).strip()
    if FOOTER_DISCLAIMER:
        body = f"{body}\n\n{FOOTER_DISCLAIMER}"
    return body


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


def _normalize_bucket(article: dict[str, Any]) -> str:
    bucket = str(article.get("source_bucket") or article.get("channel") or "").strip()
    if bucket == BUCKET_TOPIC:
        return BUCKET_TOPIC
    if bucket == BUCKET_ACTIVITY:
        return BUCKET_ACTIVITY
    return BUCKET_INFO


def _filter_articles_by_bucket(articles: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    return [article for article in articles if _normalize_bucket(article) == bucket]


def _build_url_bucket_map(articles: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for article in articles:
        url = str(article.get("url") or "").strip()
        if url:
            mapping[url] = _normalize_bucket(article)
    return mapping


def _build_url_article_map(articles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for article in articles:
        url = str(article.get("url") or "").strip()
        if url:
            mapping[url] = article
    return mapping


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


def _classify_section_bucket(section: dict[str, Any], url_bucket_map: dict[str, str]) -> str:
    counts: Counter[str] = Counter()
    for url in _extract_section_urls(section):
        bucket = url_bucket_map.get(url, BUCKET_INFO)
        counts[bucket] += 1
    if not counts:
        return BUCKET_INFO
    if counts[BUCKET_INFO] > 0:
        return BUCKET_INFO
    if counts[BUCKET_ACTIVITY] > 0:
        return BUCKET_ACTIVITY
    return BUCKET_TOPIC


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


def render_kr36_brief_email(markdown_text: str, step6_path: Path | None = None) -> RenderedEmail:
    """Render the 36Kr brief into a homepage-style HTML email."""

    title, summary_items, topic_sections = _parse_brief_markdown(markdown_text)
    lead_text, section_intros, run_stats = _partition_summary_items(summary_items)

    raw_articles = _load_articles(step6_path) if step6_path else []
    url_channel_map = _load_url_channel_map(step6_path) if step6_path else {}
    url_bucket_map = _build_url_bucket_map(raw_articles)
    url_article_map = _build_url_article_map(raw_articles)

    topic_articles = _filter_articles_by_bucket(raw_articles, BUCKET_TOPIC)
    activity_articles = _filter_articles_by_bucket(raw_articles, BUCKET_ACTIVITY)

    topic_analysis_sections: list[dict[str, Any]] = []
    activity_analysis_sections: list[dict[str, Any]] = []
    info_analysis_sections: list[dict[str, Any]] = []
    for section in topic_sections:
        bucket = _classify_section_bucket(section, url_bucket_map)
        if bucket == BUCKET_TOPIC:
            topic_analysis_sections.append(section)
        elif bucket == BUCKET_ACTIVITY:
            activity_analysis_sections.append(section)
        else:
            info_analysis_sections.append(section)

    present_info_channels = _collect_present_info_channels(info_analysis_sections, url_channel_map)

    anchor_links: list[str] = []
    home_sections: list[str] = []

    if topic_articles or topic_analysis_sections:
        anchor_links.append(
            f'<a class="jump-chip" href="#section-topics">专题（{len(topic_articles)} 条）</a>'
        )
        topic_links = _render_topic_source_block(topic_articles)
        topic_analysis_html = "".join(
            _render_section(section, url_channel_map, skip_link_blocks=True) for section in topic_analysis_sections
        )
        analysis_block = (
            '<div class="analysis-stack">'
            f"{topic_analysis_html}"
            "</div>"
            if topic_analysis_html
            else ""
        )
        home_sections.append(
            _render_section_group(
                section_id="section-topics",
                title="专题",
                count_label=f"（{len(topic_articles)} 条）",
                intro=section_intros.get("topic", ""),
                body_html=analysis_block + topic_links,
            )
        )

    if activity_articles or activity_analysis_sections:
        anchor_links.append(
            f'<a class="jump-chip" href="#section-activities">活动（{len(activity_articles)} 条）</a>'
        )
        activity_source_block = _render_activity_source_block(activity_articles)
        activity_analysis_html = "".join(
            _render_section(
                section,
                url_channel_map,
                skip_link_blocks=True,
                url_bucket_map=url_bucket_map,
                url_article_map=url_article_map,
            )
            for section in activity_analysis_sections
        )
        analysis_block = (
            '<div class="analysis-stack">'
            f"{activity_analysis_html}"
            "</div>"
            if activity_analysis_html
            else ""
        )
        home_sections.append(
            _render_section_group(
                section_id="section-activities",
                title="活动",
                count_label=f"（{len(activity_articles)} 条）",
                intro=section_intros.get("activity", ""),
                body_html=analysis_block + activity_source_block,
            )
        )

    if info_analysis_sections:
        anchor_links.append(
            f'<a class="jump-chip" href="#section-analysis">资讯（{len(info_analysis_sections)} 个主题）</a>'
        )
        info_nav = ""
        if present_info_channels:
            chips = "".join(f'<span class="info-chip">{_html.escape(channel)}</span>' for channel in present_info_channels)
            info_nav = (
                '<p class="info-subhead">具体子栏目</p>'
                f'<div class="info-chip-row">{chips}</div>'
            )
        info_source_groups = _render_info_channel_source_groups(raw_articles, url_channel_map)
        info_analysis_html = "".join(
            _render_section(
                section,
                url_channel_map,
                skip_link_blocks=True,
                url_bucket_map=url_bucket_map,
            )
            for section in info_analysis_sections
        )
        home_sections.append(
            _render_section_group(
                section_id="section-analysis",
                title="资讯",
                count_label=f"（{len(info_analysis_sections)} 个主题）",
                intro=section_intros.get("info", ""),
                body_html=info_source_groups + f'<div class="analysis-stack">{info_analysis_html}</div>',
                extra_html=info_nav,
            )
        )

    if not home_sections:
        home_sections.append(
            _render_section_group(
                section_id="section-empty",
                title="暂无内容",
                count_label="",
                intro="",
                body_html='<p class="empty-tip">请检查上游抓取结果或 step 6 产物。</p>',
                heading_trailing_colon=False,
            )
        )

    brief_lead_html = (
        f'<p class="brief-lead">{_html.escape(lead_text.strip())}</p>' if str(lead_text or "").strip() else ""
    )
    brief_stats_html = ""
    if run_stats:
        stat_lis = []
        for sk, sv in run_stats:
            if not str(sk or "").strip() and not str(sv or "").strip():
                continue
            if str(sk or "").strip():
                stat_lis.append(
                    "<li>"
                    f'<span class="brief-stat-key">{_html.escape(str(sk).strip())}</span>'
                    f'<span class="brief-stat-sep">：</span>'
                    f'<span class="brief-stat-val">{_html.escape(str(sv).strip())}</span>'
                    "</li>"
                )
            else:
                stat_lis.append(f"<li>{_html.escape(str(sv).strip())}</li>")
        if stat_lis:
            brief_stats_html = (
                '<details class="brief-run-details">'
                '<summary class="brief-run-summary">运行数据</summary>'
                '<ul class="brief-run-stats">'
                + "".join(stat_lis)
                + "</ul></details>"
            )
    jump_bar = f'<div class="jump-bar">{"".join(anchor_links)}</div>' if anchor_links else ""

    html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{_html.escape(title)}</title>
    <style>
      *,*::before,*::after{{box-sizing:border-box}}
      /* 要点概览式：白底、衬线正文、宽行距、大标题居中（与 c114 模板无共用） */
      body{{margin:0;padding:0;background:#fff;color:#000;
        font-family:"Noto Serif SC","Source Han Serif SC","SimSun","STSong",serif;
        font-size:15px;line-height:1.95;text-align:justify;}}
      /* 与 kr36_step4_brief_*_email 一致：全宽容器、1080 上限 */
      .wrapper{{max-width:1080px;margin:0 auto;padding:24px 16px 40px;}}

      .hero{{text-align:center;padding-bottom:20px;margin-bottom:28px;
        border-bottom:1px solid #000;}}
      .hero-title{{margin:0;font-size:20px;font-weight:700;line-height:1.45;letter-spacing:.04em;}}
      .brief-lead{{margin:16px 0 0;text-align:left;font-size:15px;font-weight:700;
        line-height:1.85;color:#000;}}
      .brief-run-details{{margin:10px 0 0;text-align:left;max-width:100%;}}
      .brief-run-summary{{cursor:pointer;font-size:12px;color:#333;list-style:none;}}
      .brief-run-details[open] .brief-run-summary{{margin-bottom:6px;}}
      .brief-run-stats{{list-style:none;margin:0;padding:0 0 0 12px;
        font-size:12px;color:#333;line-height:1.6;}}
      .brief-run-stats li{{margin:2px 0;}}
      .brief-stat-sep{{margin:0 0.1em;}}
      .hero-meta{{font-size:12px;color:#333;margin-top:8px;}}
      .jump-bar{{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;
        margin-top:16px;}}
      .jump-chip{{font-size:12px;color:#000;text-decoration:underline;padding:0 2px;}}
      .jump-chip:hover{{color:#333;}}

      .home-section{{margin-top:32px;}}
      .section-header{{display:block;text-align:left;margin-bottom:14px;}}
      .section-title{{margin:0;font-size:16px;font-weight:700;letter-spacing:.02em;}}
      .section-title-kr{{display:flex;flex-wrap:wrap;align-items:baseline;gap:0;}}
      .section-title-kr .section-title-colon{{font-weight:700;}}
      .section-title-kr .section-count{{font-size:13px;font-weight:400;color:#333;margin:0;}}
      .section-intro{{margin:0 0 10px;font-size:14px;color:#333;line-height:1.85;text-align:justify;}}
      .info-subhead{{margin:0 0 6px;font-size:14px;font-weight:700;text-align:left;}}

      /* 各分区下主题条目前自动编号 1）2）3）… 贴近 Word 要点体例 */
      .home-section .analysis-stack{{counter-reset:kr36-theme;}}
      .home-section .analysis-card{{counter-increment:kr36-theme;
        padding:16px 0 18px;border-bottom:1px solid #c8c8c8;}}
      .home-section .analysis-card:last-child{{border-bottom:none;}}
      .analysis-head{{display:block;margin-bottom:8px;}}
      .analysis-title{{margin:0;font-size:15px;font-weight:700;line-height:1.65;text-align:justify;}}
      .home-section .analysis-title::before{{
        content:counter(kr36-theme)"）";font-weight:700;margin-right:0.15em;}}
      .section-badges{{display:inline-flex;flex-wrap:wrap;gap:4px;margin-top:6px;}}
      .section-badge{{font-size:12px;font-weight:400;border:0;padding:0;color:#333;}}
      .section-badge::before{{content:"[";}}
      .section-badge::after{{content:"]";}}

      .block{{margin-top:10px;}}
      .block:first-of-type{{margin-top:0;}}
      .block-title{{margin:0 0 2px;font-size:14px;font-weight:700;color:#000;}}
      .block-text{{margin:0;font-size:15px;color:#000;line-height:1.95;text-align:justify;}}
      .indented-text{{text-indent:2em;}}

      ul{{margin:4px 0 6px;padding-left:1.4em;}}
      li{{margin:3px 0;font-size:15px;color:#000;line-height:1.9;text-align:justify;}}
      .link-list{{padding-left:0;list-style:none;}}
      .link-list li{{text-align:left;}}
      .link-list a,.link-list li a{{color:#000;text-decoration:underline;font-size:14px;}}
      .link-badge{{font-size:12px;color:#333;margin-right:4px;}}

      .topic-source-block{{margin-top:12px;padding-top:10px;}}
      .topic-source-block .block-title{{border-top:1px solid #999;padding-top:8px;}}
      .topic-source-list{{margin-top:4px;}}
      .topic-source-item{{padding:1px 0;}}

      .activity-source-badges{{display:inline;}}
      .activity-source-badges::before{{content:"（";}}
      .activity-source-badges::after{{content:"）";}}
      .activity-status,.activity-countdown{{font-size:12px;font-weight:400;
        color:#333;border:0;padding:0;}}

      .info-chip-row{{display:flex;flex-wrap:wrap;gap:4px 10px;
        margin:10px 0 12px;justify-content:flex-start;}}
      .info-chip{{font-size:12px;color:#000;border:0;padding:0;}}
      .info-chip::after{{content:"、";}}
      .info-chip:last-child::after{{content:"";}}
      .info-channel-grid{{
        display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
        gap:16px 20px;margin-bottom:16px;}}
      .info-channel-title{{margin:0 0 4px;font-size:14px;font-weight:700;}}
      .info-source-list{{display:flex;flex-direction:column;gap:1px;}}
      .info-source-item{{display:block!important;}}
      .info-source-date{{font-size:12px;color:#333;margin-left:4px;}}

      .stats{{font-size:12px;color:#333;margin-top:8px;}}
      .empty-tip{{color:#333;font-size:14px;}}
      .footer{{margin-top:40px;padding-top:12px;border-top:1px solid #000;
        font-size:12px;color:#333;text-align:center;}}

      @media (max-width:640px){{
        .wrapper{{padding:20px 12px 32px;}}
        .info-channel-grid{{grid-template-columns:1fr;}}
      }}
    </style>
  </head>
  <body>
    <div class="wrapper">
      <header class="hero">
        <h1 class="hero-title">{_html.escape(title)}</h1>
        {brief_lead_html}
        {brief_stats_html}
        {jump_bar}
      </header>
      {''.join(home_sections)}
      <div class="footer">{_html.escape(FOOTER_DISCLAIMER)}</div>
    </div>
  </body>
</html>
"""

    plain_text = _kr36_build_plain_text(
        title=title,
        lead=lead_text,
        run_stats=run_stats,
        topics=topic_sections,
    )
    return RenderedEmail(subject=title, text=plain_text, html=html_body)
