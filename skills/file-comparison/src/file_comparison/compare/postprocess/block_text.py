"""块内 item_id 与正文回查。

职责：按 `old_item_ids` / `new_item_ids` 从 compare block 的条目表取展示文本；
`focus_text` 缺失时作为 normalize / repair 的共用回退。
"""

from __future__ import annotations


def block_item_text_from_ids(*, item_ids: list[str], items_by_id: dict[str, str], fallback_text: str = "") -> str:
    """优先按 item_id 回查原始条目文本，缺失时回退到模型 focus_text。"""
    text = "\n".join(items_by_id[item_id] for item_id in item_ids if item_id in items_by_id and items_by_id[item_id]).strip()
    return text or fallback_text.strip()
