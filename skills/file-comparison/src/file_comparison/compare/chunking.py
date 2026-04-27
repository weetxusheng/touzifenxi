"""负责按层级切分章节、过滤不变内容并生成对照候选块。"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable

from .extractor import clean_lines, extract_fund_name, full_body_without_title
from .models import ComparisonRow, Section


INNER_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（[一二三四五六七八九十]+）)")


def heading_context(lines: list[str], index: int) -> str | None:
    """向上回溯当前行所属的最近内部标题。"""
    for current in range(index, -1, -1):
        if INNER_HEADING_RE.match(lines[current]):
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
    match = INNER_HEADING_RE.match(lines[0])
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
    if lines and INNER_HEADING_RE.match(lines[0]):
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
    for line in lines:
        if INNER_HEADING_RE.match(line) and current:
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


def is_only_numbering_changed(old_text: str, new_text: str) -> bool:
    """判断两段文本是否只是编号变化、正文未变。"""
    if old_text in {"新增", "删除"} or new_text in {"新增", "删除"}:
        return False
    if old_text == new_text:
        return False
    return strip_leading_numbering(old_text) == strip_leading_numbering(new_text)


def remove_fully_equal_lines(old_text: str, new_text: str) -> tuple[str, str]:
    """移除左右文本中按行完全一致的内容，只保留差异行。"""
    if old_text in {"新增", "删除"} or new_text in {"新增", "删除"}:
        return old_text, new_text
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    old_changed: list[str] = []
    new_changed: list[str] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
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
    if first_line.startswith(subchapter) or INNER_HEADING_RE.match(first_line):
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
