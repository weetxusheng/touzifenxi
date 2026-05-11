"""对照行级合并与章题补行（`ComparisonRow` 列表阶段）。

职责：在 `rows_from_block_operations_payload` 产出行之后、产品名等专项规范化之前，
按章节合并连续删除、同章纯删/纯增段，并据 Section 元数据插入同号章题变更行。
正文拼接委托 `row_combine`，本模块只决定「哪些行合成一行」。
不负责：块内 operation 规范化、相似度 replace（见 `normalize_payload`、`block_rows`）。
"""

from __future__ import annotations

from ..models import ComparisonRow, Section
from .row_combine import combine_comparison_run_text


def _is_pure_delete_row(row: ComparisonRow) -> bool:
    return str(row.new_text).strip() == "删除" and str(row.old_text).strip() not in ("", "新增", "删除")


def _is_pure_add_row(row: ComparisonRow) -> bool:
    return str(row.old_text).strip() == "新增" and str(row.new_text).strip() not in ("", "删除", "新增")


def merge_pure_delete_and_add_runs(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """同一章节内连续、且每行仅为「纯删除」或「纯新增」时，若同时含删与增则并为一行（不检验相似度）。

    适用于整章/整段在模型侧只产出 delete 与 add、无 replace 的展示（如第四部分旧章整删 + 新侧一条说明）；
    在 `merge_consecutive_delete_rows` 与 `insert_section_title_change_rows` 之后再执行。
    """
    if not rows:
        return rows
    out: list[ComparisonRow] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        if not (_is_pure_delete_row(row) or _is_pure_add_row(row)):
            out.append(row)
            i += 1
            continue
        chapter = row.chapter
        run: list[ComparisonRow] = []
        while i < len(rows) and rows[i].chapter == chapter and (
            _is_pure_delete_row(rows[i]) or _is_pure_add_row(rows[i])
        ):
            run.append(rows[i])
            i += 1
        del_rows = [r for r in run if _is_pure_delete_row(r)]
        add_rows = [r for r in run if _is_pure_add_row(r)]
        if del_rows and add_rows:
            out.append(
                ComparisonRow(
                    chapter=chapter,
                    subchapter="",
                    old_text=combine_comparison_run_text(del_rows, side="old"),
                    new_text=combine_comparison_run_text(add_rows, side="new"),
                )
            )
        else:
            out.extend(run)
    return out


def merge_consecutive_delete_rows(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """同一章节下连续「右侧为删除」合并为一行（小标题可不同），减少表格碎行与章节内不对齐感。"""
    if not rows:
        return rows
    out: list[ComparisonRow] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if row.new_text != "删除":
            out.append(row)
            index += 1
            continue
        chapter = row.chapter
        run_start = index
        run_rows: list[ComparisonRow] = [row]
        index += 1
        while index < len(rows) and rows[index].new_text == "删除" and rows[index].chapter == chapter:
            run_rows.append(rows[index])
            index += 1
        if len(run_rows) == 1:
            out.append(rows[run_start])
            continue
        out.append(
            ComparisonRow(
                chapter=chapter,
                subchapter="",
                old_text=combine_comparison_run_text(run_rows, side="old"),
                new_text="删除",
            )
        )
    return out


def insert_section_title_change_rows(
    rows: list[ComparisonRow],
    *,
    old_sections: list[Section],
    new_sections: list[Section],
) -> list[ComparisonRow]:
    """同号章节在旧/新文档中 Section.title 不一致时，在该章节首条内容行前插入一行「旧题 | 新题」。

    `CompareBlock.chapter_title` 与各行 `chapter` 列均取自旧版标题，模型批次里通常也不会单独报章节名变更；
    本函数在合并 batch 行之后补足，使「第四部分 … 发售」→「第四部分 … 发售历史沿革」出现在同一数据行左右列。
    """
    if not rows or not old_sections or not new_sections:
        return rows
    old_by_number = {s.number: s for s in old_sections}
    new_by_number = {s.number: s for s in new_sections}
    changed: dict[str, tuple[str, str]] = {}
    for number, old_sec in old_by_number.items():
        new_sec = new_by_number.get(number)
        if new_sec is None:
            continue
        o_title = old_sec.title.strip()
        n_title = new_sec.title.strip()
        if o_title != n_title:
            changed[number] = (o_title, n_title)
    if not changed:
        return rows
    title_to_number = {s.title.strip(): s.number for s in old_sections}
    already_covered: set[str] = set()
    for row in rows:
        number = title_to_number.get(str(row.chapter).strip())
        if not number or number not in changed:
            continue
        o_title, n_title = changed[number]
        if str(row.old_text).strip() == o_title and str(row.new_text).strip() == n_title:
            already_covered.add(number)
    inserted: set[str] = set()
    out: list[ComparisonRow] = []
    for row in rows:
        number = title_to_number.get(str(row.chapter).strip())
        if (
            number
            and number in changed
            and number not in already_covered
            and number not in inserted
        ):
            o_title, n_title = changed[number]
            out.append(
                ComparisonRow(
                    chapter=row.chapter,
                    subchapter="",
                    old_text=o_title,
                    new_text=n_title,
                )
            )
            inserted.add(number)
        out.append(row)
    return out
