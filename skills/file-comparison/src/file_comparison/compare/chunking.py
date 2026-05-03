"""负责按层级切分章节、过滤不变内容并生成对照候选块。"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable

from .extractor import clean_lines, full_body_without_title
from .models import ChapterBatch, CompareUnit, ComparisonRow, Section

INNER_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（(?:[一二三四五六七八九十]+|\d+)）)")
CHINESE_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
DISPLAY_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、)")
DECIMAL_HEADING_RE = re.compile(r"^\d+、")


def active_display_heading_re(lines: list[str]) -> re.Pattern[str]:
    """返回当前章节用于切分表格二级内容的标题规则。"""
    if any(CHINESE_HEADING_RE.match(line) for line in lines):
        return CHINESE_HEADING_RE
    return DISPLAY_HEADING_RE
OMITTED_EQUAL_MARKER = "……"


def heading_context(lines: list[str], index: int) -> str | None:
    """向上回溯当前行所属的最近内部标题。"""
    heading_re = active_display_heading_re(lines)
    for current in range(index, -1, -1):
        if heading_re.match(lines[current]):
            return lines[current]
    return None


def format_chunk(lines: list[str], start: int, end: int) -> str:
    """把一段行切片重新拼成带标题上下文的比较块。"""
    chunk = [line for line in lines[start:end] if line]
    context = heading_context(lines, start)
    if context and (not chunk or chunk[0] != context):
        chunk.insert(0, context)
    return "\n".join(chunk).strip()


def join_chunks(chunks: Iterable[str]) -> str:
    """把多个文本块按省略分隔符合并成一个展示块。"""
    return "\n……\n".join(chunk for chunk in chunks if chunk.strip())


@dataclass(slots=True)
class SubchapterInfo:
    """描述某段文本是否带有可复用的小标题。"""

    label: str
    consume_line: bool


def subchapter_info(text: str) -> SubchapterInfo | None:
    """识别文本首行是否是可单独提取的内部标题。"""
    if text in {"新增", "删除"}:
        return None
    lines = clean_lines(text)
    if not lines:
        return None
    match = DISPLAY_HEADING_RE.match(lines[0])
    if not match:
        return None
    first_line = lines[0]
    prefix = match.group(1)
    colon_index = first_line.find("：")
    if colon_index != -1 and colon_index <= 30:
        return SubchapterInfo(label=first_line[: colon_index + 1], consume_line=False)
    if len(first_line) <= 40:
        return SubchapterInfo(label=first_line, consume_line=True)
    return SubchapterInfo(label=prefix, consume_line=False)


def remove_first_inner_heading(text: str, *, consume_line: bool = True) -> str:
    """按规则移除首个内部标题行，保留正文。"""
    if text in {"新增", "删除"}:
        return text
    if not consume_line:
        return text
    lines = clean_lines(text)
    if lines and DISPLAY_HEADING_RE.match(lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def build_comparison_row(chapter: str, old_text: str, new_text: str) -> ComparisonRow:
    """把一对旧文/新文文本块包装成可展示的对照行。"""
    old_info = subchapter_info(old_text)
    new_info = subchapter_info(new_text)
    old_heading = old_info.label if old_info else None
    new_heading = new_info.label if new_info else None
    if old_info and new_info and old_heading != new_heading:
        return ComparisonRow(chapter=chapter, subchapter=new_heading, old_text=old_text, new_text=new_text)
    subchapter = new_heading or old_heading or ""
    if subchapter:
        old_body = remove_first_inner_heading(old_text, consume_line=old_info.consume_line if old_info else False)
        new_body = remove_first_inner_heading(new_text, consume_line=new_info.consume_line if new_info else False)
        return ComparisonRow(chapter=chapter, subchapter=subchapter, old_text=old_body or old_text, new_text=new_body or new_text)
    return ComparisonRow(chapter=chapter, old_text=old_text, new_text=new_text)


def split_blocks(lines: list[str]) -> list[list[str]]:
    """按内部标题把章节正文切成多个块。"""
    blocks: list[list[str]] = []
    current: list[str] = []
    heading_re = active_display_heading_re(lines)
    for line in lines:
        if heading_re.match(line) and current:
            blocks.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def block_label(block: str) -> str:
    """返回一个文本块用于展示的标签。"""
    info = subchapter_info(block)
    if info is not None:
        return info.label
    first_line = clean_lines(block)[0] if clean_lines(block) else block
    return first_line


def block_match_key(block: str) -> str:
    """返回用于左右文本块对齐的归一化键。"""
    label = block_label(block)
    key = INNER_HEADING_RE.sub("", label, count=1).strip()
    return key or label


def strip_leading_numbering(text: str) -> str:
    """去掉每行前缀编号，用于判断是否仅序号变化。"""
    lines = [INNER_HEADING_RE.sub("", line.strip(), count=1).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def line_content_key(line: str) -> str:
    """返回忽略开头编号后的行内容键，用于过滤编号顺延造成的伪变化。"""
    return INNER_HEADING_RE.sub("", line.strip(), count=1).strip()


def is_only_numbering_changed(old_text: str, new_text: str) -> bool:
    """判断两段文本是否只是编号变化、正文未变。"""
    if old_text in {"新增", "删除"} or new_text in {"新增", "删除"}:
        return False
    if old_text == new_text:
        return False
    return strip_leading_numbering(old_text) == strip_leading_numbering(new_text)


def first_line_matches_subchapter(first_line: str, subchapter: str) -> bool:
    """判断正文首行是否已经包含同一个二级标题，允许编号顺延。"""
    if first_line.startswith(subchapter):
        return True
    first_kind = _display_heading_kind(first_line)
    subchapter_kind = _display_heading_kind(subchapter)
    if not first_kind or first_kind != subchapter_kind:
        return False
    first_key = line_content_key(first_line)
    subchapter_key = line_content_key(subchapter)
    return bool(subchapter_key and first_key.startswith(subchapter_key))


def _display_heading_kind(text: str) -> str:
    """返回可展示标题的编号类型。"""
    if CHINESE_HEADING_RE.match(text):
        return "chinese"
    if DECIMAL_HEADING_RE.match(text):
        return "decimal"
    return ""


def remove_fully_equal_lines(old_text: str, new_text: str) -> tuple[str, str]:
    """移除左右文本中正文一致的内容，只保留真实差异行。"""
    if old_text in {"新增", "删除"} or new_text in {"新增", "删除"}:
        return old_text, new_text
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    old_keys = [line_content_key(line) for line in old_lines]
    new_keys = [line_content_key(line) for line in new_lines]
    old_changed: list[str] = []
    new_changed: list[str] = []
    matcher = difflib.SequenceMatcher(a=old_keys, b=new_keys)
    opcodes = matcher.get_opcodes()
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            has_previous_change = bool(old_changed or new_changed)
            has_later_change = any(later_tag != "equal" for later_tag, *_rest in opcodes[index + 1 :])
            has_omitted_content = any(line.strip() for line in old_lines[i1:i2]) or any(line.strip() for line in new_lines[j1:j2])
            equal_context_lines = [
                line
                for line in old_lines[i1:i2]
                if DISPLAY_HEADING_RE.match(line.strip())
            ]
            if equal_context_lines and has_later_change:
                for line in equal_context_lines:
                    if not old_changed or old_changed[-1] != line:
                        old_changed.append(line)
                    if not new_changed or new_changed[-1] != line:
                        new_changed.append(line)
                continue
            if has_previous_change and has_later_change and has_omitted_content:
                if old_changed and old_changed[-1] != OMITTED_EQUAL_MARKER:
                    old_changed.append(OMITTED_EQUAL_MARKER)
                if new_changed and new_changed[-1] != OMITTED_EQUAL_MARKER:
                    new_changed.append(OMITTED_EQUAL_MARKER)
            continue
        if tag in {"replace", "delete"}:
            old_changed.extend(line for line in old_lines[i1:i2] if line.strip())
        if tag in {"replace", "insert"}:
            new_changed.extend(line for line in new_lines[j1:j2] if line.strip())
    old_result = "\n".join(old_changed).strip()
    new_result = "\n".join(new_changed).strip()
    if not old_result and new_result:
        old_result = "新增"
    if old_result and not new_result:
        new_result = "删除"
    return old_result, new_result


def append_changed_pair(old_chunks: list[str], new_chunks: list[str], old_block: str, new_block: str) -> None:
    """仅在文本真正变化时，把一对块追加进候选结果。"""
    if old_block == new_block or is_only_numbering_changed(old_block, new_block):
        return
    old_chunks.append(old_block)
    new_chunks.append(new_block)


def append_label_aligned_replacement(old_chunks: list[str], new_chunks: list[str], old_blocks: list[str], new_blocks: list[str]) -> None:
    """在 replace 场景下按块标签重新对齐左右文本。"""
    old_labels = [block_match_key(block) for block in old_blocks]
    new_labels = [block_match_key(block) for block in new_blocks]
    used_old: set[int] = set()
    used_new: set[int] = set()
    for new_index, new_label in enumerate(new_labels):
        old_index = next((index for index, old_label in enumerate(old_labels) if index not in used_old and old_label == new_label), None)
        if old_index is None:
            continue
        used_old.add(old_index)
        used_new.add(new_index)
        append_changed_pair(old_chunks, new_chunks, old_blocks[old_index], new_blocks[new_index])
    if not used_old and len(old_blocks) == len(new_blocks):
        for old_block, new_block in zip(old_blocks, new_blocks):
            append_changed_pair(old_chunks, new_chunks, old_block, new_block)
        return
    for index, old_block in enumerate(old_blocks):
        if index not in used_old:
            old_chunks.append(old_block)
            new_chunks.append("删除")
    for index, new_block in enumerate(new_blocks):
        if index not in used_new:
            old_chunks.append("新增")
            new_chunks.append(new_block)


def build_block_chunks(old_lines: list[str], new_lines: list[str]) -> tuple[list[str], list[str]]:
    """按块级 diff 生成左右对应的候选文本片段。"""
    old_blocks = ["\n".join(block).strip() for block in split_blocks(old_lines)]
    new_blocks = ["\n".join(block).strip() for block in split_blocks(new_lines)]
    if len(old_blocks) <= 1 and len(new_blocks) <= 1:
        return [], []
    old_chunks: list[str] = []
    new_chunks: list[str] = []
    matcher = difflib.SequenceMatcher(a=[block_match_key(block) for block in old_blocks], b=[block_match_key(block) for block in new_blocks])
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for old_block, new_block in zip(old_blocks[i1:i2], new_blocks[j1:j2]):
                append_changed_pair(old_chunks, new_chunks, old_block, new_block)
            continue
        if tag == "replace":
            append_label_aligned_replacement(old_chunks, new_chunks, old_blocks[i1:i2], new_blocks[j1:j2])
            continue
        old_chunk = join_chunks(old_blocks[i1:i2]) if i1 != i2 else "新增"
        new_chunk = join_chunks(new_blocks[j1:j2]) if j1 != j2 else "删除"
        old_chunks.append(old_chunk)
        new_chunks.append(new_chunk)
    return old_chunks, new_chunks


def build_section_rows(old_section: Section, new_section: Section) -> list[ComparisonRow]:
    """比较同一章节的旧版与新版正文，生成展示行。"""
    old_lines = clean_lines(old_section.body)
    new_lines = clean_lines(new_section.body)
    if old_lines == new_lines and old_section.title == new_section.title:
        return []
    if old_section.title != new_section.title:
        chapter = f"{old_section.number}（章节标题调整）"
        return [ComparisonRow(chapter=chapter, old_text=f"{old_section.title}\n{full_body_without_title(old_section)}".strip(), new_text=f"{new_section.title}\n{full_body_without_title(new_section)}".strip())]
    old_chunks, new_chunks = build_block_chunks(old_lines, new_lines)
    if not old_chunks and not new_chunks:
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            old_chunks.append(format_chunk(old_lines, i1, i2) if i1 != i2 else "新增")
            new_chunks.append(format_chunk(new_lines, j1, j2) if j1 != j2 else "删除")
    rows: list[ComparisonRow] = []
    for old_chunk, new_chunk in zip(old_chunks, new_chunks):
        if is_only_numbering_changed(old_chunk, new_chunk):
            continue
        row = build_comparison_row(old_section.title, old_chunk, new_chunk)
        filtered_old_text, filtered_new_text = remove_fully_equal_lines(row.old_text, row.new_text)
        if not filtered_old_text and not filtered_new_text:
            continue
        rows.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=filtered_old_text,
                new_text=filtered_new_text,
            )
        )
    return rows


def build_rows(old_sections: list[Section], new_sections: list[Section]) -> list[ComparisonRow]:
    """比较两份文档的全部章节，生成完整对照行。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    rows: list[ComparisonRow] = []
    name_row = build_name_row(old_by_number, new_by_number)
    if name_row:
        rows.append(name_row)
    ordered_numbers: list[str] = []
    for section in old_sections + new_sections:
        if section.number not in ordered_numbers:
            ordered_numbers.append(section.number)
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        if not old_section or not new_section:
            if old_section:
                rows.append(build_comparison_row(old_section.title, full_body_without_title(old_section), "删除"))
            elif new_section:
                rows.append(build_comparison_row(new_section.title, "新增", full_body_without_title(new_section)))
            continue
        rows.extend(build_section_rows(old_section, new_section))
    return rows


