"""C114 多步任务统一中间结果 checkpoint 管理。"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

VALID_CHECKPOINT_STATUSES = (
    "pending",
    "success",
    "error",
    "parse_error",
    "postprocess_error",
    "aborted",
)


def checkpoint_path_for_step(
    *,
    output_path: Path,
    step_name: str,
    report_date: str,
    prefix: str = "c114",
) -> Path:
    """为某一步生成单步累计 checkpoint JSON 路径。"""

    normalized_date = report_date.replace("-", "")
    normalized_prefix = str(prefix).strip() or "c114"
    return (output_path.parent / "checkpoints" / f"{normalized_prefix}_{step_name}_checkpoint_{normalized_date}.json").resolve()


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _build_status_summary(entries: dict[str, dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(entry.get("status", "pending")) for entry in entries.values())
    summary = {"total": len(entries)}
    for status in VALID_CHECKPOINT_STATUSES:
        summary[status] = int(counter.get(status, 0))
    return summary


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class StepCheckpointStore:
    """单步累计 checkpoint 的线程安全读写封装。"""

    def __init__(
        self,
        *,
        checkpoint_path: Path,
        step_name: str,
        report_date: str,
        input_path: Path,
        output_path: Path,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.checkpoint_path = checkpoint_path
        self.step_name = step_name
        self.report_date = report_date
        self.input_path = input_path
        self.output_path = output_path
        self._lock = RLock()
        base_payload = payload or {
            "step": step_name,
            "report_date": report_date,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "generated_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "status_summary": {"total": 0, **{status: 0 for status in VALID_CHECKPOINT_STATUSES}},
            "entries": [],
        }
        self._payload = base_payload
        self._entries_by_id: dict[str, dict[str, Any]] = {
            str(entry.get("entry_id")): entry
            for entry in base_payload.get("entries", [])
            if isinstance(entry, dict) and entry.get("entry_id")
        }
        self._refresh_metadata(save=False)

    @classmethod
    def load_or_create(
        cls,
        *,
        checkpoint_path: Path,
        step_name: str,
        report_date: str,
        input_path: Path,
        output_path: Path,
    ) -> StepCheckpointStore:
        if checkpoint_path.exists():
            try:
                payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                return cls(
                    checkpoint_path=checkpoint_path,
                    step_name=step_name,
                    report_date=report_date,
                    input_path=input_path,
                    output_path=output_path,
                    payload=payload,
                )
            except FileNotFoundError:
                pass
        return cls(
            checkpoint_path=checkpoint_path,
            step_name=step_name,
            report_date=report_date,
            input_path=input_path,
            output_path=output_path,
        )

    def _refresh_metadata(self, *, save: bool) -> None:
        self._payload["step"] = self.step_name
        self._payload["report_date"] = self.report_date
        self._payload["input_path"] = str(self.input_path)
        self._payload["output_path"] = str(self.output_path)
        self._payload["status_summary"] = _build_status_summary(self._entries_by_id)
        self._payload["updated_at"] = _utc_now_iso()
        self._payload["entries"] = [self._entries_by_id[key] for key in sorted(self._entries_by_id)]
        if save:
            _atomic_write_json(self.checkpoint_path, self._payload)

    def save(self) -> None:
        with self._lock:
            self._refresh_metadata(save=True)

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries_by_id.get(entry_id)
            if entry is None:
                return None
            return json.loads(json.dumps(entry, ensure_ascii=False))

    def get_result(self, entry_id: str) -> Any:
        entry = self.get_entry(entry_id)
        if entry is None or entry.get("status") != "success":
            return None
        return entry.get("result")

    def is_success(self, entry_id: str) -> bool:
        entry = self.get_entry(entry_id)
        return bool(entry and entry.get("status") == "success")

    def successful_entry_ids(self) -> set[str]:
        with self._lock:
            return {
                entry_id
                for entry_id, entry in self._entries_by_id.items()
                if str(entry.get("status")) == "success"
            }

    def record_entry(
        self,
        *,
        entry_id: str,
        status: str,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
        provider: str | None = None,
        source: str | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> None:
        normalized_status = str(status).strip() or "pending"
        if normalized_status not in VALID_CHECKPOINT_STATUSES:
            raise ValueError(f"不支持的 checkpoint 状态：{status}")
        with self._lock:
            previous = self._entries_by_id.get(entry_id)
            attempt_count = int(previous.get("attempt_count", 0)) + 1 if previous else 1
            entry = {
                "entry_id": entry_id,
                "status": normalized_status,
                "attempt_count": attempt_count,
                "provider": provider or (previous.get("provider") if previous else ""),
                "source": source or (previous.get("source") if previous else ""),
                "last_updated_at": _utc_now_iso(),
                "request_context": request_context or (previous.get("request_context") if previous else {}),
                "result": result,
                "error": error or {},
            }
            self._entries_by_id[entry_id] = entry
            self._refresh_metadata(save=True)
