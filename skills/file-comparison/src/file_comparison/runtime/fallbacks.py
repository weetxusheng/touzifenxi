"""集中实现模型失败后的本地兜底策略。"""

from __future__ import annotations

from dataclasses import dataclass

from ..compare.chunking import build_rows
from ..compare.models import ChapterBatch, ComparisonRow


@dataclass(slots=True)
class FallbackResult:
    """描述一次 fallback 的名称、结果行和是否成功。"""

    name: str
    rows: list[ComparisonRow]
    success: bool
    error: str = ""


def rows_from_compare_blocks(batch: ChapterBatch) -> list[ComparisonRow]:
    """把块级 diff 上下文转换为本地兜底对照行。"""
    return [
        ComparisonRow(
            chapter=block.chapter_title,
            subchapter=block.parent_path,
            old_text="\n".join(item.text for item in block.old_items).strip() or "新增",
            new_text="\n".join(item.text for item in block.new_items).strip() or "删除",
        )
        for block in batch.compare_blocks
    ]


def rows_from_compare_units(batch: ChapterBatch) -> list[ComparisonRow]:
    """兼容旧 unit 结构的 fallback。"""
    return [
        ComparisonRow(
            chapter=unit.chapter_title,
            subchapter=unit.subchapter,
            old_text=unit.old_text,
            new_text=unit.new_text,
        )
        for unit in batch.compare_units
    ]


def run_batch_fallback(batch: ChapterBatch) -> FallbackResult:
    """按 compare-units 优先、规则 diff 次之的顺序执行 fallback。"""
    if batch.compare_blocks:
        rows = rows_from_compare_blocks(batch)
        return FallbackResult(name="compare-blocks", rows=rows, success=bool(rows))
    if batch.compare_units:
        rows = rows_from_compare_units(batch)
        return FallbackResult(name="compare-units", rows=rows, success=bool(rows))
    rows = build_rows(list(batch.old_sections), list(batch.new_sections))
    return FallbackResult(name="rule", rows=rows, success=bool(rows))
