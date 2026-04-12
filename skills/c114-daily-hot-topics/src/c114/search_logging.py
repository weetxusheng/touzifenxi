"""C114 step 3 搜索日志工具。"""

from __future__ import annotations

import atexit
import json
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .search_types import SearchQuery

SEARCH_TRACE_LOG_PREFIX = "c114_search_trace"


def search_trace_log_name_for_step(step_name: str, report_date: date | str) -> str:
    normalized_step = str(step_name).strip() or "unknown"
    normalized_date = str(report_date).replace("-", "")
    return f"{SEARCH_TRACE_LOG_PREFIX}_{normalized_step}_{normalized_date}.jsonl"


def _trace_phase_for_status(status: str) -> str:
    """把搜索日志状态映射成更明确的请求阶段。"""

    return {
        "started": "request_started",
        "success": "response_received",
        "error": "request_failed",
        "aborted": "request_aborted",
    }.get(status, "unknown")


def _trace_response_kind_for_status(status: str) -> str:
    """把搜索请求的响应形态转成更易读的分类。"""

    if status == "started":
        return "not_received_yet"
    if status == "success":
        return "provider_results"
    if status == "error":
        return "no_response_body"
    if status == "aborted":
        return "not_received_before_abort"
    return "unknown"


class SearchTraceLogger:
    """记录 step 3 搜索请求的 provider、query、耗时与结果数。"""

    def __init__(self, trace_path: Path) -> None:
        self.trace_path = trace_path.resolve()
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self.trace_path.touch(exist_ok=True)
        self._lock = threading.Lock()
        self._pending_records: dict[str, dict[str, Any]] = {}
        atexit.register(self.flush_pending_records_on_exit)

    def write_record(
        self,
        *,
        request_id: str | None,
        query: SearchQuery,
        provider: str,
        status: str,
        duration_ms: float,
        result_count: int = 0,
        raw_result_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "request_id": request_id,
            "timestamp": datetime.now().astimezone().isoformat(),
            "step": "step_3",
            "query_type": query.query_type,
            "query": query.value,
            "provider": provider,
            "status": status,
            "phase": _trace_phase_for_status(status),
            "response_text_present": False,
            "response_kind": _trace_response_kind_for_status(status),
            "duration_ms": round(duration_ms, 2),
            "raw_result_count": raw_result_count,
            "result_count": result_count,
        }
        if error_message is not None:
            record["error"] = {"message": error_message}
        if request_id:
            if status == "started":
                self._pending_records[request_id] = {"query": query, "provider": provider}
            elif status in {"success", "error", "aborted"}:
                self._pending_records.pop(request_id, None)
        with self._lock:
            if not self.trace_path.parent.exists():
                return
            with self.trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_started(self, *, query: SearchQuery, provider: str) -> None:
        request_id = self._next_request_id(query)
        self.write_record(
            request_id=request_id,
            query=query,
            provider=provider,
            status="started",
            duration_ms=0.0,
        )
        self._thread_local.last_request_id = request_id

    @property
    def _thread_local(self) -> threading.local:
        holder = getattr(self, "_thread_local_holder", None)
        if holder is None:
            holder = threading.local()
            self._thread_local_holder = holder
        return holder

    def current_request_id(self) -> str | None:
        return getattr(self._thread_local, "last_request_id", None)

    def _next_request_id(self, query: SearchQuery) -> str:
        return f"{query.query_type}-{threading.get_ident()}-{time.time_ns()}"

    def flush_pending_records_on_exit(self) -> None:
        pending_items = list(self._pending_records.items())
        for request_id, context in pending_items:
            query = context.get("query")
            if not isinstance(query, SearchQuery):
                continue
            self.write_record(
                request_id=request_id,
                query=query,
                provider=str(context.get("provider", "unknown")),
                status="aborted",
                duration_ms=0.0,
                error_message="进程退出时该搜索请求仍未完成。",
            )
