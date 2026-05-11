
"""多文件对任务执行与状态落盘。

职责：`run_task` 扫描 pairs、逐 pair 调用 `compare_pair`、任务级 status/checkpoint。
不负责：网页后台线程（见 `task_manager`）。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ...runtime.checkpoint import PairCheckpointStore, TaskCheckpointStore, atomic_write_json
from ...runtime.config import FileComparisonRuntimeConfig
from ...runtime.execution import BatchManifest, PairManifest, TaskManifest, task_status_from_pairs
from ...runtime.settings import prepare_run_dir, resolve_paths
from ..artifacts import write_status_json
from ..models import PairMatch
from ..pairing import scan_folder_for_pairs
from .abort_control import USER_ABORT_PAIR_MESSAGE, clear_abort_requested_flag, is_abort_requested
from .pair_compare import compare_pair

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
    for index, pair in enumerate(pairs):
        if is_abort_requested(run_dir):
            clear_abort_requested_flag(run_dir)
            abort_msg = USER_ABORT_PAIR_MESSAGE
            for rest_pair in pairs[index:]:
                rest_pair_dir = run_dir / "pairs" / rest_pair.pair_id
                for subdir in ("source", "extracted", "llm", "outputs"):
                    (rest_pair_dir / subdir).mkdir(parents=True, exist_ok=True)
                task_store.record_pair(pair_id=rest_pair.pair_id, status="aborted", error=abort_msg, duration_ms=0)
                atomic_write_json(
                    rest_pair_dir / "pair.json",
                    {
                        "pair_id": rest_pair.pair_id,
                        "key": rest_pair.key,
                        "old_path": str(rest_pair.old_path),
                        "new_path": str(rest_pair.new_path),
                        "old_label": rest_pair.old_label,
                        "new_label": rest_pair.new_label,
                        "status": "aborted",
                        "error": abort_msg,
                        "duration_ms": 0,
                    },
                )
                pair_manifests.append(
                    PairManifest(
                        pair_id=rest_pair.pair_id,
                        key=rest_pair.key,
                        old_path=str(rest_pair.old_path),
                        new_path=str(rest_pair.new_path),
                        status="aborted",
                        error=abort_msg,
                        duration_ms=0,
                    )
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
            break
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