def build_name_row(old_sections: dict[str, Section], new_sections: dict[str, Section]) -> ComparisonRow | None:
    """为基金名称变化生成一条单独的对照行。"""
    old_section = old_sections.get("第三部分")
    new_section = new_sections.get("第三部分")
    if not old_section or not new_section:
        return None
    old_lines = clean_lines(old_section.body)
    new_lines = clean_lines(new_section.body)
    try:
        old_name = old_lines[old_lines.index("一、基金名称") + 1]
        new_name = new_lines[new_lines.index("一、基金名称") + 1]
    except (ValueError, IndexError):
        return None
    if old_name == new_name:
        return None
    return ComparisonRow(chapter="第三部分  基金的基本情况", subchapter="一、基金名称", old_text=old_name, new_text=new_name)


def row_display_text(text: str, subchapter: str) -> str:
    """把对照行还原成适合再次发送给模型的展示文本。"""
    if not subchapter or text in {"新增", "删除"}:
        return text
    first_line = text.split("\n", 1)[0]
    if first_line_matches_subchapter(first_line, subchapter):
        return text
    return f"{subchapter}\n{text}"


def build_candidate_section(section: Section, rows: list[ComparisonRow], *, side: str) -> Section | None:
    """把章节内真实变化的行重新压缩成精简版 section。"""
    display_chunks: list[str] = []
    for row in rows:
        text = row.old_text if side == "old" else row.new_text
        if not text.strip():
            continue
        display_chunks.append(row_display_text(text, row.subchapter))
    if not display_chunks:
        return None
    body = "\n".join([section.title, *display_chunks]).strip()
    return Section(number=section.number, title=section.title, body=body)


