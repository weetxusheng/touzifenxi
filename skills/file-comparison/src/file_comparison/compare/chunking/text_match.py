
"""编号忽略、行级相等判定与差异行裁剪。

职责：去行首编号比较、仅编号变化、全同行剔除、小标题与正文首行匹配。
不负责：块级 diff opcode（见 `rule_rows`）。
"""

from __future__ import annotations

import difflib

import re

from ..extractor import clean_lines
from .constants import INNER_HEADING_RE, OMITTED_EQUAL_MARKER
from .headings import _display_heading_kind, is_preservable_equal_context_line

_CHINESE_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九", start=0)}
_FIRST_CLAUSE_ORDINAL_RE = re.compile(
    r"^\s*(?:[（(]([一二三四五六七八九十百零\d]+)[)）]|([一二三四五六七八九十百零\d]+)、)"
)

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
    has_replace_in_chain = any(op_tag == "replace" for op_tag, *_ in opcodes)
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
                has_non_preservable_omitted = any(
                    line.strip() and not is_preservable_equal_context_line(line)
                    for line in old_lines[i1:i2]
                ) or any(
                    line.strip() and not is_preservable_equal_context_line(line)
                    for line in new_lines[j1:j2]
                )
                for old_line, new_line in equal_context_pairs:
                    if not old_changed or old_changed[-1] != old_line:
                        old_changed.append(old_line)
                    if not new_changed or new_changed[-1] != new_line:
                        new_changed.append(new_line)
                if has_non_preservable_omitted:
                    if old_changed and old_changed[-1] != OMITTED_EQUAL_MARKER:
                        old_changed.append(OMITTED_EQUAL_MARKER)
                    if new_changed and new_changed[-1] != OMITTED_EQUAL_MARKER:
                        new_changed.append(OMITTED_EQUAL_MARKER)
                continue
            if has_previous_change and has_omitted_content and (has_later_change or has_replace_in_chain):
                if old_changed and old_changed[-1] != OMITTED_EQUAL_MARKER:
                    old_changed.append(OMITTED_EQUAL_MARKER)
                if new_changed and new_changed[-1] != OMITTED_EQUAL_MARKER:
                    new_changed.append(OMITTED_EQUAL_MARKER)
            elif (
                not has_previous_change
                and has_omitted_content
                and has_later_change
            ):
                # 开头 equal block 含非空相同正文(且未识别为 preservable heading)被剥,
                # 后面还有 change。此时本 item 的差异不是从原文开头开始, 应在头部补 `......`,
                # 让 docx 渲染时 subchapter 与 changed 行之间能看出有省略 (用户场景:
                # 八、xxx 后接 "发生上述情形之一..." 这类非 numbered 长正文)。
                if not old_changed or old_changed[-1] != OMITTED_EQUAL_MARKER:
                    old_changed.append(OMITTED_EQUAL_MARKER)
                if not new_changed or new_changed[-1] != OMITTED_EQUAL_MARKER:
                    new_changed.append(OMITTED_EQUAL_MARKER)
            continue
        if tag in {"replace", "delete"}:
            old_changed.extend(line for line in old_lines[i1:i2] if line.strip())
        if tag in {"replace", "insert"}:
            new_changed.extend(line for line in new_lines[j1:j2] if line.strip())
    # 头部 marker: 首条编号 > 1 说明前面同 subchapter 内还有更早条款被切到本 item 之外;
    # 视觉上需要在最前面补 `......`, 否则 docx 渲染会把 subchapter 紧贴 "2、" 看起来没省略。
    # 仅在双侧都有内容(真实 replace 场景)时补; 单 delete/insert 整段已经用 "删除/新增"
    # marker 表达, 不必再添加头部省略号。
    def _prepend_marker_if_first_ordinal_gt_one(lines: list[str]) -> None:
        if not lines:
            return
        head = lines[0]
        if head == OMITTED_EQUAL_MARKER:
            return
        ordinal = _first_line_clause_ordinal(head)
        if ordinal is not None and ordinal > 1:
            lines.insert(0, OMITTED_EQUAL_MARKER)

    if old_changed and new_changed:
        _prepend_marker_if_first_ordinal_gt_one(old_changed)
        _prepend_marker_if_first_ordinal_gt_one(new_changed)

    old_result = "\n".join(old_changed).strip()
    new_result = "\n".join(new_changed).strip()
    if not old_result and new_result:
        old_result = "新增"
    if old_result and not new_result:
        new_result = "删除"
    return old_result, new_result


def _first_line_clause_ordinal(text: str) -> int | None:
    """取文本首行的编号序号。'2、xxx'→2; '（一）xxx'→1; 无编号→None。"""
    if not text:
        return None
    head = text.split("\n", 1)[0]
    match = _FIRST_CLAUSE_ORDINAL_RE.match(head)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    if not raw:
        return None
    if raw.isdigit():
        try:
            return int(raw)
        except ValueError:
            return None
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(raw) == 1:
        return _CHINESE_DIGITS.get(raw)
    return None
