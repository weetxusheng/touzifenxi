"""负责按层级切分章节、过滤不变内容并生成对照候选块。"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable

from .extractor import clean_lines, full_body_without_title
from .models import ChapterBatch, CompareBlock, CompareBlockItem, CompareUnit, ComparisonRow, Section

INNER_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（(?:[一二三四五六七八九十]+|\d+)）)")
CHINESE_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
PAREN_CHINESE_HEADING_RE = re.compile(r"^（[一二三四五六七八九十]+）")
DISPLAY_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（[一二三四五六七八九十]+）)")
DECIMAL_HEADING_RE = re.compile(r"^\d+、")
CHINESE_CONTEXT_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")
MAX_PARENT_HEADING_CHARS = 40
# 同 compare block 内，去行首序号后判定「可成对剔除」时，允许 old/new 列表下标相差不超过该值。
COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW = 5


def active_display_heading_re(lines: list[str]) -> re.Pattern[str]:
    """返回当前章节用于切分表格二级内容的标题规则。"""
    if any(CHINESE_HEADING_RE.match(line) for line in lines):
        return CHINESE_CONTEXT_HEADING_RE
    return DISPLAY_HEADING_RE


OMITTED_EQUAL_MARKER = "......"


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
    return f"\n{OMITTED_EQUAL_MARKER}\n".join(chunk for chunk in chunks if chunk.strip())


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
    if len(lines) >= 2 and CHINESE_HEADING_RE.match(lines[0]) and PAREN_CHINESE_HEADING_RE.match(lines[1]):
        return SubchapterInfo(label=f"{lines[0]}\n{lines[1]}", consume_line=True)
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
    if len(lines) >= 2 and CHINESE_HEADING_RE.match(lines[0]) and PAREN_CHINESE_HEADING_RE.match(lines[1]):
        lines = lines[2:]
        return "\n".join(lines).strip()
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
    parent_heading = ""
    for line in lines:
        if CHINESE_HEADING_RE.match(line):
            blocks.append(current)
            current = [line]
            parent_heading = line
            continue
        if PAREN_CHINESE_HEADING_RE.match(line) and heading_re.match(line):
            if current:
                blocks.append(current)
            current = [parent_heading, line] if parent_heading else [line]
            continue
        if heading_re.match(line) and current:
            blocks.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return [block for block in blocks if block]


def block_label(block: str) -> str:
    """返回一个文本块用于展示的标签。"""
    info = subchapter_info(block)
    if info is not None:
        return info.label
    first_line = clean_lines(block)[0] if clean_lines(block) else block
    return first_line


def is_generic_numbering_label(label: str) -> bool:
    """判断标签是否只剩下纯编号前缀，没有保留正文语义。"""
    lines = clean_lines(label)
    return bool(lines) and all(bool(INNER_HEADING_RE.fullmatch(line.strip())) for line in lines)


def block_match_key(block: str) -> str:
    """返回用于左右文本块对齐的归一化键。"""
    info = subchapter_info(block)
    block_lines = clean_lines(block)
    if info is not None and not info.consume_line and is_generic_numbering_label(info.label) and block_lines:
        key = line_content_key(block_lines[0]).strip()
        return key or block_lines[0]
    label = info.label if info is not None else block_label(block)
    key = "\n".join(line_content_key(line) for line in clean_lines(label)).strip()
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


def text_starts_with_subchapter(text: str, subchapter: str) -> bool:
    """判断正文开头是否已经包含完整二级标题，支持多行标题。"""
    if text.startswith(subchapter):
        return True
    text_lines = clean_lines(text)
    subchapter_lines = clean_lines(subchapter)
    if not subchapter_lines or len(text_lines) < len(subchapter_lines):
        return False
    for text_line, subchapter_line in zip(text_lines, subchapter_lines):
        if not first_line_matches_subchapter(text_line, subchapter_line):
            return False
    return True


def _display_heading_kind(text: str) -> str:
    """返回可展示标题的编号类型。"""
    if CHINESE_HEADING_RE.match(text):
        return "chinese"
    if PAREN_CHINESE_HEADING_RE.match(text):
        return "paren_chinese"
    if DECIMAL_HEADING_RE.match(text):
        return "decimal"
    return ""


def is_preservable_equal_context_line(line: str) -> bool:
    """判断 equal 块中的行是否应作为结构上下文保留。"""
    stripped = line.strip()
    return bool(CHINESE_HEADING_RE.match(stripped) or PAREN_CHINESE_HEADING_RE.match(stripped))


def is_structural_parent_heading(line: str) -> bool:
    """判断中文编号行是否更像结构父标题，而不是正文长条款。"""
    stripped = line.strip()
    return bool(CHINESE_HEADING_RE.match(stripped) and len(stripped) <= MAX_PARENT_HEADING_CHARS)


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
            equal_context_pairs = [
                (old_line, new_line)
                for old_line, new_line in zip(old_lines[i1:i2], new_lines[j1:j2])
                if is_preservable_equal_context_line(old_line) or is_preservable_equal_context_line(new_line)
            ]
            if equal_context_pairs and has_later_change:
                for old_line, new_line in equal_context_pairs:
                    if not old_changed or old_changed[-1] != old_line:
                        old_changed.append(old_line)
                    if not new_changed or new_changed[-1] != new_line:
                        new_changed.append(new_line)
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


def body_lines_without_title(section: Section) -> list[str]:
    """返回章节正文行，去掉与标题重复的首行。"""
    lines = clean_lines(section.body)
    if lines and lines[0] == section.title:
        return lines[1:]
    return lines


def _item_text_from_block_lines(block_lines: list[str]) -> tuple[str, str]:
    """把块行规整成 parent_path 和 item_text。"""
    if not block_lines:
        return "", ""
    if len(block_lines) >= 2 and is_structural_parent_heading(block_lines[0]) and PAREN_CHINESE_HEADING_RE.match(block_lines[1]):
        return block_lines[0], "\n".join(block_lines[1:]).strip()
    return "", "\n".join(block_lines).strip()


def section_items_for_blocks(section: Section) -> list[tuple[str, str]]:
    """把章节拆成送模条目，保留同层顺序与原始文本。"""
    lines = body_lines_without_title(section)
    if not lines:
        return []
    blocks = split_blocks(lines)
    if not blocks:
        return []
    items: list[tuple[str, str]] = []
    for index, block in enumerate(blocks):
        if len(block) == 1 and is_structural_parent_heading(block[0]):
            next_block = blocks[index + 1] if index + 1 < len(blocks) else None
            if next_block and len(next_block) >= 2 and next_block[0] == block[0]:
                continue
        if len(block) >= 2 and is_structural_parent_heading(block[0]):
            if PAREN_CHINESE_HEADING_RE.match(block[1]):
                items.append((block[0], "\n".join(block[1:]).strip()))
                continue
            current_nested: list[str] = []
            nested_items: list[list[str]] = []
            for line in block[1:]:
                if INNER_HEADING_RE.match(line) and current_nested:
                    nested_items.append(current_nested)
                    current_nested = [line]
                    continue
                current_nested.append(line)
            if current_nested:
                nested_items.append(current_nested)
            if nested_items:
                for nested_block in nested_items:
                    items.append((block[0], "\n".join(nested_block).strip()))
                continue
        items.append(_item_text_from_block_lines(block))
    if items and all(not parent_path for parent_path, _text in items):
        return items
    return [(parent_path, text) for parent_path, text in items if text]


@dataclass(slots=True)
class ParentPathGroup:
    """表示忽略编号后对齐的一组父标题。"""

    key: str
    display_path: str


def parent_path_match_key(parent_path: str) -> str:
    """返回父标题匹配键，允许“十六、其他”与“十七、其他”对齐。"""
    lines = clean_lines(parent_path)
    if not lines:
        return ""
    return "\n".join(line_content_key(line) for line in lines).strip()


def ordered_parent_groups(old_items: list[tuple[str, str]], new_items: list[tuple[str, str]]) -> list[ParentPathGroup]:
    """按新版优先的出现顺序返回父标题语义分组。"""
    paths_by_key: dict[str, str] = {}
    ordered_keys: list[str] = []
    for parent_path, _text in [*new_items, *old_items]:
        key = parent_path_match_key(parent_path)
        if key not in paths_by_key:
            paths_by_key[key] = parent_path
            ordered_keys.append(key)
    return [ParentPathGroup(key=key, display_path=paths_by_key[key]) for key in ordered_keys]


def block_item_texts(items: tuple[CompareBlockItem, ...]) -> tuple[str, ...]:
    """返回 block 内条目文本序列，用于判断 block 是否完全未变。"""
    return tuple(item.text for item in items)


def _matched_indices_stripped_equal_within_window(
    old_block_items: tuple[CompareBlockItem, ...],
    new_block_items: tuple[CompareBlockItem, ...],
    *,
    neighbor_window: int,
) -> tuple[set[int], set[int]]:
    """返回去序号后正文相同、且 old 下标 i 与 new 下标 j 满足 |i-j|<=neighbor_window 的配对下标。"""
    old_n = len(old_block_items)
    new_n = len(new_block_items)
    candidates: list[tuple[int, int, int]] = []
    for i in range(old_n):
        key_old = strip_leading_numbering(old_block_items[i].text)
        j_lo = max(0, i - neighbor_window)
        j_hi = min(new_n, i + neighbor_window + 1)
        for j in range(j_lo, j_hi):
            if strip_leading_numbering(new_block_items[j].text) != key_old:
                continue
            candidates.append((abs(i - j), i, j))
    candidates.sort()
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for _dist, i, j in candidates:
        if i in matched_old or j in matched_new:
            continue
        matched_old.add(i)
        matched_new.add(j)
    return matched_old, matched_new


def filter_compare_block_items_by_stripped_numbering_identity(
    old_block_items: tuple[CompareBlockItem, ...],
    new_block_items: tuple[CompareBlockItem, ...],
    *,
    neighbor_window: int | None = None,
) -> tuple[tuple[CompareBlockItem, ...], tuple[CompareBlockItem, ...]]:
    """同块内减少送模噪音：先全文完全一致剔除，再在邻域内配对去行首序号后相同的条目。

    全文交集与原先一致。随后在所有满足 ``|i-j| <= neighbor_window``（默认
    ``COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW``）且
    ``strip_leading_numbering(old[i]) == strip_leading_numbering(new[j])`` 的 (i,j) 中，
    按 ``|i-j|`` 升序贪心配对，已配对的下标不再参与；配对成功的两侧条目一并丢弃。
    """
    if not old_block_items and not new_block_items:
        return old_block_items, new_block_items

    window = (
        neighbor_window
        if neighbor_window is not None
        else COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW
    )

    old_texts = {item.text for item in old_block_items}
    new_texts = {item.text for item in new_block_items}
    identical_texts = old_texts & new_texts
    if identical_texts:
        old_block_items = tuple(item for item in old_block_items if item.text not in identical_texts)
        new_block_items = tuple(item for item in new_block_items if item.text not in identical_texts)

    if not old_block_items or not new_block_items:
        return old_block_items, new_block_items

    matched_old, matched_new = _matched_indices_stripped_equal_within_window(
        old_block_items, new_block_items, neighbor_window=window
    )
    kept_old = tuple(
        item for index, item in enumerate(old_block_items) if index not in matched_old
    )
    kept_new = tuple(
        item for index, item in enumerate(new_block_items) if index not in matched_new
    )
    return kept_old, kept_new


def build_compare_blocks_for_llm(
    old_sections: list[Section],
    new_sections: list[Section],
) -> tuple[list[CompareBlock], list[dict[str, object]]]:
    """把章节差异拆成父标题块级上下文。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers: list[str] = []
    for section in old_sections + new_sections:
        if section.number not in ordered_numbers:
            ordered_numbers.append(section.number)

    compare_blocks: list[CompareBlock] = []
    chapter_summaries: list[dict[str, object]] = []
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        chapter_title = old_section.title if old_section is not None else new_section.title
        old_items = section_items_for_blocks(old_section) if old_section is not None else []
        new_items = section_items_for_blocks(new_section) if new_section is not None else []
        parent_groups = ordered_parent_groups(old_items, new_items)
        chapter_blocks: list[CompareBlock] = []
        old_item_sequence = 1
        new_item_sequence = 1
        for block_index, parent_group in enumerate(parent_groups, start=1):
            old_block_items = tuple(
                CompareBlockItem(item_id=f"{number}-old-{old_item_sequence + index:03d}", text=text)
                for index, (_path, text) in enumerate(
                    item for item in old_items if parent_path_match_key(item[0]) == parent_group.key
                )
            )
            old_item_sequence += len(old_block_items)
            new_block_items = tuple(
                CompareBlockItem(item_id=f"{number}-new-{new_item_sequence + index:03d}", text=text)
                for index, (_path, text) in enumerate(
                    item for item in new_items if parent_path_match_key(item[0]) == parent_group.key
                )
            )
            new_item_sequence += len(new_block_items)
            if not old_block_items and not new_block_items:
                continue
            old_block_items, new_block_items = filter_compare_block_items_by_stripped_numbering_identity(
                old_block_items, new_block_items
            )
            if not old_block_items and not new_block_items:
                continue
            if block_item_texts(old_block_items) == block_item_texts(new_block_items):
                continue
            chapter_blocks.append(
                CompareBlock(
                    block_id=f"{number}-block-{block_index:03d}",
                    chapter_number=number,
                    chapter_title=chapter_title,
                    parent_path=parent_group.display_path,
                    old_items=old_block_items,
                    new_items=new_block_items,
                )
            )
        compare_blocks.extend(chapter_blocks)
        chapter_summaries.append(
            {
                "chapter_number": number,
                "chapter_title": chapter_title,
                "original_old_chars": len(old_section.body) if old_section is not None else 0,
                "original_new_chars": len(new_section.body) if new_section is not None else 0,
                "block_count": len(chapter_blocks),
                "sent_old_chars": sum(sum(len(item.text) for item in block.old_items) for block in chapter_blocks),
                "sent_new_chars": sum(sum(len(item.text) for item in block.new_items) for block in chapter_blocks),
                "kept_for_llm": bool(chapter_blocks),
                "reason": (
                    "added"
                    if old_section is None and new_section is not None
                    else "deleted"
                    if new_section is None and old_section is not None
                    else "changed"
                    if chapter_blocks
                    else "unchanged"
                ),
            }
        )
    return compare_blocks, chapter_summaries


