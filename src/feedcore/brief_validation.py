"""Validate four-dimension briefs for downstream filtering."""

from __future__ import annotations

import re

# Model must output this alone when it cannot fill all four dimensions substantively.
BRIEF_REJECT_INCOMPLETE_SENTINEL = "BRIEF_REJECTED_INCOMPLETE_DIMENSIONS"

_BRIEF_LENGTH_BUDGET_OVERHEAD = 60
_BRIEF_LENGTH_BUDGET_MIN = 260
_SECTION_LABELS = {
    "fact": "事实",
    "bg": "背景",
    "impact": "产生的影响",
    "contra": "反面观点 / 数据矛盾点",
}

_PLACEHOLDER_EXACT = frozenset(
    {
        "正文未涉及",
        "正文未涉及。",
        "需其它来源核实",
        "需其它来源核实。",
        "需核实",
        "需核实。",
        "正文未见明显矛盾口径",
        "正文未见明显矛盾口径。",
        "（正文未单独交代背景脉络。）",
        "（正文未明确归纳影响。）",
        "（正文未见对立表述或数据冲突。）",
        "（未能从正文提炼可核实要点。）",
        "（本地模式未能从正文形成连贯总括；可改用模型获取完整归纳。）",
    }
)

# Section headers we require (#### level).
_HEADER_FACT = re.compile(r"^####\s*事实\s*$")
_HEADER_BG = re.compile(r"^####\s*背景\s*$")
_HEADER_IMPACT = re.compile(r"^####\s*产生的影响\s*$")
_HEADER_CONTRA = re.compile(r"^####\s*反面观点")

_HEADERS: list[tuple[str, re.Pattern[str]]] = [
    ("fact", _HEADER_FACT),
    ("bg", _HEADER_BG),
    ("impact", _HEADER_IMPACT),
    ("contra", _HEADER_CONTRA),
]


def _strip_bullet_prefix(line: str) -> str:
    t = line.strip()
    for prefix in ("- ", "* ", "• "):
        if t.startswith(prefix):
            return t[len(prefix) :].strip()
    mo = re.match(r"^\d{1,2}[.)]\s+", t)
    if mo:
        return t[mo.end() :].strip()
    return t


def _line_is_placeholder_only(line: str) -> bool:
    t = _strip_bullet_prefix(line)
    if not t:
        return True
    if t in _PLACEHOLDER_EXACT:
        return True
    # Local brief / single-line bracket placeholders
    if re.fullmatch(r"（[^）]{2,120}）", t):
        return True
    # Minimax-style single-line fillers
    if re.fullmatch(r"(正文未涉及|需其它来源核实|正文未见明显矛盾口径)[。]?", t):
        return True
    return False


def _section_has_substantive_line(body: str) -> bool:
    if not (body or "").strip():
        return False
    for line in body.splitlines():
        if not _line_is_placeholder_only(line):
            return True
    return False


def _parse_four_sections(brief_md: str) -> dict[str, str]:
    lines = brief_md.splitlines()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        s = line.strip()
        key: str | None = None
        for name, pat in _HEADERS:
            if pat.match(s):
                key = name
                break
        if key is not None:
            current = key
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _normalized_char_count(text: str) -> int:
    return len(" ".join((text or "").split()))


def brief_has_reasonable_length(brief_md: str, source_text: str) -> bool:
    """True when the brief does not grow materially beyond the source article text."""
    brief_chars = _normalized_char_count(brief_md)
    source_chars = _normalized_char_count(source_text)
    if brief_chars <= 0 or source_chars <= 0:
        return False
    budget = source_chars + _BRIEF_LENGTH_BUDGET_OVERHEAD
    budget = max(_BRIEF_LENGTH_BUDGET_MIN, budget)
    return brief_chars <= budget


def compact_four_dimension_brief(brief_md: str, source_text: str) -> str:
    raw = (brief_md or "").strip()
    if not raw or raw == BRIEF_REJECT_INCOMPLETE_SENTINEL:
        return raw
    source_chars = _normalized_char_count(source_text)
    target_budget = _compact_budget(source_chars)
    if source_chars > 500 and _normalized_char_count(raw) <= target_budget:
        return raw

    sections = _parse_four_section_items(raw)
    profiles = _compact_profiles(source_chars)
    for profile in profiles:
        candidate = _compose_compact_brief(sections, profile)
        if _normalized_char_count(candidate) <= target_budget:
            return candidate
    return _compose_compact_brief(sections, profiles[-1])


def _parse_four_section_items(brief_md: str) -> dict[str, list[str]]:
    parsed = _parse_four_sections(brief_md)
    out: dict[str, list[str]] = {}
    for key in ("fact", "bg", "impact", "contra"):
        items: list[str] = []
        for line in parsed.get(key, "").splitlines():
            item = _normalize_compact_text(_strip_bullet_prefix(line))
            if not item or _line_is_placeholder_only(item):
                continue
            items.append(item)
        out[key] = _dedupe_preserve_order(items)
    return out


