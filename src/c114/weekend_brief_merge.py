"""周一默认跑周六+周日时，将两天 Step6 Markdown 按主题与子章节合并为一篇。"""

from __future__ import annotations

import re
from datetime import date

_SUBSECTION_ORDER = (
    "核心判断",
    "增量信息",
    "产业/公司影响",
    "需要继续跟踪的点",
    "源地址",
    "补充地址",
)

_YMD = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")


def _norm_ymd(s: str) -> str | None:
    m = _YMD.search(s or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _parse_link_bullet(line: str) -> tuple[str, str, str] | None:
    """解析 `- 标题 | 日期? | URL` 或 `- 标题 | URL`（从右侧切分，避免标题中含 |）。"""
    raw = line.strip()
    if not raw.startswith("- "):
        return None
    body = raw[2:].strip()
    parts = body.rsplit(" | ", 2)
    if len(parts) == 3:
        title, mid, url = parts[0].strip(), parts[1].strip(), parts[2].strip()
        return title, mid, url
    if len(parts) == 2:
        return parts[0].strip(), "", parts[1].strip()
    return None


def _render_link_bullet(title: str, date_part: str, url: str) -> str:
    url = url.strip()
    title = title.strip()
    if date_part.strip():
        return f"- {title} | {date_part.strip()} | {url}"
    return f"- {title} | {url}"


def _norm_title_for_match(title: str) -> str:
    t = (title or "").strip().lower()
    # 统一去掉常见站点后缀与分隔噪声，便于“源/补充”跨站同文去重。
    for token in ("_新浪科技_新浪网", "_转载", "新浪科技", "新浪网", "通信", "光纤"):
        t = t.replace(token, " ")
    t = re.sub(r"[\s\|\-_/：:（）()【】\[\]，,。\.]+", "", t)
    return t


def _link_key(url: str) -> str:
    return url.strip().lower()


def _merge_link_subsections(a: str, b: str, fallback_dates: tuple[str, str]) -> str:
    """合并两个「源地址」或「补充地址」小节文本，按 URL 去重，保留顺序：先 a 后 b。"""
    da, db = fallback_dates
    seen: set[str] = set()
    out_lines: list[str] = []

    def ingest(block: str, fallback_date: str) -> None:
        for line in block.splitlines():
            s = line.strip()
            if not s or s == "- 无":
                continue
            parsed = _parse_link_bullet(line)
            if parsed:
                title, mid, url = parsed
                key = _link_key(url)
                if key in seen:
                    continue
                seen.add(key)
                date_part = mid.strip()
                if not date_part or date_part == "日期未知":
                    date_part = _norm_ymd(f"{title} {mid}") or fallback_date
                elif _norm_ymd(date_part):
                    date_part = _norm_ymd(date_part) or date_part
                out_lines.append(_render_link_bullet(title, date_part, url))
            else:
                out_lines.append(line)

    ingest(a, da)
    ingest(b, db)
    if not out_lines:
        return "- 无"
    return "\n".join(out_lines)


def _collect_source_signatures(block: str, fallback_date: str) -> list[tuple[str, str, str]]:
    """收集源地址签名：(url_key, normalized_date, normalized_title)。"""
    out: list[tuple[str, str, str]] = []
    for line in block.splitlines():
        parsed = _parse_link_bullet(line)
        if not parsed:
            continue
        title, mid, url = parsed
        date_part = _norm_ymd(mid) or fallback_date
        out.append((_link_key(url), date_part, _norm_title_for_match(title)))
    return out


def _is_supp_duplicate_source(title: str, date_part: str, url: str, source_signatures: list[tuple[str, str, str]]) -> bool:
    """判断补充链接是否与源地址重复（同 URL 或同日且标题主干高度重合）。"""
    u = _link_key(url)
    d = _norm_ymd(date_part) or date_part
    t = _norm_title_for_match(title)
    for su, sd, st in source_signatures:
        if u and u == su:
            return True
        if d and sd and d == sd and t and st:
            if t == st:
                return True
            # 同日标题包含关系，视为同一事件复述（如 C114 原文 vs 门户转载）。
            if (len(t) >= 8 and len(st) >= 8) and (t in st or st in t):
                return True
    return False


def _merge_text_blocks(a: str, b: str) -> str:
    a, b = (a or "").strip(), (b or "").strip()
    if a and b:
        return f"{a}\n\n{b}"
    return a or b


def _parse_step6_topics(md: str) -> tuple[str, list[str], dict[str, dict[str, str]]]:
    """返回 (一级标题不含换行, 运行摘要行列表不含 ## 标题行, topics[主题名][小节]=正文)。"""
    lines = md.splitlines()
    h1 = ""
    i = 0
    if lines and lines[0].startswith("# "):
        h1 = lines[0][2:].strip()
        i = 1

    summary_lines: list[str] = []
    topics: dict[str, dict[str, str]] = {}
    mode: str | None = None
    current_topic: str | None = None
    current_sub: str | None = None
    buf: list[str] = []

    def flush_sub() -> None:
        nonlocal buf, current_topic, current_sub
        if current_topic and current_sub is not None:
            topics.setdefault(current_topic, {})[current_sub] = "\n".join(buf).strip()
        buf = []

    while i < len(lines):
        line = lines[i]
        if line.startswith("## ") and not line.startswith("###"):
            flush_sub()
            name = line[3:].strip()
            if name == "运行摘要":
                mode = "summary"
                current_topic = None
                current_sub = None
            else:
                mode = "topic"
                current_topic = name
                topics.setdefault(name, {})
                current_sub = None
            i += 1
            continue
        if line.startswith("### ") and mode == "topic" and current_topic:
            flush_sub()
            current_sub = line[4:].strip()
            buf = []
            i += 1
            continue
        if mode == "summary":
            summary_lines.append(line)
        elif mode == "topic" and current_sub is not None:
            buf.append(line)
        i += 1
    flush_sub()
    return h1, summary_lines, topics


def _merge_running_summaries(sat_lines: list[str], sun_lines: list[str]) -> list[str]:
    """对运行摘要中的「- xxx：数字」行做相加；其余行保留周六块中的非空行。"""
    num_pat = re.compile(r"^-\s*(.+?)：(\d+)\s*$")

    def collect_numeric(lines: list[str]) -> dict[str, int]:
        acc: dict[str, int] = {}
        for line in lines:
            raw = line.strip()
            m = num_pat.match(raw)
            if m:
                label = m.group(1).strip()
                acc[label] = acc.get(label, 0) + int(m.group(2))
        return acc

    sat_n = collect_numeric(sat_lines)
    sun_n = collect_numeric(sun_lines)
    order: list[str] = []
    for lab in sat_n:
        order.append(lab)
    for lab in sun_n:
        if lab not in order:
            order.append(lab)
    out: list[str] = []
    for lab in order:
        total = sat_n.get(lab, 0) + sun_n.get(lab, 0)
        out.append(f"- {lab}：{total}")
    if out:
        return out
    return sat_lines or sun_lines


def merge_weekend_step6_markdown(sat_text: str, sun_text: str, saturday: date, sunday: date) -> str:
    """按主题与子章节合并两天简报；主标题为日期区间。"""
    _, sat_sum, sat_topics = _parse_step6_topics(sat_text)
    _, sun_sum, sun_topics = _parse_step6_topics(sun_text)

    sat_iso, sun_iso = saturday.isoformat(), sunday.isoformat()
    title = f"C114 主题简报（{sat_iso} 至 {sun_iso}）"

    merged_summary = _merge_running_summaries(sat_sum, sun_sum)

    topic_order: list[str] = []
    seen: set[str] = set()
    for name in sat_topics:
        topic_order.append(name)
        seen.add(name)
    for name in sun_topics:
        if name not in seen:
            topic_order.append(name)
            seen.add(name)

    chunks: list[str] = [f"# {title}", "", "## 运行摘要", ""]
    chunks.extend(merged_summary if merged_summary else sat_sum + sun_sum)
    if chunks[-1] != "":
        chunks.append("")

    for topic in topic_order:
        st = sat_topics.get(topic, {})
        sn = sun_topics.get(topic, {})
        source_signatures: list[tuple[str, str, str]] = []
        chunks.append("")
        chunks.append(f"## {topic}")
        chunks.append("")
        for sub in _SUBSECTION_ORDER:
            if sub not in st and sub not in sn:
                continue
            chunks.append(f"### {sub}")
            chunks.append("")
            a = st.get(sub, "").strip()
            b = sn.get(sub, "").strip()
            if sub == "源地址":
                body = _merge_link_subsections(a, b, (sat_iso, sun_iso))
                source_signatures = _collect_source_signatures(body, sun_iso)
                chunks.append(body)
            elif sub == "补充地址":
                raw_body = _merge_link_subsections(a, b, (sat_iso, sun_iso))
                filtered: list[str] = []
                for line in raw_body.splitlines():
                    parsed = _parse_link_bullet(line)
                    if not parsed:
                        if line.strip():
                            filtered.append(line)
                        continue
                    title, mid, url = parsed
                    d = _norm_ymd(mid) or sun_iso
                    if _is_supp_duplicate_source(title, d, url, source_signatures):
                        continue
                    filtered.append(_render_link_bullet(title, d, url))
                body = "\n".join(filtered) if filtered else "- 无"
                chunks.append(body)
            else:
                body = _merge_text_blocks(a, b)
                if body:
                    chunks.append(body)
            chunks.append("")

    while chunks and chunks[-1] == "":
        chunks.pop()
    return "\n".join(chunks).strip() + "\n"


__all__ = ["merge_weekend_step6_markdown"]