def row_display_text(text: str, subchapter: str) -> str:
    """把对照行还原成适合再次发送给模型的展示文本。"""
    if not subchapter or text in {"新增", "删除"}:
        return text
    if text_starts_with_subchapter(text, subchapter):
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


def compare_block_chars(block: CompareBlock) -> int:
    """计算单个 compare block 的左右文本总字符数。"""
    return sum(len(item.text) for item in block.old_items) + sum(len(item.text) for item in block.new_items)


def split_compare_block_by_items(block: CompareBlock, *, max_compare_block_chars: int) -> list[CompareBlock]:
    """按 sibling 边界拆分单个过长 block。"""
    old_items = list(block.old_items)
    new_items = list(block.new_items)
    chunk_pairs: list[tuple[list[CompareBlockItem], list[CompareBlockItem]]] = []
    current_old: list[CompareBlockItem] = []
    current_new: list[CompareBlockItem] = []
    current_chars = 0
    item_count = max(len(old_items), len(new_items))
    for index in range(item_count):
        old_item = old_items[index] if index < len(old_items) else None
        new_item = new_items[index] if index < len(new_items) else None
        pair_chars = (len(old_item.text) if old_item else 0) + (len(new_item.text) if new_item else 0)
        if current_old or current_new:
            if current_chars + pair_chars > max_compare_block_chars:
                chunk_pairs.append((current_old, current_new))
                current_old = []
                current_new = []
                current_chars = 0
        if old_item is not None:
            current_old.append(old_item)
        if new_item is not None:
            current_new.append(new_item)
        current_chars += pair_chars
    if current_old or current_new:
        chunk_pairs.append((current_old, current_new))
    parts: list[CompareBlock] = []
    for index, (old_chunk, new_chunk) in enumerate(chunk_pairs):
        parts.append(
            CompareBlock(
                block_id=f"{block.block_id}-part-{index + 1:03d}",
                chapter_number=block.chapter_number,
                chapter_title=block.chapter_title,
                parent_path=block.parent_path,
                old_items=tuple(old_chunk),
                new_items=tuple(new_chunk),
            )
        )
    return parts


