"""修复模型返回中常见的结构化 JSON 包装问题。"""

from __future__ import annotations

from typing import Any

from .parser import ResponseParseError, validate_payload


def repair_response_payload(payload: dict[str, Any], *, default_chapter: str) -> dict[str, Any]:
    """尝试把接近合法的模型输出修复为标准结构。"""
    if "units" in payload or "chapters" in payload:
        return validate_payload(payload)
    subsections = payload.get("subsections")
    if isinstance(subsections, list):
        repaired = {
            "chapters": [
                {
                    "chapter": default_chapter,
                    "subsections": subsections,
                }
            ]
        }
        return validate_payload(repaired)
    raise ResponseParseError("repair failed: unsupported payload shape")


def build_repair_record(
    *,
    repair_type: str,
    before_payload: dict[str, Any],
    success: bool,
    payload: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    """构造可落盘的 repair 记录。"""
    return {
        "repair_type": repair_type,
        "before_summary": summarize_payload(before_payload),
        "after_summary": summarize_payload(payload or {}),
        "success": success,
        "error": error,
        "payload": payload or {},
    }


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """返回 payload 的轻量摘要，避免日志里重复大段文本。"""
    units = payload.get("units")
    chapters = payload.get("chapters")
    subsections = payload.get("subsections")
    return {
        "keys": sorted(str(key) for key in payload.keys()),
        "unit_count": len(units) if isinstance(units, list) else 0,
        "chapter_count": len(chapters) if isinstance(chapters, list) else 0,
        "subsection_count": len(subsections) if isinstance(subsections, list) else 0,
    }
