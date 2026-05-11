"""章节切块、本地规则 diff 与送模批次分组。

子模块分工见 `skills/file-comparison/docs/chunking-development-guide.md`。
"""

from __future__ import annotations

from .batching import (
    batch_compare_block_chars,
    batch_compare_chars,
    chunk_lines_by_char_limit,
    compare_block_chars,
    compare_unit_chars,
    group_compare_blocks_into_batches,
    group_compare_units_into_batches,
    group_sections_into_batches,
    split_batches_by_compare_block_limit,
    split_batches_by_compare_unit_limit,
    split_compare_block_by_items,
    split_compare_unit_by_lines,
    split_oversized_compare_batch,
    split_oversized_compare_block_batch,
    split_oversized_compare_blocks,
    split_oversized_compare_units,
)
from .blocks import block_label, block_match_key, is_generic_numbering_label, split_blocks
from .compare_blocks import (
    build_candidate_section,
    build_compare_blocks_for_llm,
    filter_compare_block_items_by_stripped_numbering_identity,
    row_display_text,
)
from .compare_units import build_compare_units_for_llm, preprocess_sections_for_llm
from .constants import COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW, OMITTED_EQUAL_MARKER
from .headings import (
    SubchapterInfo,
    active_display_heading_re,
    format_chunk,
    heading_context,
    join_chunks,
    remove_first_inner_heading,
    subchapter_info,
)
from .rule_rows import (
    build_block_chunks,
    build_comparison_row,
    build_name_row,
    build_rows,
    build_section_rows,
    body_lines_without_title,
)
from .section_items import ParentPathGroup, ordered_parent_groups, parent_path_match_key, section_items_for_blocks
from .text_match import (
    first_line_matches_subchapter,
    is_only_numbering_changed,
    line_content_key,
    remove_fully_equal_lines,
    strip_leading_numbering,
    text_starts_with_subchapter,
)

__all__ = (
    "COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW",
    "OMITTED_EQUAL_MARKER",
    "ParentPathGroup",
    "SubchapterInfo",
    "active_display_heading_re",
    "batch_compare_block_chars",
    "batch_compare_chars",
    "block_label",
    "block_match_key",
    "body_lines_without_title",
    "build_block_chunks",
    "build_candidate_section",
    "build_compare_blocks_for_llm",
    "build_compare_units_for_llm",
    "build_comparison_row",
    "build_name_row",
    "build_rows",
    "build_section_rows",
    "chunk_lines_by_char_limit",
    "compare_block_chars",
    "compare_unit_chars",
    "filter_compare_block_items_by_stripped_numbering_identity",
    "first_line_matches_subchapter",
    "format_chunk",
    "group_compare_blocks_into_batches",
    "group_compare_units_into_batches",
    "group_sections_into_batches",
    "heading_context",
    "is_generic_numbering_label",
    "is_only_numbering_changed",
    "join_chunks",
    "line_content_key",
    "ordered_parent_groups",
    "parent_path_match_key",
    "preprocess_sections_for_llm",
    "remove_first_inner_heading",
    "remove_fully_equal_lines",
    "row_display_text",
    "section_items_for_blocks",
    "split_batches_by_compare_block_limit",
    "split_batches_by_compare_unit_limit",
    "split_blocks",
    "split_compare_block_by_items",
    "split_compare_unit_by_lines",
    "split_oversized_compare_batch",
    "split_oversized_compare_block_batch",
    "split_oversized_compare_blocks",
    "split_oversized_compare_units",
    "strip_leading_numbering",
    "subchapter_info",
    "text_starts_with_subchapter",
)
