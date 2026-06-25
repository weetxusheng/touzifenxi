
"""章节内标题识别与小标题提取。

职责：内部标题上下文、小标题 `SubchapterInfo`、结构父标题判定。
不负责：跨块对齐与送模条目（见 `blocks`、`section_items`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extractor import clean_lines
from .constants import (
        CHINESE_CONTEXT_HEADING_RE,
        CHINESE_HEADING_RE,
        DECIMAL_HEADING_RE,
        DISPLAY_HEADING_RE,
        MAX_PARENT_HEADING_CHARS,
        OMITTED_EQUAL_MARKER,
        PAREN_CHINESE_HEADING_RE,
    )

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
    """判断 equal 块中的行是否应作为结构上下文保留。

    保留:
        - 中文编号 (一、二、) / 带括号中文编号 ((一)(二)) 始终保留 — 它们是章节小标题。
        - 数字编号 (2、xxx) 仅当行短 (≤ MAX_PARENT_HEADING_CHARS) 且不以句末标点结尾时,
          视作"数字小标题"(如 `2、巨额赎回的处理方式`)。带句号/分号的长行是普通条款正文 (如
          `2、发生基金合同规定...时。`), 不保留。
    """
    stripped = line.strip()
    if CHINESE_HEADING_RE.match(stripped) or PAREN_CHINESE_HEADING_RE.match(stripped):
        return True
    if DECIMAL_HEADING_RE.match(stripped) and len(stripped) <= MAX_PARENT_HEADING_CHARS:
        if stripped and stripped[-1] not in ("。", ".", "；", ";"):
            return True
    return False

def is_structural_parent_heading(line: str) -> bool:
    """判断中文编号行是否更像结构父标题，而不是正文长条款。"""
    stripped = line.strip()
    return bool(CHINESE_HEADING_RE.match(stripped) and len(stripped) <= MAX_PARENT_HEADING_CHARS)
