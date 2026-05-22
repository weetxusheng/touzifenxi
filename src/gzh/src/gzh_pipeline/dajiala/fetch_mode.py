"""抓取模式与落盘/解析业务日（与 ``GZH_DAJIALA_FETCH_MODE`` 一致）。"""

from __future__ import annotations

import os
from datetime import date, timedelta

from gzh_pipeline.util.text import biz_date_for_path, parse_biz_date


def canonical_fetch_mode(value: str) -> str | None:
    """
    today | yesterday | latest | all。

    别名：last/allday 等；``prev_day`` / ``previous_day`` → yesterday。
    ``lastday`` → latest（非日历前一天）。
    """
    m = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "lastday": "latest",
        "last": "latest",
        "prev_day": "yesterday",
        "previous_day": "yesterday",
        "prev": "yesterday",
        "allday": "all",
        "all_day": "all",
        "history": "all",
    }
    m = aliases.get(m, m)
    if m in ("today", "yesterday", "latest", "all"):
        return m
    return None


def fetch_mode_from_env() -> str:
    """读 ``GZH_DAJIALA_FETCH_MODE``；空或未识别则 ``today``。"""
    raw = os.getenv("GZH_DAJIALA_FETCH_MODE", "").strip()
    if raw:
        m = canonical_fetch_mode(raw)
        if m:
            return m
    return "today"


def resolve_export_biz_date(
    mode: str,
    *,
    reference: date | None = None,
    reference_str: str | None = None,
) -> str:
    """
    与抓取落盘 ``output_dir/{yyyyMMdd}/`` 一致的业务日（未显式 ``--biz-date`` 时）。

    ``yesterday`` 为基准日减 1 天；``today`` / ``latest`` / ``all`` 为基准日本身。
    """
    base = reference or (parse_biz_date(reference_str) if reference_str else date.today())
    if mode == "yesterday":
        base = base - timedelta(days=1)
    return biz_date_for_path(base)


def resolve_fetch_target_date(mode: str, date_cli: str) -> tuple[str, str]:
    """
    列表 API 过滤用 ISO 日期；与 ``resolve_export_biz_date`` 在默认基准下对齐。
    """
    base = parse_biz_date(date_cli.strip())
    if mode == "yesterday":
        base = base - timedelta(days=1)
        return "yesterday", base.isoformat()
    return mode, base.isoformat()
