from __future__ import annotations

from ..chunking.constants import OMITTED_EQUAL_MARKER
from ..models import ComparisonRow, Section
from .cell_heading_markers import (
    _HEADING_CN_RE,
    _HEADING_NUMERIC_RE,
    _HEADING_PAREN_CN_RE,
)


def insert_marker_when_subchapter_intro_omitted(
    rows: list[ComparisonRow],
    *,
    old_sections: list[Section],
    new_sections: list[Section],
) -> list[ComparisonRow]:
    """row-level 后处理: 当 `row.subchapter` 是 chinese/paren_cn heading, row 文本首条是 numbered,
    且 chapter body 里 subchapter 与该 numbered 之间存在 intro 引导段或前置编号被剥时,
    在 row 文本开头插入 `......`。

    重要: subchapter 文本不在 `row.old_text/new_text` 内 — 它是单独字段, 由 writer 的
    `display_text_with_subchapter` prepend 到 cell 顶部, 这里只能改 row 文本本身。
    `insert_marker_between_heading_and_numbered_continuation` 只看 cell text 内的 heading,
    无法覆盖此场景, 本函数补足这条 row 级路径。

    用户场景 (qus16): `第八部分 / 九、实施侧袋机制期间基金份额持有人大会的特殊约定 / 1、基金份额持有人...`
    body 里 "九、" 与 "1、" 之间有大段引导句被剥, 用户在 cell 顶部看不到省略提示。
    """
    if not rows:
        return rows
    old_body_by_title = {s.title.strip(): s.body for s in old_sections if s.title}
    new_body_by_title = {s.title.strip(): s.body for s in new_sections if s.title}
    cleaned: list[ComparisonRow] = []
    for row in rows:
        sub = (row.subchapter or "").strip()
        if not sub or not (_HEADING_CN_RE.match(sub) or _HEADING_PAREN_CN_RE.match(sub)):
            cleaned.append(row)
            continue
        chap = (row.chapter or "").strip()
        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_prepend_marker_when_intro_omitted(
                    row.old_text, sub, old_body_by_title.get(chap, "")
                ),
                new_text=_prepend_marker_when_intro_omitted(
                    row.new_text, sub, new_body_by_title.get(chap, "")
                ),
            )
        )
    return cleaned


def _prepend_marker_when_intro_omitted(text: str, subchapter: str, body: str) -> str:
    """row 首条是 numbered, body 里 subchapter 与该 numbered 之间有 intro 或前置编号被剥, 头部加 marker。"""
    if not text or text in ("新增", "删除") or not body:
        return text
    lines = text.split("\n")
    leading = next((l for l in lines if l.strip()), "")
    if leading.strip() == OMITTED_EQUAL_MARKER:
        return text
    first_non_marker = next(
        (l for l in lines if l.strip() and l.strip() != OMITTED_EQUAL_MARKER),
        "",
    )
    match = _HEADING_NUMERIC_RE.match(first_non_marker.strip())
    if not match:
        return text
    try:
        row_first_ordinal = int(match.group(1))
    except (ValueError, IndexError):
        return text
    if not _body_has_omitted_between_subchapter_and_row_numbered(
        body, subchapter, row_first_ordinal
    ):
        return text
    return OMITTED_EQUAL_MARKER + "\n" + text


def _body_has_omitted_between_subchapter_and_row_numbered(
    body: str, subchapter: str, row_first_ordinal: int
) -> bool:
    """body 里 subchapter 与 row 首条 numbered 之间是否有被剥内容。

    覆盖两类:
    - body 中 subchapter 下首段是 intro 引导句 (非 heading) → 必然被剥;
    - body 中 subchapter 下首个 numbered 的编号 < row 首条编号 → 前置条款被剥 (如 1..3 被剥, row 首条是 4)。
    """
    body_lines = body.split("\n")
    h_idx = -1
    for i, l in enumerate(body_lines):
        if l.strip() == subchapter.strip():
            h_idx = i
            break
    if h_idx < 0:
        return False
    for j in range(h_idx + 1, len(body_lines)):
        s = body_lines[j].strip()
        if not s:
            continue
        numeric_match = _HEADING_NUMERIC_RE.match(s)
        if numeric_match:
            try:
                body_first_ordinal = int(numeric_match.group(1))
            except (ValueError, IndexError):
                return False
            return row_first_ordinal > body_first_ordinal
        if _HEADING_CN_RE.match(s) or _HEADING_PAREN_CN_RE.match(s):
            return False
        return True
    return False
