"""normalize 之后对 blocks payload 的二次纠偏。

职责：条款搬迁场景下 add/delete 升格为 replace；replace 已引用 new_item 时去掉冗余 add。
不负责：renumber 折叠与漏报补全（见 `normalize_payload`）。
"""

from __future__ import annotations

from typing import Any

from .block_ref import clause_relocation_similarity, compare_block_item_text_maps, resolve_operation_item_texts


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
                nt = resolve_operation_item_texts(new_ids, local_by_id=empty_local, global_by_id=global_new)
                if not nt.strip():
                    continue
                add_ops.append((bi, oi, new_ids, nt.strip(), chapter_key))
            elif typ == "delete":
                old_ids = [str(x).strip() for x in op.get("old_item_ids", []) if str(x).strip()]
                new_ids = [str(x).strip() for x in op.get("new_item_ids", []) if str(x).strip()]
                if not old_ids or new_ids:
                    continue
                ot = resolve_operation_item_texts(old_ids, local_by_id=empty_local, global_by_id=global_old)
                if not ot.strip():
                    continue
                del_ops.append((bi, oi, old_ids, ot.strip(), chapter_key))

    candidates: list[tuple[float, tuple[int, int], tuple[int, int], list[str]]] = []
    for abi, aoi, new_ids, nt, ch_a in add_ops:
        for dbi, doi, _old_ids, ot, ch_d in del_ops:
            if ch_a != ch_d or abi == dbi:
                continue
            sim = clause_relocation_similarity(ot, nt)
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
