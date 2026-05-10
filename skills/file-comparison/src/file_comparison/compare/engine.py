"""编排文件扫描、模型比较、回退策略和任务状态落盘。"""

from __future__ import annotations

import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from ..llm.client import OpenAIResponsesClient
from ..runtime.checkpoint import PairCheckpointStore, TaskCheckpointStore, atomic_write_json
from ..runtime.config import FileComparisonRuntimeConfig
from ..runtime.execution import BatchManifest, PairManifest, TaskManifest, task_status_from_pairs
from ..runtime.recovery import decide_batch_recovery
from ..runtime.settings import AppPaths, prepare_run_dir, resolve_paths
from .artifacts import write_preprocess_artifacts, write_rule_preprocess_artifacts, write_status_json
from .batch_processor import _process_llm_batch
from .chunking import (
    build_compare_blocks_for_llm,
    build_rows,
    group_compare_blocks_into_batches,
)
from .extractor import extract_fund_name_or_empty, extract_text, split_sections
from .models import ChapterBatch, CompareResult, ComparisonRow, PairMatch
from .pairing import (
    build_output_document_paths,
    build_output_document_stem,
    build_pair_key,
    sanitize_output_filename_part,
    scan_folder_for_pairs,
)
from .postprocess import (
    insert_section_title_change_rows,
    merge_consecutive_delete_rows,
    merge_pure_delete_and_add_runs,
    normalize_block_operations_payload,
    normalize_product_name_rows,
    rows_from_block_operations_payload,
    rows_from_llm_payload,
    rows_from_unit_decision_payload,
    validate_unit_decision_against_source,
)
from .response_readable import (
    write_readable_response_file as _write_readable_response_file,
)
from .section_rules import apply_section_skip_rules
from .writer import convert_docx_to_doc, write_docx

__all__ = (
    "TaskManager",
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

USABLE_BATCH_FINAL_STATUSES = {"succeeded"}


























































def compare_pair(
    *,
    pair: PairMatch,
    pair_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None = None,
    client: OpenAIResponsesClient | None = None,
) -> CompareResult:
    """完成单个文件对的全文抽取、比较、写表和导出。"""
    old_text = extract_text(pair.old_path)
    new_text = extract_text(pair.new_path)
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
    pair_dir.mkdir(parents=True, exist_ok=True)
    source_dir = pair_dir / "source"
    extracted_dir = pair_dir / "extracted"
    llm_dir = pair_dir / "llm"
    output_dir = pair_dir / "outputs"
    for subdir in (source_dir, extracted_dir, llm_dir, output_dir):
        subdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pair.old_path, source_dir / pair.old_path.name)
    shutil.copy2(pair.new_path, source_dir / pair.new_path.name)
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
    return merge_pure_delete_and_add_runs(titled)


def validate_complete_batch_results(
    *,
    batches: list[ChapterBatch],
    batch_results: dict[str, list[ComparisonRow]],
    llm_dir: Path,
) -> None:
    """确认每个 batch 都有可复用结果；否则禁止生成不完整对照文档。"""
    invalid_batches: list[str] = []
    for batch in batches:
        batch_dir = llm_dir / batch.batch_id
        rows = batch_results.get(batch.batch_id)
        final_status = _read_json_object(batch_dir / "final_status.json")
        status = str(final_status.get("status", "")).strip()
        legacy_parsed_exists = not final_status and (batch_dir / "parsed.json").exists()
        has_usable_status = status in USABLE_BATCH_FINAL_STATUSES or legacy_parsed_exists
        if rows is None:
            invalid_batches.append(f"{batch.batch_id}(缺少结果)")
            continue
        if batch.compare_blocks and not rows:
            invalid_batches.append(f"{batch.batch_id}(结果为空)")
            continue
        if not has_usable_status:
            invalid_batches.append(f"{batch.batch_id}(状态不可用:{status or 'missing'})")
    if invalid_batches:
        raise RuntimeError("存在未完成或无可用结果的 batch，已停止生成文档: " + "、".join(invalid_batches))






























def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件缺失、损坏或不是对象时返回空字典。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}




























