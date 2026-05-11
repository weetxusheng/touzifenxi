"""模型返回结果到最终对照行的后处理包。

流水线概要：`normalize_payload` → `payload_coerce` / `sort_operations` → `block_rows`
→（引擎侧）`row_merges` → `product_rows`；`unit_decisions` 处理非 blocks 的 units 形态。
各子模块文件头注释标明职责边界。
"""

from __future__ import annotations

import copy
from typing import Any

from ..chunking import is_only_numbering_changed, remove_fully_equal_lines
from ..models import ComparisonRow, Section

from .block_ref import (
    block_lookup_id,
    clause_relocation_similarity,
    compare_block_item_text_maps,
    resolve_operation_item_texts,
)
from .block_rows import (
    dedupe_block_operations,
    merge_lone_delete_add_when_no_replace_in_block,
    rows_from_block_operations_payload,
)
from .constants import PRODUCT_NAME_CHAPTER
from .block_text import block_item_text_from_ids
from .normalize_payload import normalize_block_operations_payload
from .payload_coerce import coerce_shifted_clause_add_delete_to_replace, drop_redundant_adds_when_replace_reuses_new_items
from .product_rows import normalize_product_name_rows
from .row_merges import (
    insert_section_title_change_rows,
    merge_consecutive_delete_rows,
    merge_pure_delete_and_add_runs,
)
from .shift_lines import normalize_line_for_shift_compare, normalize_operation_text_for_numbering
from .sort_operations import sort_block_operations_old_first
from .unit_decisions import (
    rows_from_unit_decision_payload,
    validate_unit_decision_against_source,
)


def rows_from_llm_payload(
    payload: dict[str, Any],
    compare_units: tuple[Any, ...] = (),
    compare_blocks: tuple[Any, ...] = (),
) -> list[ComparisonRow]:
    """把模型返回的结构化 payload 转成最终展示行。"""
    if isinstance(payload.get("blocks"), list):
        payload = normalize_block_operations_payload(payload, compare_blocks=compare_blocks)
        payload = copy.deepcopy(payload)
        coerce_shifted_clause_add_delete_to_replace(payload, compare_blocks=compare_blocks)
        drop_redundant_adds_when_replace_reuses_new_items(payload)
        sort_block_operations_old_first(payload, compare_blocks=compare_blocks)
        return rows_from_block_operations_payload(payload, compare_blocks=compare_blocks)
    if isinstance(payload.get("units"), list):
        return rows_from_unit_decision_payload(payload, compare_units)
    rows: list[ComparisonRow] = []
    for chapter in payload.get("chapters", []):
        chapter_name = str(chapter.get("chapter", "")).strip()
        for subsection in chapter.get("subsections", []):
            old_text = str(subsection.get("old_text", "")).strip()
            new_text = str(subsection.get("new_text", "")).strip()
            if subsection.get("numbering_only"):
                continue
            if old_text and new_text and is_only_numbering_changed(old_text, new_text):
                continue
            equal_lines = {line.strip() for line in subsection.get("fully_equal_lines", []) if str(line).strip()}
            if equal_lines:
                old_text = "\n".join(line for line in old_text.split("\n") if line.strip() not in equal_lines).strip()
                new_text = "\n".join(line for line in new_text.split("\n") if line.strip() not in equal_lines).strip()
            old_text, new_text = remove_fully_equal_lines(old_text, new_text)
            if not old_text and not new_text:
                continue
            rows.append(
                ComparisonRow(
                    chapter=chapter_name,
                    subchapter=str(subsection.get("subchapter", "")).strip(),
                    old_text=old_text or "新增",
                    new_text=new_text or "删除",
                )
            )
    return rows


# 兼容旧模块路径下的私有别名（若有测试或外部引用）
_resolve_operation_item_texts = resolve_operation_item_texts
_clause_relocation_similarity = clause_relocation_similarity

__all__ = (
    "PRODUCT_NAME_CHAPTER",
    "block_item_text_from_ids",
    "block_lookup_id",
    "clause_relocation_similarity",
    "coerce_shifted_clause_add_delete_to_replace",
    "compare_block_item_text_maps",
    "dedupe_block_operations",
    "drop_redundant_adds_when_replace_reuses_new_items",
    "insert_section_title_change_rows",
    "merge_consecutive_delete_rows",
    "merge_lone_delete_add_when_no_replace_in_block",
    "merge_pure_delete_and_add_runs",
    "normalize_block_operations_payload",
    "normalize_line_for_shift_compare",
    "normalize_operation_text_for_numbering",
    "normalize_product_name_rows",
    "resolve_operation_item_texts",
    "rows_from_block_operations_payload",
    "rows_from_llm_payload",
    "rows_from_unit_decision_payload",
    "sort_block_operations_old_first",
    "validate_unit_decision_against_source",
)
