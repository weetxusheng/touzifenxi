from __future__ import annotations

import re

from ..chunking.constants import OMITTED_EQUAL_MARKER
from ..models import ComparisonRow


_CHINESE_DIGIT_MAP = {c: i for i, c in enumerate("零一二三四五六七八九", start=0)}
_CLAUSE_ORDINAL_RE = re.compile(
    r"^\s*(?:[（(]([一二三四五六七八九十百零\d]+)[)）]|([一二三四五六七八九十百零\d]+)、)"
)


def _chinese_to_int(token: str) -> int | None:
    if not token:
        return None
    if token.isdigit():
        try:
            return int(token)
        except ValueError:
            return None
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CHINESE_DIGIT_MAP.get(left, 1) if left else 1
        ones = _CHINESE_DIGIT_MAP.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(token) == 1:
        return _CHINESE_DIGIT_MAP.get(token)
    return None


def _extract_clause_ordinal(text: str) -> int | None:
    """取首行的子目/条款序号 — 用于判断相邻行子目编号是否连续。"""
    if not text:
        return None
    first_line = text.split("\n", 1)[0]
    match = _CLAUSE_ORDINAL_RE.match(first_line)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    return _chinese_to_int(raw)


def _extract_first_inner_clause_ordinal(text: str) -> int | None:
    """子目编号开头 + 下一行也是更细级条款编号时, 返回条款编号; 用于检测"子目内部前面有省略"。

    例: row.old_text = '（二）基金管理人的权利与义务\n（12）依照法律法规...'
        第一行 (二) 是子目编号 → 第二行 (12) 是条款编号 → 返回 12
        若条款编号 > 1, 说明该子目内部 (1)~(M-1) 被省略, 应在新子目前插 `......`。
    """
    if not text:
        return None
    lines = text.split("\n", 2)
    if len(lines) < 2:
        return None
    if _extract_clause_ordinal(lines[0]) is None:
        return None
    return _extract_clause_ordinal(lines[1])


def _is_pure_delete_or_insert_special_row(row: ComparisonRow) -> bool:
    """识别 old_text='新增' 或 new_text='删除' 这类整段标记行, 它们不参与同小节合并。"""
    return str(row.old_text).strip() == "新增" or str(row.new_text).strip() == "删除"


def merge_same_subchapter_rows_with_ellipsis(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """同 chapter+subchapter 内相邻行合并到一个 cell, 行间按需补 `......`。

    合并条件:
    - 同 chapter, 同非空 subchapter
    - 上下两行都不是整段"新增/删除"特殊行(它们字级 diff 走整行标记, 不能与其它合并)

    分隔符决策:
    - 子目编号跳号 (如 (一)→(四)) 或 子目内部首条编号 > 1 → 中间需要 `......`
    - 上一行尾或下一行首已有 `......` 时, 即便条件成立也不再补, 避免连续两行 `......`

    本步骤先在每个被合并的 cell 内保留尾部 `......`(若 remove_fully_equal_lines 加过) — 让下一轮
    合并决策时能识别"上一行有省略"; 待全部 row 合并完, 由 `strip_trailing_ellipsis_per_cell`
    统一去掉行末 marker, 因为 cell 是视觉终点, 不需要"后面还有"提示。
    """
    if not rows:
        return rows
    out: list[ComparisonRow] = []
    last_ordinal: int | None = None
    for row in rows:
        last = out[-1] if out else None
        if _is_pure_delete_or_insert_special_row(row):
            out.append(row)
            last_ordinal = None
            continue
        same_subchapter_group = (
            last is not None
            and not _is_pure_delete_or_insert_special_row(last)
            and last.chapter == row.chapter
            and last.subchapter == row.subchapter
            and str(row.subchapter or "") != ""
        )
        curr_ordinal = _extract_clause_ordinal(row.old_text) or _extract_clause_ordinal(row.new_text)
        if same_subchapter_group:
            inter_clause_gap = (
                last_ordinal is not None and curr_ordinal is not None and curr_ordinal > last_ordinal + 1
            )
            inner_first = (
                _extract_first_inner_clause_ordinal(row.old_text)
                or _extract_first_inner_clause_ordinal(row.new_text)
            )
            inner_first_gap = inner_first is not None and inner_first > 1
            need_ellipsis = inter_clause_gap or inner_first_gap

            def _join(prev_text: str, next_text: str) -> str:
                prev_has_tail = prev_text.rstrip().endswith(OMITTED_EQUAL_MARKER)
                next_has_head = next_text.lstrip().startswith(OMITTED_EQUAL_MARKER)
                if need_ellipsis and not (prev_has_tail or next_has_head):
                    return f"{prev_text}\n{OMITTED_EQUAL_MARKER}\n{next_text}"
                return f"{prev_text}\n{next_text}"

            out[-1] = ComparisonRow(
                chapter=last.chapter,
                subchapter=last.subchapter,
                old_text=_join(last.old_text, row.old_text),
                new_text=_join(last.new_text, row.new_text),
            )
        else:
            out.append(row)
            last_ordinal = None
        if curr_ordinal is not None:
            last_ordinal = curr_ordinal
    return out
