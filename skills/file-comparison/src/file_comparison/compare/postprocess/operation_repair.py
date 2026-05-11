"""块内 operation 的 item_id / 顺延误配纠偏。

职责：用 focus_text 回查 item_id；识别「仅编号顺延」却被标成 replace 的条目并拆回
renumber_only / delete / add；处理被未上报顺延项夹住的低相似 replace。
不负责：块间 renumber 折叠、漏报补全（见 `normalize_payload`、`operation_coverage`）。
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from .block_text import block_item_text_from_ids
from .shift_lines import normalize_operation_text_for_numbering


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
