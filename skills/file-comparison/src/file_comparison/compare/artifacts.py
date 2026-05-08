"""file-comparison 运行产物落盘工具。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..runtime.checkpoint import atomic_write_json
from ..runtime.execution import TaskManifest
from .models import ChapterBatch, PairMatch


def write_preprocess_artifacts(
    *,
    process_dir: Path,
    pair: PairMatch,
    old_sections,
    new_sections,
    compare_blocks,
    chapter_summaries: list[dict[str, object]],
    batches: list[ChapterBatch],
    skipped_sections: list[dict[str, object]] | None = None,
) -> None:
    """把前置 diff 的统计、单元列表和 batch 计划落盘，便于排查。"""
    process_dir.mkdir(parents=True, exist_ok=True)
    original_old_chars = sum(len(section.body) for section in old_sections)
    original_new_chars = sum(len(section.body) for section in new_sections)
    sent_old_chars = sum(sum(len(item.text) for item in block.old_items) for block in compare_blocks)
    sent_new_chars = sum(sum(len(item.text) for item in block.new_items) for block in compare_blocks)
    atomic_write_json(
        process_dir / "preprocess_summary.json",
        {
            "pair_id": pair.pair_id,
            "old_label": pair.old_label,
            "new_label": pair.new_label,
            "original_section_count_old": len(old_sections),
            "original_section_count_new": len(new_sections),
            "changed_chapter_count": sum(1 for item in chapter_summaries if item["kept_for_llm"]),
            "compare_block_count": len(compare_blocks),
            "batch_count": len(batches),
            "original_old_chars": original_old_chars,
            "original_new_chars": original_new_chars,
            "sent_old_chars": sent_old_chars,
            "sent_new_chars": sent_new_chars,
            "compression_ratio_old": 0 if original_old_chars == 0 else round(sent_old_chars / original_old_chars, 4),
            "compression_ratio_new": 0 if original_new_chars == 0 else round(sent_new_chars / original_new_chars, 4),
            "skipped_section_count": len(skipped_sections or []),
            "skipped_sections": skipped_sections or [],
            "chapters": chapter_summaries,
        },
    )
    atomic_write_json(
        process_dir / "compare_blocks.json",
        {
            "pair_id": pair.pair_id,
            "items": [
                {
                    "block_id": block.block_id,
                    "chapter_number": block.chapter_number,
                    "chapter_title": block.chapter_title,
                    "parent_path": block.parent_path,
                    "old_items": [{"item_id": item.item_id, "text": item.text} for item in block.old_items],
                    "new_items": [{"item_id": item.item_id, "text": item.text} for item in block.new_items],
                }
                for block in compare_blocks
            ],
        },
    )
    atomic_write_json(
        process_dir / "batch_plan.json",
        {
            "pair_id": pair.pair_id,
            "batches": [
                {
                    "batch_id": batch.batch_id,
                    "chapter_numbers": list(batch.chapter_numbers),
                    "block_count": len(batch.compare_blocks),
                    "sent_old_chars": sum(sum(len(item.text) for item in block.old_items) for block in batch.compare_blocks),
                    "sent_new_chars": sum(sum(len(item.text) for item in block.new_items) for block in batch.compare_blocks),
                    "block_ids": [block.block_id for block in batch.compare_blocks],
                }
                for batch in batches
            ],
        },
    )


def write_rule_preprocess_artifacts(
    *,
    process_dir: Path,
    pair: PairMatch,
    old_sections,
    new_sections,
    skipped_sections: list[dict[str, object]] | None = None,
) -> None:
    """为纯规则模式写出轻量预处理摘要，便于排查过滤行为。"""
    process_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        process_dir / "preprocess_summary.json",
        {
            "pair_id": pair.pair_id,
            "old_label": pair.old_label,
            "new_label": pair.new_label,
            "original_section_count_old": len(old_sections),
            "original_section_count_new": len(new_sections),
            "llm_enabled": False,
            "skipped_section_count": len(skipped_sections or []),
            "skipped_sections": skipped_sections or [],
        },
    )


def write_status_json(run_dir: Path, manifest: TaskManifest) -> None:
    """把任务当前状态写成页面轮询使用的 `status.json`。"""
    previous_payload = _read_json_object(run_dir / "status.json")
    started_at = manifest.started_at or str(previous_payload.get("started_at", "")).strip()
    updated_at = manifest.updated_at or datetime.now().astimezone().isoformat()
    all_batches = [batch for pair in manifest.pairs for batch in pair.batches]
    planned_batch_count = manifest.planned_batch_count or sum(pair.planned_batch_count or len(pair.batches) for pair in manifest.pairs)
    failed_provider_count = sum(
        1
        for batch in all_batches
        if batch.provider and not batch.provider_available and batch.provider != "fallback-rule"
    )
    repair_count = sum(1 for batch in all_batches if batch.repair_used)
    fallback_count = sum(1 for batch in all_batches if batch.fallback_name)
    current_batch = next((batch for batch in all_batches if batch.status not in {"success", "aborted"}), None)
    if current_batch is None and all_batches:
        current_batch = all_batches[-1]
    payload = {
        "task_id": manifest.task_id,
        "run_dir": str(manifest.run_dir),
        "status": manifest.status,
        "duration_ms": manifest.duration_ms,
        "started_at": started_at,
        "updated_at": updated_at,
        "poll_interval_seconds": manifest.poll_interval_seconds,
        "pair_count": manifest.pair_count,
        "success_count": manifest.success_count,
        "failed_count": manifest.failed_count,
        "completed_pair_count": manifest.completed_pair_count,
        "failed_pair_count": manifest.failed_pair_count,
        "governance_summary": {
            "current_batch_id": current_batch.batch_id if current_batch else "",
            "current_provider": current_batch.provider if current_batch else "",
            "current_call_status": current_batch.call_status if current_batch else "",
            "completed_batch_count": sum(1 for batch in all_batches if batch.status == "success"),
            "failed_batch_count": sum(1 for batch in all_batches if batch.status not in {"success", "pending"}),
            "provider_failure_count": failed_provider_count,
            "repair_count": repair_count,
            "fallback_count": fallback_count,
            "planned_batch_count": planned_batch_count,
            "total_batch_count": planned_batch_count,
        },
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
                "planned_batch_count": pair.planned_batch_count or len(pair.batches),
                "docx_available": bool(pair.docx_path and Path(pair.docx_path).exists()),
                "doc_available": bool(pair.doc_path and Path(pair.doc_path).exists()),
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
                        "call_status": batch.call_status,
                        "repair_used": batch.repair_used,
                        "fallback_name": batch.fallback_name,
                        "resume_from": batch.resume_from,
                    }
                    for batch in pair.batches
                ],
            }
            for pair in manifest.pairs
        ],
        "notes": list(manifest.notes),
    }
    atomic_write_json(run_dir / "status.json", payload)


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件缺失、损坏或不是对象时返回空字典。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}
