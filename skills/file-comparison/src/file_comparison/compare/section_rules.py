"""章节级过滤规则。"""

from __future__ import annotations

from typing import Any


def section_matches_pattern(section: Any, patterns: tuple[str, ...]) -> str:
    """返回章节标题命中的跳过规则；未命中时返回空字符串。"""
    title = str(getattr(section, "title", "")).strip()
    for pattern in patterns:
        if pattern and pattern in title:
            return pattern
    return ""


def signature_tail_match_index(lines: list[str], patterns: tuple[str, ...]) -> tuple[int | None, str]:
    """定位正文中签署页尾巴的起始行。"""
    for index, line in enumerate(lines):
        stripped = line.strip()
        for pattern in patterns:
            if pattern and pattern in stripped:
                return index, pattern
    return None, ""


def apply_section_skip_rules(sections: list[Any], patterns: tuple[str, ...]) -> tuple[list[Any], list[dict[str, object]]]:
    """按配置剔除签署页等非正文内容，并返回可落盘的跳过记录。"""
    if not patterns:
        return sections, []
    filtered_sections = []
    records: list[dict[str, object]] = []
    for section in sections:
        title_pattern = section_matches_pattern(section, patterns)
        if title_pattern:
            records.append(
                {
                    "section_number": section.number,
                    "section_title": section.title,
                    "action": "skip_section",
                    "reason": "signature_page",
                    "matched_pattern": title_pattern,
                    "removed_line_count": len(section.body.splitlines()),
                }
            )
            continue
        lines = section.body.splitlines()
        tail_index, body_pattern = signature_tail_match_index(lines, patterns)
        if tail_index is None:
            filtered_sections.append(section)
            continue
        kept_lines = lines[:tail_index]
        records.append(
            {
                "section_number": section.number,
                "section_title": section.title,
                "action": "trim_tail",
                "reason": "signature_page",
                "matched_pattern": body_pattern,
                "removed_line_count": len(lines) - tail_index,
            }
        )
        if kept_lines:
            filtered_sections.append(type(section)(number=section.number, title=section.title, body="\n".join(kept_lines).strip()))
    return filtered_sections, records