def build_compare_units_for_llm(
    old_sections: list[Section],
    new_sections: list[Section],
) -> tuple[list[CompareUnit], list[dict[str, object]]]:
    """把章节差异拆成条目级单元，并返回可审计的预处理摘要。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers: list[str] = []
    for section in old_sections + new_sections:
        if section.number not in ordered_numbers:
            ordered_numbers.append(section.number)

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
    ordered_numbers: list[str] = []
    for section in old_sections + new_sections:
        if section.number not in ordered_numbers:
            ordered_numbers.append(section.number)

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


def group_compare_units_into_batches(
    compare_units: list[CompareUnit],
    batch_size: int,
    *,
    max_batch_chars: int = 0,
    oversized_batch_size: int = 2,
    max_compare_units_per_batch: int = 0,
    max_compare_unit_chars: int = 0,
) -> list[ChapterBatch]:
    """按章节数分组；超大 batch 自动降为更小章节组或更小 unit 组。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    if oversized_batch_size <= 0:
        raise ValueError("oversized_batch_size 必须大于 0")
    compare_units = split_oversized_compare_units(
        compare_units,
        max_compare_unit_chars=max_compare_unit_chars,
    )
    initial_batches = _group_units_by_chapter_limit(compare_units, batch_size)
    if max_batch_chars <= 0:
        refined_batches = initial_batches
    else:
        refined_batches = []
        for batch in initial_batches:
            refined_batches.extend(
                split_oversized_compare_batch(
                    batch,
                    max_batch_chars=max_batch_chars,
                    oversized_batch_size=oversized_batch_size,
                )
            )
    unit_limited_batches = split_batches_by_compare_unit_limit(
        refined_batches,
        max_compare_units_per_batch=max_compare_units_per_batch,
    )
    return _renumber_batches(unit_limited_batches)


