"""爱集微 published-time 多形态解析。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

SHANGHAI = timezone(timedelta(hours=8))

_REL_RE = re.compile(r"^(\d+)\s*(分钟|小时|天)前$")
_YESTERDAY_RE = re.compile(r"^昨天\s+(\d{1,2}):(\d{2})$")
_MM_DD_RE = re.compile(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$")
_YYYY_MM_DD_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$")


def parse_laoyaoba_published_time(
    text: str,
    *,
    reference_now: Optional[datetime] = None,
) -> Optional[datetime]:
    """把爱集微 `<span class="published-time">` 文本解析为 Asia/Shanghai aware datetime。

    Returns None when the format is unrecognized — callers should fall back to
    HTTP Last-Modified header or crawl time.
    """

    s = (text or "").strip()
    if not s:
        return None

    now = reference_now or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)

    m = _REL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "分钟":
            return now - timedelta(minutes=n)
        if unit == "小时":
            return now - timedelta(hours=n)
        if unit == "天":
            return now - timedelta(days=n)

    m = _YESTERDAY_RE.match(s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        y = (now - timedelta(days=1)).date()
        return datetime(y.year, y.month, y.day, h, mi, 0, tzinfo=SHANGHAI)

    m = _YYYY_MM_DD_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h = int(m.group(4)) if m.group(4) else 0
        mi = int(m.group(5)) if m.group(5) else 0
        return datetime(y, mo, d, h, mi, 0, tzinfo=SHANGHAI)

    m = _MM_DD_RE.match(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        h = int(m.group(3)) if m.group(3) else 0
        mi = int(m.group(4)) if m.group(4) else 0
        year = now.year
        if (mo, d) > (now.month, now.day):
            year -= 1
        return datetime(year, mo, d, h, mi, 0, tzinfo=SHANGHAI)

    return None
