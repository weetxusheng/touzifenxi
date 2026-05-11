"""产品名等专项对照行展示调整。

职责：在通用行合并完成后，对「基金名称」等固定章节做行序或展示层微调。
不负责：块内 operation 与通用删增合并逻辑。
"""

from __future__ import annotations

from ..models import ComparisonRow
from .constants import PRODUCT_NAME_CHAPTER


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
