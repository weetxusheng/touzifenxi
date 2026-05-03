"""提供 batch 级恢复点识别与结果复用。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class BatchRecoveryDecision:
    """描述恢复时对某个 batch 的处理方式。"""

    action: str
    payload: dict[str, Any] | None = None
    source_path: Path | None = None


def decide_batch_recovery(batch_dir: Path) -> BatchRecoveryDecision:
    """根据 batch 已落盘文件判断是否可以复用结果。"""
    final_status_path = batch_dir / "final_status.json"
    final_status = _read_json(final_status_path)
    status = str(final_status.get("status", "")).strip()
    next_step = str(final_status.get("next_resume_step", "")).strip()

    parsed_path = batch_dir / "parsed.json"
    if not final_status and parsed_path.exists():
        return BatchRecoveryDecision(action="reuse_parsed", payload=_read_json(parsed_path), source_path=parsed_path)

    if status == "succeeded" and next_step in {"", "reuse_parsed"}:
        referenced_parsed_path = _safe_child_path(batch_dir, str(final_status.get("parsed_file", "")).strip())
        if referenced_parsed_path is not None and referenced_parsed_path.exists():
            return BatchRecoveryDecision(
                action="reuse_parsed",
                payload=_read_json(referenced_parsed_path),
                source_path=referenced_parsed_path,
            )
        if parsed_path.exists():
            return BatchRecoveryDecision(action="reuse_parsed", payload=_read_json(parsed_path), source_path=parsed_path)

    repair_path = batch_dir / "repair.json"
    repair_payload = _read_json(repair_path)
    if status == "succeeded" and next_step == "reuse_repair" and repair_payload.get("success"):
        payload = repair_payload.get("payload")
        if isinstance(payload, dict):
            return BatchRecoveryDecision(action="reuse_repair", payload=payload, source_path=repair_path)

    if status == "fallback_succeeded":
        return BatchRecoveryDecision(action="continue")

    return BatchRecoveryDecision(action="continue")


def _read_json(path: Path) -> dict[str, Any]:
    """安全读取 JSON 对象，缺失或非法时返回空对象。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_child_path(parent: Path, filename: str) -> Path | None:
    """把 final_status 里的相对文件名解析成 batch 目录内路径。"""
    if not filename:
        return None
    candidate = Path(filename)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return None
    return parent / candidate