def split_oversized_compare_blocks(
    compare_blocks: list[CompareBlock],
    *,
    max_compare_block_chars: int,
) -> list[CompareBlock]:
    """把单个过长 compare block 按 sibling 边界拆成 part。"""
    if max_compare_block_chars <= 0:
        return compare_blocks
    refined: list[CompareBlock] = []
    for block in compare_blocks:
        if compare_block_chars(block) <= max_compare_block_chars:
            refined.append(block)
            continue
        refined.extend(split_compare_block_by_items(block, max_compare_block_chars=max_compare_block_chars))
    return refined


def _group_blocks_by_chapter_limit(compare_blocks: list[CompareBlock], batch_size: int) -> list[ChapterBatch]:
    """按章节数量上限把 compare blocks 分组。"""
    batches: list[ChapterBatch] = []
    current_blocks: list[CompareBlock] = []
    current_numbers: list[str] = []
    current_seen_numbers: set[str] = set()
    for block in compare_blocks:
        block_is_new_chapter = block.chapter_number not in current_seen_numbers
        if current_blocks and block_is_new_chapter and len(current_seen_numbers) >= batch_size:
            batches.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(current_numbers),
                    compare_blocks=tuple(current_blocks),
                )
            )
            current_blocks = []
            current_numbers = []
            current_seen_numbers = set()
        current_blocks.append(block)
        if block.chapter_number not in current_seen_numbers:
            current_seen_numbers.add(block.chapter_number)
            current_numbers.append(block.chapter_number)
    if current_blocks:
        batches.append(
            ChapterBatch(
                batch_id="",
                chapter_numbers=tuple(current_numbers),
                compare_blocks=tuple(current_blocks),
            )
        )
    return batches


