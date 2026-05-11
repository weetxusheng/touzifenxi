
"""按内部标题切分章节正文块。

职责：`split_blocks`、块标签与 `block_match_key`。
不负责：整章对照行与 compare block（见 `rule_rows`、`compare_blocks`）。
"""

from __future__ import annotations

from ..extractor import clean_lines
from .constants import CHINESE_HEADING_RE, INNER_HEADING_RE, PAREN_CHINESE_HEADING_RE
from .headings import active_display_heading_re, subchapter_info
from .text_match import line_content_key

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
