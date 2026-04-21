"""36Kr brief email renderer.

This renderer keeps all content on one page instead of hiding it behind tabs.
Raw topic and activity items come from the step-1 JSON output, while step-6
theme analysis cards stay visible in dedicated homepage sections.
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

from utils.tools.output.email import RenderedEmail, build_plain_text, normalize_block_lines, split_metric_line

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

    return (
        f'<section class="analysis-card" id="{_anchor(title)}">'
        f'<div class="analysis-head"><h3 class="analysis-title">{_html.escape(title)}</h3>{badge_row}</div>'
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
) -> str:
    intro_stripped = str(intro or "").strip()
    intro_html = (
        f'<p class="section-intro">{_html.escape(intro_stripped)}</p>' if intro_stripped else ""
    )
    return (
        f'<section class="home-section" id="{section_id}">'
        '<div class="section-header">'
        f'<div><h2 class="section-title">{_html.escape(title)}</h2>{intro_html}</div>'
        f'<span class="section-count">{_html.escape(count_label)}</span>'
        "</div>"
        f"{extra_html}"
        f"{body_html}"
        "</section>"
    )


def render_kr36_brief_email(markdown_text: str, step6_path: Path | None = None) -> RenderedEmail:
    """Render the 36Kr brief into a homepage-style HTML email."""

    title, summary_items, topic_sections = _parse_brief_markdown(markdown_text)

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
            f'<a class="jump-chip" href="#section-topics">专题精选 ({len(topic_articles)})</a>'
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
                title="专题精选",
                count_label=f"{len(topic_articles)} 条",
                intro="",
                body_html=analysis_block + topic_links,
            )
        )

    if activity_articles or activity_analysis_sections:
        anchor_links.append(
            f'<a class="jump-chip" href="#section-activities">活动速览 ({len(activity_articles)})</a>'
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
                title="活动速览",
                count_label=f"{len(activity_articles)} 条",
                intro="",
                body_html=analysis_block + activity_source_block,
            )
        )

    if info_analysis_sections:
        anchor_links.append(
            f'<a class="jump-chip" href="#section-analysis">资讯主题分析 ({len(info_analysis_sections)})</a>'
        )
        info_nav = ""
        if present_info_channels:
            chips = "".join(f'<span class="info-chip">{_html.escape(channel)}</span>' for channel in present_info_channels)
            info_nav = f'<div class="info-chip-row">{chips}</div>'
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
                title="资讯主题分析",
                count_label=f"{len(info_analysis_sections)} 个主题",
                intro="",
                body_html=info_source_groups + f'<div class="analysis-stack">{info_analysis_html}</div>',
                extra_html=info_nav,
            )
        )

    if not home_sections:
        home_sections.append(
            _render_section_group(
                section_id="section-empty",
                title="暂无内容",
                count_label="0",
                intro="",
                body_html='<p class="empty-tip">请检查上游抓取结果或 step 6 产物。</p>',
            )
        )

    # Runtime counters stay in logs/markdown; the HTML report starts with content navigation.
    summary_html = ""
    stats_block = f'<div class="stats">{summary_html}</div>' if summary_html else ""
    jump_bar = f'<div class="jump-bar">{"".join(anchor_links)}</div>' if anchor_links else ""

    html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{_html.escape(title)}</title>
    <style>
      *,*::before,*::after{{box-sizing:border-box}}
      body{{margin:0;padding:0;background:#eef2f6;color:#102235;
        font-family:"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;}}
      .wrapper{{max-width:1080px;margin:0 auto;padding:24px 16px 40px;}}
      .hero{{background:linear-gradient(180deg,#ffffff 0%,#f7fafc 100%);
        border:1px solid rgba(16,34,53,.08);border-radius:22px;padding:28px 30px;
        box-shadow:0 18px 42px rgba(17,34,53,.08);}}
      .hero-title{{margin:0;font-size:26px;line-height:1.25;letter-spacing:-.02em;}}
      .hero-divider{{width:64px;height:4px;border-radius:999px;margin-top:14px;
        background:linear-gradient(90deg,#0f5ea8 0%,#79a9cf 100%);}}
      .stats{{margin-top:14px;font-size:13px;line-height:1.9;color:#506172;}}
      .stat-item{{display:inline-block;margin-right:18px;white-space:nowrap;}}
      .jump-bar{{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px;}}
      .jump-chip{{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;
        background:#ffffff;border:1px solid #cfe0ee;color:#0f5ea8;font-size:13px;font-weight:600;
        text-decoration:none;box-shadow:0 4px 12px rgba(15,94,168,.08);}}
      .home-section{{margin-top:20px;background:#ffffff;border-radius:22px;padding:24px 26px;
        border:1px solid rgba(16,34,53,.08);box-shadow:0 18px 42px rgba(17,34,53,.06);}}
      .section-header{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;}}
      .section-title{{margin:0;font-size:22px;letter-spacing:-.02em;}}
      .section-intro{{margin:8px 0 0;color:#5b6b7b;font-size:14px;line-height:1.7;}}
      .section-count{{display:inline-flex;align-items:center;justify-content:center;min-width:84px;
        padding:8px 12px;border-radius:999px;background:#e9f2fb;color:#0f5ea8;font-size:13px;font-weight:700;}}
      .topic-source-list{{margin-top:18px;}}
      .topic-source-block{{margin-top:18px;padding:16px 18px;border-radius:16px;
        background:#f8fbfd;border:1px solid #e4edf4;border-left:4px solid #79a9cf;}}
      .topic-source-block .topic-source-list{{margin-top:0;}}
      .topic-source-item{{background:#fbfdff;}}
      .activity-source-item{{align-items:flex-start;flex-wrap:wrap;gap:8px 10px;}}
      .activity-source-badges{{display:inline-flex;flex-wrap:wrap;gap:8px;align-items:center;
        margin-top:4px;width:100%;}}
      .activity-source-list .activity-status{{padding:4px 10px;border-radius:999px;background:#eef5fb;color:#0f5ea8;
        font-size:11px;font-weight:700;white-space:nowrap;}}
      .activity-countdown{{font-size:12px;color:#0f5ea8;font-weight:700;white-space:nowrap;}}
      .info-chip-row{{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px;}}
      .info-chip,.section-badge,.link-badge{{display:inline-flex;align-items:center;padding:5px 10px;
        border-radius:999px;background:#f3f7fa;color:#476077;font-size:12px;font-weight:600;}}
      .info-channel-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:18px;}}
      .info-channel-card{{padding:16px 18px;border:1px solid #e4edf4;border-radius:16px;background:#fbfdff;}}
      .info-channel-title{{margin:0 0 10px;font-size:15px;color:#102235;}}
      .info-source-list{{display:grid;gap:4px;}}
      .info-source-item{{display:block!important;}}
      .info-source-date{{display:inline-block;margin-left:8px;color:#7b8a98;font-size:12px;}}
      .analysis-stack{{display:grid;gap:16px;margin-top:18px;}}
      .analysis-card{{border:1px solid #dbe8f2;border-radius:18px;padding:22px 24px;background:#ffffff;
        box-shadow:0 8px 24px rgba(17,34,53,.04);}}
      .analysis-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;}}
      .analysis-title{{margin:0;font-size:20px;line-height:1.35;}}
      .section-badges{{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;}}
      .block{{margin-top:16px;padding-top:16px;border-top:1px solid #edf2f6;}}
      .block:first-of-type{{margin-top:0;padding-top:0;border-top:0;}}
      .block-title{{margin:0 0 10px;font-size:13px;color:#33516c;letter-spacing:.01em;font-weight:800;}}
      .block-text{{margin:0;color:#314457;font-size:14px;line-height:1.9;}}
      .indented-text{{text-indent:2em;}}
      ul{{margin:0;padding-left:18px;}}
      li{{margin:6px 0;color:#314457;font-size:14px;line-height:1.8;}}
      .link-list{{padding-left:0;list-style:none;}}
      .link-list li{{display:flex;align-items:flex-start;gap:8px;}}
      .link-list a{{color:#0f5ea8;text-decoration:none;line-height:1.7;}}
      .empty-tip{{margin:18px 0 0;color:#738394;font-size:14px;line-height:1.8;}}
      .footer{{margin-top:28px;text-align:center;color:#738394;font-size:12px;}}
      @media (max-width: 720px) {{
        .wrapper{{padding:16px 12px 28px;}}
        .hero{{padding:22px 20px;}}
        .home-section{{padding:20px 18px;}}
        .section-header,.analysis-head{{flex-direction:column;align-items:flex-start;}}
        .section-badges{{justify-content:flex-start;}}
      }}
    </style>
  </head>
  <body>
    <div class="wrapper">
      <section class="hero">
        <h1 class="hero-title">{_html.escape(title)}</h1>
        <div class="hero-divider"></div>
        {stats_block}
        {jump_bar}
      </section>
      {''.join(home_sections)}
      <div class="footer">{_html.escape(FOOTER_DISCLAIMER)}</div>
    </div>
  </body>
</html>
"""

    plain_text = build_plain_text(
        title=title,
        role_line="",
        summary_items=summary_items,
        topics=topic_sections,
        footer_disclaimer=FOOTER_DISCLAIMER,
    )
    return RenderedEmail(subject=title, text=plain_text, html=html_body)
