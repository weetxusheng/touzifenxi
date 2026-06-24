"""请求/响应脱敏，避免密钥写入审计 JSON。"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(?i)^(key|api[_-]?key|access[_-]?token|accesstoken|authorization|cookie|verifycode|verify_code|secret)$"
)


def redact_mapping(obj: dict[str, Any] | None) -> dict[str, Any]:
    if not obj:
        return {}
    out: dict[str, Any] = {}
    for k, v in obj.items():
        ks = str(k)
        if _SENSITIVE_KEYS.match(ks):
            out[ks] = "***"
        elif isinstance(v, dict):
            out[ks] = redact_mapping(v)
        elif isinstance(v, list):
            out[ks] = [redact_mapping(x) if isinstance(x, dict) else x for x in v]
        else:
            out[ks] = v
    return out


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    h = dict(headers)
    _hdr = re.compile(r"(?i)^(authorization|cookie|access[-_]?token|accesstoken)$")
    for k in list(h.keys()):
        kk = str(k)
        if _hdr.match(kk) or (kk.lower() == "accesstoken"):
            h[k] = "***"
    return h


def maybe_truncate_body(value: Any, max_bytes: int) -> Any:
    """超限则在同一 JSON 内用截断结构表示（需求 §5.3）。"""
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    raw: str
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False)
    b = raw.encode("utf-8")
    if len(b) <= max_bytes:
        return value
    head = raw[: max_bytes // 2]
    tail = raw[-max_bytes // 4 :] if len(raw) > max_bytes // 4 else ""
    import hashlib

    return {
        "truncated": True,
        "original_byte_length": len(b),
        "sha256_hex": hashlib.sha256(b).hexdigest(),
        "head": head,
        "tail": tail,
        "note": "超过 AUDIT_STEP_BODY_MAX_BYTES，已截断；全文哈希见 sha256_hex",
    }


def deep_copy_json_safe(obj: Any) -> Any:
    return copy.deepcopy(obj)
