"""模型返回结果到最终对照行的后处理。"""

from __future__ import annotations

import copy
import difflib
import re
from typing import Any

from .chunking import (
    is_only_numbering_changed,
    remove_fully_equal_lines,
    strip_leading_numbering,
    text_starts_with_subchapter,
)
from .models import ComparisonRow, Section

PRODUCT_NAME_CHAPTER = "基金名称"


def rows_from_llm_payload(
    payload: dict[str, Any],
    compare_units: tuple[Any, ...] = (),
    compare_blocks: tuple[Any, ...] = (),
) -> list[ComparisonRow]:
    """把模型返回的结构化 payload 转成最终展示行。"""
    if isinstance(payload.get("blocks"), list):
        payload = normalize_block_operations_payload(payload, compare_blocks=compare_blocks)
        payload = copy.deepcopy(payload)
        coerce_shifted_clause_add_delete_to_replace(payload, compare_blocks=compare_blocks)
        drop_redundant_adds_when_replace_reuses_new_items(payload)
        return rows_from_block_operations_payload(payload, compare_blocks=compare_blocks)
    if isinstance(payload.get("units"), list):
        return rows_from_unit_decision_payload(payload, compare_units)
    rows: list[ComparisonRow] = []
    for chapter in payload.get("chapters", []):
        chapter_name = str(chapter.get("chapter", "")).strip()
        for subsection in chapter.get("subsections", []):
            old_text = str(subsection.get("old_text", "")).strip()
            new_text = str(subsection.get("new_text", "")).strip()
            if subsection.get("numbering_only"):
                continue
            if old_text and new_text and is_only_numbering_changed(old_text, new_text):
                continue
            equal_lines = {line.strip() for line in subsection.get("fully_equal_lines", []) if str(line).strip()}
            if equal_lines:
                old_text = "\n".join(line for line in old_text.split("\n") if line.strip() not in equal_lines).strip()
                new_text = "\n".join(line for line in new_text.split("\n") if line.strip() not in equal_lines).strip()
            old_text, new_text = remove_fully_equal_lines(old_text, new_text)
            if not old_text and not new_text:
                continue
            rows.append(
                ComparisonRow(
                    chapter=chapter_name,
                    subchapter=str(subsection.get("subchapter", "")).strip(),
                    old_text=old_text or "新增",
                    new_text=new_text or "删除",
                )
            )
    return rows


def block_lookup_id(block_id: str) -> str:
    """把 split 后的 part block_id 归一到原始父块，便于回查源文本。"""
    normalized = str(block_id).strip()
    if "-part-" in normalized:
        return normalized.split("-part-", 1)[0]
    return normalized


