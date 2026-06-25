from __future__ import annotations

from ..chunking.constants import OMITTED_EQUAL_MARKER
from ..models import ComparisonRow


def strip_trailing_ellipsis_per_cell(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """去掉每个 cell 文本末尾的 `......` — cell 是视觉终点, 不需要"后面还有"提示。

    `remove_fully_equal_lines` 给每个 item 尾部加 `......` 是为了让 `merge_same_subchapter_rows_with_ellipsis`
    能识别"上一项有省略"; 一旦合并完成、不再追加内容, cell 末尾那个 marker 反而误导(它说"后面还有",
    但 cell 已结束)。本步应放在合并之后、写 docx 之前。
    """
    if not rows:
        return rows
    cleaned: list[ComparisonRow] = []
    for row in rows:
        def _strip(text: str) -> str:
            stripped = text.rstrip()
            while stripped.endswith(OMITTED_EQUAL_MARKER):
                stripped = stripped[: -len(OMITTED_EQUAL_MARKER)].rstrip()
            return stripped

        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_strip(row.old_text),
                new_text=_strip(row.new_text),
            )
        )
    return cleaned


# === 按 CLAUDE.md 规则: 新增函数追加到文件末尾 ===


def strip_leading_ellipsis_when_no_subchapter(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """当 row.subchapter 为空(chapter 下散条目)时, 去掉 cell 文本开头的 `......`。

    `remove_fully_equal_lines` 在首条编号 > 1 时会前置 `......`, 是为了让 `subchapter\\n2、xxx`
    在 docx 渲染时不直接相连; 但若 row 没有 subchapter, cell 顶部就是这条 item 自身, 它本来就是
    一条独立的条目对比 (如释义里的 `4、基金合同：...`), 加 `......` 反而像 "前面有省略" 但前面
    其实没有同 subchapter 的上下文 — 这是误导。本步骤仅处理空 subchapter, 不动有 subchapter 的行。
    """
    if not rows:
        return rows
    cleaned: list[ComparisonRow] = []
    for row in rows:
        sub = str(row.subchapter or "").strip()
        if sub:
            cleaned.append(row)
            continue
        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_strip_leading_omitted_marker_lines(row.old_text),
                new_text=_strip_leading_omitted_marker_lines(row.new_text),
            )
        )
    return cleaned


def _strip_leading_omitted_marker_lines(text: str) -> str:
    """从文本开头连续去掉等于 `......` 的行(允许仅含空白), 返回剩余文本。"""
    lines = text.split("\n")
    while lines and lines[0].strip() == OMITTED_EQUAL_MARKER:
        lines.pop(0)
    return "\n".join(lines)


def dedupe_consecutive_omitted_markers(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """折叠每条 row 内同一 cell 文本中连续多次出现的 `......` 行为一行。

    出现连续 `......` 的根因: `remove_fully_equal_lines` 给每个 item 加了头部/尾部 marker,
    `merge_same_subchapter_rows_with_ellipsis` 合并相邻 row 时再次拼接, 若 prev row 尾与
    next row 头都带 marker, 合并结果就是 `...... \\n ......`。这里做兜底统一去重, 避免
    每个合并点都得专门判定。
    """
    if not rows:
        return rows
    cleaned: list[ComparisonRow] = []
    for row in rows:
        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_collapse_consecutive_omitted_marker_lines(row.old_text),
                new_text=_collapse_consecutive_omitted_marker_lines(row.new_text),
            )
        )
    return cleaned


def _collapse_consecutive_omitted_marker_lines(text: str) -> str:
    """文本内, 连续多行 `......`(允许仅含空白) 折叠成单行 marker, 保留其它内容原样。"""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if line.strip() == OMITTED_EQUAL_MARKER:
            if out and out[-1].strip() == OMITTED_EQUAL_MARKER:
                continue
        out.append(line)
    return "\n".join(out)
