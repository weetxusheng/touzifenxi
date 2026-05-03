"""提供模型调用状态机、timeline 记录和统一失败分类。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checkpoint import atomic_write_json, utc_now_iso

CALL_STATUSES = {
    "prepared",
    "requesting",
    "responded",
    "normalized",
    "repaired",
    "succeeded",
    "timeout",
    "infra_error",
    "parse_error",
    "postprocess_error",
    "fallback_succeeded",
    "failed",
}


@dataclass(slots=True)
class CallEvent:
    """表示一次模型调用流程中的单个状态事件。"""

    status: str
    provider: str = ""
    attempt_index: int = 0
    duration_ms: int = 0
    error: str = ""
    retry_class: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=utc_now_iso)

    def as_dict(self) -> dict[str, Any]:
        """返回可写入 JSON 的事件字典。"""
        return {
            "status": self.status,
            "provider": self.provider,
            "attempt_index": self.attempt_index,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "retry_class": self.retry_class,
            "details": self.details,
            "recorded_at": self.recorded_at,
        }


class CallTimeline:
    """把单个 batch 的调用过程持续落成 timeline 文件。"""

    def __init__(self, batch_dir: Path, *, batch_id: str) -> None:
        """初始化 timeline 存储位置和基础状态。"""
        self.batch_dir = batch_dir
        self.batch_id = batch_id
        self.started_at = time.perf_counter()
        self.events: list[CallEvent] = []

    def record(
        self,
        status: str,
        *,
        provider: str = "",
        attempt_index: int = 0,
        duration_ms: int = 0,
        error: str = "",
        retry_class: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """追加一个状态事件，并立即写回 `timeline.json`。"""
        if status not in CALL_STATUSES:
            raise ValueError(f"unsupported call status: {status}")
        self.events.append(
            CallEvent(
                status=status,
                provider=provider,
                attempt_index=attempt_index,
                duration_ms=duration_ms,
                error=error,
                retry_class=retry_class,
                details=details or {},
            )
        )
        atomic_write_json(self.batch_dir / "timeline.json", self.payload())

    def finalize(
        self,
        status: str,
        *,
        attempt_count: int,
        provider: str = "",
        fallback: str = "",
        error: str = "",
        recoverable: bool = False,
        next_resume_step: str = "",
        parsed_file: str = "",
    ) -> None:
        """写出 batch 调用的最终状态摘要。"""
        if status not in CALL_STATUSES:
            raise ValueError(f"unsupported final call status: {status}")
        payload = {
            "batch_id": self.batch_id,
            "status": status,
            "attempt_count": attempt_count,
            "provider": provider,
            "fallback": fallback,
            "error": error,
            "recoverable": recoverable,
            "next_resume_step": next_resume_step,
            "duration_ms": int((time.perf_counter() - self.started_at) * 1000),
            "event_count": len(self.events),
            "updated_at": utc_now_iso(),
        }
        if parsed_file:
            payload["parsed_file"] = parsed_file
        atomic_write_json(self.batch_dir / "final_status.json", payload)

    def payload(self) -> dict[str, Any]:
        """返回 timeline 的完整 JSON 结构。"""
        return {
            "batch_id": self.batch_id,
            "duration_ms": int((time.perf_counter() - self.started_at) * 1000),
            "events": [event.as_dict() for event in self.events],
        }


def classify_exception_status(exc: Exception) -> tuple[str, str]:
    """把异常统一归类为调用状态和 retry class。"""
    retry_class = str(getattr(exc, "retry_class", "") or "").strip()
    message = str(exc)
    lowered = message.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout", retry_class or "infra"
    if retry_class == "infra" or bool(getattr(exc, "infrastructure_error", False)):
        return "infra_error", retry_class or "infra"
    if retry_class == "parse":
        return "parse_error", retry_class
    if retry_class == "postprocess":
        return "postprocess_error", retry_class
    return "infra_error", retry_class or "infra"
