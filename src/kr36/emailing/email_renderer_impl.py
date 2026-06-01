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
from typing import Any, Optional, Tuple
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
    # /information/AI、/information/contact 等列表的 step1 栏目名，须与 source_adapter 一致。
    "AI",
    "创投",
)
# 简报「资讯」栏只展示以下三个独立栏目（与 step1「栏目」一致）；其余资讯子类不进简报。
KR36_BRIEF_INFO_CHANNELS: tuple[str, ...] = ("36氪独家", "AI", "创投")
KR36_BRIEF_INFO_CHANNELS_SET: frozenset[str] = frozenset(KR36_BRIEF_INFO_CHANNELS)
BUCKET_TOPIC = "专题"
BUCKET_ACTIVITY = "活动"
BUCKET_INFO = "资讯"
# 活动区默认最多展示条数；已结束场次于展示前筛除（见 _is_kr36_activity_ended）。
KR36_ACTIVITY_VISIBLE_MAX = 8
# 通用栏头截断默认（单段兜底）；专题 step5 跨 category 总述见下。
KR36_TOPIC_INFO_SECTION_BLURB_MAX_CHARS = 200
# 邮件里专题「运行摘要 + rollup」合并后再截断，须大于下方资讯 rollup 上限以容纳导言。
KR36_TOPIC_BRIEF_SUMMARY_MAX_CHARS = 720
# 资讯栏总览；专题 step5 跨 category 总述与之共用上限（勿用默认 200 字，否则栏头只够写第一节）。
KR36_INFO_SECTION_BLURB_MAX_CHARS = 520
# 资讯逐条观点（omit 源地址 HTML）：与栏头总述同量级，避免 260 字硬截断。
KR36_INFO_VIEWPOINT_MAX_CHARS = 520
FOOTER_DISCLAIMER = "本报告由系统自动生成，仅供参考，不构成投资建议。"
PLACEHOLDER_LINK_TEXT = {"无", "暂无", "日期未知"}
# step5 逐条观点里 `【子类名】` 与专题/资讯子类分组的行首标记（与 per-item 扁平标题一致）
_KR36_SUBCLASS_IN_POINT_LABEL: re.Pattern[str] = re.compile(r"^【([^】]+)】\s*")

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


def _is_compact_viewpoint_head(head: str, body: str) -> bool:
    """是否可将「head：body」视作结论总起（而非正文中的普通冒号）。

    中文结论句常含逗号（「巴菲特持币防御，美股强势难续」「顶尖AI模型…违规操作，暴露出…」），
    不应因此拦截；只有句末标点（。；！？等）才说明 head 本身已是一个完整句段。
    """
    h = str(head or "").strip()
    b = str(body or "").strip()
    if not (h and b):
        return False
    # 仅接受短结论（≤ 60 字），避免把长句段中的「如下所述：」等引导句误判为结论。
    if len(h) > 60:
        return False
    # 句末标点表明 head 本身已成完整句段，不应再作结论标题。
    # 中文逗号（，）和顿号（、）是连接词，允许出现在结论中。
    if re.search(r"[。；！？,.!?]", h):
        return False
    # 避免「1）xx：...」等编号列表被识别为结论。
    if re.match(r"^\d+\s*[）\)]", h):
        return False
    # 避免书名号内冒号（如《明末：渊虚之羽》）被截断。
    if "《" in h and "》" in b:
        return False
    # 「包括/如下/例如」通常是引导后文，不应当作结论短语。
    if re.search(r"(包括|如下|例如|如|主要有)$", h):
        return False
    return True


# 大标题行：「一、…」「十一、…」等单独成行时顶格展示，不参与 vp-content 整块缩进。
_KR36_LEADING_CN_CHAPTER_LINE: re.Pattern[str] = re.compile(
    r"^[一二三四五六七八九十百零]+、"
)

# 子类区块序号：一、二、…十、十一、…
_CN_ORD_CHARS = "一二三四五六七八九十"


def _cn_subclass_ordinal(n: int) -> str:
    """将正整数 n 转为中文序号前缀，如 1→'一、'，11→'十一、'。"""
    if 1 <= n <= 10:
        return _CN_ORD_CHARS[n - 1] + "、"
    if 11 <= n <= 19:
        return "十" + _CN_ORD_CHARS[n - 11] + "、"
    if n == 20:
        return "二十、"
    return str(n) + "、"


def _strip_leading_cn_chapter_line(viewpoint: str) -> tuple[str | None, str]:
    """若首行为中文序号大标题（一、二、…），拆出 (该行, 余下正文)；否则 (None, 原文)。"""
    t = str(viewpoint or "").strip()
    if not t:
        return None, ""
    lines = t.splitlines()
    if len(lines) < 2:
        return None, t
    first = lines[0].strip()
    if not _KR36_LEADING_CN_CHAPTER_LINE.match(first):
        return None, t
    rest = "\n".join(lines[1:]).strip()
    return first, rest


def _split_viewpoint_compact_inner(work: str) -> tuple[str | None, str]:
    """在已去掉大标题行的正文上，做「主观点/解释」或首处「：」结论拆分。"""
    t = str(work or "").strip()
    if not t:
        return None, ""
    if re.search(r"主观点\s*[：:]", t):
        for splitter in (r"\s*[；;]\s*解释\s*[：:]\s*", r"\s*解释\s*[：:]\s*"):
            parts = re.split(splitter, t, maxsplit=1)
            if len(parts) == 2:
                head = re.sub(r"^\s*主观点\s*[：:]\s*", "", parts[0].strip()).strip()
                body = parts[1].strip()
                if head and body and len(head) <= 200:
                    return head, body
    if "：" in t and "主观点" not in t and not re.match(r"^\s*主观点", t):
        i = t.find("：")
        if 0 < i <= 120:
            head = t[:i].strip()
            body = t[i + 1 :].strip()
            bad = head in (
                "提炼标题",
                "主观点",
                "内容要点",
                "解释",
            ) or head.startswith("提炼标题")
            if head and body and not bad and len(head) <= 200 and _is_compact_viewpoint_head(head, body):
                return head, body
    return None, t


def _split_viewpoint_for_point_topic_row(viewpoint: str) -> tuple[str | None, str]:
    """
    将「主观点/解释」或「结论句：展开」拆成 (结论, 展开)。

    若可拆分，用结论作为「xx 的观点」中可点链的 **xx**（替代仅文章标题），避免标题与首句重复。
    不可拆时返回 (None, 原文)。
    """
    t = str(viewpoint or "").strip()
    if not t:
        return None, ""
    _ch, tail = _strip_leading_cn_chapter_line(t)
    work = tail if _ch else t
    return _split_viewpoint_compact_inner(work)


def _strip_low_quality_body_prefix(text: str) -> str:
    """若正文描述以禁用的免责前缀（如「公开可解析信息有限，待核对：」）开头，剥去该前缀。"""
    t = text.strip()
    for prefix in _LOW_QUALITY_SUMMARY_PREFIXES:
        if t.startswith(prefix):
            # 去掉前缀及紧随的冒号/空格
            rest = t[len(prefix):].lstrip("：: \u3000")
            return rest if rest else ""
    return t


