"""编排文件扫描、模型比较、回退策略和任务状态落盘。"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..llm.client import OpenAIResponsesClient
from ..llm.parser import ResponseParseError, parse_response_payload
from ..llm.retry import RetryClassifier, RetryClassifierConfig
from ..runtime.checkpoint import PairCheckpointStore, TaskCheckpointStore, atomic_write_json
from ..runtime.config import FileComparisonRuntimeConfig
from ..runtime.execution import BatchManifest, PairManifest, TaskManifest, task_status_from_pairs
from ..runtime.settings import AppPaths, prepare_run_dir, resolve_paths
from .chunking import (
    build_rows,
    group_sections_into_batches,
    is_only_numbering_changed,
    preprocess_sections_for_llm,
    remove_fully_equal_lines,
)
from .extractor import extract_fund_name, extract_text, split_sections
from .models import ChapterBatch, CompareResult, ComparisonRow, PairMatch
from .writer import convert_docx_to_doc, write_docx


MONTH_RE = re.compile(r"(?P<month>\d{1,2})月")


def build_pair_key(path: Path, month_pattern: str) -> tuple[str, int | None]:
    """根据文件名生成配对主键，并提取月份信息。"""
    pattern = re.compile(month_pattern)
    stem = path.stem
    match = pattern.search(stem)
    month = int(match.group("month")) if match and match.groupdict().get("month") else None
    key = pattern.sub("", stem)
    key = re.sub(r"[_\-\s]+", "", key)
    return key, month


def scan_folder_for_pairs(folder_path: Path, month_pattern: str) -> list[PairMatch]:
    """扫描目录中的文档并按“去月份后文件名”自动配对。"""
    groups: dict[str, list[tuple[Path, int | None]]] = {}
    for path in sorted(folder_path.iterdir()):
        if path.suffix.lower() not in {".doc", ".docx"} or path.name.startswith(".~"):
            continue
        key, month = build_pair_key(path, month_pattern)
        groups.setdefault(key, []).append((path, month))
    pairs: list[PairMatch] = []
    for index, (key, items) in enumerate(sorted(groups.items()), start=1):
        if len(items) < 2:
            continue
        sorted_items = sorted(items, key=lambda item: ((item[1] is None), item[1] if item[1] is not None else 999, item[0].name))
        old_path, old_month = sorted_items[0]
        new_path, new_month = sorted_items[-1]
        pairs.append(
            PairMatch(
                pair_id=f"pair-{index:03d}",
                key=key,
                old_path=old_path,
                new_path=new_path,
                old_label=old_path.name,
                new_label=new_path.name,
                old_month=old_month,
                new_month=new_month,
            )
        )
    return pairs


def rows_from_llm_payload(payload: dict[str, Any]) -> list[ComparisonRow]:
    """把模型返回的结构化 payload 转成最终展示行。"""
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
    old_name = extract_fund_name(old_text)
    new_name = extract_fund_name(new_text)
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
        {"sections": [{"number": section.number, "title": section.title, "body": section.body} for section in old_sections]},
    )
    atomic_write_json(
        extracted_dir / "new_sections.json",
        {"sections": [{"number": section.number, "title": section.title, "body": section.body} for section in new_sections]},
    )
    if runtime_config.llm_mode == "responses":
        prepared_old_sections, prepared_new_sections = preprocess_sections_for_llm(old_sections, new_sections)
        rows = compare_pair_with_llm(
            pair=pair,
            old_sections=prepared_old_sections,
            new_sections=prepared_new_sections,
            llm_dir=llm_dir,
            runtime_config=runtime_config,
            pair_store=pair_store,
            client=client or OpenAIResponsesClient(runtime_config),
        )
    else:
        rows = build_rows(old_sections, new_sections)
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_path = output_dir / "comparison.docx"
    doc_path = output_dir / "comparison.doc"
    write_docx(rows, docx_path, old_name, new_name)
    convert_docx_to_doc(docx_path, doc_path)
    return CompareResult(pair_id=pair.pair_id, old_name=old_name, new_name=new_name, rows=rows, docx_path=docx_path, doc_path=doc_path)


def compare_pair_with_llm(
    *,
    pair: PairMatch,
    old_sections,
    new_sections,
    llm_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None,
    client: OpenAIResponsesClient,
) -> list[ComparisonRow]:
    """对单个文件对执行本地预处理、批次并行和多模型比较。"""
    successful = pair_store.successful_entry_ids() if pair_store else set()
    retry_classifier = RetryClassifier(
        RetryClassifierConfig(
            infra_max_attempts=runtime_config.llm.infra_max_attempts,
            parse_max_attempts=runtime_config.llm.parse_max_attempts,
            postprocess_max_attempts=runtime_config.llm.postprocess_max_attempts,
            fatal_max_attempts=runtime_config.llm.fatal_max_attempts,
        )
    )
    provider_chain = (
        client.provider_chain_for_attempts()
        if hasattr(client, "provider_chain_for_attempts")
        else runtime_config.llm.providers
    )
    batches: list[ChapterBatch] = [
        ChapterBatch(
            batch_id=f"batch-{index:03d}",
            chapter_numbers=tuple(section.number for section in old_batch or new_batch),
            old_sections=old_batch,
            new_sections=new_batch,
        )
        for index, (old_batch, new_batch) in enumerate(group_sections_into_batches(old_sections, new_sections, runtime_config.llm.chapter_batch_size), start=1)
    ]
    batch_results: dict[str, list[ComparisonRow]] = {}
    pending_batches: list[tuple[int, ChapterBatch]] = []
    for batch_index, batch in enumerate(batches):
        batch_dir = llm_dir / batch.batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        if batch.batch_id in successful:
            parsed_path = batch_dir / "parsed.json"
            batch_results[batch.batch_id] = rows_from_llm_payload(json.loads(parsed_path.read_text(encoding="utf-8")))
            continue
        pending_batches.append((batch_index, batch))
    if pending_batches:
        max_workers = min(runtime_config.execution.per_pair_max_workers, len(pending_batches))
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
                    retry_classifier=retry_classifier,
                    provider_chain=provider_chain,
                ): batch.batch_id
                for batch_index, batch in pending_batches
            }
            for future in as_completed(futures):
                batch_id, rows = future.result()
                batch_results[batch_id] = rows
    ordered_rows: list[ComparisonRow] = []
    for batch in batches:
        ordered_rows.extend(batch_results.get(batch.batch_id, []))
    return ordered_rows


def _process_llm_batch(
    *,
    pair: PairMatch,
    batch: ChapterBatch,
    batch_index: int,
    llm_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None,
    client: OpenAIResponsesClient,
    retry_classifier: RetryClassifier,
    provider_chain,
) -> tuple[str, list[ComparisonRow]]:
    """执行单个章节批次的模型比较、重试和规则回退。"""
    batch_dir = llm_dir / batch.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    routed_provider_chain = _provider_chain_for_batch(
        provider_chain=provider_chain,
        batch_index=batch_index,
        runtime_config=runtime_config,
    )
    rows: list[ComparisonRow] = []
    last_error = ""
    batch_succeeded = False
    should_fallback = False
    parse_attempts = retry_classifier.max_attempts_for("parse", default=runtime_config.llm.parse_max_attempts)
    postprocess_attempts = retry_classifier.max_attempts_for("postprocess", default=runtime_config.llm.postprocess_max_attempts)
    infra_attempts = retry_classifier.max_attempts_for("infra", default=runtime_config.llm.infra_max_attempts) * max(1, len(routed_provider_chain))
    parse_failures = 0
    postprocess_failures = 0
    infra_failures = 0
    attempt_index = 0
    while True:
        attempt_index += 1
        provider_config = routed_provider_chain[(attempt_index - 1) % len(routed_provider_chain)]
        request_payload = client.build_request_payload(
            pair_id=pair.pair_id,
            batch=batch,
            provider_config=provider_config,
        )
        request_context = {
            "pair_id": pair.pair_id,
            "chapter_range": list(batch.chapter_numbers),
            "old_section_titles": [section.title for section in batch.old_sections],
            "new_section_titles": [section.title for section in batch.new_sections],
            "provider": provider_config.provider,
            "model": provider_config.model,
            "batch_size": runtime_config.llm.chapter_batch_size,
            "attempt_index": attempt_index,
        }
        client.write_request(batch_dir / "request.json", request_payload)
        client.write_request(
            batch_dir / f"request.attempt-{attempt_index:02d}.{provider_config.provider}.json",
            request_payload,
        )
        attempt_started_at = time.perf_counter()
        try:
            raw_response = client.post(request_payload, provider_config=provider_config)
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            client.write_response(batch_dir / "response.json", raw_response)
            client.write_response(
                batch_dir / f"response.attempt-{attempt_index:02d}.{provider_config.provider}.json",
                raw_response,
            )
            parsed = parse_response_payload(raw_response)
            (batch_dir / "parsed.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (batch_dir / f"parsed.attempt-{attempt_index:02d}.{provider_config.provider}.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            batch_rows = rows_from_llm_payload(parsed)
            rows.extend(batch_rows)
            if pair_store:
                pair_store.record_entry(
                    entry_id=batch.batch_id,
                    status="success",
                    result={"row_count": len(batch_rows)},
                    provider=provider_config.provider,
                    request_context=request_context,
                    duration_ms=attempt_duration_ms,
                    provider_available=True,
                )
            batch_succeeded = True
            break
        except json.JSONDecodeError as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"JSONDecodeError: {exc}"
            parse_failures += 1
            if pair_store:
                pair_store.record_entry(
                    entry_id=batch.batch_id,
                    status="parse_error",
                    error={"message": last_error},
                    provider=provider_config.provider,
                    request_context=request_context,
                    duration_ms=attempt_duration_ms,
                    provider_available=False,
                )
        except ResponseParseError as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"ResponseParseError: {exc}"
            parse_failures += 1
            if pair_store:
                pair_store.record_entry(
                    entry_id=batch.batch_id,
                    status="parse_error",
                    error={"message": last_error},
                    provider=provider_config.provider,
                    request_context=request_context,
                    duration_ms=attempt_duration_ms,
                    provider_available=False,
                )
        except ValueError as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"ValueError: {exc}"
            postprocess_failures += 1
            if pair_store:
                pair_store.record_entry(
                    entry_id=batch.batch_id,
                    status="postprocess_error",
                    error={"message": last_error},
                    provider=provider_config.provider,
                    request_context=request_context,
                    duration_ms=attempt_duration_ms,
                    provider_available=False,
                )
        except Exception as exc:  # noqa: BLE001
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            infra_failures += 1
            if pair_store:
                pair_store.record_entry(
                    entry_id=batch.batch_id,
                    status="error",
                    error={"message": last_error},
                    provider=provider_config.provider,
                    request_context=request_context,
                    duration_ms=attempt_duration_ms,
                    provider_available=False,
                )
            (batch_dir / "error.txt").write_text(last_error + "\n", encoding="utf-8")
            if infra_failures >= infra_attempts:
                should_fallback = True
                break
            continue
        (batch_dir / "error.txt").write_text(last_error + "\n", encoding="utf-8")
        max_total_attempts = max(parse_attempts, postprocess_attempts, infra_attempts)
        if (
            parse_failures >= parse_attempts
            or postprocess_failures >= postprocess_attempts
            or infra_failures >= infra_attempts
            or attempt_index >= max_total_attempts
        ):
            should_fallback = True
            break
    if should_fallback and not batch_succeeded:
        fallback_rows = build_rows(list(batch.old_sections), list(batch.new_sections))
        rows.extend(fallback_rows)
        if pair_store:
            pair_store.record_entry(
                entry_id=batch.batch_id,
                status="success",
                result={"row_count": len(fallback_rows), "fallback": True},
                provider="fallback-rule",
                request_context={
                    "pair_id": pair.pair_id,
                    "chapter_range": list(batch.chapter_numbers),
                    "old_section_titles": [section.title for section in batch.old_sections],
                    "new_section_titles": [section.title for section in batch.new_sections],
                    "provider": "fallback-rule",
                    "model": runtime_config.llm.model,
                    "batch_size": runtime_config.llm.chapter_batch_size,
                    "attempt_index": attempt_index,
                },
                duration_ms=0,
                provider_available=True,
            )
    return batch.batch_id, rows


def _provider_chain_for_batch(*, provider_chain, batch_index: int, runtime_config: FileComparisonRuntimeConfig):
    """按批次序号和路由配置生成本批首发 provider 顺序。"""
    if not runtime_config.llm.task_routing.enabled or not runtime_config.llm.task_routing.batch_compare:
        return tuple(provider_chain)
    providers_by_name = {provider.provider: provider for provider in provider_chain}
    configured_names = [name for name in runtime_config.llm.task_routing.batch_compare if name in providers_by_name]
    if not configured_names:
        return tuple(provider_chain)
    start_index = batch_index % len(configured_names)
    rotated_names = configured_names[start_index:] + configured_names[:start_index]
    ordered_chain = [providers_by_name[name] for name in rotated_names]
    ordered_names = set(rotated_names)
    ordered_chain.extend(provider for provider in provider_chain if provider.provider not in ordered_names)
    return tuple(ordered_chain)


def write_status_json(run_dir: Path, manifest: TaskManifest) -> None:
    """把任务当前状态写成页面轮询使用的 `status.json`。"""
    payload = {
        "task_id": manifest.task_id,
        "run_dir": str(manifest.run_dir),
        "status": manifest.status,
        "duration_ms": manifest.duration_ms,
        "poll_interval_seconds": manifest.poll_interval_seconds,
        "pair_count": manifest.pair_count,
        "success_count": manifest.success_count,
        "failed_count": manifest.failed_count,
        "completed_pair_count": manifest.completed_pair_count,
        "failed_pair_count": manifest.failed_pair_count,
        "pairs": [
            {
                "pair_id": pair.pair_id,
                "key": pair.key,
                "status": pair.status,
                "old_path": pair.old_path,
                "new_path": pair.new_path,
                "docx_path": pair.docx_path,
                "doc_path": pair.doc_path,
                "error": pair.error,
                "duration_ms": pair.duration_ms,
                "completed_batch_count": pair.completed_batch_count,
                "failed_batch_count": pair.failed_batch_count,
                "batches": [
                    {
                        "batch_id": batch.batch_id,
                        "status": batch.status,
                        "chapter_range": list(batch.chapter_range),
                        "attempt_count": batch.attempt_count,
                        "provider": batch.provider,
                        "error": batch.error,
                        "duration_ms": batch.duration_ms,
                        "total_duration_ms": batch.total_duration_ms,
                        "provider_available": batch.provider_available,
                    }
                    for batch in pair.batches
                ],
            }
            for pair in manifest.pairs
        ],
        "notes": list(manifest.notes),
    }
    atomic_write_json(run_dir / "status.json", payload)


def run_task(*, folder_path: Path, runtime_config: FileComparisonRuntimeConfig, run_dir: Path | None = None, client: OpenAIResponsesClient | None = None) -> TaskManifest:
    """执行一次完整批处理任务，并持续落盘任务状态。"""
    paths = resolve_paths()
    run_dir = run_dir or prepare_run_dir(paths.runs_root)
    task_started_at = time.perf_counter()
    pairs = scan_folder_for_pairs(folder_path, runtime_config.pairing.month_pattern)
    task_id = run_dir.name
    task_store = TaskCheckpointStore.load_or_create(run_dir / "checkpoints" / "task_checkpoint.json", task_id=task_id)
    atomic_write_json(
        run_dir / "task.json",
        {
            "task_id": task_id,
            "folder_path": str(folder_path),
            "pair_count": len(pairs),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
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
        pair_started_at = time.perf_counter()
        try:
            task_store.record_pair(pair_id=pair.pair_id, status="llm_running")
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

    def create_task(self, folder_path: Path) -> TaskManifest:
        """创建一个后台运行任务，并立即返回初始 manifest。"""
        run_dir = prepare_run_dir(self.paths.runs_root)
        task_id = run_dir.name
        pairs = scan_folder_for_pairs(folder_path, self.runtime_config.pairing.month_pattern)
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
        )
        atomic_write_json(
            run_dir / "task.json",
            {
                "task_id": task_id,
                "folder_path": str(folder_path),
                "pair_count": len(pairs),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        write_status_json(run_dir, initial_manifest)
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return initial_manifest

    def _run_background(self, folder_path: Path, run_dir: Path) -> None:
        """在线程中执行真实任务，并在结束后清理线程登记。"""
        try:
            run_task(folder_path=folder_path, runtime_config=self.runtime_config, run_dir=run_dir)
        finally:
            with self._lock:
                self._threads.pop(run_dir.name, None)