def run_task(
    *,
    folder_path: Path,
    runtime_config: FileComparisonRuntimeConfig,
    run_dir: Path | None = None,
    client: OpenAIResponsesClient | None = None,
    pairs: list[PairMatch] | None = None,
) -> TaskManifest:
    """执行一次完整批处理任务，并持续落盘任务状态。"""
    paths = resolve_paths()
    run_dir = run_dir or prepare_run_dir(paths.runs_root)
    task_started_at = time.perf_counter()
    task_started_wall_at = datetime.now().astimezone().isoformat()
    pairs = pairs or scan_folder_for_pairs(folder_path, runtime_config.pairing.month_pattern)
    task_id = run_dir.name
    task_store = TaskCheckpointStore.load_or_create(run_dir / "checkpoints" / "task_checkpoint.json", task_id=task_id)
    atomic_write_json(
        run_dir / "task.json",
        {
            "task_id": task_id,
            "folder_path": str(folder_path),
            "pair_count": len(pairs),
            "generated_at": task_started_wall_at,
            "started_at": task_started_wall_at,
        },
    )
    write_status_json(
        run_dir,
        TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status="running",
            poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=0,
            failed_count=0,
            completed_pair_count=0,
            failed_pair_count=0,
            pairs=tuple(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="pending",
                )
                for pair in pairs
            ),
            duration_ms=0,
            started_at=task_started_wall_at,
        ),
    )
    pair_manifests: list[PairManifest] = []
    for pair in pairs:
        pair_dir = run_dir / "pairs" / pair.pair_id
        for subdir in ("source", "extracted", "llm", "outputs"):
            (pair_dir / subdir).mkdir(parents=True, exist_ok=True)
        pair_store = PairCheckpointStore.load_or_create(run_dir / "checkpoints" / f"pair_{pair.pair_id}_checkpoint.json", pair_id=pair.pair_id, task_id=task_id)
        task_store.record_pair(pair_id=pair.pair_id, status="extracting")
        atomic_write_json(
            pair_dir / "pair.json",
            {
                "pair_id": pair.pair_id,
                "key": pair.key,
                "old_path": str(pair.old_path),
                "new_path": str(pair.new_path),
                "old_label": pair.old_label,
                "new_label": pair.new_label,
                "status": "extracting",
            },
        )
        write_status_json(
            run_dir,
            TaskManifest(
                task_id=task_id,
                run_dir=run_dir,
                status="running",
                poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
                pair_count=len(pairs),
                success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                pairs=(
                    *pair_manifests,
                    PairManifest(
                        pair_id=pair.pair_id,
                        key=pair.key,
                        old_path=str(pair.old_path),
                        new_path=str(pair.new_path),
                        status="extracting",
                    ),
                ),
                duration_ms=int((time.perf_counter() - task_started_at) * 1000),
            ),
        )
        pair_started_at = time.perf_counter()
        try:
            task_store.record_pair(pair_id=pair.pair_id, status="llm_running")
            write_status_json(
                run_dir,
                TaskManifest(
                    task_id=task_id,
                    run_dir=run_dir,
                    status="running",
                    poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
                    pair_count=len(pairs),
                    success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                    failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                    completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                    failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                    pairs=(
                        *pair_manifests,
                        PairManifest(
                            pair_id=pair.pair_id,
                            key=pair.key,
                            old_path=str(pair.old_path),
                            new_path=str(pair.new_path),
                            status="llm_running" if runtime_config.llm_mode == "responses" else "postprocessing",
                        ),
                    ),
                    duration_ms=int((time.perf_counter() - task_started_at) * 1000),
                ),
            )
            result = compare_pair(pair=pair, pair_dir=pair_dir, runtime_config=runtime_config, pair_store=pair_store, client=client)
            pair_payload = pair_store.payload()
            summary = pair_payload["status_summary"]
            atomic_write_json(
                pair_dir / "pair.json",
                {
                    "pair_id": pair.pair_id,
                    "key": pair.key,
                    "old_path": str(pair.old_path),
                    "new_path": str(pair.new_path),
                    "old_label": pair.old_label,
                    "new_label": pair.new_label,
                    "status": "completed",
                    "docx_path": str(result.docx_path),
                    "doc_path": str(result.doc_path) if result.doc_path else "",
                    "row_count": len(result.rows),
                    "duration_ms": int((time.perf_counter() - pair_started_at) * 1000),
                    "completed_batch_count": int(summary.get("success", 0)),
                    "failed_batch_count": int(summary.get("error", 0))
                    + int(summary.get("parse_error", 0))
                    + int(summary.get("postprocess_error", 0))
                    + int(summary.get("aborted", 0)),
                    "planned_batch_count": len(pair_payload["entries"]),
                    "docx_available": result.docx_path.exists(),
                    "doc_available": bool(result.doc_path and result.doc_path.exists()),
                },
            )
            pair_duration_ms = int((time.perf_counter() - pair_started_at) * 1000)
            task_store.record_pair(pair_id=pair.pair_id, status="completed", summary=summary, duration_ms=pair_duration_ms)
            pair_manifests.append(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="completed",
                    docx_path=str(result.docx_path),
                    doc_path=str(result.doc_path) if result.doc_path else "",
                    duration_ms=pair_duration_ms,
                    completed_batch_count=int(summary.get("success", 0)),
                    failed_batch_count=int(summary.get("error", 0))
                    + int(summary.get("parse_error", 0))
                    + int(summary.get("postprocess_error", 0))
                    + int(summary.get("aborted", 0)),
                    planned_batch_count=len(pair_payload["entries"]),
                    batches=tuple(
                        BatchManifest(
                            batch_id=entry["entry_id"],
                            status=entry["status"],
                            chapter_range=tuple(entry.get("request_context", {}).get("chapter_range", [])),
                            attempt_count=int(entry.get("attempt_count", 0)),
                            provider=str(entry.get("provider", "")),
                            error=str(entry.get("error", {}).get("message", "")),
                            duration_ms=int(entry.get("duration_ms", 0) or 0),
                            total_duration_ms=int(entry.get("total_duration_ms", 0) or 0),
                            provider_available=bool(entry.get("provider_available", True)),
                            call_status=str(entry.get("call_status", "")),
                            repair_used=bool(entry.get("repair_used", False)),
                            fallback_name=str(entry.get("fallback_name", "")),
                            resume_from=str(entry.get("resume_from", "")),
                        )
                        for entry in pair_payload["entries"]
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            pair_duration_ms = int((time.perf_counter() - pair_started_at) * 1000)
            task_store.record_pair(pair_id=pair.pair_id, status="failed", error=str(exc), duration_ms=pair_duration_ms)
            pair_manifests.append(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="failed",
                    error=str(exc),
                    duration_ms=pair_duration_ms,
                )
            )
            atomic_write_json(
                pair_dir / "pair.json",
                {
                    "pair_id": pair.pair_id,
                    "key": pair.key,
                    "old_path": str(pair.old_path),
                    "new_path": str(pair.new_path),
                    "old_label": pair.old_label,
                    "new_label": pair.new_label,
                    "status": "failed",
                    "error": str(exc),
                    "duration_ms": pair_duration_ms,
                },
            )
        manifest = TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status=task_status_from_pairs(pair_manifests),
            poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
            failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
            pairs=tuple(pair_manifests),
            duration_ms=int((time.perf_counter() - task_started_at) * 1000),
            completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
            failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
        )
        write_status_json(run_dir, manifest)
    final_manifest = TaskManifest(
        task_id=task_id,
        run_dir=run_dir,
        status=task_status_from_pairs(pair_manifests),
        poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
        pair_count=len(pairs),
        success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
        failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
        pairs=tuple(pair_manifests),
        duration_ms=int((time.perf_counter() - task_started_at) * 1000),
        completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
        failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
    )
    write_status_json(run_dir, final_manifest)
    return final_manifest


