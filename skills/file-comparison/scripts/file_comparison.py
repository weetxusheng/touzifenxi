"""提供 file-comparison skill 的单次文档对照 CLI 入口。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if __name__ == "file_comparison":
    sys.modules.pop(__name__, None)

from file_comparison.compare.chunking import (  # noqa: E402
    INNER_HEADING_RE,
    SubchapterInfo,
    build_comparison_row,
    build_name_row,
    build_rows,
    build_section_rows,
    build_candidate_section,
    block_label,
    block_match_key,
    build_block_chunks,
    heading_context,
    is_only_numbering_changed,
    join_chunks,
    remove_first_inner_heading,
    split_blocks,
    preprocess_sections_for_llm,
    row_display_text,
    strip_leading_numbering,
    subchapter_info,
)
from file_comparison.compare.engine import (  # noqa: E402
    build_pair_key,
    compare_pair,
    compare_pair_with_llm,
    rows_from_llm_payload,
    run_task,
    scan_folder_for_pairs,
)
from file_comparison.compare.extractor import (  # noqa: E402
    NumberingLevel,
    Section,
    clean_lines,
    extract_docx_text,
    extract_fund_name,
    extract_text,
    format_counter,
    format_number_label,
    full_body_without_title,
    int_to_letters,
    int_to_roman,
    normalize_title,
    paragraph_numbering,
    read_numbering_levels,
    section_number,
    split_sections,
)
from file_comparison.compare.models import ComparisonRow, PairMatch  # noqa: E402
from file_comparison.compare.writer import (  # noqa: E402
    CHANGE_BLUE,
    DELETE_RED,
    MAX_UNDERLINE_CHARS,
    build_new_revision_paragraphs,
    build_old_revision_paragraphs,
    convert_docx_to_doc,
    display_text_with_subchapter,
    write_docx,
)
from file_comparison.runtime.checkpoint import atomic_write_json  # noqa: E402
from file_comparison.runtime.config import load_file_comparison_runtime_config  # noqa: E402


def with_timestamp(path: Path, timestamp: str) -> Path:
    """为输出文件名追加时间戳，避免覆盖旧产物。"""
    return path.with_name(f"{path.stem}_{timestamp}{path.suffix}")


def main() -> None:
    """解析 CLI 参数并执行单次文件对照生成。"""
    parser = argparse.ArgumentParser(description="生成 Word 章节对照表")
    parser.add_argument("--old", required=True, type=Path, help="旧版文档路径")
    parser.add_argument("--new", required=True, type=Path, help="新版文档路径")
    parser.add_argument("--docx-output", required=True, type=Path, help="输出 docx 路径")
    parser.add_argument("--doc-output", type=Path, help="输出 doc 路径")
    args = parser.parse_args()

    runtime_config = load_file_comparison_runtime_config(ROOT)
    pair = PairMatch(
        pair_id="single-pair",
        key=args.old.stem,
        old_path=args.old.resolve(),
        new_path=args.new.resolve(),
        old_label=args.old.name,
        new_label=args.new.name,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = args.docx_output.parent / f".tmp-file-comparison-{timestamp}"
    output_root.mkdir(parents=True, exist_ok=True)
    result = compare_pair(pair=pair, pair_dir=output_root, runtime_config=runtime_config)

    docx_output = with_timestamp(args.docx_output, timestamp)
    docx_output.parent.mkdir(parents=True, exist_ok=True)
    docx_output.write_bytes(result.docx_path.read_bytes())
    print(docx_output)
    if args.doc_output:
        doc_output = with_timestamp(args.doc_output, timestamp)
        if result.doc_path and result.doc_path.exists():
            doc_output.write_bytes(result.doc_path.read_bytes())
        else:
            convert_docx_to_doc(docx_output, doc_output)
        print(doc_output)


if __name__ == "__main__":
    main()