def _render_viewpoint_row_body_html(viewpoint: str) -> str:
    """列表项正文：内联紧跟 point-topic 冒号，不换行、不缩进。
    若首句已用作链上标题，只渲染冒号后描述段，避免重复。"""
    t = str(viewpoint or "").strip()
    if not t:
        return ""
    ch, tail = _strip_leading_cn_chapter_line(t)
    work = tail if ch else t
    h, rest = _split_viewpoint_compact_inner(work)
    body_src = rest if h and rest else work
    # 描述体以禁用前缀开头时，剥去该前缀，保留后续有效内容
    body_src = _strip_low_quality_body_prefix(body_src)
    if not body_src:
        return ""
    body_html = _render_inline_markdown(body_src)
    if ch:
        return (
            f'<span class="vp-chapter-inline">{_html.escape(ch)}</span>'
            f'<span class="vp-inline">{body_html}</span>'
        )
    return f'<span class="vp-inline">{body_html}</span>'


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
        _subclass = block.get("subclass_groups")
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
        if show_points_block and isinstance(_subclass, list) and _subclass and bucket_title in (
            BUCKET_TOPIC,
            BUCKET_INFO,
        ):
            for subclass, pairs in _subclass:
                if not isinstance(pairs, list):
                    continue
                sc = (str(subclass) or "").strip() or "未分类"
                lines.append(f" {sc}：")
                for i, pair in enumerate(pairs, start=1):
                    if not isinstance(pair, tuple) or len(pair) < 2:
                        continue
                    topic_title, viewpoint = str(pair[0]), str(pair[1])
                    t = topic_title.strip() or "待补充"
                    v = viewpoint.strip() or "暂无可提炼观点，待补充。"
                    u = str(pair[2]).strip() if len(pair) >= 3 else ""
                    link_suffix = f" 链接：{u}" if u else ""
                    lead, vrest = _split_viewpoint_for_point_topic_row(v)
                    label_plain = f"{lead} 的观点：{vrest}" if lead and vrest else f"{t} 的观点：{v}"
                    lines.append(f"  {i}) {label_plain}{link_suffix}")
        elif show_points_block:
            if isinstance(points, list) and points:
                for index, item in enumerate(points, start=1):
                    if not isinstance(item, tuple) or len(item) < 2:
                        continue
                    topic_title = str(item[0]).strip() or "待补充主题"
                    viewpoint = str(item[1]).strip() or "暂无可提炼观点，待补充。"
                    u = str(item[2]).strip() if len(item) >= 3 else ""
                    link_suffix = f" 链接：{u}" if u else ""
                    lead, vrest = _split_viewpoint_for_point_topic_row(viewpoint)
                    label_plain = (
                        f"{lead} 的观点：{vrest}"
                        if lead and vrest
                        else f"{topic_title} 的观点：{viewpoint}"
                    )
                    lines.append(f" {index}) {label_plain}{link_suffix}")
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
                            f"状态：{str(source.get('status') or '待补充')}；"
                            f"倒计时：{str(source.get('countdown') or '待补充')}；"
                            f"链接：{str(source.get('url') or '待补充')}。"
                        )
                    else:
                        lines.append(
                            " "
                            f"{index}) 名称：{str(source.get('title') or '待补充')}；"
                            f"时间：{str(source.get('time') or '待补充')}；"
                            f"地点：{str(source.get('city') or '待补充')}；"
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
        elif bucket_title == BUCKET_ACTIVITY:
            lines.append(" 暂无活动")
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


def _dedup_punctuation(text: str) -> str:
    """消除连续重复或互相矛盾的标点，如 。； → 。，；。 → 。，。。 → 。，；； → ；。"""
    t = str(text or "")
    # 句末符号后紧跟分隔符 → 保留句末符号，丢掉分隔符
    t = re.sub(r"([。！？!?])[；;，,、]+", r"\1", t)
    # 分隔符后紧跟句末符号 → 保留句末符号，丢掉分隔符
    t = re.sub(r"[；;，,、]+([。！？!?])", r"\1", t)
    # 连续相同句末符号 → 保留一个
    t = re.sub(r"([。！？!?])\1+", r"\1", t)
    # 连续分号 → 一个
    t = re.sub(r"[；;]{2,}", "；", t)
    # 连续逗号/顿号 → 一个
    t = re.sub(r"[，,、]{2,}", "，", t)
    return t


def _normalize_sentence(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    normalized = _dedup_punctuation(normalized)
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


def _clip_readable_text(text: str, max_chars: int, *, min_chars: int = 60) -> str:
    """
    可读截断：优先按完整句收束；若首句过长，则按最近分句符收束并补句号。
    目的：避免“……”“半句断裂”等阅读不友好的摘要输出。
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return ""
    if len(t) <= max_chars:
        return t

    # 先按句号/问号/感叹号切句，尽量保留完整句。
    sentence_parts = re.findall(r"[^。！？!?]+[。！？!?]?", t)
    merged = ""
    for part in sentence_parts:
        p = part.strip()
        if not p:
            continue
        candidate = f"{merged}{p}"
        if len(candidate) <= max_chars:
            merged = candidate
            continue
        break
    if merged and len(merged) >= min(min_chars, max_chars):
        if merged[-1] not in "。！？!?":
            merged = merged.rstrip("，,；;：:") + "。"
        return merged

    # 首句过长：按最近分句符（逗号/分号/顿号）收束，避免半截词。
    head = t[:max_chars]
    cut = max(head.rfind("，"), head.rfind(","), head.rfind("；"), head.rfind(";"), head.rfind("、"))
    if cut >= max(16, min_chars // 2):
        head = head[:cut]
    head = head.rstrip("，,；;：:。.!?！？ ").strip()
    return f"{head}。"


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
) -> list[tuple[str, str, str]]:
    points: list[tuple[str, str, str]] = []
    for section in sections:
        topic_title = str(section.get("title") or "").strip() or "待补充主题"
        viewpoint = _trim_text(_extract_section_viewpoint(section), 180)
        if not viewpoint:
            continue
        sec_urls = _extract_section_urls(section)
        link = sec_urls[0] if sec_urls else ""
        points.append((topic_title, viewpoint, link))
    if points:
        return points
    for article in fallback_articles[:5]:
        title = str(article.get("title") or "").strip()
        u = str(article.get("url") or "").strip()
        if title:
            points.append((title, "该条目为原始抓取信息，正文分析待补充。", u))
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
) -> list[tuple[str, str, str]]:
    points: list[tuple[str, str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or "").strip() or "待补充主题"
        raw_url = str(source.get("url") or "").strip()
        url = _normalize_url_for_match(raw_url) or raw_url
        viewpoint = ""
        if url:
            viewpoint = str(article_viewpoint_map.get(url) or "").strip()
            if not viewpoint:
                viewpoint = str(section_url_viewpoint_map.get(url) or "").strip()
        if not viewpoint:
            viewpoint = "该条目正文分析待补充。"
        display_url = raw_url if (raw_url.startswith("http://") or raw_url.startswith("https://")) else url
        points.append(
            (title, _trim_text(_normalize_sentence(viewpoint), 180), display_url)
        )
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
        "summary": str((article or {}).get("summary") or ""),
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


def _html_point_topic_label(topic: str, url: str, *, viewpoint: str = "") -> str:
    """可点链文 + 全角冒号。

    专题与资讯一致：``viewpoint``（step5 摘要）能稳健拆成「观点：解释」时，**链文用观点句**
    （冒号前结论，对应提示词 25～40 字规范），**不用** ``topic`` 里的文章标题充当观点；
    无法拆分时才用 ``topic`` 作链文。``href`` 始终为原文 ``url``。
    """
    t = (topic or "").strip() or "待补充"
    u = (url or "").strip()
    h, vbody = _split_viewpoint_for_point_topic_row(viewpoint)
    link_text = h if h and vbody else t
    if u.startswith("http://") or u.startswith("https://"):
        return (
            '<span class="point-topic">'
            f'<a class="point-topic-link" href="{_html.escape(u, quote=True)}" '
            'target="_blank" rel="noopener">'
            f"{_html.escape(link_text)}</a>："
            "</span>"
        )
    return f'<span class="point-topic">{_html.escape(link_text)}：</span>'


def _html_point_topic_label_for_row(label: str, url: str, *, viewpoint: str = "") -> str:
    """
    扁平行：``【子类】文章标题`` 时前缀不链；链文优先用摘要拆出的观点句，否则用标题段（``rest``）。
    """
    u = (url or "").strip()
    lab = (label or "").strip() or "待补充"
    h, vbody = _split_viewpoint_for_point_topic_row(viewpoint)
    link_text = h if h and vbody else None
    if u.startswith("http://") or u.startswith("https://"):
        mo = _KR36_SUBCLASS_IN_POINT_LABEL.match(lab)
        if mo:
            g = (mo.group(1) or "").strip() or "未分类"
            rest = _KR36_SUBCLASS_IN_POINT_LABEL.sub("", lab, count=1).strip() or "待补充"
            display = link_text or rest
            return (
                '<span class="point-topic">'
                f"【{_html.escape(g)}】"
                f'<a class="point-topic-link" href="{_html.escape(u, quote=True)}" '
                'target="_blank" rel="noopener">'
                f"{_html.escape(display)}</a>："
                "</span>"
            )
        return _html_point_topic_label(lab, u, viewpoint=viewpoint)
    if link_text and vbody and not (u.startswith("http://") or u.startswith("https://")):
        mo2 = _KR36_SUBCLASS_IN_POINT_LABEL.match(lab)
        if mo2:
            g2 = (mo2.group(1) or "").strip() or "未分类"
            return (
                f'<span class="point-topic">【{_html.escape(g2)}】'
                f"{_html.escape(link_text)}：</span>"
            )
        return f'<span class="point-topic">{_html.escape(link_text)}：</span>'
    return _html_point_topic_label(lab, u, viewpoint=viewpoint)


def _html_escape_section_summary(text: str) -> str:
    """栏头总结：将换行渲染为 <br>，供专题一句 + 领域归纳 等多句拼接使用。"""
    t = (text or "").strip()
    if not t:
        return '<p class="section-summary"></p>'

    def _clean_summary_sentence(s: str) -> str:
        """移除句中包含禁用低质量前缀或片段的「、」分隔短句。"""
        parts = s.split("、")
        clean = [
            p for p in parts
            if not any(p.strip().startswith(pfx) for pfx in _LOW_QUALITY_SUMMARY_PREFIXES)
            and not any(frag in p for frag in _LOW_QUALITY_SUMMARY_FRAGMENTS)
        ]
        return "、".join(clean) if clean else ""

    if "\n" in t:
        lines = [_clean_summary_sentence(line.strip()) for line in t.splitlines()]
        lines = [ln for ln in lines if ln]
        body = "<br>".join(_html.escape(ln) for ln in lines)
        return f'<p class="section-summary">{body}</p>' if body else '<p class="section-summary"></p>'
    cleaned = _clean_summary_sentence(t)
    if not cleaned:
        return '<p class="section-summary"></p>'
    return f'<p class="section-summary">{_html.escape(cleaned)}</p>'


def _render_subclass_grouped_viewpoints_html(
    groups: list[tuple[str, list[tuple[str, str, str]]]],
) -> str:
    """专题/资讯：按子类分块；链文优先观点句，与专题同款。"""
    blocks: list[str] = []
    ordinal_idx = 0
    for subclass, pairs in groups:
        sc = (subclass or "").strip() or "未分类"
        title_line = _title_trailing_colon(sc)
        li_parts: list[str] = []
        for row in pairs:
            if len(row) >= 3:
                topic, viewpoint, u = str(row[0]), str(row[1]), str(row[2] or "").strip()
            else:
                topic, viewpoint, u = str(row[0]), str(row[1]), ""
            # 跳过低质量条目（前缀 startswith 或正文 contains 禁用片段）
            _vp_check = str(viewpoint or "").strip()
            _tp_check = str(topic or "").strip()
            if any(_vp_check.startswith(p) for p in _LOW_QUALITY_SUMMARY_PREFIXES):
                continue
            if any(_tp_check.startswith(p) for p in _LOW_QUALITY_SUMMARY_PREFIXES):
                continue
            if any(f in _vp_check for f in _LOW_QUALITY_SUMMARY_FRAGMENTS):
                continue
            if any(f in _tp_check for f in _LOW_QUALITY_SUMMARY_FRAGMENTS):
                continue
            li_parts.append(
                "<li>"
                f"{_html_point_topic_label(topic, u, viewpoint=viewpoint)}"
                f"{_render_viewpoint_row_body_html(viewpoint)}"
                "</li>"
            )
        items_html = "".join(li_parts)
        if not items_html:
            continue
        ordinal_idx += 1
        ordinal = _cn_subclass_ordinal(ordinal_idx)
        blocks.append(
            '<div class="subclass-block">'
            f'<div class="subclass-heading">{_html.escape(ordinal + title_line)}</div>'
            f'<ol class="viewpoints-list">{items_html}</ol>'
            "</div>"
        )
    if not blocks:
        return ""
    return f'<div class="section-groups">{"".join(blocks)}</div>'


def _render_bucket_section_html(
    *,
    title: str,
    summary: str,
    points: list[tuple[str, str, str]],
    sources: list[dict[str, str]],
    show_topic_activity_viewpoints: bool = False,
    subclass_groups: list[tuple[str, list[tuple[str, str, str]]]] | None = None,
) -> str:
    activity_omit_detail = bool(
        title == BUCKET_ACTIVITY and show_topic_activity_viewpoints and bool(sources)
    )

    viewpoints_html = ""
    use_subclass = bool(
        subclass_groups
        and title in (BUCKET_TOPIC, BUCKET_INFO)
    )
    if not activity_omit_detail and use_subclass:
        viewpoints_html = _render_subclass_grouped_viewpoints_html(subclass_groups or [])
    elif not activity_omit_detail and (
        title == BUCKET_INFO
        or (show_topic_activity_viewpoints and title in (BUCKET_TOPIC, BUCKET_ACTIVITY) and points)
    ):
        li_flat: list[str] = []
        for row in points:
            if len(row) >= 3:
                topic, viewpoint, u = str(row[0]), str(row[1]), str(row[2] or "").strip()
            else:
                topic, viewpoint, u = str(row[0]), str(row[1]), ""
            li_flat.append(
                "<li>"
                f"{_html_point_topic_label_for_row(topic, u, viewpoint=viewpoint)}"
                f"{_render_viewpoint_row_body_html(viewpoint)}"
                "</li>"
            )
        items_html = "".join(li_flat)
        if items_html:
            viewpoints_html = f'<ol class="viewpoints">{items_html}</ol>'
    elif not activity_omit_detail and title == BUCKET_INFO and not points and not use_subclass:
        viewpoints_html = (
            '<ol class="viewpoints">'
            '<li><span class="point-topic">暂无可提炼观点：</span>待补充。</li>'
            "</ol>"
        )

    if title == BUCKET_ACTIVITY and sources:
        def _activity_desc_text(item: dict[str, str]) -> str:
            """活动卡片描述：优先原文描述，不做二次总结；仅过滤纯时间地点串。"""
            for key in ("description_raw", "description", "summary", "viewpoint"):
                txt = str(item.get(key) or "").strip()
                if not txt:
                    continue
                if re.match(r"^\s*时间\s*[：:]", txt) and "|" in txt:
                    continue
                # 过滤微信等页面拦截导致的无效噪声文案
                if any(
                    bad in txt
                    for bad in (
                        "环境异常",
                        "完成验证后即可继续访问",
                        "去验证",
                        "轻点两下取消赞",
                        "轻点两下取消在看",
                    )
                ):
                    continue
                txt = re.sub(r"\s*[|｜]\s*时间\s*[：:].*$", "", txt).strip()
                txt = re.sub(r"\s*[|｜]\s*地点\s*[：:].*$", "", txt).strip()
                txt = re.sub(r"\s*[|｜]\s*(?:\d+天后开始|今天开始|进行中|已结束|报名中)\s*$", "", txt).strip()
                return txt
            return "待补充"

        if activity_omit_detail:
            source_items_html = "".join(
                '<article class="activity-card activity-card-omit">'
                f'<h4 class="activity-card-title activity-card-title-omit">'
                f'<a href="{_html.escape(str(item.get("url") or "#"), quote=True)}" target="_blank" rel="noopener">'
                f'{_html.escape(str(item.get("title") or "待补充"))}</a></h4>'
                f'<p class="activity-field">{_html.escape(_activity_desc_text(item))}</p>'
                '<p class="activity-field"><span class="activity-field-label">时间：</span>'
                f'{_html.escape(str(item.get("time") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">地点：</span>'
                f'{_html.escape(str(item.get("city") or "待补充"))}</p>'
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
                f'<p class="activity-field">{_html.escape(_activity_desc_text(item))}</p>'
                '<p class="activity-field"><span class="activity-field-label">时间：</span>'
                f'{_html.escape(str(item.get("time") or "待补充"))}</p>'
                '<p class="activity-field"><span class="activity-field-label">地点：</span>'
                f'{_html.escape(str(item.get("city") or "待补充"))}</p>'
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
    if not source_items_html and title == BUCKET_ACTIVITY:
        source_block_html = (
            '<div class="activity-cards">'
            '<article class="activity-card activity-card-empty">'
            '<p class="activity-field">暂无活动</p>'
            "</article>"
            "</div>"
        )
    elif not source_items_html:
        source_block_html = ""
    elif title == BUCKET_ACTIVITY:
        source_block_html = f'<div class="activity-cards">{source_items_html}</div>'
    else:
        source_block_html = f'<ol class="sources">{source_items_html}</ol>'

    if title == BUCKET_ACTIVITY:
        block_title = ""
    elif activity_omit_detail and source_block_html:
        block_title = "活动信息"
    elif source_block_html:
        block_title = "源地址"
    else:
        block_title = ""
    if block_title:
        source_title_html = f'<h3 class="source-title">{_html.escape(block_title)}</h3>{source_block_html}'
    else:
        source_title_html = source_block_html

    summary_html = _html_escape_section_summary(summary) if str(summary or "").strip() else ""

    return (
        '<section class="brief-section">'
        f'<h2 class="section-title">{_html.escape(title)}</h2>'
        f"{summary_html}"
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
    将旧格式「提炼标题：…；内容要点：…」、带标签「主观点：…；解释：…」、以及
    新「冒号总起」格式「结论句：一段正文」均规范为纯文本，
    便于纯文本摘要和不支持 HTML 的场景使用。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    # 带标签：主观点：…；解释：… → 规范为「观点：解释」（全角冒号、无小标题字样）
    if re.search(r"主观点\s*[：:]", t):
        for splitter in (r"\s*[；;]\s*解释\s*[：:]\s*", r"\s*解释\s*[：:]\s*"):
            parts = re.split(splitter, t, maxsplit=1)
            if len(parts) == 2:
                head = re.sub(r"^\s*主观点\s*[：:]\s*", "", parts[0].strip()).strip()
                body = parts[1].strip()
                # 去掉 Markdown 粗体标记，仅保留文本
                body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)
                if head and body:
                    return f"{head}：{body}"
        only = re.match(r"^\s*主观点\s*[：:]\s*(.+)$", t, re.DOTALL)
        if only:
            return re.sub(r"\*\*([^*]+)\*\*", r"\1", only.group(1).strip())
    # 冒号总起式：{结论}：{展开}（首处全角「：」，无「主观点/提炼标题/解释」等标签）
    if "：" in t and "主观点" not in t and not re.match(r"^\s*主观点", t):
        _idx0 = t.find("：")
        if 0 < _idx0 <= 120:
            _h0 = t[:_idx0].strip()
            _bad_label_head = _h0 in (
                "提炼标题",
                "主观点",
                "内容要点",
                "解释",
            ) or _h0.startswith("提炼标题")
            if _h0 and not _bad_label_head:
                return re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    # 旧格式：提炼标题：…；内容要点：…
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


def _render_inline_markdown(text: str) -> str:
    """将文本中的 **粗体** 转换为 <strong>，其余内容 HTML 转义。"""
    parts = re.split(r"(\*\*[^*]+\*\*)", str(text or ""))
    out: list[str] = []
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            inner = _html.escape(part[2:-2])
            out.append(f"<strong>{inner}</strong>")
        else:
            out.append(_html.escape(part))
    return "".join(out)


def _render_viewpoint_as_html(viewpoint: str) -> str:
    """
    将观点文本渲染为 HTML。

    - **冒号总起** ``{结论句}：{展开}``（首处全角冒号）：结论加粗，展开段内支持 **bold**。
    - 带标签 ``主观点：…；解释：…``：去掉标签，版式同冒号总起（观点加粗 + ： + 解释）。
    - 旧格式 ``提炼标题：…；内容要点：…``：同上，无「主观点/解释」小标题行。
    - 其他：整段转义 + 行内 **bold**。
    """
    t = str(viewpoint or "").strip()
    if not t:
        return ""

    # 带标签：主观点：…；解释：… → 与冒号总起式相同版式（不展示「主观点/解释」字样）
    if re.search(r"主观点\s*[：:]", t):
        for splitter in (r"\s*[；;]\s*解释\s*[：:]\s*", r"\s*解释\s*[：:]\s*"):
            parts = re.split(splitter, t, maxsplit=1)
            if len(parts) == 2:
                head = re.sub(r"^\s*主观点\s*[：:]\s*", "", parts[0].strip()).strip()
                body = parts[1].strip()
                head_html = _render_inline_markdown(head)
                body_html = _render_inline_markdown(body)
                return (
                    '<span class="vp-line">'
                    f'<strong class="vp-main">{head_html}</strong>'
                    '<span class="vp-colon">：</span>'
                    f'<span class="vp-body">{body_html}</span>'
                    "</span>"
                )
        # 只有主观点，没有解释
        only = re.match(r"^\s*主观点\s*[：:]\s*(.+)$", t, re.DOTALL)
        if only:
            head_html = _render_inline_markdown(only.group(1).strip())
            return (
                '<span class="vp-line">'
                f'<strong class="vp-main">{head_html}</strong>'
                "</span>"
            )

    # 冒号总起式：{结论}：{展开}（无「主观点/解释」标签；与 step5 新 summary 一致）
    if "：" in t:
        _ci = t.find("：")
        if 0 < _ci <= 120:
            _ch = t[:_ci].strip()
            _cb = t[_ci + 1 :].strip()
            _bad_ch = _ch in ("提炼标题", "主观点", "内容要点", "解释") or _ch.startswith(
                "提炼标题"
            )
            if _ch and _cb and not _bad_ch and _is_compact_viewpoint_head(_ch, _cb):
                return (
                    '<span class="vp-line">'
                    f'<strong class="vp-main">{_render_inline_markdown(_ch)}</strong>'
                    '<span class="vp-colon">：</span>'
                    f'<span class="vp-body">{_render_inline_markdown(_cb)}</span>'
                    "</span>"
                )

    # 旧格式：提炼标题：…；内容要点：…
    if re.search(r"内容要点\s*[：:]", t):
        for splitter in (r"\s*[；;]\s*内容要点\s*[：:]\s*", r"\s*内容要点\s*[：:]\s*"):
            parts = re.split(splitter, t, maxsplit=1)
            if len(parts) == 2:
                head = re.sub(r"^\s*提炼标题\s*[：:]\s*", "", parts[0].strip()).strip()
                body = parts[1].strip()
                return (
                    '<span class="vp-line">'
                    f'<strong class="vp-main">{_render_inline_markdown(head)}</strong>'
                    '<span class="vp-colon">：</span>'
                    f'<span class="vp-body">{_render_inline_markdown(body)}</span>'
                    "</span>"
                )

    # 普通文本：直接转义
    return _render_inline_markdown(t)


def _compose_item_viewpoint(summary: str, core_points: list[str]) -> str:
    """
    组合 step5 的 summary + core_points 成最终可渲染的观点字符串。

    - **冒号总起** ``{结论}：{展开}``：把未出现在 summary 中的 core_points 用「；」接在展开段后。
    - 带标签 ``主观点：…；解释：…``：追加进「解释」段（兼容旧输出）。
    - 其他：沿用原始拼接逻辑。
    """
    summary_text = str(summary or "").strip()
    points: list[str] = []
    for point in core_points:
        text = str(point or "").strip()
        if text and text not in points:
            points.append(text)

    # 仅保留明确的元数据前缀；"文章"/"报道"/"属于" 过于泛化，会误杀正常财经内容
    low_signal_hints = (
        "这是一篇",
        "这是一条",
        "该内容为",
        "该页面为",
        "该页面是",
        "专题索引",
        "导航页",
    )
    is_low_signal_summary = bool(summary_text) and any(hint in summary_text for hint in low_signal_hints)

    def _finish(s: str) -> str:
        return _normalize_sentence(_polish_step5_viewpoint_display(s))

    # 新格式：主观点：…；解释：… —— 保留结构，追加 core_points 到解释部分
    if summary_text and re.search(r"主观点\s*[：:]", summary_text) and not is_low_signal_summary:
        extra: list[str] = []
        for p in points[:3]:
            # 去掉 Markdown 粗体后做包含检查，避免重复
            p_plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", p)
            if p_plain and p_plain not in summary_text and p not in summary_text:
                extra.append(p)
        if extra:
            # 把附加要点拼入解释部分
            extra_text = "；".join(extra)
            # 找「解释：」后的部分并追加
            for splitter in (r"(\s*[；;]\s*解释\s*[：:]\s*)", r"(\s*解释\s*[：:]\s*)"):
                m = re.search(splitter, summary_text)
                if m:
                    prefix = summary_text[: m.end()]
                    body = summary_text[m.end():]
                    body_stripped = body.rstrip("。！？.!?")
                    return f"{prefix}{body_stripped}；{extra_text}。"
        return summary_text

    # 冒号总起式：{结论}：{展开} —— 将 core_points 追加到展开段末尾（与 step5 新 summary 一致）
    if summary_text and not is_low_signal_summary and "主观点" not in summary_text:
        _ix = summary_text.find("：")
        if 0 < _ix <= 100:
            _lead = summary_text[:_ix].strip()
            _rest = summary_text[_ix + 1 :].strip()
            _bad_lead = _lead in (
                "提炼标题",
                "主观点",
                "内容要点",
                "解释",
            ) or _lead.startswith("提炼标题")
            if _lead and _rest and not _bad_lead:
                extra_colon: list[str] = []
                for p in points[:3]:
                    p_plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", p)
                    if p_plain and p_plain not in summary_text and p not in summary_text:
                        extra_colon.append(p)
                if extra_colon:
                    rest2 = _rest.rstrip("。！？.!?；;")
                    clean_extra = [p.rstrip("。！？.!?；;") for p in extra_colon]
                    return _dedup_punctuation(f"{_lead}：{rest2}；{'；'.join(clean_extra)}。")
                return summary_text

    # 旧格式：提炼标题：…；内容要点：… —— 经 _polish 去掉标签；可追加不重复的 core_points
    if summary_text and re.search(r"内容要点\s*[：:]", summary_text) and not is_low_signal_summary:
        extra_old: list[str] = []
        for p in points[:3]:
            if p and p not in summary_text:
                extra_old.append(p)
        if extra_old:
            base = summary_text.rstrip("。！？.!?；;")
            clean_old = [p.rstrip("。！？.!?；;") for p in extra_old]
            merged_old = _dedup_punctuation(f"{base}；{'；'.join(clean_old)}。")
        else:
            merged_old = summary_text
        return _finish(merged_old)

    if summary_text and not is_low_signal_summary:
        if points and points[0] not in summary_text:
            p0 = points[0].rstrip("。！？.!?；;")
            return _finish(_dedup_punctuation(f"{summary_text.rstrip('。！？.!?；;')}；{p0}"))
        return _finish(summary_text)

    if points:
        clean_pts = [p.rstrip("。！？.!?；;") for p in points[:2]]
        merged = "；".join(clean_pts)
        if summary_text and summary_text not in merged and not is_low_signal_summary:
            merged = _dedup_punctuation(f"{summary_text.rstrip('。！？.!?；;')}；{merged}")
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


_LOW_QUALITY_SUMMARY_PREFIXES: tuple[str, ...] = (
    "公开可解析信息有限",
    "公开可解析",
    "页面因技术风控",
    "原文页面因技术原因",
    "原文内容因技术原因",
    "原文内容因技术风控",
    "原文内容不足",
    "原文内容较少",
    "原文内容过少",
    # LLM 生成的免责前缀变体
    "信息因页面风控技术无法直接获取",
    "专题内容因技术风控无法直接解析",
    "项目因技术风控无法解析",
    "项目信息因页面技术风控无法获取",
    "内容因技术风控",
    "相关信息待补充",
)

# 包含检查：summary/viewpoint 正文中含以下片段即视为低质量，整条跳过
_LOW_QUALITY_SUMMARY_FRAGMENTS: tuple[str, ...] = (
    "因技术原因无法完整获取",
    "因页面访问受限而无法获取",
    "因页面访问受限无法获取",
    "因技术风控无法直接获取",
    "无法提炼具体事实、数据及核心论点",
    "具体内容（如案例、数据、观点）无法解析",
    "原文无法解析而暂缺",
    "可解析信息有限",
    "可解析内容有限",
    # 内容不足相关：LLM 输出或原文抓取失败后的套话，整条跳过
    "原文内容不足，无法进行有效分析",
    "无法进行有效分析",
    "内容不足，无法",
    "原文内容不足",
    "内容过少，无法",
    "原文过短，无法",
)


def _step5_item_should_omit(item: Any) -> bool:
    """
    返回 True 表示该条目应从简报中跳过：
      1. LLM 输出了 omit: true（数据确实完全不可用）
      2. summary 以已知低质量前缀开头
      3. summary 正文中含低质量片段（"因技术原因无法完整获取" 等）
    """
    try:
        ana = getattr(item, "analysis", None)
        if ana is None:
            return False
        if getattr(ana, "omit", False):
            return True
        s = (getattr(ana, "summary", None) or "").strip()
        if any(s.startswith(p) for p in _LOW_QUALITY_SUMMARY_PREFIXES):
            return True
        if any(f in s for f in _LOW_QUALITY_SUMMARY_FRAGMENTS):
            return True
    except Exception:
        pass
    return False


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
    t = str(text or "")
    # 清理异常字符/Markdown 残留，避免栏头出现不通顺文案
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = t.replace("\uFFFD", "").replace("�", "")
    t = re.sub(r"[\x00-\x1f]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    return _clip_readable_text(t, max(24, max_chars), min_chars=80)


def _rollup_phrase_from_step5_item(item: Any, *, max_chars: int = 40) -> str:
    """从 step5 单条提炼用于栏头总述的短语（结论句优先，其次标题）。"""
    v = _compose_item_viewpoint(item.analysis.summary, list(item.analysis.core_points))
    text_in = (str(v) if v and str(v).strip() else str(item.analysis.summary or "")).strip()
    polished = _polish_step5_viewpoint_display(text_in) if text_in else ""
    polished = _normalize_sentence(polished)

    # 优先取「结论：展开」中的结论句（若可稳健拆分）。
    head, _rest = _split_viewpoint_for_point_topic_row(polished)
    phrase = (head or "").strip()
    if not phrase:
        phrase = (str(getattr(item, "original_title", "") or "").strip()) or ""
    if not phrase:
        phrase = polished
    phrase = re.sub(r"\*\*([^*]+)\*\*", r"\1", phrase)
    phrase = phrase.replace("\uFFFD", "").replace("�", "")
    phrase = re.split(r"[|｜]", phrase, maxsplit=1)[0].strip()
    # 去掉来源尾缀，避免摘要口水化
    phrase = re.sub(r"\s*[|｜]\s*36\s*氪.*$", "", phrase)
    phrase = phrase.replace("|", "：").replace("｜", "：")
    phrase = re.sub(r"\s+", " ", phrase)
    phrase = re.sub(r"[。；;：:]+$", "", phrase).strip()
    if not phrase:
        return ""
    return _clip_readable_text(phrase, max(12, max_chars), min_chars=12).rstrip("。")


def _rollup_join_phrases(phrases: list[str], *, cap: int, sep: str) -> tuple[str, str]:
    """将多条结论短语拼成栏头一句；专题与资讯均用分号衔接，便于概述下方多文。"""
    n = max(1, cap)
    cleaned: list[str] = []
    for p in phrases[:n]:
        q = (p or "").strip().rstrip("。；; ")
        if q:
            cleaned.append(q)
    lead = sep.join(cleaned)
    suffix = "等" if len(phrases) > n else ""
    return lead, suffix


def _synthesize_step5_bucket_rollup(
    step6_path: Path | None,
    *,
    bucket: str,
    default_topic_name: str,
    require_original_text: bool = False,
    phrase_max_chars: int = 40,
    max_phrases_per_category: int = 3,
    blurb_max_chars: int | None = None,
    resolved_channel_allowlist: frozenset[str] | None = None,
) -> str:
    """按 step5 各分类生成栏头总述：聚合结论短语成叙事概述，不报条数、不写「主线包括」。"""
    if step6_path is None:
        return ""
    umap = _build_url_article_map(_load_articles(step6_path))
    for candidate in _build_step5_path_candidates(step6_path):
        if not candidate.is_file():
            continue
        try:
            payload = load_content_analysis_inputs(candidate)
        except Exception:
            continue
        parts: list[str] = []
        for section in payload.categories:
            rows = [it for it in section.items if _channel_to_email_bucket(it.channel) == bucket]
            if resolved_channel_allowlist is not None:

                def _allow_resolved(it: Any) -> str:
                    c = _kr36_step5_item_resolved_channel(it, umap)
                    if c:
                        return c
                    u = (getattr(it, "original_url", None) or "").strip()
                    if bucket == BUCKET_INFO and u and "search/articles" in u.lower():
                        return "36氪独家"
                    return ""

                rows = [it for it in rows if _allow_resolved(it) in resolved_channel_allowlist]
            if require_original_text:
                rows = [it for it in rows if _step5_item_has_original_text(it)]
            if not rows:
                continue
            tname = (section.topic or "").strip() or default_topic_name
            phrases: list[str] = []
            for it in rows:
                p = _rollup_phrase_from_step5_item(it, max_chars=phrase_max_chars)
                if p and p not in phrases:
                    phrases.append(p)
            if not phrases:
                continue
            cap = max(1, max_phrases_per_category)
            lead, suffix = _rollup_join_phrases(phrases, cap=cap, sep="；")
            if not lead:
                continue
            parts.append(f"「{tname}」{lead}{suffix}。")
        if not parts:
            return ""
        merged = _normalize_sentence("；".join(parts))
        limit = blurb_max_chars if blurb_max_chars is not None else KR36_TOPIC_INFO_SECTION_BLURB_MAX_CHARS
        return _section_blurb_cap(merged, max_chars=limit)
    return ""


def _synthesize_step5_topic_rollup_from_all_topic_items(step6_path: Path | None) -> str:
    """
    按 step5 每个专题 `category` 下「栏目=专题」子项，拼栏头 `section-summary`：
    与资讯同款——用各文结论文以分号串联成概述，**不写**条数与「主线包括」；
    多档 category 之间也用分号衔接；总字数上限与资讯栏头同为 ``KR36_INFO_SECTION_BLURB_MAX_CHARS``（避免默认 200 字只覆盖第一节）。
    """
    return _synthesize_step5_bucket_rollup(
        step6_path,
        bucket=BUCKET_TOPIC,
        default_topic_name="专题",
        require_original_text=False,
        max_phrases_per_category=8,
        blurb_max_chars=KR36_INFO_SECTION_BLURB_MAX_CHARS,
        resolved_channel_allowlist=frozenset({BUCKET_TOPIC}),
    )


def _synthesize_step5_info_rollup_from_all_info_items(step6_path: Path | None) -> str:
    """
    每档 step5 ``category`` 下「栏目=资讯」子项的结论短语，拼资讯栏头：叙事概述、不报条数；
    短语之间用分号衔接，仍受总字数上限约束（非原文照搬）。
    """
    return _synthesize_step5_bucket_rollup(
        step6_path,
        bucket=BUCKET_INFO,
        default_topic_name="资讯",
        require_original_text=False,
        phrase_max_chars=72,
        max_phrases_per_category=8,
        blurb_max_chars=KR36_INFO_SECTION_BLURB_MAX_CHARS,
        resolved_channel_allowlist=KR36_BRIEF_INFO_CHANNELS_SET,
    )


def _subclass_groups_from_gtv_rows(
    rows: list[tuple[str, str, str, str]],
) -> list[tuple[str, list[tuple[str, str, str]]]] | None:
    """按 step5 的 category.topic（g）将 (标题, 观点, 原文链) 分子类；全未填主题名则不分组。"""
    if not rows:
        return None
    order: list[str] = []
    m: dict[str, list[tuple[str, str, str]]] = {}
    for g, t, v, u in rows:
        key = (g or "").strip() or "未分类"
        if key not in m:
            order.append(key)
            m[key] = []
        m[key].append((t, v, (u or "").strip()))
    if len(order) == 1 and order[0] == "未分类":
        return None
    return [(k, m[k]) for k in order]


def _subclass_groups_from_bracket_labels(
    points: list[tuple[str, str, str]],
) -> list[tuple[str, list[tuple[str, str, str]]]] | None:
    """
    当扁平行首均为 `【子类名】条目标题` 时，拆出子类用于与 step5 聚合主题同构的展示。
    任一条无此前缀则返回 None，回退为扁平列表。
    """
    if not points:
        return None
    order: list[str] = []
    m: dict[str, list[tuple[str, str, str]]] = {}
    for label, v, u in points:
        mo = _KR36_SUBCLASS_IN_POINT_LABEL.match((label or "").strip())
        if not mo:
            return None
        key = (mo.group(1) or "").strip() or "未分类"
        rest = _KR36_SUBCLASS_IN_POINT_LABEL.sub("", (label or "").strip(), count=1).strip() or "待补充"
        if key not in m:
            order.append(key)
            m[key] = []
        m[key].append((rest, v, (u or "").strip()))
    if len(order) == 1 and order[0] == "未分类":
        return None
    return [(k, m[k]) for k in order]


def _sort_kr36_brief_info_subclass_groups(
    groups: list[tuple[str, list[tuple[str, str, str]]]],
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """资讯子类按 36氪独家 → AI → 创投 固定顺序输出（中文序号仍由渲染层生成）。"""
    rank = {ch: i for i, ch in enumerate(KR36_BRIEF_INFO_CHANNELS)}
    return sorted(groups, key=lambda g: rank.get((g[0] or "").strip().rstrip("："), 99))


def _load_step5_per_item_points_by_bucket(
    step6_path: Path | None,
    *,
    viewpoint_max_chars: int = 260,
    url_article_map: dict[str, dict[str, Any]] | None = None,
    max_activity_subitems: int = KR36_ACTIVITY_VISIBLE_MAX,
) -> Optional[
        Tuple[
            dict[str, list[tuple[str, str, str]]],
            dict[str, list[tuple[str, list[tuple[str, str, str]]]]],
        ]
    ]:
    """
    从 step5 分析结果按 `item.channel` 归入 专题/活动/资讯，每条一行 (展示标题, 观点, 原文链)；
    同文件解析出供 HTML 的「子类（聚合主题名） → 子项观点」结构（仅专题/资讯有）。

    与 Markdown 的 ``##`` 分栏无关，用于「不展示源地址」时把约 10 条子项逐条列观点；
    若同一栏内有多个 `category.topic`（多档专题），标题前会加 `【主题名】` 区分。
    活动子项会结合 hot_topics（``url_article_map``）筛掉已结束，并截断为最多
    ``max_activity_subitems`` 条。找不到 step5 文件时返回 None。

    返回 (flat, grouped)；grouped 的 key 为「专题/资讯」栏名，value 为有序列表
    (子类标题, list[(子项标题, 观点, 原文链)])。
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
        raw: dict[str, list[tuple[str, str, str, str]]] = {
            BUCKET_TOPIC: [],
            BUCKET_ACTIVITY: [],
            BUCKET_INFO: [],
        }
        umap = url_article_map or {}
        for category in payload.categories:
            g = (category.topic or "").strip()
            for item in category.items:
                u_item = (item.original_url or "").strip()
                b = _resolve_step5_item_bucket(
                    item_channel=item.channel,
                    item_url=u_item,
                    url_article_map=umap,
                )
                art_scope: dict[str, Any] | None = None
                if u_item and umap:
                    _a = umap.get(u_item) or umap.get(_normalize_url_for_match(u_item) or "")
                    if isinstance(_a, dict):
                        art_scope = _a
                s5_ch = str(getattr(item, "channel", None) or "").strip()
                if b == BUCKET_INFO:
                    if not _kr36_brief_info_article_in_scope(
                        art_scope, url=u_item, step5_channel=s5_ch
                    ):
                        continue
                if b == BUCKET_TOPIC:
                    if _kr36_brief_info_article_in_scope(
                        art_scope, url=u_item, step5_channel=s5_ch
                    ):
                        continue
                t = (item.original_title or "").strip() or "（未命名）"
                v = _compose_item_viewpoint(item.analysis.summary, list(item.analysis.core_points))
                if not (v and str(v).strip()):
                    v = str(item.analysis.summary or "").strip() or "（本条目暂无可生成观点。）"
                v_cap = (
                    max(viewpoint_max_chars, KR36_INFO_VIEWPOINT_MAX_CHARS)
                    if b == BUCKET_INFO
                    else viewpoint_max_chars
                )
                v = _clip_readable_text(
                    _normalize_sentence(str(v)),
                    v_cap,
                    min_chars=max(80, v_cap // 2),
                )
                if _step5_item_should_omit(item):
                    continue
                # 资讯：原文抓取明确失败时（status=fail/block/risk 等）也整条过滤；
                # 仅当 original_content 字段存在且 text 为空且 status 指示失败时才跳过，
                # 避免误杀无 original_content 字段的活动/专题子项。
                if b == BUCKET_INFO and not _step5_item_has_original_text(item):
                    continue
                if b == BUCKET_ACTIVITY and umap:
                    u = u_item
                    if u:
                        art = umap.get(u) or umap.get(_normalize_url_for_match(u) or "")
                        if art and not _include_kr36_activity_in_feed(art):
                            continue
                row_g = g
                if b == BUCKET_INFO and u_item:
                    ch_res = ""
                    if isinstance(art_scope, dict):
                        ch_res = str(art_scope.get("channel") or "").strip()
                    if not ch_res:
                        ch_res = s5_ch
                    if ch_res in KR36_BRIEF_INFO_CHANNELS_SET:
                        row_g = ch_res
                    elif (not ch_res or ch_res == BUCKET_INFO) and "search/articles" in u_item.lower():
                        row_g = "36氪独家"
                raw[b].append((row_g, t, v, u_item))
        if max_activity_subitems and raw[BUCKET_ACTIVITY]:
            raw[BUCKET_ACTIVITY] = raw[BUCKET_ACTIVITY][: max(0, max_activity_subitems)]
        out: dict[str, list[tuple[str, str, str]]] = {}
        grouped: dict[str, list[tuple[str, list[tuple[str, str, str]]]]] = {}
        for bucket, rows in raw.items():
            if not rows:
                out[bucket] = []
                continue
            groups = {g for g, _, _, _ in rows if g}
            multi_group = len(groups) > 1
            flat: list[tuple[str, str, str]] = []
            for g, t, v, u in rows:
                if multi_group and g:
                    label = f"【{g}】{t}"
                else:
                    label = t
                flat.append((label, v, (u or "").strip()))
            out[bucket] = flat
            if bucket in (BUCKET_TOPIC, BUCKET_INFO):
                sg = _subclass_groups_from_gtv_rows(rows)
                if sg:
                    grouped[bucket] = sg
        return (out, grouped)
    return None


def _load_step5_activity_omit_enriched(
    step6_path: Path | None,
    url_article_map: dict[str, dict[str, Any]],
    *,
    viewpoint_max_chars: int = 260,
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
                v = _clip_readable_text(
                    _normalize_sentence(str(v)),
                    viewpoint_max_chars,
                    min_chars=max(80, viewpoint_max_chars // 2),
                )
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
            # 卡片「描述」使用 step5 原始内容摘要，不使用 analysis 的主观点/解释文本。
            raw_desc = ""
            try:
                raw_desc = str(getattr(item.original_content, "summary", "") or "").strip()
            except Exception:
                raw_desc = ""
            base["description_raw"] = raw_desc
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


def _build_omit_info_comprehensive_summary(
    points: list[tuple[str, str, str]],
    *,
    intro_text: str = "",
    max_chars: int = KR36_INFO_SECTION_BLURB_MAX_CHARS,
) -> str:
    """资讯多条时的栏头 fallback：用结论文分号串联成概述，不报条数、不套「主线包括」。"""
    if len(points) <= 1:
        return ""

    topics: list[str] = []
    heads: list[str] = []
    for label, viewpoint, _u in points:
        raw = str(label or "").strip()
        topic = _KR36_SUBCLASS_IN_POINT_LABEL.sub("", raw, count=1).strip() if raw else ""
        if topic and topic not in topics:
            topics.append(_trim_text(topic, 40))
        h, _rest = _split_viewpoint_for_point_topic_row(str(viewpoint or ""))
        hv = (h or "").strip()
        if hv and hv not in heads:
            heads.append(_trim_text(hv, 56))

    cleaned_heads = [h.strip().rstrip("。；; ") for h in heads[:8] if (h or "").strip()]
    if cleaned_heads:
        summary = "；".join(cleaned_heads) + "。"
    elif topics:
        summary = "；".join(t.strip().rstrip("。；; ") for t in topics[:8]) + "。"
    else:
        summary = "本期资讯要点并列呈现，详见下列观点。"
    intro = (intro_text or "").strip()
    if intro:
        summary = f"{intro} {summary}"
    summary = _normalize_sentence(summary)
    summary = _section_blurb_cap(summary, max_chars=max_chars)
    return summary


def _infer_bucket_from_url(url: str) -> str | None:
    normalized = str(url or "").strip()
    if not normalized:
        return None
    low = normalized.lower()
    if re.search(r"^https?://(?:www\.)?36kr\.com/search/articles/", normalized):
        return BUCKET_INFO
    if "/information/" in low and "36kr.com" in low:
        return BUCKET_INFO
    if re.search(r"^https?://(?:www\.)?36kr\.com/topics/", normalized):
        return BUCKET_TOPIC
    if re.search(r"^https?://(?:www\.)?36kr\.com/(?:sign-up-activity|activity)/", normalized):
        return BUCKET_ACTIVITY
    return None


def _kr36_title_implied_brief_channel(title: str) -> str | None:
    """标题中常见「独家/首发/硬氪」标记 → 简报资讯三栏之一（与 36kr 编辑习惯对齐）。"""
    compact = re.sub(r"[\s\u3000]+", "", title or "")
    if "36氪独家" in compact or "氪独家" in compact:
        return "36氪独家"
    if "36氪首发" in compact or "氪首发" in compact:
        return "创投"
    if "硬氪专访" in compact:
        return "创投"
    return None


def _kr36_effective_listing_channel(
    article: dict[str, Any],
    url_channel_map: dict[str, str],
) -> str:
    """合并 step1 CSV 栏目、hot 栏目与标题推断，供简报分桶与资讯子类展示。"""
    url = str(article.get("url") or "").strip()
    hot_ch = str(article.get("channel") or "").strip()
    csv_ch = ""
    if url:
        csv_ch = str(
            url_channel_map.get(url) or url_channel_map.get(_normalize_url_for_match(url) or "") or ""
        ).strip()
    th = _kr36_title_implied_brief_channel(str(article.get("title") or ""))
    if th:
        return th
    if csv_ch in KR36_BRIEF_INFO_CHANNELS_SET:
        return csv_ch
    if hot_ch in KR36_BRIEF_INFO_CHANNELS_SET:
        return hot_ch
    if csv_ch:
        return csv_ch
    return hot_ch


def _dedupe_hot_articles_for_brief(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 URL 在 hot_topics 中可能重复（专题收录 + 搜索/资讯列表各一条），简报优先保留资讯三栏条目。"""

    def score(x: dict[str, Any]) -> int:
        ch = str(x.get("channel") or "").strip()
        sb = str(x.get("source_bucket") or "").strip()
        s = 0
        if ch in KR36_BRIEF_INFO_CHANNELS_SET:
            s += 100
        if sb in KR36_BRIEF_INFO_CHANNELS_SET:
            s += 80
        if _kr36_title_implied_brief_channel(str(x.get("title") or "")):
            s += 40
        if ch != BUCKET_TOPIC:
            s += 5
        return s

    by_key: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for a in articles:
        url = str(a.get("url") or "").strip()
        if not url:
            continue
        k = _normalize_url_for_match(url) or url
        if k not in by_key:
            order.append(k)
            by_key[k] = []
        by_key[k].append(a)
    out: list[dict[str, Any]] = []
    for k in order:
        cands = by_key[k]
        out.append(cands[0] if len(cands) == 1 else max(cands, key=score))
    no_url = [a for a in articles if not str(a.get("url") or "").strip()]
    return out + no_url


def _patch_articles_for_kr36_brief(
    articles: list[dict[str, Any]], step6_path: Path
) -> list[dict[str, Any]]:
    merged = _dedupe_hot_articles_for_brief(articles)
    cmap = _load_url_channel_map(step6_path)
    for a in merged:
        eff = _kr36_effective_listing_channel(a, cmap)
        if eff:
            a["channel"] = eff
    return merged


def _kr36_brief_info_article_in_scope(
    article: dict[str, Any] | None,
    *,
    url: str = "",
    step5_channel: str = "",
) -> bool:
    """简报「资讯」栏是否收录：仅 36氪独家 / AI / 创投；无 hot 时以 step5 栏目为准。"""
    u = (url or str((article or {}).get("url") or "")).strip()
    ch = str((article or {}).get("channel") or "").strip() if article else ""
    s5 = (step5_channel or "").strip()
    if ch in KR36_BRIEF_INFO_CHANNELS_SET:
        return True
    # step5 分析稿栏目：无 hot 或 hot 仍为「专题」占位时，以 item.channel 为准
    if s5 in KR36_BRIEF_INFO_CHANNELS_SET:
        return True
    if not ch and "search/articles" in u.lower():
        return True
    # step1 偶发栏目写作「资讯」的搜索条目，归入 36氪独家 检索链路
    if ch == BUCKET_INFO and "search/articles" in u.lower():
        return True
    return False


def _kr36_step5_item_resolved_channel(item: Any, url_article_map: dict[str, dict[str, Any]]) -> str:
    u = (getattr(item, "original_url", None) or "").strip()
    if u and url_article_map:
        art = url_article_map.get(u) or url_article_map.get(_normalize_url_for_match(u) or "")
        if isinstance(art, dict):
            ch = str(art.get("channel") or "").strip()
            if ch:
                return ch
    return str(getattr(item, "channel", None) or "").strip()


def _normalize_bucket(article: dict[str, Any]) -> str:
    """专题/活动/资讯归桶：资讯子类栏目优先；再按 URL 路径；最后 source_bucket/channel。

    避免 source_bucket=专题 盖住搜索页 URL、或 AI/创投 栏目被算进专题。
    """
    channel = str(article.get("channel") or "").strip()
    source_bucket = str(article.get("source_bucket") or "").strip()
    url = str(article.get("url") or "").strip()

    if channel in INFO_SUBCATEGORIES:
        return BUCKET_INFO

    inferred = _infer_bucket_from_url(url)
    if inferred == BUCKET_ACTIVITY:
        return BUCKET_ACTIVITY
    if inferred == BUCKET_INFO:
        return BUCKET_INFO
    if inferred == BUCKET_TOPIC:
        return BUCKET_TOPIC

    if source_bucket == BUCKET_ACTIVITY or channel == BUCKET_ACTIVITY:
        return BUCKET_ACTIVITY

    if source_bucket == BUCKET_TOPIC or channel == BUCKET_TOPIC:
        return BUCKET_TOPIC

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
    """活动区/omit 子项：仅「进行中 + 待开始」；无源数据时保留。

    过滤依据仅为 metadata 中的 activity_status / start_label 状态字段，
    不受发布日期或简报生成日期影响——即活动在周三/周六推送周期内只要
    状态有效就持续展示，直至状态变为「已结束」。
    """
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


def _resolve_step5_item_bucket(
    *,
    item_channel: str,
    item_url: str,
    url_article_map: dict[str, dict[str, Any]] | None = None,
) -> str:
    """
    step5 分桶：以 hot_topics 条目的归桶为准（与简报 URL 统计一致）；无源数据时退回 item.channel。
    """
    base = _channel_to_email_bucket(item_channel)
    u = (item_url or "").strip()
    if not u or not url_article_map:
        return base
    art = url_article_map.get(u) or url_article_map.get(_normalize_url_for_match(u) or "")
    if not isinstance(art, dict):
        return base
    return _normalize_bucket(art)


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
    return [channel for channel in KR36_BRIEF_INFO_CHANNELS if channel in present] or [
        c for c in present if c in KR36_BRIEF_INFO_CHANNELS_SET
    ]


def _collect_section_channel_labels(section: dict[str, Any], url_channel_map: dict[str, str]) -> list[str]:
    labels: list[str] = []
    for url in _extract_section_urls(section):
        channel = str(url_channel_map.get(url, "")).strip()
        if channel in {BUCKET_TOPIC, BUCKET_ACTIVITY, BUCKET_INFO} or not channel:
            continue
        if channel not in labels:
            labels.append(channel)
    return [channel for channel in KR36_BRIEF_INFO_CHANNELS if channel in labels] or [
        c for c in labels if c in KR36_BRIEF_INFO_CHANNELS_SET
    ]


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
    grouped: dict[str, list[dict[str, Any]]] = {channel: [] for channel in KR36_BRIEF_INFO_CHANNELS}
    for article in articles:
        if _normalize_bucket(article) != BUCKET_INFO:
            continue
        if not _kr36_brief_info_article_in_scope(article):
            continue
        url = str(article.get("url") or "").strip()
        channel = str(article.get("channel") or "").strip()
        if channel not in grouped:
            channel = str(url_channel_map.get(url) or "").strip()
        if channel not in grouped and "search/articles" in url.lower():
            channel = "36氪独家"
        if channel not in grouped:
            continue
        grouped[channel].append(article)

    blocks: list[str] = []
    for channel in KR36_BRIEF_INFO_CHANNELS:
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

    def _clean_analysis_text(text: str) -> str:
        cleaned = str(text or "").strip()
        # 清理模型/前端截断痕迹，避免页面出现 "..."。
        cleaned = re.sub(r"\s*\.\.\.\s*$", "", cleaned)
        cleaned = re.sub(r"\s*…+\s*$", "", cleaned)
        cleaned = cleaned.replace("... [为简报截断]", "").replace("... [truncated by topic_fulltext_index]", "")
        return cleaned.strip()

    def _build_impact_fallback(seed_text: str) -> str:
        seed = _first_complete_sentence(_clean_analysis_text(seed_text), max_chars=120).strip()
        if not seed:
            return "结合全文判断，该事件已对相关主体的业务节奏与行业预期形成现实影响，后续以新增数据持续校验。"
        return f"结合正文判断，{seed}，其影响将继续传导至相关业务节奏与行业预期，需用后续数据持续验证。"

    def _build_counterpoint_fallback(seed_text: str) -> str:
        seed = _first_complete_sentence(_clean_analysis_text(seed_text), max_chars=120).strip()
        if not seed:
            return "反面情形在于关键前提未兑现或后续数据与当前叙事背离，需持续跟踪证据链并动态修正判断。"
        return (
            f"反面情形在于：若“{seed}”对应的关键前提未兑现，或后续数据与当前叙事背离，"
            "则结论需要下修并重估。"
        )

    text_by_subtitle: dict[str, str] = {}
    for subtitle, items in subsection_map.items():
        if subtitle in {"源地址", "补充地址", "增量信息", "需要继续跟踪的点"}:
            continue
        if not isinstance(items, list) or not items:
            continue
        text = " ".join(str(item).strip() for item in items if str(item).strip())
        text = _clean_analysis_text(text)
        if text:
            text_by_subtitle[str(subtitle)] = text

    fact_text = text_by_subtitle.get("事实", "")
    desc_text = text_by_subtitle.get("描述", "")
    if fact_text and desc_text and _clean_analysis_text(fact_text) == _clean_analysis_text(desc_text):
        text_by_subtitle.pop("描述", None)

    seed_text = " ".join(
        text_by_subtitle.get(key, "")
        for key in ("事实", "背景", "背景/原因", "核心判断", "描述")
    ).strip()
    if "产生的影响" not in text_by_subtitle:
        text_by_subtitle["产生的影响"] = _build_impact_fallback(seed_text)
    if "反面观点 / 数据矛盾点" not in text_by_subtitle:
        text_by_subtitle["反面观点 / 数据矛盾点"] = _build_counterpoint_fallback(seed_text)

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
            text = text_by_subtitle.get(str(subtitle), "")
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

    existing_titles = {str(subtitle) for subtitle in subsection_map.keys()}
    for subtitle in ("产生的影响", "反面观点 / 数据矛盾点"):
        if subtitle in existing_titles:
            continue
        text = text_by_subtitle.get(subtitle, "").strip()
        if not text:
            continue
        blocks.append(
            '<div class="block">'
            f'<h4 class="block-title">{_html.escape(subtitle)}</h4>'
            f'<p class="block-text">{_html.escape(text)}</p>'
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
    if raw_articles and step6_path is not None:
        raw_articles = _patch_articles_for_kr36_brief(raw_articles, step6_path)
    url_bucket_map = _build_url_bucket_map(raw_articles)
    url_article_map = _build_url_article_map(raw_articles)
    article_viewpoint_map = _load_article_viewpoint_map(step6_path)
    topic_articles = [
        a
        for a in raw_articles
        if _normalize_bucket(a) == BUCKET_TOPIC and not _kr36_brief_info_article_in_scope(a)
    ]
    activity_articles = _filter_kr36_visible_activities(
        _filter_articles_by_bucket(raw_articles, BUCKET_ACTIVITY)
    )
    info_articles = [
        a
        for a in raw_articles
        if _normalize_bucket(a) == BUCKET_INFO and _kr36_brief_info_article_in_scope(a)
    ]

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

    step5_full_flat: dict[str, list[tuple[str, str, str]]] | None = None
    step5_subclass_by_bucket: (
        dict[str, list[tuple[str, list[tuple[str, str, str]]]]] | None
    ) = None
    if step6_path:
        _step5 = _load_step5_per_item_points_by_bucket(
            step6_path,
            url_article_map=url_article_map,
            max_activity_subitems=KR36_ACTIVITY_VISIBLE_MAX,
        )
        if _step5 is not None:
            step5_full_flat, step5_subclass_by_bucket = _step5
    per_item_by_bucket: dict[str, list[tuple[str, str, str]]] | None = (
        step5_full_flat if omit_source_links else None
    )
    if omit_source_links:
        if per_item_by_bucket is not None:
            topic_points = per_item_by_bucket.get(BUCKET_TOPIC) or _build_bucket_points(
                sections=topic_analysis_sections, fallback_articles=[]
            )
            activity_points = per_item_by_bucket.get(BUCKET_ACTIVITY) or _build_bucket_points(
                sections=activity_analysis_sections, fallback_articles=[]
            )
            _info_step5 = per_item_by_bucket.get(BUCKET_INFO)
            if _info_step5 is None:
                info_points = _build_bucket_points(
                    sections=info_analysis_sections, fallback_articles=[]
                )
            else:
                info_points = _info_step5
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

    def _merge_subclass_for_bucket(
        bucket: str, points: list[tuple[str, str, str]]
    ) -> list[tuple[str, list[tuple[str, str, str]]]] | None:
        if step5_subclass_by_bucket and step5_subclass_by_bucket.get(bucket):
            sg = step5_subclass_by_bucket[bucket]
            if bucket == BUCKET_INFO:
                return _sort_kr36_brief_info_subclass_groups(sg)
            return sg
        br = _subclass_groups_from_bracket_labels(points)
        if bucket == BUCKET_INFO and br:
            return _sort_kr36_brief_info_subclass_groups(br)
        return br

    topic_subclass_groups = _merge_subclass_for_bucket(BUCKET_TOPIC, topic_points)
    info_subclass_groups = _merge_subclass_for_bucket(BUCKET_INFO, info_points)

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
    topic_summary = _section_blurb_cap(
        topic_summary, max_chars=KR36_TOPIC_BRIEF_SUMMARY_MAX_CHARS
    )

    if omit_source_links:
        # 活动区在邮件卡片里直接展示原始活动信息，不再渲染总结概览。
        activity_summary = ""
    else:
        activity_summary = _build_bucket_summary(
            bucket_title=BUCKET_ACTIVITY,
            sections=activity_analysis_sections,
            intro_text=section_intros.get("activity", ""),
            raw_article_count=len(activity_articles),
            activity_sources=activity_sources,
        )

    info_rollup = _synthesize_step5_info_rollup_from_all_info_items(step6_path) if step6_path else ""
    if omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_INFO):
        if len(info_points) <= 1:
            info_summary = ""
        elif (info_rollup or "").strip():
            info_summary = _section_blurb_cap(
                str(info_rollup or "").strip(), max_chars=KR36_INFO_SECTION_BLURB_MAX_CHARS
            )
        else:
            info_summary = _build_omit_info_comprehensive_summary(
                info_points,
                intro_text=section_intros.get("info", ""),
                max_chars=KR36_INFO_SECTION_BLURB_MAX_CHARS,
            )
    elif (info_rollup or "").strip():
        ir = (info_rollup or "").strip()
        intro_i = (section_intros.get("info", "") or "").strip()
        info_summary = _normalize_sentence(f"{intro_i} {ir}") if intro_i else ir
    else:
        info_summary = _build_bucket_summary(
            bucket_title=BUCKET_INFO,
            sections=info_analysis_sections,
            intro_text=section_intros.get("info", ""),
            raw_article_count=len(info_articles),
        )
    if not (omit_source_links and per_item_by_bucket and per_item_by_bucket.get(BUCKET_INFO)):
        info_summary = _section_blurb_cap(
            info_summary, max_chars=KR36_INFO_SECTION_BLURB_MAX_CHARS
        )

    bucket_blocks: list[dict[str, object]] = [
        {
            "title": BUCKET_TOPIC,
            "summary": topic_summary,
            "points": topic_points,
            "subclass_groups": topic_subclass_groups,
            "sources": topic_sources,
        },
        {
            "title": BUCKET_ACTIVITY,
            "summary": activity_summary,
            "points": activity_points,
            "subclass_groups": None,
            "sources": activity_sources,
        },
        {
            "title": BUCKET_INFO,
            "summary": info_summary,
            "points": info_points,
            "subclass_groups": info_subclass_groups,
            "sources": info_sources,
        },
    ]

    lead_html = f'<p class="lead">{_html.escape(lead_text.strip())}</p>' if lead_text.strip() else ""
    def _subclass_arg(
        block: dict[str, object],
    ) -> list[tuple[str, list[tuple[str, str, str]]]] | None:
        sg = block.get("subclass_groups")
        return sg if isinstance(sg, list) else None

    section_html = "".join(
        _render_bucket_section_html(
            title=str(block["title"]),
            summary=str(block["summary"]),
            points=block.get("points", []) if isinstance(block.get("points", []), list) else [],
            sources=block.get("sources", []) if isinstance(block.get("sources", []), list) else [],
            show_topic_activity_viewpoints=omit_source_links,
            subclass_groups=_subclass_arg(block),
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
      .section-groups{{margin:4px 0 0 0;}}
      .subclass-block{{margin:12px 0 4px;}}
      .subclass-block:first-child{{margin-top:0;}}
      .subclass-heading{{
        font-size:16px;font-weight:700;color:#102235;
        margin:0 0 6px;padding:0;line-height:1.5;
      }}
      .viewpoints-list{{margin:0;padding-left:20px;list-style:decimal;}}
      .viewpoints-list > li{{
        margin:6px 0;line-height:1.8;color:#314457;
        font-size:14px;list-style-position:outside;
      }}
      .viewpoints-list > li:first-child{{margin-top:0;}}
      .viewpoints-list > li:last-child{{margin-bottom:0;}}
      .viewpoints{{margin:0;padding-left:18px;}}
      .viewpoints li{{margin:12px 0;line-height:1.8;color:#314457;font-size:14px;list-style-position:outside;}}
      .viewpoints-no-index{{padding-left:0;list-style:none;}}
      .point-topic{{font-weight:700;color:#2d4d69;display:inline;margin-bottom:0;}}
      .point-topic .point-topic-link{{color:#0f5ea8;text-decoration:none;}}
      .point-topic .point-topic-link:hover{{text-decoration:underline;}}
      .vp-inline{{display:inline;color:#314457;}}
      .vp-chapter-inline{{display:inline;font-weight:700;color:#1a3a52;margin-right:2px;}}
      .vp-label{{font-weight:700;color:#1a3a52;margin-right:2px;}}
      .vp-main{{color:#1a3a52;font-weight:700;}}
      .vp-body{{color:#314457;}}
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
      .activity-card-title-omit a{{color:#0f5ea8;text-decoration:none;}}
      .activity-card-title-omit a:hover{{text-decoration:underline;}}
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
