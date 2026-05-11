
"""本地规则 diff：章节/块级对照行生成。

职责：`build_rows` / `build_section_rows` 等规则模式对照行。
不负责：LLM compare block 与批次（见 `compare_blocks`、`batching`）。
"""

from __future__ import annotations

import difflib

from ..extractor import clean_lines, full_body_without_title, ordered_unique_section_numbers
from ..models import ComparisonRow, Section
from .blocks import block_match_key, split_blocks
from .headings import format_chunk, join_chunks, remove_first_inner_heading, subchapter_info
from .text_match import is_only_numbering_changed, remove_fully_equal_lines

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
    ordered_numbers = ordered_unique_section_numbers(old_sections, new_sections)
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

def body_lines_without_title(section: Section) -> list[str]:
    """返回章节正文行，去掉与标题重复的首行。"""
    lines = clean_lines(section.body)
    if lines and lines[0] == section.title:
        return lines[1:]
    return lines