def split_oversized_compare_units(
    compare_units: list[CompareUnit],
    *,
    max_compare_unit_chars: int,
) -> list[CompareUnit]:
    """把单个过长 compare unit 按行拆成 part，避免单次返回过长。"""
    if max_compare_unit_chars <= 0:
        return compare_units
    refined: list[CompareUnit] = []
    for unit in compare_units:
        if compare_unit_chars(unit) <= max_compare_unit_chars:
            refined.append(unit)
            continue
        refined.extend(split_compare_unit_by_lines(unit, max_compare_unit_chars=max_compare_unit_chars))
    return refined


def split_compare_unit_by_lines(unit: CompareUnit, *, max_compare_unit_chars: int) -> list[CompareUnit]:
    """按左右文本的行边界拆分单个 compare unit。"""
    old_lines = unit.old_text.splitlines() or [unit.old_text]
    new_lines = unit.new_text.splitlines() or [unit.new_text]
    old_chunks = chunk_lines_by_char_limit(old_lines, max_compare_unit_chars=max_compare_unit_chars)
    new_chunks = chunk_lines_by_char_limit(new_lines, max_compare_unit_chars=max_compare_unit_chars)
    chunk_count = max(len(old_chunks), len(new_chunks))
    parts: list[CompareUnit] = []
    for index in range(chunk_count):
        old_text = "\n".join(old_chunks[index]) if index < len(old_chunks) else ""
        new_text = "\n".join(new_chunks[index]) if index < len(new_chunks) else ""
        parts.append(
            CompareUnit(
                unit_id=f"{unit.unit_id}-part-{index + 1:03d}",
                chapter_number=unit.chapter_number,
                chapter_title=unit.chapter_title,
                subchapter=unit.subchapter,
                old_text=old_text,
                new_text=new_text,
            )
        )
    return parts


