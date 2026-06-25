"""单文件对抽取、LLM 批次比较与写表编排。

职责：`compare_pair`、`compare_pair_with_llm`；目录落盘、模式分支、多 batch 行合并入口。
不负责：单次 LLM 请求实现（见 `batch_processor`）、切块算法（见 `chunking`）。
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ...llm.client import OpenAIResponsesClient
from ...runtime.checkpoint import PairCheckpointStore, atomic_write_json
from ...runtime.config import FileComparisonRuntimeConfig
from ...runtime.recovery import decide_batch_recovery
from ..artifacts import write_preprocess_artifacts, write_rule_preprocess_artifacts
from ..batch_processor import _process_llm_batch
from ..chunking import build_compare_blocks_for_llm, build_rows, group_compare_blocks_into_batches
from ..extractor import extract_fund_name_or_empty, extract_text, split_sections
from ..models import ChapterBatch, CompareResult, ComparisonRow, PairMatch
from ..pairing import build_output_document_paths
from ..postprocess import (
    dedupe_consecutive_omitted_markers,
    drop_redundant_numeric_heading_after_marker,
    drop_repeated_short_heading_after_marker_or_heading,
    insert_marker_between_heading_and_numbered_continuation,
    insert_marker_when_subchapter_intro_omitted,
    insert_section_title_change_rows,
    merge_consecutive_delete_rows,
    merge_pure_delete_and_add_runs,
    merge_same_subchapter_rows_with_ellipsis,
    normalize_product_name_rows,
    rows_from_llm_payload,
    strip_leading_ellipsis_when_no_subchapter,
    strip_trailing_ellipsis_per_cell,
)
from ..section_rules import apply_section_skip_rules
from ..writer import apply_render_config, convert_docx_to_doc, write_docx
from ...upload.conversion_manager import resolve_converted_docx
from .batch_validation import validate_complete_batch_results

def compare_pair(
    *,
    pair: PairMatch,
    pair_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None = None,
    client: OpenAIResponsesClient | None = None,
) -> CompareResult:
    """完成单个文件对的全文抽取、比较、写表和导出。"""
    pair_dir.mkdir(parents=True, exist_ok=True)
    source_dir = pair_dir / "source"
    extracted_dir = pair_dir / "extracted"
    llm_dir = pair_dir / "llm"
    output_dir = pair_dir / "outputs"
    for subdir in (source_dir, extracted_dir, llm_dir, output_dir):
        subdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pair.old_path, source_dir / pair.old_path.name)
    shutil.copy2(pair.new_path, source_dir / pair.new_path.name)
    # 优先复用上传阶段已转换好的 .docx（uploads/<id>/converted/<stem>.docx）；没有就回退到原路径，
    # 由 extract_text 现场调 Word 转换。这样上传期异步预转换的产物在生成阶段会被秒过。
    # 命中 converted/ 时也同步复制一份 .docx 到 source/，让 source/ 在两条路径下都能展示「任务实际用的 .docx」，
    # 避免用户看到 source/ 只有 .doc 而误以为「没复用 converted」。
    old_source = resolve_converted_docx(pair.old_path) or pair.old_path
    new_source = resolve_converted_docx(pair.new_path) or pair.new_path
    if old_source != pair.old_path:
        shutil.copy2(old_source, source_dir / old_source.name)
    if new_source != pair.new_path:
        shutil.copy2(new_source, source_dir / new_source.name)
    old_text = extract_text(old_source, convert_dir=source_dir)
    new_text = extract_text(new_source, convert_dir=source_dir)
    old_sections = split_sections(old_text)
    new_sections = split_sections(new_text)
    old_sections, old_skipped_sections = apply_section_skip_rules(old_sections, runtime_config.compare.skip_section_patterns)
    new_sections, new_skipped_sections = apply_section_skip_rules(new_sections, runtime_config.compare.skip_section_patterns)
    skipped_sections = [
        {**record, "side": "old"}
        for record in old_skipped_sections
    ] + [
        {**record, "side": "new"}
        for record in new_skipped_sections
    ]
    old_name = extract_fund_name_or_empty(old_text)
    new_name = extract_fund_name_or_empty(new_text)
    atomic_write_json(
        extracted_dir / "old_sections.json",
        {
            "skipped_sections": [record for record in skipped_sections if record["side"] == "old"],
            "sections": [{"number": section.number, "title": section.title, "body": section.body} for section in old_sections],
        },
    )
    atomic_write_json(
        extracted_dir / "new_sections.json",
        {
            "skipped_sections": [record for record in skipped_sections if record["side"] == "new"],
            "sections": [{"number": section.number, "title": section.title, "body": section.body} for section in new_sections],
        },
    )
    if runtime_config.llm_mode == "responses":
        rows = compare_pair_with_llm(
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            llm_dir=llm_dir,
            runtime_config=runtime_config,
            pair_store=pair_store,
            client=client or OpenAIResponsesClient(runtime_config),
            process_dir=extracted_dir,
            skipped_sections=skipped_sections,
        )
    else:
        rows = build_rows(old_sections, new_sections)
        write_rule_preprocess_artifacts(
            process_dir=extracted_dir,
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            skipped_sections=skipped_sections,
        )
    rows = normalize_product_name_rows(rows, old_name, new_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_path, doc_path = build_output_document_paths(pair, output_dir)
    old_display_name = old_name or pair.old_path.stem
    new_display_name = new_name or pair.new_path.stem
    apply_render_config(runtime_config.render)
    write_docx(rows, docx_path, old_display_name, new_display_name)
    convert_docx_to_doc(docx_path, doc_path)
    return CompareResult(
        pair_id=pair.pair_id,
        old_name=old_display_name,
        new_name=new_display_name,
        rows=rows,
        docx_path=docx_path,
        doc_path=doc_path,
        metadata={
            "old_fund_name_detected": bool(old_name),
            "new_fund_name_detected": bool(new_name),
        },
    )

def compare_pair_with_llm(
    *,
    pair: PairMatch,
    old_sections,
    new_sections,
    llm_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None,
    client: OpenAIResponsesClient,
    process_dir: Path | None = None,
    skipped_sections: list[dict[str, object]] | None = None,
) -> list[ComparisonRow]:
    """对单个文件对执行本地预处理、批次并行和多模型比较。"""
    compare_blocks, chapter_summaries = build_compare_blocks_for_llm(old_sections, new_sections)
    batches = group_compare_blocks_into_batches(
        compare_blocks,
        runtime_config.llm.chapter_batch_size,
        max_batch_chars=runtime_config.llm.chapter_batch_char_limit,
        oversized_batch_size=runtime_config.llm.oversized_chapter_batch_size,
        max_compare_blocks_per_batch=runtime_config.llm.max_compare_blocks_per_batch,
        max_compare_block_chars=runtime_config.llm.max_compare_block_chars,
    )
    if process_dir is not None:
        write_preprocess_artifacts(
            process_dir=process_dir,
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            compare_blocks=compare_blocks,
            chapter_summaries=chapter_summaries,
            batches=batches,
            skipped_sections=skipped_sections,
        )
    provider_chain = (
        client.provider_chain_for_attempts()
        if hasattr(client, "provider_chain_for_attempts")
        else runtime_config.llm.providers
    )
    batch_results: dict[str, list[ComparisonRow]] = {}
    pending_batches: list[tuple[int, ChapterBatch]] = []
    all_lookup_blocks = tuple(block for batch in batches for block in batch.compare_blocks)
    for batch_index, batch in enumerate(batches):
        batch_dir = llm_dir / batch.batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        recovery = decide_batch_recovery(batch_dir)
        if recovery.action in {"reuse_parsed", "reuse_repair"} and recovery.payload is not None:
            batch_results[batch.batch_id] = rows_from_llm_payload(
                recovery.payload, compare_blocks=all_lookup_blocks
            )
            continue
        pending_batches.append((batch_index, batch))
    if pending_batches:
        max_workers = min(runtime_config.execution.per_pair_max_workers, len(pending_batches))
        batch_errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _process_llm_batch,
                    pair=pair,
                    batch=batch,
                    batch_index=batch_index,
                    llm_dir=llm_dir,
                    runtime_config=runtime_config,
                    pair_store=pair_store,
                    client=client,
                    provider_chain=provider_chain,
                    item_lookup_blocks=all_lookup_blocks,
                ): batch.batch_id
                for batch_index, batch in pending_batches
            }
            for future in as_completed(futures):
                try:
                    batch_id, rows = future.result()
                    batch_results[batch_id] = rows
                except Exception as exc:  # noqa: BLE001
                    batch_errors.append(f"{futures[future]}: {type(exc).__name__}: {exc}")
        if batch_errors:
            raise RuntimeError("存在未完成或无可用结果的 batch，已停止生成文档: " + "；".join(batch_errors))
    validate_complete_batch_results(batches=batches, batch_results=batch_results, llm_dir=llm_dir)
    ordered_rows: list[ComparisonRow] = []
    for batch in batches:
        ordered_rows.extend(batch_results[batch.batch_id])
    merged = merge_consecutive_delete_rows(ordered_rows)
    titled = insert_section_title_change_rows(
        merged,
        old_sections=list(old_sections),
        new_sections=list(new_sections),
    )
    pure_merged = merge_pure_delete_and_add_runs(titled)
    same_sub_merged = merge_same_subchapter_rows_with_ellipsis(pure_merged)
    intro_marker_inserted = insert_marker_when_subchapter_intro_omitted(
        same_sub_merged,
        old_sections=list(old_sections),
        new_sections=list(new_sections),
    )
    heading_marker_inserted = insert_marker_between_heading_and_numbered_continuation(
        intro_marker_inserted
    )
    redundant_heading_dropped = drop_redundant_numeric_heading_after_marker(heading_marker_inserted)
    list_lead_dropped = drop_repeated_short_heading_after_marker_or_heading(redundant_heading_dropped)
    no_orphan_lead = strip_leading_ellipsis_when_no_subchapter(list_lead_dropped)
    deduped_markers = dedupe_consecutive_omitted_markers(no_orphan_lead)
    return strip_trailing_ellipsis_per_cell(deduped_markers)
