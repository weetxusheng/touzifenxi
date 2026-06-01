"""Shared low-level helpers for kr36 source processing."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

KR36_PROJECT_ROOT = Path(__file__).resolve().parents[3]
KR36_COOKIES_FILE = KR36_PROJECT_ROOT / "config" / "kr36_cookies.json"
KR36_DEBUG_LOG_FILE = KR36_PROJECT_ROOT / "log.txt"
_kr36_debug_log_path_override: Path | None = None


def _safe_path_component(value: str, *, fallback: str = "topic") -> str:
    token = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    token = token.strip(" .")
    if not token:
        token = fallback
    return token[:80]


def _safe_filename(value: str, *, fallback: str = "item") -> str:
    token = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    token = re.sub(r"\s+", "_", token)
    token = token.strip("._")
    if not token:
        token = fallback
    return token[:120]


@contextmanager
def kr36_debug_log_file(path: Path) -> Iterator[None]:
    """Temporarily redirect kr36 debug logs to a specific file."""
    global _kr36_debug_log_path_override
    prev = _kr36_debug_log_path_override
    _kr36_debug_log_path_override = path.resolve()
    try:
        yield
    finally:
        _kr36_debug_log_path_override = prev


def _resolve_kr36_debug_log_file() -> Path:
    custom_log_file = os.getenv("KR36_LOG_FILE", "").strip()
    if custom_log_file:
        return Path(custom_log_file)
    if _kr36_debug_log_path_override is not None:
        return _kr36_debug_log_path_override
    return KR36_DEBUG_LOG_FILE


def _append_kr36_debug_log(message: str) -> None:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} {message}"
        log_file = _resolve_kr36_debug_log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as stream:
            stream.write(f"{line}\n")
        echo_stdout = str(os.getenv("KR36_LOG_ECHO_STDOUT", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if echo_stdout:
            print(line)
    except Exception:
        return

