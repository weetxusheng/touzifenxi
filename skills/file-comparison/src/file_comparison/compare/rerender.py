"""复用已完成 run 的批次结果重新生成正式文档。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..runtime.config import load_file_comparison_runtime_config
from ..runtime.recovery import decide_batch_recovery
from ..runtime.settings import iter_pair_dirs, pair_dir_for, resolve_paths
from .chunking import build_compare_blocks_for_llm, build_rows, group_compare_blocks_into_batches
from .engine import (
    build_output_document_paths,
    normalize_product_name_rows,
    validate_complete_batch_results,
)
from .extractor import extract_fund_name_or_empty, extract_text
from .models import ComparisonRow, PairMatch, Section
from .postprocess import (
    dedupe_consecutive_omitted_markers,
    drop_redundant_numeric_heading_after_marker,
    drop_repeated_short_heading_after_marker_or_heading,
    insert_marker_between_heading_and_numbered_continuation,
    insert_marker_when_subchapter_intro_omitted,
    insert_section_title_change_rows,
    merge_consecutive_delete_rows,
    merge_pure_delete_and_add_runs,
    merge_same_subchapter_rows_with_ellipsis,
    rows_from_llm_payload,
    strip_leading_ellipsis_when_no_subchapter,
    strip_trailing_ellipsis_per_cell,
)
from .writer import apply_render_config, convert_docx_to_doc, write_docx

SKILL_ROOT = Path(__file__).resolve().parents[3]


def read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件缺失、损坏或不是对象时抛出明确异常。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"缺少文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return payload


def resolve_run_dir(value: str) -> Path:
    """把 task_id 或 run 目录解析成绝对路径。"""
    candidate = Path(value).expanduser()
    if candidate.exists():
        return candidate.resolve()
    paths = resolve_paths(SKILL_ROOT)
    run_dir = paths.runs_root / value
    if run_dir.exists():
        return run_dir.resolve()
    raise FileNotFoundError(f"未找到 run 目录或 task_id: {value}")


def load_sections(path: Path) -> list[Section]:
    """从 extracted/*_sections.json 读取章节列表。"""
    payload = read_json_object(path)
    sections = payload.get("sections")
    if not isinstance(sections, list):
        raise ValueError(f"缺少 sections 数组: {path}")
    return [
        Section(
            number=str(item.get("number", "")),
            title=str(item.get("title", "")),
            body=str(item.get("body", "")),
        )
        for item in sections
        if isinstance(item, dict)
    ]


def load_pair_match(pair_dir: Path) -> PairMatch:
    """从 pair.json 读取文件对元数据。"""
    payload = read_json_object(pair_dir / "pair.json")
    return PairMatch(
        pair_id=str(payload.get("pair_id", pair_dir.name)),
        key=str(payload.get("key", pair_dir.name)),
        old_path=Path(str(payload.get("old_path", ""))),
        new_path=Path(str(payload.get("new_path", ""))),
        old_label=str(payload.get("old_label", "")),
        new_label=str(payload.get("new_label", "")),
    )


def read_fund_name(path: Path, fallback: str) -> str:
    """优先从源文档识别基金名称，失败时退回文件名。"""
    if path.exists():
        try:
            return extract_fund_name_or_empty(extract_text(path)) or fallback
        except Exception:  # noqa: BLE001
            return fallback
    return fallback


def rows_from_stored_batches(pair_dir: Path, old_sections: list[Section], new_sections: list[Section]) -> list[ComparisonRow]:
    """复用已有模型解析结果与 fallback 状态生成 rows，不发起任何请求。"""
    runtime_config = load_file_comparison_runtime_config(SKILL_ROOT)
    compare_blocks, _summaries = build_compare_blocks_for_llm(old_sections, new_sections)
    batches = group_compare_blocks_into_batches(
        compare_blocks,
        runtime_config.llm.chapter_batch_size,
        max_batch_chars=runtime_config.llm.chapter_batch_char_limit,
        oversized_batch_size=runtime_config.llm.oversized_chapter_batch_size,
        max_compare_blocks_per_batch=runtime_config.llm.max_compare_blocks_per_batch,
        max_compare_block_chars=runtime_config.llm.max_compare_block_chars,
    )
    llm_dir = pair_dir / "llm"
    batch_results: dict[str, list[ComparisonRow]] = {}
    all_lookup_blocks = tuple(block for batch in batches for block in batch.compare_blocks)
    for batch in batches:
        batch_dir = llm_dir / batch.batch_id
        recovery = decide_batch_recovery(batch_dir)
        if recovery.action in {"reuse_parsed", "reuse_repair"} and recovery.payload is not None:
            batch_results[batch.batch_id] = rows_from_llm_payload(
                recovery.payload, compare_blocks=all_lookup_blocks
            )
            continue
        raise RuntimeError(f"{batch.batch_id} 没有可复用的模型解析结果；本地 fallback 不允许生成正式文档")
    validate_complete_batch_results(batches=batches, batch_results=batch_results, llm_dir=llm_dir)
    rows: list[ComparisonRow] = []
    for batch in batches:
        rows.extend(batch_results[batch.batch_id])
    merged = merge_consecutive_delete_rows(rows)
    titled = insert_section_title_change_rows(
        merged,
        old_sections=old_sections,
        new_sections=new_sections,
    )
    pure_merged = merge_pure_delete_and_add_runs(titled)
    same_sub_merged = merge_same_subchapter_rows_with_ellipsis(pure_merged)
    intro_marker_inserted = insert_marker_when_subchapter_intro_omitted(
        same_sub_merged,
        old_sections=old_sections,
        new_sections=new_sections,
    )
    heading_marker_inserted = insert_marker_between_heading_and_numbered_continuation(
        intro_marker_inserted
    )
    redundant_heading_dropped = drop_redundant_numeric_heading_after_marker(heading_marker_inserted)
    list_lead_dropped = drop_repeated_short_heading_after_marker_or_heading(redundant_heading_dropped)
    no_orphan_lead = strip_leading_ellipsis_when_no_subchapter(list_lead_dropped)
    deduped_markers = dedupe_consecutive_omitted_markers(no_orphan_lead)
    return strip_trailing_ellipsis_per_cell(deduped_markers)


def rows_from_current_local_rules(old_sections: list[Section], new_sections: list[Section]) -> list[ComparisonRow]:
    """用当前本地 diff 与后处理规则重新生成 rows，完全忽略已有模型结果。"""
    return build_rows(old_sections, new_sections)


def output_paths(pair_dir: Path, *, pair: PairMatch, overwrite: bool, timestamp: str | None = None) -> tuple[Path, Path]:
    """根据是否覆盖决定本次重新渲染的输出文件路径。"""
    output_dir = pair_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        existing_docx = next((path for path in sorted(output_dir.glob("*.docx")) if not path.name.startswith(".~")), None)
        existing_doc = next((path for path in sorted(output_dir.glob("*.doc")) if not path.name.startswith(".~")), None)
        if existing_docx and existing_doc:
            return existing_docx, existing_doc
    return build_output_document_paths(pair, output_dir, timestamp=timestamp or datetime.now().strftime("%Y%m%d_%H%M%S"))


def rerender_pair(pair_dir: Path, *, mode: str, overwrite: bool) -> dict[str, Any]:
    """重新渲染一个 pair 的 Word 输出，并返回摘要。"""
    pair = load_pair_match(pair_dir)
    old_sections = load_sections(pair_dir / "extracted" / "old_sections.json")
    new_sections = load_sections(pair_dir / "extracted" / "new_sections.json")
    if mode == "stored":
        rows = rows_from_stored_batches(pair_dir, old_sections, new_sections)
    elif mode == "local":
        rows = rows_from_current_local_rules(old_sections, new_sections)
    else:
        raise ValueError(f"未知模式: {mode}")
    old_name = read_fund_name(pair.old_path, pair.old_path.stem or pair.old_label or "旧版")
    new_name = read_fund_name(pair.new_path, pair.new_path.stem or pair.new_label or "新版")
    rows = normalize_product_name_rows(rows, old_name, new_name)
    docx_path, doc_path = output_paths(pair_dir, pair=pair, overwrite=overwrite)
    apply_render_config(load_file_comparison_runtime_config(SKILL_ROOT).render)
    write_docx(rows, docx_path, old_name, new_name)
    convert_docx_to_doc(docx_path, doc_path)
    return {
        "pair_id": pair.pair_id,
        "mode": mode,
        "row_count": len(rows),
        "docx_path": str(docx_path),
        "doc_path": str(doc_path),
    }


def pair_dirs_for_run(run_dir: Path, pair_id: str) -> list[Path]:
    """根据 pair_id 参数返回需要重渲染的 pair 目录列表。"""
    if pair_id:
        candidate = pair_dir_for(run_dir, pair_id)
        if not candidate.exists():
            raise FileNotFoundError(f"未找到 pair: {pair_id}")
        return [candidate]
    return iter_pair_dirs(run_dir)