def batch_compare_block_chars(batch: ChapterBatch) -> int:
    """计算 batch 中实际发送给模型的 block 总字符数。"""
    return sum(compare_block_chars(block) for block in batch.compare_blocks)


def split_oversized_compare_block_batch(
    batch: ChapterBatch,
    *,
    max_batch_chars: int,
    oversized_batch_size: int,
) -> list[ChapterBatch]:
    """递归拆分超长 block batch，优先按章节数收缩。"""
    if max_batch_chars <= 0 or batch_compare_block_chars(batch) <= max_batch_chars or len(batch.chapter_numbers) <= 1:
        return [batch]
    next_batch_size = oversized_batch_size if len(batch.chapter_numbers) > oversized_batch_size else 1
    split_batches = _group_blocks_by_chapter_limit(list(batch.compare_blocks), next_batch_size)
    refined: list[ChapterBatch] = []
    for split_batch in split_batches:
        refined.extend(
            split_oversized_compare_block_batch(
                split_batch,
                max_batch_chars=max_batch_chars,
                oversized_batch_size=oversized_batch_size,
            )
        )
    return refined


def split_batches_by_compare_block_limit(
    batches: list[ChapterBatch],
    *,
    max_compare_blocks_per_batch: int,
) -> list[ChapterBatch]:
    """按 compare block 数拆分 batch，但不拆开同一章节。"""
    if max_compare_blocks_per_batch <= 0:
        return batches
    refined: list[ChapterBatch] = []
    for batch in batches:
        blocks = list(batch.compare_blocks)
        if len(blocks) <= max_compare_blocks_per_batch:
            refined.append(batch)
            continue
        chapter_groups: list[list[CompareBlock]] = []
        for block in blocks:
            if not chapter_groups or chapter_groups[-1][0].chapter_number != block.chapter_number:
                chapter_groups.append([block])
            else:
                chapter_groups[-1].append(block)
        current: list[CompareBlock] = []
        for chapter_group in chapter_groups:
            if current and len(current) + len(chapter_group) > max_compare_blocks_per_batch:
                refined.append(
                    ChapterBatch(
                        batch_id="",
                        chapter_numbers=tuple(dict.fromkeys(block.chapter_number for block in current)),
                        compare_blocks=tuple(current),
                    )
                )
                current = []
            current.extend(chapter_group)
        if current:
            refined.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(dict.fromkeys(block.chapter_number for block in current)),
                    compare_blocks=tuple(current),
                )
            )
    return refined