class TaskManager:
    """为本地页面提供后台任务创建与线程托管能力。"""

    def __init__(self, runtime_config: FileComparisonRuntimeConfig, paths: AppPaths | None = None) -> None:
        """保存运行配置、输出路径和后台线程表。"""
        self.runtime_config = runtime_config
        self.paths = paths or resolve_paths()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def create_task(self, folder_path: Path, pairs: list[PairMatch] | None = None) -> TaskManifest:
        """创建一个后台运行任务，并立即返回初始 manifest。"""
        run_dir = prepare_run_dir(self.paths.runs_root)
        task_id = run_dir.name
        task_started_wall_at = datetime.now().astimezone().isoformat()
        pairs = pairs or scan_folder_for_pairs(folder_path, self.runtime_config.pairing.month_pattern)
        initial_manifest = TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status="pending",
            poll_interval_seconds=self.runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=0,
            failed_count=0,
            completed_pair_count=0,
            failed_pair_count=0,
            pairs=tuple(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="pending",
                )
                for pair in pairs
            ),
            started_at=task_started_wall_at,
        )
        atomic_write_json(
            run_dir / "task.json",
            {
                "task_id": task_id,
                "folder_path": str(folder_path),
                "pair_count": len(pairs),
                "generated_at": task_started_wall_at,
                "started_at": task_started_wall_at,
            },
        )
        write_status_json(run_dir, initial_manifest)
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir, pairs), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return initial_manifest

    def rerun_batch(self, task_id: str, pair_id: str, batch_id: str) -> dict[str, str]:
        """清理单个 batch 的恢复现场，并启动同任务恢复执行。"""
        task_id = str(task_id).strip()
        pair_id = str(pair_id).strip()
        batch_id = str(batch_id).strip()
        if not task_id or not pair_id or not batch_id:
            raise ValueError("task_id、pair_id、batch_id 不能为空")
        with self._lock:
            current_thread = self._threads.get(task_id)
            if current_thread and current_thread.is_alive():
                raise RuntimeError("任务仍在运行，当前请求结束后再重跑该批次")
        run_dir = self.paths.runs_root / task_id
        if not run_dir.exists():
            raise FileNotFoundError(f"task not found: {task_id}")
        task_payload = _read_json_object(run_dir / "task.json")
        status_payload = _read_json_object(run_dir / "status.json")
        pairs = _pairs_from_status_payload(status_payload)
        if not pairs:
            raise ValueError("任务状态中没有可恢复的文件配对")
        target_pair = next((pair for pair in pairs if pair.pair_id == pair_id), None)
        if target_pair is None:
            raise ValueError(f"pair not found: {pair_id}")
        self._clear_batch_for_rerun(run_dir=run_dir, pair_id=pair_id, batch_id=batch_id)
        folder_path = Path(str(task_payload.get("folder_path") or target_pair.old_path.parent)).expanduser().resolve()
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir, pairs), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return {"task_id": task_id, "pair_id": pair_id, "batch_id": batch_id, "status": "running"}

    def _clear_batch_for_rerun(self, *, run_dir: Path, pair_id: str, batch_id: str) -> None:
        """删除单个 batch 的过程文件、checkpoint entry 和旧产物。"""
        batch_dir = run_dir / "pairs" / pair_id / "llm" / batch_id
        if batch_dir.exists():
            shutil.rmtree(batch_dir)
        pair_checkpoint_path = run_dir / "checkpoints" / f"pair_{pair_id}_checkpoint.json"
        if pair_checkpoint_path.exists():
            pair_store = PairCheckpointStore.load_or_create(pair_checkpoint_path, pair_id=pair_id, task_id=run_dir.name)
            pair_store.remove_entry(batch_id)
        outputs_dir = run_dir / "pairs" / pair_id / "outputs"
        for target in tuple(outputs_dir.glob("*.docx")) + tuple(outputs_dir.glob("*.doc")):
            if target.exists():
                target.unlink()

    def _run_background(self, folder_path: Path, run_dir: Path, pairs: list[PairMatch]) -> None:
        """在线程中执行真实任务，并在结束后清理线程登记。"""
        try:
            run_task(folder_path=folder_path, runtime_config=self.runtime_config, run_dir=run_dir, pairs=pairs)
        finally:
            with self._lock:
                self._threads.pop(run_dir.name, None)


def _pairs_from_status_payload(payload: dict[str, Any]) -> list[PairMatch]:
    """从 status.json 中恢复任务的文件配对列表。"""
    raw_pairs = payload.get("pairs", [])
    if not isinstance(raw_pairs, list):
        return []
    pairs: list[PairMatch] = []
    for index, item in enumerate(raw_pairs, start=1):
        if not isinstance(item, dict):
            continue
        old_path = Path(str(item.get("old_path", ""))).expanduser().resolve()
        new_path = Path(str(item.get("new_path", ""))).expanduser().resolve()
        if not old_path.exists() or not new_path.exists():
            continue
        pairs.append(
            PairMatch(
                pair_id=str(item.get("pair_id") or f"pair-{index:03d}"),
                key=str(item.get("key") or old_path.stem),
                old_path=old_path,
                new_path=new_path,
                old_label=old_path.name,
                new_label=new_path.name,
            )
        )
    return pairs
