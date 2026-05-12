"""在 Windows 上将标准流尽量切到 UTF-8，减少重定向到文件时的中文乱码。"""

from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    """对 stdout/stderr/stdin 调用 ``reconfigure(encoding='utf-8')``（若支持且当前非 UTF-8）。"""
    if sys.platform != "win32":
        return
    for name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            enc = (getattr(stream, "encoding", None) or "").lower()
            if enc in ("utf-8", "utf8"):
                continue
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError, TypeError):
            continue
