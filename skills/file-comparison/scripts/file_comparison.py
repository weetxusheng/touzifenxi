"""提供 file-comparison skill 的单次文档对照 CLI 入口。"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if __name__ == "file_comparison":
    sys.modules.pop(__name__, None)

from file_comparison.compare.chunking import (  # noqa: E402
    OMITTED_EQUAL_MARKER,
    build_compare_blocks_for_llm,
    build_compare_units_for_llm,
    build_comparison_row,
    build_section_rows,
    group_compare_blocks_into_batches,
    group_compare_units_into_batches,
    preprocess_sections_for_llm,
    remove_fully_equal_lines,
)
from file_comparison.compare.engine import (  # noqa: E402
    apply_section_skip_rules,
    build_output_document_paths,
    build_output_document_stem,
    compare_pair,
    normalize_product_name_rows,
)
from file_comparison.compare.extractor import (  # noqa: E402
    Section,
    extract_fund_name_or_empty,
    format_number_label,
    split_sections,
)
from file_comparison.compare.models import (  # noqa: E402
    CompareBlock,
    CompareBlockItem,
    CompareUnit,
    ComparisonRow,
    PairMatch,
)
from file_comparison.compare.writer import (  # noqa: E402
    build_new_revision_paragraphs,
    build_old_revision_paragraphs,
    convert_docx_to_doc,
    display_text_with_subchapter,
    old_change_style,
    write_docx,
)
from file_comparison.runtime.config import load_file_comparison_runtime_config  # noqa: E402
from file_comparison.runtime.python_env import maybe_reexec_into_project_python  # noqa: E402

__all__ = (
    "CompareUnit",
    "CompareBlock",
    "CompareBlockItem",
    "ComparisonRow",
    "PairMatch",
    "Section",
    "apply_section_skip_rules",
    "build_output_document_paths",
    "build_output_document_stem",
    "build_compare_blocks_for_llm",
    "build_compare_units_for_llm",
    "build_comparison_row",
    "build_new_revision_paragraphs",
    "build_old_revision_paragraphs",
    "build_section_rows",
    "comparison_output_path",
    "display_text_with_subchapter",
    "extract_fund_name_or_empty",
    "format_number_label",
    "group_compare_blocks_into_batches",
    "group_compare_units_into_batches",
    "main",
    "month_label",
    "OMITTED_EQUAL_MARKER",
    "normalize_product_name_rows",
    "old_change_style",
    "preprocess_sections_for_llm",
    "remove_fully_equal_lines",
    "safe_filename_part",
    "split_sections",
    "with_timestamp",
    "write_docx",
)


MONTH_LABEL_RE = re.compile(r"\d{1,2}月")
FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\r\n]+')


def with_timestamp(path: Path, timestamp: str) -> Path:
    """为输出文件名追加时间戳，避免覆盖旧产物。"""
    return path.with_name(f"{path.stem}_{timestamp}{path.suffix}")


def safe_filename_part(value: str) -> str:
    """把产品名等业务文本清洗成可作为文件名的片段。"""
    cleaned = FILENAME_UNSAFE_RE.sub("", value).strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned or "未识别产品"


def month_label(path: Path) -> str:
    """从文件名中提取月份标签，无法识别时退回文件 stem。"""
    match = MONTH_LABEL_RE.search(path.stem)
    return match.group(0) if match else path.stem


def comparison_output_path(base_path: Path, product_name: str, old_path: Path, new_path: Path, timestamp: str) -> Path:
    """按“前文件名 与 后文件名 对照表 时间戳”生成输出路径。"""
    del product_name
    pair = PairMatch(
        pair_id="single-pair",
        key=old_path.stem,
        old_path=old_path,
        new_path=new_path,
        old_label=old_path.name,
        new_label=new_path.name,
    )
    return base_path.with_name(f"{build_output_document_stem(pair, timestamp=timestamp)}{base_path.suffix}")


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

    docx_output = comparison_output_path(args.docx_output, "", args.old, args.new, timestamp)
    docx_output.parent.mkdir(parents=True, exist_ok=True)
    docx_output.write_bytes(result.docx_path.read_bytes())
    print(docx_output)
    if args.doc_output:
        doc_output = comparison_output_path(args.doc_output, "", args.old, args.new, timestamp)
        if result.doc_path and result.doc_path.exists():
            doc_output.write_bytes(result.doc_path.read_bytes())
        else:
            convert_docx_to_doc(docx_output, doc_output)
        print(doc_output)


if __name__ == "__main__":
    maybe_reexec_into_project_python(
        skill_root=ROOT,
        script_path=Path(__file__).resolve(),
        argv=sys.argv[1:],
        current_executable=sys.executable,
        execv=os.execv,
    )
    main()