def _compact_profiles(source_chars: int) -> list[dict[str, int]]:
    if source_chars <= 140:
        return [
            {"fact_count": 1, "fact_len": 24, "other_len": 18},
            {"fact_count": 1, "fact_len": 20, "other_len": 15},
        ]
    if source_chars <= 260:
        return [
            {"fact_count": 1, "fact_len": 30, "other_len": 22},
            {"fact_count": 1, "fact_len": 24, "other_len": 18},
        ]
    if source_chars <= 500:
        return [
            {"fact_count": 1, "fact_len": 38, "other_len": 28},
            {"fact_count": 1, "fact_len": 30, "other_len": 22},
        ]
    if source_chars <= 900:
        return [
            {"fact_count": 2, "fact_len": 44, "other_len": 32},
            {"fact_count": 1, "fact_len": 36, "other_len": 26},
        ]
    return [
        {"fact_count": 2, "fact_len": 56, "other_len": 40},
        {"fact_count": 1, "fact_len": 42, "other_len": 30},
    ]


def _compact_budget(source_chars: int) -> int:
    if source_chars <= 140:
        return 140
    if source_chars <= 260:
        return 170
    if source_chars <= 500:
        return 220
    if source_chars <= 900:
        return 300
    return 420


def _compose_compact_brief(sections: dict[str, list[str]], profile: dict[str, int]) -> str:
    lines: list[str] = []
    for key in ("fact", "bg", "impact", "contra"):
        lines.append(f"#### {_SECTION_LABELS[key]}")
        lines.extend(_section_lines(key, sections.get(key, []), profile))
        lines.append("")
    return "\n".join(lines).rstrip()


def _section_lines(section: str, items: list[str], profile: dict[str, int]) -> list[str]:
    count = profile["fact_count"] if section == "fact" else 1
    max_chars = profile["fact_len"] if section == "fact" else profile["other_len"]
    selected = _select_compact_items(items, count=count, max_chars=max_chars, prefer_data=section == "fact")
    if not selected:
        selected = [_fallback_section_line(section, max_chars=max_chars)]
    return [f"- {item}" for item in selected]


def _select_compact_items(items: list[str], *, count: int, max_chars: int, prefer_data: bool) -> list[str]:
    ranked = sorted(items, key=lambda item: _compact_item_score(item, prefer_data=prefer_data), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for item in ranked:
        compact = _condense_item(item, max_chars=max_chars, prefer_data=prefer_data)
        if not compact or compact in seen:
            continue
        out.append(compact)
        seen.add(compact)
        if len(out) >= count:
            break
    return out


def _compact_item_score(text: str, *, prefer_data: bool) -> tuple[int, int]:
    clean = _normalize_compact_text(text)
    digit_bonus = sum(ch.isdigit() for ch in clean) * 3
    keyword_bonus = sum(token in clean for token in ("年", "月", "%", "亿美元", "万人", "发布", "宣布", "上涨", "下跌")) * 2
    data_bonus = digit_bonus + keyword_bonus if prefer_data else keyword_bonus
    return (data_bonus, -abs(len(clean) - 28))


def _condense_item(text: str, *, max_chars: int, prefer_data: bool) -> str:
    clean = _normalize_compact_text(text)
    if not clean:
        return ""
    clauses = _split_clauses(clean)
    if prefer_data:
        clauses = sorted(clauses, key=lambda item: _compact_item_score(item, prefer_data=True), reverse=True)
    candidate = clauses[0] if clauses else clean
    candidate = re.sub(r"^(报道称|报道指出|文章称|文中称|The article|The report)\s*", "", candidate, flags=re.I)
    candidate = candidate.strip("，,；;。:： ")
    if len(candidate) > max_chars:
        candidate = _truncate_compact_text(candidate, max_chars=max_chars)
    return candidate


def _truncate_compact_text(text: str, *, max_chars: int) -> str:
    prefix = text[:max_chars].rstrip("，,；;。:： ")
    clause_boundary = re.sub(r"[，、；：,;:][^，、；：,;:]*$", "", prefix).rstrip("，,；;。:： ")
    if clause_boundary != prefix and len(clause_boundary) >= max(8, max_chars // 2):
        return clause_boundary
    return _trim_trailing_soft_suffixes(prefix) or prefix


def _trim_trailing_soft_suffixes(text: str) -> str:
    candidate = text.rstrip("，,；;。:： ")
    for suffix in ("可能", "以及", "和", "与", "及", "或"):
        if candidate.endswith(suffix) and len(candidate) - len(suffix) >= 8:
            return candidate[: -len(suffix)].rstrip("，,；;。:： ")
    return candidate


def _split_clauses(text: str) -> list[str]:
    primary = [part.strip() for part in re.split(r"[。；;！？?!]", text) if part.strip()]
    out: list[str] = []
    for part in primary or [text]:
        if len(part) > 32:
            out.extend(piece.strip() for piece in re.split(r"[，,：:]", part) if piece.strip())
        else:
            out.append(part)
    return out or [text]


def _normalize_compact_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _fallback_section_line(section: str, *, max_chars: int) -> str:
    mapping = {
        "fact": "保留原文核心事实",
        "bg": "原文背景信息较少",
        "impact": "可见影响仍待补充",
        "contra": "原文争议信息有限",
    }
    return mapping[section][:max_chars]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def brief_passes_four_dimension_gate(brief_md: str) -> bool:
    """True only if all four sections exist and each has at least one non-placeholder line."""
    raw = (brief_md or "").strip()
    if not raw:
        return False
    first = raw.splitlines()[0].strip() if raw.splitlines() else ""
    if raw == BRIEF_REJECT_INCOMPLETE_SENTINEL or first == BRIEF_REJECT_INCOMPLETE_SENTINEL:
        return False
    sections = _parse_four_sections(raw)
    for key in ("fact", "bg", "impact", "contra"):
        body = sections.get(key, "")
        if not _section_has_substantive_line(body):
            return False
    return True
