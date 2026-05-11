"""文件对照任务编排包。

子模块分工见 `skills/file-comparison/docs/engine-development-guide.md`。
"""

from __future__ import annotations

from ..batch_processor import _process_llm_batch
from ..pairing import (
    build_output_document_paths,
    build_output_document_stem,
    build_pair_key,
    sanitize_output_filename_part,
    scan_folder_for_pairs,
)
from ..postprocess import (
    normalize_block_operations_payload,
    normalize_product_name_rows,
    rows_from_block_operations_payload,
    rows_from_llm_payload,
    rows_from_unit_decision_payload,
    validate_unit_decision_against_source,
)
from ..response_readable import write_readable_response_file as _write_readable_response_file
from ..section_rules import apply_section_skip_rules
from .batch_validation import USABLE_BATCH_FINAL_STATUSES, validate_complete_batch_results
from .pair_compare import compare_pair, compare_pair_with_llm
from .task_manager import TaskManager
from .task_runner import run_task

__all__ = (
    "TaskManager",
    "USABLE_BATCH_FINAL_STATUSES",
    "apply_section_skip_rules",
    "build_output_document_paths",
    "build_output_document_stem",
    "build_pair_key",
    "compare_pair",
    "compare_pair_with_llm",
    "normalize_block_operations_payload",
    "normalize_product_name_rows",
    "rows_from_block_operations_payload",
    "rows_from_llm_payload",
    "rows_from_unit_decision_payload",
    "sanitize_output_filename_part",
    "scan_folder_for_pairs",
    "validate_complete_batch_results",
    "validate_unit_decision_against_source",
    "_process_llm_batch",
    "_write_readable_response_file",
    "run_task",
)