def chunk_lines_by_char_limit(lines: list[str], *, max_compare_unit_chars: int) -> list[list[str]]:
    """把文本行按字符上限分块；单行超过上限时保留该整行。"""
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    side_limit = max(1, max_compare_unit_chars // 2)
    for line in lines:
        line_chars = len(line)
        separator_chars = 1 if current else 0
        if current and current_chars + separator_chars + line_chars > side_limit:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(line)
        current_chars += (1 if current_chars else 0) + line_chars
    if current:
        chunks.append(current)
    return chunks


def compare_unit_chars(unit: CompareUnit) -> int:
    """计算单个 compare unit 的左右文本总字符数。"""
    return len(unit.old_text) + len(unit.new_text)


def split_batches_by_compare_unit_limit(
    batches: list[ChapterBatch],
    *,
    max_compare_units_per_batch: int,
) -> list[ChapterBatch]:
    """按 compare unit 数继续拆分，避免模型输出过长被截断。"""
    if max_compare_units_per_batch <= 0:
        return batches
    refined: list[ChapterBatch] = []
    for batch in batches:
        units = list(batch.compare_units)
        if len(units) <= max_compare_units_per_batch:
            refined.append(batch)
            continue
        for start in range(0, len(units), max_compare_units_per_batch):
            chunk = units[start : start + max_compare_units_per_batch]
            refined.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(dict.fromkeys(unit.chapter_number for unit in chunk)),
                    compare_units=tuple(chunk),
                )
            )
    return refined