def compare_block_item_text_maps(compare_blocks: tuple[Any, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """汇总本批所有 compare block 的 item_id → 正文，供跨块引用 old/new_item_ids 时回查。"""
    old_by_id: dict[str, str] = {}
    new_by_id: dict[str, str] = {}
    for block in compare_blocks:
        for item in block.old_items:
            old_by_id[str(item.item_id)] = str(item.text).strip()
        for item in block.new_items:
            new_by_id[str(item.item_id)] = str(item.text).strip()
    return old_by_id, new_by_id


def _resolve_operation_item_texts(
    item_ids: list[str],
    *,
    local_by_id: dict[str, str],
    global_by_id: dict[str, str],
) -> str:
    """先查当前块，再查批次内全局 map（模型可能在 replace 里引用其它块的 item_id）。"""
    parts: list[str] = []
    for raw_id in item_ids:
        item_id = str(raw_id).strip()
        text = local_by_id.get(item_id) or global_by_id.get(item_id, "")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _clause_relocation_similarity(old_text: str, new_text: str) -> float:
    """判断「某条 delete 的旧正文」与「另一处 add 的新正文」是否实为同一条款挪位（如整章根下新增 vs 小标题下整删）。"""
    a = old_text.strip()
    b = new_text.strip()
    if not a or not b:
        return 0.0
    strip_a = strip_leading_numbering(a)
    strip_b = strip_leading_numbering(b)
    stripped_ratio = difflib.SequenceMatcher(None, strip_a, strip_b).ratio()
    raw_ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return max(stripped_ratio, raw_ratio)


def coerce_shifted_clause_add_delete_to_replace(
    payload: dict[str, Any],
    compare_blocks: tuple[Any, ...],
    *,
    similarity_threshold: float = 0.85,
) -> None:
    """将同章内「仅 new 的根块 add」与「高度相似正文的 delete」并为 replace，避免无/新增 + 连续删把「三」并进「一、二」。

    典型：第五部分 block-001 add(new-001) 与 block-004 delete(old-006) 实为修订，模型拆成 add+delete 时在此纠偏。
    同块内「单删 + 单增」且高相似时的一行展示由 `merge_lone_delete_add_when_no_replace_in_block` 负责，不依赖本函数的相似度。
    """
    block_list = payload.get("blocks")
    if not isinstance(block_list, list) or not compare_blocks:
        return
    global_old, global_new = compare_block_item_text_maps(compare_blocks)
    empty_local: dict[str, str] = {}

    add_ops: list[tuple[int, int, list[str], str, str]] = []
    del_ops: list[tuple[int, int, list[str], str, str]] = []
    for bi, bp in enumerate(block_list):
        if not isinstance(bp, dict):
            continue
        chapter_key = str(bp.get("chapter", "")).strip()
        ops = bp.get("operations")
        if not isinstance(ops, list):
            continue
        for oi, op in enumerate(ops):
            if not isinstance(op, dict):
                continue
            typ = str(op.get("type", "")).strip()
            if typ == "add":
                new_ids = [str(x).strip() for x in op.get("new_item_ids", []) if str(x).strip()]
                old_ids = [str(x).strip() for x in op.get("old_item_ids", []) if str(x).strip()]
                if not new_ids or old_ids:
                    continue
                nt = _resolve_operation_item_texts(new_ids, local_by_id=empty_local, global_by_id=global_new)
                if not nt.strip():
                    continue
                add_ops.append((bi, oi, new_ids, nt.strip(), chapter_key))
            elif typ == "delete":
                old_ids = [str(x).strip() for x in op.get("old_item_ids", []) if str(x).strip()]
                new_ids = [str(x).strip() for x in op.get("new_item_ids", []) if str(x).strip()]
                if not old_ids or new_ids:
                    continue
                ot = _resolve_operation_item_texts(old_ids, local_by_id=empty_local, global_by_id=global_old)
                if not ot.strip():
                    continue
                del_ops.append((bi, oi, old_ids, ot.strip(), chapter_key))

    candidates: list[tuple[float, tuple[int, int], tuple[int, int], list[str]]] = []
    for abi, aoi, new_ids, nt, ch_a in add_ops:
        for dbi, doi, _old_ids, ot, ch_d in del_ops:
            if ch_a != ch_d or abi == dbi:
                continue
            sim = _clause_relocation_similarity(ot, nt)
            if sim >= similarity_threshold:
                candidates.append((sim, (abi, aoi), (dbi, doi), new_ids))
    candidates.sort(key=lambda item: item[0], reverse=True)

    matched_add: set[tuple[int, int]] = set()
    matched_del: set[tuple[int, int]] = set()
    del_to_new_ids: dict[tuple[int, int], list[str]] = {}
    for _sim, add_ref, del_ref, new_ids in candidates:
        if add_ref in matched_add or del_ref in matched_del:
            continue
        matched_add.add(add_ref)
        matched_del.add(del_ref)
        del_to_new_ids[del_ref] = new_ids

    for bi, bp in enumerate(block_list):
        if not isinstance(bp, dict):
            continue
        ops = bp.get("operations")
        if not isinstance(ops, list):
            continue
        rebuilt: list[dict[str, Any]] = []
        for oi, op in enumerate(ops):
            if not isinstance(op, dict):
                continue
            if (bi, oi) in matched_add:
                continue
            if (bi, oi) in del_to_new_ids:
                new_op = dict(op)
                new_op["type"] = "replace"
                new_op["old_item_ids"] = list(op.get("old_item_ids", []))
                new_op["new_item_ids"] = list(del_to_new_ids[(bi, oi)])
                rebuilt.append(new_op)
                continue
            rebuilt.append(op)
        bp["operations"] = rebuilt


def drop_redundant_adds_when_replace_reuses_new_items(payload: dict[str, Any]) -> None:
    """同章内若已有 replace 引用某 new_item_id，则去掉仅重复展示该条的 add（模型常同时写根下 add 与小标题下 replace）。"""
    block_list = payload.get("blocks")
    if not isinstance(block_list, list):
        return
    new_ids_in_replace_by_chapter: dict[str, set[str]] = {}
    for bp in block_list:
        if not isinstance(bp, dict):
            continue
        chapter_key = str(bp.get("chapter", "")).strip()
        for op in bp.get("operations", []) or []:
            if not isinstance(op, dict):
                continue
            if str(op.get("type", "")).strip() != "replace":
                continue
            bucket = new_ids_in_replace_by_chapter.setdefault(chapter_key, set())
            for raw_id in op.get("new_item_ids", []) or []:
                nid = str(raw_id).strip()
                if nid:
                    bucket.add(nid)

    for bp in block_list:
        if not isinstance(bp, dict):
            continue
        chapter_key = str(bp.get("chapter", "")).strip()
        reused = new_ids_in_replace_by_chapter.get(chapter_key, set())
        if not reused:
            continue
        ops = bp.get("operations")
        if not isinstance(ops, list):
            continue
        rebuilt: list[dict[str, Any]] = []
        for op in ops:
            if not isinstance(op, dict):
                rebuilt.append(op)
                continue
            if str(op.get("type", "")).strip() != "add":
                rebuilt.append(op)
                continue
            new_ids = [str(x).strip() for x in op.get("new_item_ids", []) if str(x).strip()]
            if new_ids and all(nid in reused for nid in new_ids):
                continue
            rebuilt.append(op)
        bp["operations"] = rebuilt


def _combine_deleted_run_old_text(run_rows: list[ComparisonRow]) -> str:
    """合并连续删除段的左侧正文；小标题不同时各自保留在段首，同小标题续段只拼正文。"""
    parts: list[str] = []
    last_subchapter: str | None = None
    for row in run_rows:
        sc = str(row.subchapter or "").strip()
        body = str(row.old_text or "").strip()
        if body in {"", "新增", "删除"}:
            if body:
                parts.append(body)
            last_subchapter = None
            continue
        if sc:
            if text_starts_with_subchapter(body, sc):
                segment = body
            else:
                segment = f"{sc}\n{body}"
            if last_subchapter == sc and parts:
                parts.append(body)
            else:
                parts.append(segment)
            last_subchapter = sc
        else:
            parts.append(body)
            last_subchapter = None
    return "\n".join(p for p in parts if p).strip()


def _combine_added_run_new_text(run_rows: list[ComparisonRow]) -> str:
    """合并连续「新增」占位行的右侧正文，规则与 `_combine_deleted_run_old_text` 对称。"""
    parts: list[str] = []
    last_subchapter: str | None = None
    for row in run_rows:
        sc = str(row.subchapter or "").strip()
        body = str(row.new_text or "").strip()
        if body in {"", "新增", "删除"}:
            if body:
                parts.append(body)
            last_subchapter = None
            continue
        if sc:
            if text_starts_with_subchapter(body, sc):
                segment = body
            else:
                segment = f"{sc}\n{body}"
            if last_subchapter == sc and parts:
                parts.append(body)
            else:
                parts.append(segment)
            last_subchapter = sc
        else:
            parts.append(body)
            last_subchapter = None
    return "\n".join(p for p in parts if p).strip()


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
                    old_text=_combine_deleted_run_old_text(del_rows),
                    new_text=_combine_added_run_new_text(add_rows),
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
                old_text=_combine_deleted_run_old_text(run_rows),
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


def block_item_text_from_ids(*, item_ids: list[str], items_by_id: dict[str, str], fallback_text: str = "") -> str:
    """优先按 item_id 回查原始条目文本，缺失时回退到模型 focus_text。"""
    text = "\n".join(items_by_id[item_id] for item_id in item_ids if item_id in items_by_id and items_by_id[item_id]).strip()
    return text or fallback_text.strip()


def normalize_operation_text_for_numbering(text: str) -> str:
    """去掉常见编号前缀后返回正文，用于识别误判的 add/delete 顺延项。"""
    lines = [normalize_line_for_shift_compare(line) for line in str(text).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalize_block_operations_payload(payload: dict[str, Any], *, compare_blocks: tuple[Any, ...]) -> dict[str, Any]:
    """把模型误判的 add/delete 顺延项折叠成 renumber_only，避免误报新增/删除。"""
    if not isinstance(payload.get("blocks"), list) or not compare_blocks:
        return payload
    blocks_by_id = {str(block.block_id): block for block in compare_blocks}
    normalized_blocks: list[dict[str, Any]] = []
    source_blocks_for_payload: list[Any | None] = []
    for block_payload in payload.get("blocks", []):
        payload_block_id = str(block_payload.get("block_id", "")).strip()
        source_block = blocks_by_id.get(block_lookup_id(payload_block_id))
        operations = block_payload.get("operations", [])
        if source_block is None or not isinstance(operations, list):
            normalized_blocks.append(block_payload)
            source_blocks_for_payload.append(source_block)
            continue
        old_items_by_id = {str(item.item_id): str(item.text).strip() for item in source_block.old_items}
        new_items_by_id = {str(item.item_id): str(item.text).strip() for item in source_block.new_items}
        normalized_operations = [dict(operation) for operation in operations if isinstance(operation, dict)]
        normalized_operations = [
            repair_operation_item_ids_from_focus_text(
                operation,
                old_items_by_id=old_items_by_id,
                new_items_by_id=new_items_by_id,
            )
            for operation in normalized_operations
        ]
        for operation in normalized_operations:
            if str(operation.get("type", "")).strip() != "replace":
                continue
            old_text = block_item_text_from_ids(
                item_ids=list(operation.get("old_item_ids", [])),
                items_by_id=old_items_by_id,
                fallback_text=str(operation.get("old_focus_text", "")),
            )
            new_text = block_item_text_from_ids(
                item_ids=list(operation.get("new_item_ids", [])),
                items_by_id=new_items_by_id,
                fallback_text=str(operation.get("new_focus_text", "")),
            )
            if old_text and new_text and is_only_numbering_changed(old_text, new_text):
                operation["type"] = "renumber_only"
        add_candidates: list[dict[str, Any]] = []
        delete_candidates: list[dict[str, Any]] = []
        for index, operation in enumerate(normalized_operations):
            operation_type = str(operation.get("type", "")).strip()
            if operation_type == "add":
                new_text = block_item_text_from_ids(
                    item_ids=list(operation.get("new_item_ids", [])),
                    items_by_id=new_items_by_id,
                    fallback_text=str(operation.get("new_focus_text", "")),
                )
                add_candidates.append(
                    {
                        "index": index,
                        "operation": operation,
                        "normalized_text": normalize_operation_text_for_numbering(new_text),
                        "new_text": new_text,
                    }
                )
            elif operation_type == "delete":
                old_text = block_item_text_from_ids(
                    item_ids=list(operation.get("old_item_ids", [])),
                    items_by_id=old_items_by_id,
                    fallback_text=str(operation.get("old_focus_text", "")),
                )
                delete_candidates.append(
                    {
                        "index": index,
                        "operation": operation,
                        "normalized_text": normalize_operation_text_for_numbering(old_text),
                        "old_text": old_text,
                    }
                )
        matched_add_indexes: set[int] = set()
        delete_replacements: dict[int, dict[str, Any]] = {}
        for delete_candidate in delete_candidates:
            normalized_text = str(delete_candidate["normalized_text"]).strip()
            if not normalized_text:
                continue
            matched_add = next(
                (
                    add_candidate
                    for add_candidate in add_candidates
                    if add_candidate["index"] not in matched_add_indexes
                    and add_candidate["normalized_text"] == normalized_text
                ),
                None,
            )
            if matched_add is None:
                continue
            matched_add_indexes.add(int(matched_add["index"]))
            delete_operation = delete_candidate["operation"]
            add_operation = matched_add["operation"]
            delete_replacements[int(delete_candidate["index"])] = {
                "type": "renumber_only",
                "old_item_ids": list(delete_operation.get("old_item_ids", [])),
                "new_item_ids": list(add_operation.get("new_item_ids", [])),
                "old_focus_text": delete_candidate["old_text"],
                "new_focus_text": matched_add["new_text"],
                "confidence": min(
                    float(delete_operation.get("confidence", 1.0) or 1.0),
                    float(add_operation.get("confidence", 1.0) or 1.0),
                ),
                "reason": "内容完全一致，仅编号顺延；后处理已将误判的新增/删除归并为 renumber_only。",
            }
        rebuilt_operations: list[dict[str, Any]] = []
        for index, operation in enumerate(normalized_operations):
            if index in matched_add_indexes:
                continue
            replacement = delete_replacements.get(index)
            if replacement is not None:
                rebuilt_operations.append(replacement)
                continue
            rebuilt_operations.append(operation)
        rebuilt_operations = repair_shifted_item_misreplace_operations(
            rebuilt_operations,
            old_items_by_id=old_items_by_id,
            new_items_by_id=new_items_by_id,
        )
        if "-part-" not in payload_block_id:
            rebuilt_operations = fill_uncovered_block_item_operations(
                rebuilt_operations,
                old_items_by_id=old_items_by_id,
                new_items_by_id=new_items_by_id,
            )
        rebuilt_operations = dedupe_block_operations(rebuilt_operations)
        normalized_block = dict(block_payload)
        normalized_block["operations"] = rebuilt_operations
        normalized_blocks.append(normalized_block)
        source_blocks_for_payload.append(source_block)
    cross_add_candidates: list[dict[str, Any]] = []
    cross_delete_candidates: list[dict[str, Any]] = []
    for block_index, block_payload in enumerate(normalized_blocks):
        source_block = source_blocks_for_payload[block_index]
        if source_block is None:
            continue
        old_items_by_id = {str(item.item_id): str(item.text).strip() for item in source_block.old_items}
        new_items_by_id = {str(item.item_id): str(item.text).strip() for item in source_block.new_items}
        parent_block_id = block_lookup_id(str(block_payload.get("block_id", "")).strip())
        for operation_index, operation in enumerate(block_payload.get("operations", [])):
            operation_type = str(operation.get("type", "")).strip()
            if operation_type == "add":
                new_text = block_item_text_from_ids(
                    item_ids=list(operation.get("new_item_ids", [])),
                    items_by_id=new_items_by_id,
                    fallback_text=str(operation.get("new_focus_text", "")),
                )
                cross_add_candidates.append(
                    {
                        "ref": (block_index, operation_index),
                        "parent_block_id": parent_block_id,
                        "operation": operation,
                        "normalized_text": normalize_operation_text_for_numbering(new_text),
                        "new_text": new_text,
                    }
                )
            elif operation_type == "delete":
                old_text = block_item_text_from_ids(
                    item_ids=list(operation.get("old_item_ids", [])),
                    items_by_id=old_items_by_id,
                    fallback_text=str(operation.get("old_focus_text", "")),
                )
                cross_delete_candidates.append(
                    {
                        "ref": (block_index, operation_index),
                        "parent_block_id": parent_block_id,
                        "operation": operation,
                        "normalized_text": normalize_operation_text_for_numbering(old_text),
                        "old_text": old_text,
                    }
                )
    matched_cross_add_refs: set[tuple[int, int]] = set()
    cross_delete_replacements: dict[tuple[int, int], dict[str, Any]] = {}
    for delete_candidate in cross_delete_candidates:
        normalized_text = str(delete_candidate["normalized_text"]).strip()
        if not normalized_text:
            continue
        matched_add = next(
            (
                add_candidate
                for add_candidate in cross_add_candidates
                if add_candidate["ref"] not in matched_cross_add_refs
                and add_candidate["parent_block_id"] == delete_candidate["parent_block_id"]
                and add_candidate["normalized_text"] == normalized_text
            ),
            None,
        )
        if matched_add is None:
            continue
        matched_cross_add_refs.add(tuple(matched_add["ref"]))
        delete_operation = delete_candidate["operation"]
        add_operation = matched_add["operation"]
        cross_delete_replacements[tuple(delete_candidate["ref"])] = {
            "type": "renumber_only",
            "old_item_ids": list(delete_operation.get("old_item_ids", [])),
            "new_item_ids": list(add_operation.get("new_item_ids", [])),
            "old_focus_text": delete_candidate["old_text"],
            "new_focus_text": matched_add["new_text"],
            "confidence": min(
                float(delete_operation.get("confidence", 1.0) or 1.0),
                float(add_operation.get("confidence", 1.0) or 1.0),
            ),
            "reason": "内容完全一致，仅编号顺延；后处理已将跨 part 误判的新增/删除归并为 renumber_only。",
        }
    final_blocks: list[dict[str, Any]] = []
    for block_index, block_payload in enumerate(normalized_blocks):
        final_operations: list[dict[str, Any]] = []
        for operation_index, operation in enumerate(block_payload.get("operations", [])):
            ref = (block_index, operation_index)
            if ref in matched_cross_add_refs:
                continue
            replacement = cross_delete_replacements.get(ref)
            if replacement is not None:
                final_operations.append(replacement)
                continue
            final_operations.append(operation)
        next_block = dict(block_payload)
        next_block["operations"] = final_operations
        final_blocks.append(next_block)
    normalized_payload = dict(payload)
    normalized_payload["blocks"] = final_blocks
    return normalized_payload


def repair_operation_item_ids_from_focus_text(
    operation: dict[str, Any],
    *,
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> dict[str, Any]:
    """当模型把可见编号误写成 item_id 时，用 focus_text 回查真实 item_id。"""
    repaired = dict(operation)
    repaired["old_item_ids"] = repair_side_item_ids_from_focus_text(
        item_ids=list(operation.get("old_item_ids", [])),
        focus_text=str(operation.get("old_focus_text", "")),
        items_by_id=old_items_by_id,
    )
    repaired["new_item_ids"] = repair_side_item_ids_from_focus_text(
        item_ids=list(operation.get("new_item_ids", [])),
        focus_text=str(operation.get("new_focus_text", "")),
        items_by_id=new_items_by_id,
    )
    return repaired


def repair_side_item_ids_from_focus_text(
    *,
    item_ids: list[Any],
    focus_text: str,
    items_by_id: dict[str, str],
) -> list[str]:
    """单侧 item_id 与 focus_text 冲突时，优先采用唯一匹配 focus 的源 item。"""
    normalized_ids = [str(item_id) for item_id in item_ids]
    focus = normalize_text_for_focus_match(focus_text)
    if not focus or len(normalized_ids) > 1:
        return normalized_ids
    current_text = block_item_text_from_ids(
        item_ids=normalized_ids,
        items_by_id=items_by_id,
    )
    if text_matches_focus(current_text, focus):
        return normalized_ids
    matched_ids = [
        item_id
        for item_id, text in items_by_id.items()
        if text_matches_focus(text, focus)
    ]
    if len(matched_ids) == 1:
        return matched_ids
    return normalized_ids


def text_matches_focus(text: str, normalized_focus: str) -> bool:
    """判断源条目文本是否能解释模型返回的 focus_text。"""
    normalized_text = normalize_text_for_focus_match(text)
    if bool(normalized_text) and (
        normalized_focus == normalized_text
        or normalized_focus in normalized_text
        or normalized_text in normalized_focus
    ):
        return True
    focus_without_number = normalize_text_for_focus_match(normalize_operation_text_for_numbering(normalized_focus))
    text_without_number = normalize_text_for_focus_match(normalize_operation_text_for_numbering(normalized_text))
    return bool(focus_without_number and text_without_number) and (
        focus_without_number == text_without_number
        or focus_without_number in text_without_number
        or text_without_number in focus_without_number
    )


def normalize_text_for_focus_match(text: str) -> str:
    """压平空白以便比较模型 focus_text 与源条目。"""
    return re.sub(r"\s+", "", str(text).strip())


def repair_shifted_item_misreplace_operations(
    operations: list[dict[str, Any]],
    *,
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    """纠正模型把“正文未变但编号顺延”的条目错拿去 replace 的情况。"""
    old_to_stable_new, new_to_stable_old = stable_numbering_shift_pairs(
        old_items_by_id=old_items_by_id,
        new_items_by_id=new_items_by_id,
    )
    if not old_to_stable_new and not new_to_stable_old:
        return operations
    old_order = {item_id: index for index, item_id in enumerate(old_items_by_id)}
    new_order = {item_id: index for index, item_id in enumerate(new_items_by_id)}
    referenced_old_ids = {
        str(item_id)
        for operation in operations
        for item_id in operation.get("old_item_ids", [])
    }
    referenced_new_ids = {
        str(item_id)
        for operation in operations
        for item_id in operation.get("new_item_ids", [])
    }
    unreported_stable_pairs = [
        (old_id, new_id)
        for old_id, new_id in old_to_stable_new.items()
        if old_id not in referenced_old_ids and new_id not in referenced_new_ids
    ]
    repaired: list[dict[str, Any]] = []
    for operation in operations:
        if str(operation.get("type", "")).strip() != "replace":
            repaired.append(operation)
            continue
        old_ids = [str(item_id) for item_id in operation.get("old_item_ids", [])]
        new_ids = [str(item_id) for item_id in operation.get("new_item_ids", [])]
        unshifted_old_ids = [item_id for item_id in old_ids if item_id not in old_to_stable_new]
        unshifted_new_ids = [item_id for item_id in new_ids if item_id not in new_to_stable_old]
        has_shifted_side = len(unshifted_old_ids) != len(old_ids) or len(unshifted_new_ids) != len(new_ids)
        if not has_shifted_side:
            if should_split_crossed_shift_replace(
                old_ids=old_ids,
                new_ids=new_ids,
                unreported_stable_pairs=unreported_stable_pairs,
                old_order=old_order,
                new_order=new_order,
                old_items_by_id=old_items_by_id,
                new_items_by_id=new_items_by_id,
            ):
                repaired.extend(
                    split_unaligned_replace_as_delete_add(
                        operation,
                        old_item_ids=old_ids,
                        new_item_ids=new_ids,
                        old_items_by_id=old_items_by_id,
                        new_items_by_id=new_items_by_id,
                    )
                )
                continue
            repaired.append(operation)
            continue
        repaired.extend(
            split_operation_after_shift_repair(
                operation,
                old_item_ids=unshifted_old_ids,
                new_item_ids=unshifted_new_ids,
                old_items_by_id=old_items_by_id,
                new_items_by_id=new_items_by_id,
            )
        )
    return repaired


def should_split_crossed_shift_replace(
    *,
    old_ids: list[str],
    new_ids: list[str],
    unreported_stable_pairs: list[tuple[str, str]],
    old_order: dict[str, int],
    new_order: dict[str, int],
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> bool:
    """识别“新增/删除夹在顺延项两侧，却被模型误合成 replace”的情况。"""
    if len(old_ids) != 1 or len(new_ids) != 1:
        return False
    old_id = old_ids[0]
    new_id = new_ids[0]
    old_index = old_order.get(old_id)
    new_index = new_order.get(new_id)
    if old_index is None or new_index is None:
        return False
    has_crossed_stable_pair = any(
        (
            old_order.get(stable_old_id, old_index) < old_index
            and new_index < new_order.get(stable_new_id, new_index)
        )
        or (
            old_index < old_order.get(stable_old_id, old_index)
            and new_order.get(stable_new_id, new_index) < new_index
        )
        for stable_old_id, stable_new_id in unreported_stable_pairs
    )
    if not has_crossed_stable_pair:
        return False
    old_text = normalize_operation_text_for_numbering(old_items_by_id.get(old_id, ""))
    new_text = normalize_operation_text_for_numbering(new_items_by_id.get(new_id, ""))
    if not old_text or not new_text:
        return False
    return difflib.SequenceMatcher(a=old_text, b=new_text).ratio() < 0.45


def split_unaligned_replace_as_delete_add(
    operation: dict[str, Any],
    *,
    old_item_ids: list[str],
    new_item_ids: list[str],
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    """把被未上报顺延项夹住的低相似 replace 拆成独立删除和新增。"""
    old_text = block_item_text_from_ids(item_ids=old_item_ids, items_by_id=old_items_by_id)
    new_text = block_item_text_from_ids(item_ids=new_item_ids, items_by_id=new_items_by_id)
    base = {
        "confidence": operation.get("confidence", 1.0),
        "reason": "同块内存在未上报的编号顺延项，且本 replace 左右正文相似度很低；后处理已拆为 delete + add。",
    }
    return [
        {
            "type": "delete",
            "old_item_ids": old_item_ids,
            "new_item_ids": [],
            "old_focus_text": old_text,
            "new_focus_text": "",
            **base,
        },
        {
            "type": "add",
            "old_item_ids": [],
            "new_item_ids": new_item_ids,
            "old_focus_text": "",
            "new_focus_text": new_text,
            **base,
        },
    ]


def stable_numbering_shift_pairs(
    *,
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """找出忽略编号后正文唯一相同的 old/new 条目对。"""
    old_ids_by_text: dict[str, list[str]] = {}
    new_ids_by_text: dict[str, list[str]] = {}
    for item_id, text in old_items_by_id.items():
        normalized = normalize_operation_text_for_numbering(text)
        if normalized:
            old_ids_by_text.setdefault(normalized, []).append(item_id)
    for item_id, text in new_items_by_id.items():
        normalized = normalize_operation_text_for_numbering(text)
        if normalized:
            new_ids_by_text.setdefault(normalized, []).append(item_id)
    old_to_new: dict[str, str] = {}
    new_to_old: dict[str, str] = {}
    for normalized_text, old_ids in old_ids_by_text.items():
        new_ids = new_ids_by_text.get(normalized_text, [])
        if len(old_ids) != 1 or len(new_ids) != 1:
            continue
        old_to_new[old_ids[0]] = new_ids[0]
        new_to_old[new_ids[0]] = old_ids[0]
    return old_to_new, new_to_old


def split_operation_after_shift_repair(
    operation: dict[str, Any],
    *,
    old_item_ids: list[str],
    new_item_ids: list[str],
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    """把被顺延条目污染的 replace 拆回真实 add/delete/replace。"""
    base = {
        "confidence": operation.get("confidence", 1.0),
        "reason": "模型把正文未变但编号顺延的条目错配为 replace；后处理已按源条目事实纠偏。",
    }
    old_text = block_item_text_from_ids(item_ids=old_item_ids, items_by_id=old_items_by_id)
    new_text = block_item_text_from_ids(item_ids=new_item_ids, items_by_id=new_items_by_id)
    if old_item_ids and new_item_ids:
        return [
            {
                "type": "replace",
                "old_item_ids": old_item_ids,
                "new_item_ids": new_item_ids,
                "old_focus_text": old_text,
                "new_focus_text": new_text,
                **base,
            }
        ]
    if old_item_ids:
        return [
            {
                "type": "delete",
                "old_item_ids": old_item_ids,
                "new_item_ids": [],
                "old_focus_text": old_text,
                "new_focus_text": "",
                **base,
            }
        ]
    if new_item_ids:
        return [
            {
                "type": "add",
                "old_item_ids": [],
                "new_item_ids": new_item_ids,
                "old_focus_text": "",
                "new_focus_text": new_text,
                **base,
            }
        ]
    return [
        {
            "type": "renumber_only",
            "old_item_ids": list(operation.get("old_item_ids", [])),
            "new_item_ids": list(operation.get("new_item_ids", [])),
            "old_focus_text": str(operation.get("old_focus_text", "")),
            "new_focus_text": str(operation.get("new_focus_text", "")),
            **base,
        }
    ]


def fill_uncovered_block_item_operations(
    operations: list[dict[str, Any]],
    *,
    old_items_by_id: dict[str, str],
    new_items_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    """补齐模型漏报的单侧独有条目，防止未覆盖 item 静默丢失。"""
    if not (has_dense_item_id_sequence(old_items_by_id) and has_dense_item_id_sequence(new_items_by_id)):
        return operations
    covered_old_ids = {
        str(item_id)
        for operation in operations
        for item_id in operation.get("old_item_ids", [])
    }
    covered_new_ids = {
        str(item_id)
        for operation in operations
        for item_id in operation.get("new_item_ids", [])
    }
    old_to_stable_new, new_to_stable_old = stable_numbering_shift_pairs(
        old_items_by_id=old_items_by_id,
        new_items_by_id=new_items_by_id,
    )
    old_order = {item_id: index for index, item_id in enumerate(old_items_by_id)}
    new_order = {item_id: index for index, item_id in enumerate(new_items_by_id)}
    completed = list(operations)
    for item_id, text in old_items_by_id.items():
        if item_id in covered_old_ids:
            continue
        stable_new_id = old_to_stable_new.get(item_id)
        if stable_new_id and stable_new_id not in covered_new_ids:
            covered_new_ids.add(stable_new_id)
            continue
        insert_operation_by_source_order(
            completed,
            {
                "type": "delete",
                "old_item_ids": [item_id],
                "new_item_ids": [],
                "old_focus_text": text,
                "new_focus_text": "",
                "confidence": 1.0,
                "reason": "模型未覆盖该旧侧条目，且无法用完全一致或仅编号顺延的新侧条目解释；后处理补充 delete。",
            },
            old_order=old_order,
            new_order=new_order,
        )
        covered_old_ids.add(item_id)
    for item_id, text in new_items_by_id.items():
        if item_id in covered_new_ids:
            continue
        stable_old_id = new_to_stable_old.get(item_id)
        if stable_old_id and stable_old_id not in covered_old_ids:
            covered_old_ids.add(stable_old_id)
            continue
        insert_operation_by_source_order(
            completed,
            {
                "type": "add",
                "old_item_ids": [],
                "new_item_ids": [item_id],
                "old_focus_text": "",
                "new_focus_text": text,
                "confidence": 1.0,
                "reason": "模型未覆盖该新侧条目，且无法用完全一致或仅编号顺延的旧侧条目解释；后处理补充 add。",
            },
            old_order=old_order,
            new_order=new_order,
        )
        covered_new_ids.add(item_id)
    return completed


def has_dense_item_id_sequence(items_by_id: dict[str, str]) -> bool:
    """只对完整连续的 block 做覆盖补漏，避免稀疏诊断片段被误判为漏报。"""
    if len(items_by_id) <= 1:
        return True
    numbers: list[int] = []
    for item_id in items_by_id:
        match = re.search(r"-(\d+)$", item_id)
        if match is None:
            return False
        numbers.append(int(match.group(1)))
    numbers.sort()
    return numbers == list(range(numbers[0], numbers[-1] + 1))


def insert_operation_by_source_order(
    operations: list[dict[str, Any]],
    operation: dict[str, Any],
    *,
    old_order: dict[str, int],
    new_order: dict[str, int],
) -> None:
    """把补齐的操作插回源条目顺序附近，避免展示顺序跳到最后。"""
    old_indexes = [old_order[item_id] for item_id in operation.get("old_item_ids", []) if item_id in old_order]
    new_indexes = [new_order[item_id] for item_id in operation.get("new_item_ids", []) if item_id in new_order]
    if old_indexes:
        target_index = min(old_indexes)
        for index, existing in enumerate(operations):
            existing_old_indexes = [
                old_order[item_id]
                for item_id in existing.get("old_item_ids", [])
                if item_id in old_order
            ]
            if existing_old_indexes and min(existing_old_indexes) > target_index:
                operations.insert(index, operation)
                return
    if new_indexes:
        target_index = min(new_indexes)
        for index, existing in enumerate(operations):
            existing_new_indexes = [
                new_order[item_id]
                for item_id in existing.get("new_item_ids", [])
                if item_id in new_order
            ]
            if existing_new_indexes and min(existing_new_indexes) > target_index:
                operations.insert(index, operation)
                return
    operations.append(operation)


def merge_lone_delete_add_when_no_replace_in_block(
    operations: list[dict[str, Any]],
    *,
    old_by_id: dict[str, str],
    new_by_id: dict[str, str],
    global_old_by_id: dict[str, str],
    global_new_by_id: dict[str, str],
    similarity_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """同块内 substantive 恰好为「一条 delete + 一条 add」时，若两侧正文足够相似则并成一条 replace。

    可与同块其它 replace 共存（典型：章节标题删+增与正文多条 replace 同批送模）。对照表上标题一行展示左右正文，
    而不是「删除」+「新增」两行。须过相似度门槛，避免把无关删、增误并（如 normalize 分拆后误配，相似度约 0.18）。
    """
    if not operations:
        return operations
    substantive: list[tuple[int, dict[str, Any]]] = []
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            continue
        typ = str(op.get("type", "")).strip()
        if typ in {"match", "renumber_only"}:
            continue
        substantive.append((i, op))
    if not substantive:
        return operations
    deletes = [(i, op) for i, op in substantive if str(op.get("type", "")).strip() == "delete"]
    adds = [(i, op) for i, op in substantive if str(op.get("type", "")).strip() == "add"]
    if len(deletes) != 1 or len(adds) != 1:
        return operations
    di, dop = deletes[0]
    ai, aop = adds[0]
    del_ids = [str(x).strip() for x in dop.get("old_item_ids", []) if str(x).strip()]
    add_ids = [str(x).strip() for x in aop.get("new_item_ids", []) if str(x).strip()]
    old_text = _resolve_operation_item_texts(
        del_ids, local_by_id=old_by_id, global_by_id=global_old_by_id
    ).strip()
    new_text = _resolve_operation_item_texts(
        add_ids, local_by_id=new_by_id, global_by_id=global_new_by_id
    ).strip()
    if not old_text or not new_text:
        return operations
    if _clause_relocation_similarity(old_text, new_text) < similarity_threshold:
        return operations
    synthetic: dict[str, Any] = {
        "type": "replace",
        "old_item_ids": list(dop.get("old_item_ids", [])),
        "new_item_ids": list(aop.get("new_item_ids", [])),
        "old_focus_text": str(dop.get("old_focus_text", "") or ""),
        "new_focus_text": str(aop.get("new_focus_text", "") or ""),
        "confidence": min(
            float(dop.get("confidence", 1.0) or 1.0),
            float(aop.get("confidence", 1.0) or 1.0),
        ),
        "reason": str(dop.get("reason", "") or aop.get("reason", "") or "同块单删与单增合并为一行对照。"),
    }
    insert_at = min(di, ai)
    rebuilt: list[dict[str, Any]] = []
    inserted = False
    for i, op in enumerate(operations):
        if i == di or i == ai:
            if not inserted and i == insert_at:
                rebuilt.append(synthetic)
                inserted = True
            continue
        rebuilt.append(op)
    if not inserted:
        rebuilt.append(synthetic)
    return rebuilt


def dedupe_block_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去掉模型重复引用同一组 item_id 产生的重复操作。"""
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for operation in operations:
        key = (
            str(operation.get("type", "")).strip(),
            tuple(str(item_id) for item_id in operation.get("old_item_ids", [])),
            tuple(str(item_id) for item_id in operation.get("new_item_ids", [])),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(operation)
    return deduped


def rows_from_block_operations_payload(payload: dict[str, Any], compare_blocks: tuple[Any, ...]) -> list[ComparisonRow]:
    """把块级操作集回查原始 compare block 后转成最终展示行。"""
    blocks_by_id = {str(block.block_id): block for block in compare_blocks}
    global_old_by_id, global_new_by_id = compare_block_item_text_maps(compare_blocks)
    rows: list[ComparisonRow] = []
    for block_payload in payload.get("blocks", []):
        source_block = blocks_by_id.get(block_lookup_id(str(block_payload.get("block_id", "")).strip()))
        if source_block is None:
            continue
        old_items_by_id = {str(item.item_id): str(item.text).strip() for item in source_block.old_items}
        new_items_by_id = {str(item.item_id): str(item.text).strip() for item in source_block.new_items}
        raw_operations = block_payload.get("operations", [])
        if not isinstance(raw_operations, list):
            raw_operations = []
        op_dicts = [op for op in raw_operations if isinstance(op, dict)]
        operations = merge_lone_delete_add_when_no_replace_in_block(
            op_dicts,
            old_by_id=old_items_by_id,
            new_by_id=new_items_by_id,
            global_old_by_id=global_old_by_id,
            global_new_by_id=global_new_by_id,
        )
        for operation in operations:
            operation_type = str(operation.get("type", "")).strip()
            if operation_type in {"match", "renumber_only"}:
                continue
            old_ids = [str(x).strip() for x in operation.get("old_item_ids", []) if str(x).strip()]
            new_ids = [str(x).strip() for x in operation.get("new_item_ids", []) if str(x).strip()]
            old_text = _resolve_operation_item_texts(
                old_ids, local_by_id=old_items_by_id, global_by_id=global_old_by_id
            )
            new_text = _resolve_operation_item_texts(
                new_ids, local_by_id=new_items_by_id, global_by_id=global_new_by_id
            )
            if operation_type == "add":
                old_text = "新增"
            elif operation_type == "delete":
                new_text = "删除"
            elif operation_type != "replace":
                continue
            if old_text and new_text and is_only_numbering_changed(old_text, new_text):
                continue
            old_text, new_text = remove_fully_equal_lines(old_text, new_text)
            if not old_text and not new_text:
                continue
            rows.append(
                ComparisonRow(
                    chapter=str(source_block.chapter_title or block_payload.get("chapter")).strip(),
                    subchapter=str(source_block.parent_path or block_payload.get("parent_path", "")).strip(),
                    old_text=old_text or "新增",
                    new_text=new_text or "删除",
                )
            )
    return merge_consecutive_delete_rows(rows)


def rows_from_unit_decision_payload(payload: dict[str, Any], compare_units: tuple[Any, ...]) -> list[ComparisonRow]:
    """把新版 unit 判定结果回查原始 compare unit 后转成最终展示行。"""
    units_by_id = {str(unit.unit_id): unit for unit in compare_units}
    rows: list[ComparisonRow] = []
    for decision in payload.get("units", []):
        if decision.get("numbering_only") or decision.get("change_type") in {"numbering_only", "equal"}:
            continue
        if decision.get("display_strategy") == "skip":
            continue
        source_unit = units_by_id.get(str(decision.get("unit_id", "")).strip())
        if source_unit is None:
            continue
        validate_unit_decision_against_source(decision, source_unit)
        old_text, new_text = texts_from_unit_decision(decision, source_unit)
        if old_text and new_text and is_only_numbering_changed(old_text, new_text):
            continue
        old_text, new_text = remove_fully_equal_lines(old_text, new_text)
        if not old_text and not new_text:
            continue
        rows.append(
            ComparisonRow(
                chapter=str(source_unit.chapter_title or decision.get("chapter")).strip(),
                subchapter=str(source_unit.subchapter or decision.get("subchapter")).strip(),
                old_text=old_text or "新增",
                new_text=new_text or "删除",
            )
        )
    return rows


def validate_unit_decision_against_source(decision: dict[str, Any], source_unit: Any) -> None:
    """校验模型判定是否与本地 compare unit 的基本事实冲突。"""
    strategy = str(decision.get("display_strategy", "")).strip()
    change_type = str(decision.get("change_type", "")).strip()
    old_text = str(source_unit.old_text).strip()
    new_text = str(source_unit.new_text).strip()
    unit_id = str(decision.get("unit_id", "")).strip()
    if (
        strategy == "delete_old_only" or change_type in {"delete", "delete_item"}
    ) and not is_deletion_marker(new_text) and not is_shift_delete_decision(new_text, decision.get("unchanged_lines", []), old_text):
        raise ValueError(f"模型误判为整项删除: {unit_id or getattr(source_unit, 'unit_id', '')}")
    if (
        strategy == "add_new_only" or change_type in {"add", "add_item"}
    ) and not is_addition_marker(old_text) and not is_shift_add_decision(old_text, decision.get("unchanged_lines", []), new_text):
        raise ValueError(f"模型误判为整项新增: {unit_id or getattr(source_unit, 'unit_id', '')}")


def texts_from_unit_decision(decision: dict[str, Any], source_unit: Any) -> tuple[str, str]:
    """根据模型判定和原始 compare unit 生成左右展示文本。"""
    strategy = str(decision.get("display_strategy", "")).strip()
    old_text = str(source_unit.old_text).strip()
    new_text = str(source_unit.new_text).strip()
    if strategy == "delete_old_only" or decision.get("change_type") in {"delete", "delete_item"}:
        old_text = remove_decision_unchanged_lines(old_text, decision.get("unchanged_lines", []))
        if not old_text:
            old_text = str(source_unit.old_text).strip()
        return old_text.strip(), ""
    if strategy == "add_new_only" or decision.get("change_type") in {"add", "add_item"}:
        new_text = remove_decision_unchanged_lines(new_text, decision.get("unchanged_lines", []))
        if not new_text:
            new_text = str(source_unit.new_text).strip()
        return "", new_text.strip()
    return old_text.strip(), new_text.strip()


def is_deletion_marker(text: str) -> bool:
    """判断 compare unit 右侧是否确实表示整项删除。"""
    return not text.strip() or text.strip() == "删除"


def is_addition_marker(text: str) -> bool:
    """判断 compare unit 左侧是否确实表示整项新增。"""
    return not text.strip() or text.strip() == "新增"


def is_shift_delete_decision(new_text: str, unchanged_lines: Any, old_text: str) -> bool:
    """判断 delete_item 是否属于删除整行后编号上移的合法场景。"""
    old_lines = [line for line in old_text.splitlines() if line.strip()]
    new_lines = [line for line in new_text.splitlines() if line.strip()]
    return len(old_lines) > len(new_lines) and lines_are_declared_unchanged(new_lines, unchanged_lines)


def is_shift_add_decision(old_text: str, unchanged_lines: Any, new_text: str) -> bool:
    """判断 add_item 是否属于新增整行后编号上移的合法场景。"""
    old_lines = [line for line in old_text.splitlines() if line.strip()]
    new_lines = [line for line in new_text.splitlines() if line.strip()]
    return len(new_lines) > len(old_lines) and lines_are_declared_unchanged(old_lines, unchanged_lines)


def lines_are_declared_unchanged(lines: list[str], unchanged_lines: Any) -> bool:
    """判断一组正文行是否都被模型声明为后续未变行。"""
    normalized_unchanged = {
        normalize_line_for_shift_compare(str(line))
        for line in unchanged_lines
        if str(line).strip()
    }
    normalized_lines = [normalize_line_for_shift_compare(line) for line in lines if line.strip()]
    return bool(normalized_lines) and all(line in normalized_unchanged for line in normalized_lines)


def remove_decision_unchanged_lines(text: str, unchanged_lines: Any) -> str:
    """按模型判定剔除真实未变小行，比较时忽略行首编号变化。"""
    normalized_unchanged = {
        normalize_line_for_shift_compare(str(line))
        for line in unchanged_lines
        if str(line).strip()
    }
    if not normalized_unchanged:
        return text.strip()
    kept_lines = [
        line
        for line in text.splitlines()
        if normalize_line_for_shift_compare(line) not in normalized_unchanged
    ]
    return "\n".join(kept_lines).strip()


def normalize_line_for_shift_compare(line: str) -> str:
    """返回忽略常见编号前缀后的行文本，用于识别编号上移但正文未变。"""
    stripped = str(line).strip()
    stripped = re.sub(r"^（\d+）\s*", "", stripped)
    stripped = re.sub(r"^\(\d+\)\s*", "", stripped)
    stripped = re.sub(r"^\d+[、.．]\s*", "", stripped)
    stripped = re.sub(r"^[一二三四五六七八九十百]+、\s*", "", stripped)
    return stripped.strip()


def normalize_product_name_rows(rows: list[ComparisonRow], old_name: str, new_name: str) -> list[ComparisonRow]:
    """把基金名称对照固定提升到第一行，并移除原章节中的重复基金名称行。"""
    old_clean = old_name.strip()
    new_clean = new_name.strip()
    if not old_clean or not new_clean or old_clean == new_clean:
        return rows
    filtered_rows = [
        row
        for row in rows
        if not _is_product_name_row(row, old_clean=old_clean, new_clean=new_clean)
    ]
    product_row = ComparisonRow(
        chapter=PRODUCT_NAME_CHAPTER,
        old_text=old_clean,
        new_text=new_clean,
    )
    return [product_row, *filtered_rows]


def _is_product_name_row(row: ComparisonRow, *, old_clean: str, new_clean: str) -> bool:
    """判断某行是否是原章节里的基金名称重复行。"""
    marker_hit = "基金名称" in row.subchapter or "基金名称" in row.old_text or "基金名称" in row.new_text
    if not marker_hit:
        return False
    return old_clean in row.old_text and new_clean in row.new_text
