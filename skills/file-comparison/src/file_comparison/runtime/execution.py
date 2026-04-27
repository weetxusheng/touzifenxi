"""定义任务、文件对和批次在运行时暴露的状态结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


TaskStatus = Literal["pending", "running", "partial_failed", "completed", "failed", "aborted"]
PairStatus = Literal["pending", "extracting", "ready_for_llm", "llm_running", "postprocessing", "rendering", "completed", "failed"]
BatchStatus = Literal["pending", "success", "error", "parse_error", "postprocess_error", "aborted"]


@dataclass(frozen=True, slots=True)
class BatchManifest:
    """表示一个章节批次在页面和状态文件中的摘要。"""

    batch_id: str
    status: BatchStatus
    chapter_range: tuple[str, ...]
    attempt_count: int = 0
    provider: str = ""
    error: str = ""
    duration_ms: int = 0
    total_duration_ms: int = 0
    provider_available: bool = True


@dataclass(frozen=True, slots=True)
class PairManifest:
    """表示一个文件对在任务中的运行摘要。"""

    pair_id: str
    key: str
    old_path: str
    new_path: str
    status: PairStatus
    docx_path: str = ""
    doc_path: str = ""
    error: str = ""
    batches: tuple[BatchManifest, ...] = ()
    duration_ms: int = 0
    completed_batch_count: int = 0
    failed_batch_count: int = 0


@dataclass(frozen=True, slots=True)
class TaskManifest:
    """表示一次批处理任务在页面中的总体状态。"""

    task_id: str
    run_dir: Path
    status: TaskStatus
    poll_interval_seconds: float
    pair_count: int
    success_count: int
    failed_count: int
    pairs: tuple[PairManifest, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    duration_ms: int = 0
    completed_pair_count: int = 0
    failed_pair_count: int = 0


def task_status_from_pairs(pairs: list[PairManifest]) -> TaskStatus:
    """根据全部文件对状态计算任务级状态。"""
    if not pairs:
        return "pending"
    statuses = {pair.status for pair in pairs}
    if statuses == {"completed"}:
        return "completed"
    if statuses <= {"failed"}:
        return "failed"
    if "failed" in statuses and "completed" in statuses:
        return "partial_failed"
    if "failed" in statuses and len(statuses) > 1:
        return "partial_failed"
    if "aborted" in statuses:
        return "aborted"
    return "running"