def split_oversized_compare_batch(
    batch: ChapterBatch,
    *,
    max_batch_chars: int,
    oversized_batch_size: int,
) -> list[ChapterBatch]:
    """递归拆分超长 batch，优先按配置的 2 章粒度，必要时降到单章。"""
    if max_batch_chars <= 0 or batch_compare_chars(batch) <= max_batch_chars or len(batch.chapter_numbers) <= 1:
        return [batch]
    next_batch_size = oversized_batch_size if len(batch.chapter_numbers) > oversized_batch_size else 1
    split_batches = _group_units_by_chapter_limit(list(batch.compare_units), next_batch_size)
    refined: list[ChapterBatch] = []
    for split_batch in split_batches:
        refined.extend(
            split_oversized_compare_batch(
                split_batch,
                max_batch_chars=max_batch_chars,
                oversized_batch_size=oversized_batch_size,
            )
        )
    return refined


def _group_units_by_chapter_limit(compare_units: list[CompareUnit], batch_size: int) -> list[ChapterBatch]:
    """按章节数量上限把 compare units 分组，临时 batch id 稍后统一重排。"""
    batches: list[ChapterBatch] = []
    current_units: list[CompareUnit] = []
    current_numbers: list[str] = []
    current_seen_numbers: set[str] = set()
    for unit in compare_units:
        unit_is_new_chapter = unit.chapter_number not in current_seen_numbers
        if current_units and unit_is_new_chapter and len(current_seen_numbers) >= batch_size:
            batches.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(current_numbers),
                    compare_units=tuple(current_units),
                )
            )
            current_units = []
            current_numbers = []
            current_seen_numbers = set()
        current_units.append(unit)
        if unit.chapter_number not in current_seen_numbers:
            current_seen_numbers.add(unit.chapter_number)
            current_numbers.append(unit.chapter_number)
    if current_units:
        batches.append(
            ChapterBatch(
                batch_id="",
                chapter_numbers=tuple(current_numbers),
                compare_units=tuple(current_units),
            )
        )
    return batches


def _renumber_batches(batches: list[ChapterBatch]) -> list[ChapterBatch]:
    """为最终 batch 列表重新生成稳定的 batch-XXX 编号。"""
    return [
        ChapterBatch(
            batch_id=f"batch-{index:03d}",
            chapter_numbers=batch.chapter_numbers,
            old_sections=batch.old_sections,
            new_sections=batch.new_sections,
            compare_units=batch.compare_units,
        )
        for index, batch in enumerate(batches, start=1)
    ]


def batch_compare_chars(batch: ChapterBatch) -> int:
    """计算 batch 中实际发送给模型的左右文本总字符数。"""
    return sum(len(unit.old_text) + len(unit.new_text) for unit in batch.compare_units)


def group_sections_into_batches(old_sections: list[Section], new_sections: list[Section], batch_size: int) -> list[tuple[tuple[Section, ...], tuple[Section, ...]]]:
    """按章节顺序把左右 section 分组成固定大小的 batch。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers: list[str] = []
    for section in old_sections + new_sections:
        if section.number not in ordered_numbers:
            ordered_numbers.append(section.number)
    batches: list[tuple[tuple[Section, ...], tuple[Section, ...]]] = []
    current_old: list[Section] = []
    current_new: list[Section] = []
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        if old_section is not None:
            current_old.append(old_section)
        if new_section is not None:
            current_new.append(new_section)
        if len(current_old) >= batch_size or len(current_new) >= batch_size:
            batches.append((tuple(current_old), tuple(current_new)))
            current_old = []
            current_new = []
    if current_old or current_new:
        batches.append((tuple(current_old), tuple(current_new)))
    return batches
