"""块内 operations 展示顺序。

职责：同 block 内按旧版 `old_item_ids` 在源块中的下标排序，无 old 时回退 new 下标。
不负责：operation 语义修改（见 `normalize_payload`、`payload_coerce`）。
"""

from __future__ import annotations

from typing import Any

from .block_ref import block_lookup_id

def sort_block_operations_old_first(payload: dict[str, Any], *, compare_blocks: tuple[Any, ...]) -> None:
    """按 compare block 内旧版条目顺序重排各 block 的 operations（就地修改 payload）。

    主键：本块 `old_item_ids` 中的最小下标；若无旧侧引用（如纯 add），则用 `new_item_ids` 最小下标。
    次键：另一侧最小下标，便于 replace 在相同旧锚点下稳定。再按类型 tie-break，最后保留原序稳定排序。
    """
    block_list = payload.get("blocks")
    if not isinstance(block_list, list) or not compare_blocks:
        return
    blocks_by_id = {str(block.block_id): block for block in compare_blocks}
    _BIG = 10**9
    _type_rank = {"replace": 0, "delete": 1, "add": 2, "renumber_only": 3, "match": 4}

    for bp in block_list:
        if not isinstance(bp, dict):
            continue
        source = blocks_by_id.get(block_lookup_id(str(bp.get("block_id", "")).strip()))
        if source is None:
            continue
        old_order = {str(item.item_id): idx for idx, item in enumerate(source.old_items)}
        new_order = {str(item.item_id): idx for idx, item in enumerate(source.new_items)}
        ops = bp.get("operations")
        if not isinstance(ops, list) or len(ops) < 2:
            continue

        def sort_key(op: Any, orig_index: int) -> tuple[int, int, int, int]:
            if not isinstance(op, dict):
                return (_BIG, _BIG, 99, orig_index)
            typ = str(op.get("type", "")).strip()
            oids = [str(x).strip() for x in op.get("old_item_ids", []) if str(x).strip()]
            nids = [str(x).strip() for x in op.get("new_item_ids", []) if str(x).strip()]
            oix = [old_order[i] for i in oids if i in old_order]
            nix = [new_order[i] for i in nids if i in new_order]
            min_old = min(oix) if oix else _BIG
            min_new = min(nix) if nix else _BIG
            primary = min_old if min_old < _BIG else min_new
            secondary = min_new if min_new < _BIG else _BIG
            tr = _type_rank.get(typ, 5)
            return (primary, secondary, tr, orig_index)

        decorated = [(sort_key(op, i), op) for i, op in enumerate(ops)]
        decorated.sort(key=lambda t: t[0])
        bp["operations"] = [pair[1] for pair in decorated]
