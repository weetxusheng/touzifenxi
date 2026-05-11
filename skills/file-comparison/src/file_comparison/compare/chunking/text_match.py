
"""编号忽略、行级相等判定与差异行裁剪。

职责：去行首编号比较、仅编号变化、全同行剔除、小标题与正文首行匹配。
不负责：块级 diff opcode（见 `rule_rows`）。
"""

from __future__ import annotations

import difflib

from ..extractor import clean_lines
from .constants import INNER_HEADING_RE, OMITTED_EQUAL_MARKER
from .headings import _display_heading_kind, is_preservable_equal_context_line

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
