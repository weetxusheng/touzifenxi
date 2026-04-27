"""提供任务与文件对的 checkpoint 落盘和恢复能力。"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any


VALID_BATCH_STATUSES = (
    "pending",
    "success",
    "error",
    "parse_error",
    "postprocess_error",
    "aborted",
)


def utc_now_iso() -> str:
    """返回带时区的当前时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """以原子替换方式写入 JSON 文件，避免轮询读到半截内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def build_status_summary(entries: dict[str, dict[str, Any]]) -> dict[str, int]:
    """按 batch 状态汇总计数字段。"""
    counter = Counter(str(entry.get("status", "pending")) for entry in entries.values())
    summary = {"total": len(entries)}
    for status in VALID_BATCH_STATUSES:
        summary[status] = int(counter.get(status, 0))
    return summary


def compute_total_duration_ms(entries: dict[str, dict[str, Any]]) -> int:
    """累加所有 batch entry 的总耗时。"""
    total = 0
    for entry in entries.values():
        total += int(entry.get("total_duration_ms", 0) or 0)
    return total


class PairCheckpointStore:
    """维护单个文件对的 batch 级 checkpoint。"""

    def __init__(self, checkpoint_path: Path, *, pair_id: str, task_id: str, payload: dict[str, Any] | None = None) -> None:
        """初始化文件对 checkpoint，并重建内存索引。"""
        self.checkpoint_path = checkpoint_path
        self.pair_id = pair_id
        self.task_id = task_id
        self._lock = RLock()
        self._payload = payload or {
            "task_id": task_id,
            "pair_id": pair_id,
            "generated_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "status_summary": {"total": 0, **{status: 0 for status in VALID_BATCH_STATUSES}},
            "total_duration_ms": 0,
            "entries": [],
        }
        self._entries = {
            str(entry.get("entry_id")): entry
            for entry in self._payload.get("entries", [])
            if isinstance(entry, dict) and entry.get("entry_id")
        }
        self._refresh_metadata(save=False)

    @classmethod
    def load_or_create(cls, checkpoint_path: Path, *, pair_id: str, task_id: str) -> PairCheckpointStore:
        """读取已有 checkpoint，或在不存在时创建一个新的。"""
        if checkpoint_path.exists():
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            return cls(checkpoint_path, pair_id=pair_id, task_id=task_id, payload=payload)
        return cls(checkpoint_path, pair_id=pair_id, task_id=task_id)

    def _refresh_metadata(self, *, save: bool) -> None:
        """刷新摘要字段，并在需要时写回 checkpoint 文件。"""
        self._payload["task_id"] = self.task_id
        self._payload["pair_id"] = self.pair_id
        self._payload["updated_at"] = utc_now_iso()
        self._payload["status_summary"] = build_status_summary(self._entries)
        self._payload["total_duration_ms"] = compute_total_duration_ms(self._entries)
        self._payload["entries"] = [self._entries[key] for key in sorted(self._entries)]
        if save:
            atomic_write_json(self.checkpoint_path, self._payload)

    def record_entry(
        self,
        *,
        entry_id: str,
        status: str,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
        provider: str | None = None,
        request_context: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        provider_available: bool | None = None,
    ) -> None:
        """追加一次 batch 尝试结果，并更新汇总状态。"""
        normalized = str(status).strip() or "pending"
        if normalized not in VALID_BATCH_STATUSES:
            raise ValueError(f"unsupported checkpoint status: {status}")
        with self._lock:
            previous = self._entries.get(entry_id)
            attempt_count = int(previous.get("attempt_count", 0)) + 1 if previous else 1
            prior_attempts = list(previous.get("attempts", [])) if previous else []
            current_provider = provider or (previous.get("provider") if previous else "")
            current_duration_ms = int(duration_ms or 0)
            current_provider_available = (
                bool(provider_available)
                if provider_available is not None
                else normalized == "success"
            )
            current_error = error or {}
            prior_attempts.append(
                {
                    "attempt_index": attempt_count,
                    "status": normalized,
                    "provider": current_provider,
                    "provider_available": current_provider_available,
                    "duration_ms": current_duration_ms,
                    "request_context": request_context or (previous.get("request_context") if previous else {}),
                    "result": result,
                    "error": current_error,
                    "recorded_at": utc_now_iso(),
                }
            )
            self._entries[entry_id] = {
                "entry_id": entry_id,
                "status": normalized,
                "attempt_count": attempt_count,
                "provider": current_provider,
                "provider_available": current_provider_available,
                "last_updated_at": utc_now_iso(),
                "request_context": request_context or (previous.get("request_context") if previous else {}),
                "result": result,
                "error": current_error,
                "duration_ms": current_duration_ms,
                "total_duration_ms": sum(int(attempt.get("duration_ms", 0) or 0) for attempt in prior_attempts),
                "attempts": prior_attempts,
            }
            self._refresh_metadata(save=True)

    def successful_entry_ids(self) -> set[str]:
        """返回已成功完成的 batch 标识集合。"""
        with self._lock:
            return {entry_id for entry_id, entry in self._entries.items() if str(entry.get("status")) == "success"}

    def payload(self) -> dict[str, Any]:
        """返回可安全序列化的 checkpoint 副本。"""
        with self._lock:
            return json.loads(json.dumps(self._payload, ensure_ascii=False))


class TaskCheckpointStore:
    """维护任务级别的文件对状态摘要。"""

    def __init__(self, checkpoint_path: Path, *, task_id: str, payload: dict[str, Any] | None = None) -> None:
        """初始化任务 checkpoint，并重建文件对索引。"""
        self.checkpoint_path = checkpoint_path
        self.task_id = task_id
        self._lock = RLock()
        self._payload = payload or {
            "task_id": task_id,
            "generated_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "total_duration_ms": 0,
            "pairs": [],
        }
        self._pairs = {
            str(entry.get("pair_id")): entry
            for entry in self._payload.get("pairs", [])
            if isinstance(entry, dict) and entry.get("pair_id")
        }
        self._refresh(save=False)

    @classmethod
    def load_or_create(cls, checkpoint_path: Path, *, task_id: str) -> TaskCheckpointStore:
        """读取已有任务 checkpoint，或在不存在时创建新的。"""
        if checkpoint_path.exists():
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            return cls(checkpoint_path, task_id=task_id, payload=payload)
        return cls(checkpoint_path, task_id=task_id)

    def _refresh(self, *, save: bool) -> None:
        """刷新任务摘要字段，并在需要时写回磁盘。"""
        self._payload["task_id"] = self.task_id
        self._payload["updated_at"] = utc_now_iso()
        self._payload["total_duration_ms"] = sum(int(entry.get("duration_ms", 0) or 0) for entry in self._pairs.values())
        self._payload["pairs"] = [self._pairs[key] for key in sorted(self._pairs)]
        if save:
            atomic_write_json(self.checkpoint_path, self._payload)

    def record_pair(
        self,
        *,
        pair_id: str,
        status: str,
        summary: dict[str, Any] | None = None,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """记录某个文件对的最新状态摘要。"""
        with self._lock:
            previous = self._pairs.get(pair_id, {})
            self._pairs[pair_id] = {
                "pair_id": pair_id,
                "status": status,
                "summary": summary or previous.get("summary", {}),
                "error": error,
                "duration_ms": int(duration_ms or previous.get("duration_ms", 0) or 0),
                "updated_at": utc_now_iso(),
            }
            self._refresh(save=True)

    def payload(self) -> dict[str, Any]:
        """返回可安全序列化的任务 checkpoint 副本。"""
        with self._lock:
            return json.loads(json.dumps(self._payload, ensure_ascii=False))
