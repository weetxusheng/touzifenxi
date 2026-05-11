
"""compare unit 构建与章节压缩。

职责：unit 形态送模候选与 `preprocess_sections_for_llm`。
不负责：正式引擎 compare block 主路径（见 `compare_blocks`）。
"""

from __future__ import annotations

from ..extractor import full_body_without_title, ordered_unique_section_numbers
from ..models import CompareUnit, ComparisonRow, Section
from .compare_blocks import build_candidate_section, row_display_text
from .rule_rows import build_comparison_row, build_section_rows

def build_compare_units_for_llm(
    old_sections: list[Section],
    new_sections: list[Section],
) -> tuple[list[CompareUnit], list[dict[str, object]]]:
    """把章节差异拆成条目级单元，并返回可审计的预处理摘要。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers = ordered_unique_section_numbers(old_sections, new_sections)

    compare_units: list[CompareUnit] = []
    chapter_summaries: list[dict[str, object]] = []
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        chapter_title = old_section.title if old_section is not None else new_section.title
        rows: list[ComparisonRow] = []
        if old_section is None and new_section is not None:
            rows = [build_comparison_row(new_section.title, "新增", full_body_without_title(new_section))]
        elif new_section is None and old_section is not None:
            rows = [build_comparison_row(old_section.title, full_body_without_title(old_section), "删除")]
        elif old_section is not None and new_section is not None:
            rows = build_section_rows(old_section, new_section)
        chapter_units: list[CompareUnit] = []
        for row_index, row in enumerate(rows, start=1):
            old_display = row_display_text(row.old_text, row.subchapter)
            new_display = row_display_text(row.new_text, row.subchapter)
            chapter_units.append(
                CompareUnit(
                    unit_id=f"{number}-unit-{row_index:03d}",
                    chapter_number=number,
                    chapter_title=row.chapter or chapter_title,
                    subchapter=row.subchapter,
                    old_text=old_display,
                    new_text=new_display,
                )
            )
        compare_units.extend(chapter_units)
        chapter_summaries.append(
            {
                "chapter_number": number,
                "chapter_title": chapter_title,
                "original_old_chars": len(old_section.body) if old_section is not None else 0,
                "original_new_chars": len(new_section.body) if new_section is not None else 0,
                "unit_count": len(chapter_units),
                "sent_old_chars": sum(len(unit.old_text) for unit in chapter_units),
                "sent_new_chars": sum(len(unit.new_text) for unit in chapter_units),
                "kept_for_llm": bool(chapter_units),
                "reason": (
                    "added"
                    if old_section is None and new_section is not None
                    else "deleted"
                    if new_section is None and old_section is not None
                    else "changed"
                    if chapter_units
                    else "unchanged"
                ),
            }
        )
    return compare_units, chapter_summaries

def preprocess_sections_for_llm(
    old_sections: list[Section],
    new_sections: list[Section],
) -> tuple[list[Section], list[Section]]:
    """在调用模型前先剔除完全不变章节并压缩候选输入。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers = ordered_unique_section_numbers(old_sections, new_sections)

    reduced_old: list[Section] = []
    reduced_new: list[Section] = []
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        if old_section is None and new_section is not None:
            reduced_old.append(Section(number=new_section.number, title=new_section.title, body=f"{new_section.title}\n新增"))
            reduced_new.append(new_section)
            continue
        if new_section is None and old_section is not None:
            reduced_old.append(old_section)
            reduced_new.append(Section(number=old_section.number, title=old_section.title, body=f"{old_section.title}\n删除"))
            continue
        if old_section is None or new_section is None:
            continue
        if old_section.title != new_section.title:
            reduced_old.append(old_section)
            reduced_new.append(new_section)
            continue
        rows = build_section_rows(old_section, new_section)
        if not rows:
            continue
        candidate_old = build_candidate_section(old_section, rows, side="old")
        candidate_new = build_candidate_section(new_section, rows, side="new")
        if candidate_old is None or candidate_new is None:
            reduced_old.append(old_section)
            reduced_new.append(new_section)
            continue
        reduced_old.append(candidate_old)
        reduced_new.append(candidate_new)
    return reduced_old, reduced_new
