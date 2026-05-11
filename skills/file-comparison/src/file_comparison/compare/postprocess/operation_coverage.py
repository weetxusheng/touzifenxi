"""块内模型漏报条目的覆盖补全。

职责：在 item_id 连续完整的 compare block 上，为未被任何 operation 引用的单侧条目
补充 delete / add，并按源条目顺序插入；跳过可用「仅编号顺延」解释的成对条目。
不负责：顺延折叠、replace 纠偏（见 `normalize_payload`、`operation_repair`）。
"""

from __future__ import annotations

import re
from typing import Any

from .operation_repair import stable_numbering_shift_pairs


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