def _renumber_block_batches(batches: list[ChapterBatch]) -> list[ChapterBatch]:
    """为 block 批次重新生成稳定 batch 编号。"""
    return [
        ChapterBatch(
            batch_id=f"batch-{index:03d}",
            chapter_numbers=batch.chapter_numbers,
            old_sections=batch.old_sections,
            new_sections=batch.new_sections,
            compare_blocks=batch.compare_blocks,
        )
        for index, batch in enumerate(batches, start=1)
    ]


def group_compare_blocks_into_batches(
    compare_blocks: list[CompareBlock],
    batch_size: int,
    *,
    max_batch_chars: int = 0,
    oversized_batch_size: int = 2,
    max_compare_blocks_per_batch: int = 0,
    max_compare_block_chars: int = 0,
) -> list[ChapterBatch]:
    """按章节数、字符数和 block 数把 compare blocks 分批。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    if oversized_batch_size <= 0:
        raise ValueError("oversized_batch_size 必须大于 0")
    # 业务对照要求同一章节内的上下文不能因字数被拆散；max_compare_block_chars 仅保留为兼容配置。
    initial_batches = _group_blocks_by_chapter_limit(compare_blocks, batch_size)
    if max_batch_chars <= 0:
        refined_batches = initial_batches
    else:
        refined_batches = []
        for batch in initial_batches:
            refined_batches.extend(
                split_oversized_compare_block_batch(
                    batch,
                    max_batch_chars=max_batch_chars,
                    oversized_batch_size=oversized_batch_size,
                )
            )
    block_limited_batches = split_batches_by_compare_block_limit(
        refined_batches,
        max_compare_blocks_per_batch=max_compare_blocks_per_batch,
    )
    return _renumber_block_batches(block_limited_batches)


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
