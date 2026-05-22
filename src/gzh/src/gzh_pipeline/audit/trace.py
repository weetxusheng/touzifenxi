"""逐步留痕：单任务单一 JSON 文件内的 steps 数组。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from gzh_pipeline.audit.redact import maybe_truncate_body, redact_headers, redact_mapping


@dataclass
class TraceRecorder:
    """记录一次业务任务内全部步骤（如单公众号抓取、单聚合解析）。"""

    max_body_bytes: int = 2_000_000
    steps: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def add_step(
        self,
        kind: str,
        label: str,
        ok: bool,
        detail: dict[str, Any] | None = None,
        err: str | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "step_index": self._idx,
            "ts": self._ts(),
            "kind": kind,
            "label": label,
            "ok": ok,
            "detail": detail or {},
        }
        if err:
            row["error"] = err
        self.steps.append(row)
        self._idx += 1

    def add_http_request(
        self,
        label: str,
        method: str,
        url: str,
        *,
        request_headers: dict[str, str] | None = None,
        request_body: Any = None,
        response_status: int,
        response_body: Any = None,
        elapsed_ms: int,
        ok: bool = True,
        err: str | None = None,
    ) -> None:
        req_h = redact_headers(request_headers)
        if isinstance(request_body, dict):
            req_b = redact_mapping(request_body)
        else:
            req_b = request_body
        req_b = maybe_truncate_body(req_b, self.max_body_bytes)
        resp_b = maybe_truncate_body(response_body, self.max_body_bytes)
        self.add_step(
            "http_request",
            label,
            ok,
            {
                "request": {"method": method, "url": url, "headers": req_h, "body": req_b},
                "response": {
                    "status_code": response_status,
                    "elapsed_ms": elapsed_ms,
                    "body": resp_b,
                },
            },
            err=err,
        )


def write_trace_json(path: Any, summary: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"summary": summary, "steps": steps}
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def monotonic_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
