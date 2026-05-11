"""块级 operations → `ComparisonRow` 与块内展示合并。

职责：operation 去重、无 replace 时 lone delete+add 高相似合并、按 operation 生成对照行。
不负责：payload 层 renumber 折叠（见 `normalize_payload`）、跨章行合并（见 `row_merges`）。
"""

from __future__ import annotations

from typing import Any

from ..chunking import is_only_numbering_changed, remove_fully_equal_lines
from ..models import ComparisonRow
from .block_ref import block_lookup_id, clause_relocation_similarity, compare_block_item_text_maps, resolve_operation_item_texts
from .row_merges import merge_consecutive_delete_rows


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
    old_text = resolve_operation_item_texts(
        del_ids, local_by_id=old_by_id, global_by_id=global_old_by_id
    ).strip()
    new_text = resolve_operation_item_texts(
        add_ids, local_by_id=new_by_id, global_by_id=global_new_by_id
    ).strip()
    if not old_text or not new_text:
        return operations
    if clause_relocation_similarity(old_text, new_text) < similarity_threshold:
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
            old_text = resolve_operation_item_texts(
                old_ids, local_by_id=old_items_by_id, global_by_id=global_old_by_id
            )
            new_text = resolve_operation_item_texts(
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
