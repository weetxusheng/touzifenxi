"""compare block 标识与 operation 正文解析。

职责：`block_lookup_id`、块内 item 文本表、从 operation 解析 old/new 展示正文及条款搬迁相似度。
不负责：payload 折叠、行级合并（见 `normalize_payload`、`row_merges`）。
"""

from __future__ import annotations

import difflib
from typing import Any

from ..chunking import strip_leading_numbering

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


def resolve_operation_item_texts(
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


def clause_relocation_similarity(old_text: str, new_text: str) -> float:
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
