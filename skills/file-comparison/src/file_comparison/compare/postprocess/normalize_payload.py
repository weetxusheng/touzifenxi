"""块级 LLM payload 规范化入口（编排，不承载具体纠偏算法）。

职责：按 compare block 遍历 `payload.blocks`——先 `operation_repair` 回查 item_id，
块内将「仅编号变化」的 replace 与成对 add/delete 折叠为 renumber_only，再调用
`operation_repair` / `operation_coverage` / `block_rows.dedupe`；最后在同 parent 下
做跨 part 的 add/delete 配对折叠。
不负责：行级合并、章题插入、operation→ComparisonRow（见 `row_merges`、`block_rows`）。
"""

from __future__ import annotations

from typing import Any

from ..chunking import is_only_numbering_changed
from .block_ref import block_lookup_id
from .block_rows import dedupe_block_operations
from .block_text import block_item_text_from_ids
from .operation_coverage import fill_uncovered_block_item_operations
from .operation_repair import repair_operation_item_ids_from_focus_text, repair_shifted_item_misreplace_operations
from .shift_lines import normalize_operation_text_for_numbering


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
