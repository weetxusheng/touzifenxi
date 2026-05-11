
"""章节正文拆条与父标题分组。

职责：`section_items_for_blocks`、父路径分组与匹配键。
不负责：CompareBlock 组装与过滤（见 `compare_blocks`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extractor import clean_lines
from ..models import Section
from .blocks import split_blocks
from .constants import INNER_HEADING_RE, PAREN_CHINESE_HEADING_RE
from .headings import is_structural_parent_heading
from .rule_rows import body_lines_without_title
from .text_match import line_content_key

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
