"""解析任务：默认业务日、枚举 exports 下公众号目录。"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from gzh_pipeline.dajiala.fetch_mode import fetch_mode_from_env, resolve_export_biz_date
from gzh_pipeline.util.text import biz_date_for_path


def resolve_parse_biz_date(
    _input_root: Path,
    explicit: str | None = None,
    *,
    fetch_mode: str | None = None,
    reference: date | None = None,
) -> str:
    """
    未指定 ``explicit`` 时，业务日与抓取落盘一致，由 ``GZH_DAJIALA_FETCH_MODE`` 决定：

    - ``today`` / ``latest`` / ``all`` → 当天 ``yyyyMMdd`` 目录
    - ``yesterday`` → 前一天 ``yyyyMMdd`` 目录

    显式 ``--biz-date`` 时忽略环境模式。
    """
    if explicit and explicit.strip():
        return biz_date_for_path(explicit.strip())
    mode = fetch_mode if fetch_mode is not None else fetch_mode_from_env()
    return resolve_export_biz_date(mode, reference=reference)


def list_export_accounts(input_root: Path, biz_date: str) -> list[str]:
    """``exports/{biz_date}/`` 下含顶层 ``*.html`` 的子目录名（不含 ``_compare`` 等无 html 的目录）。"""
    day_dir = input_root / biz_date_for_path(biz_date)
    if not day_dir.is_dir():
        return []
    accounts: list[str] = []
    for child in sorted(day_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if any(child.glob("*.html")):
            accounts.append(child.name)
    return accounts


def resolve_parse_accounts(input_root: Path, biz_date: str, explicit: str | None = None) -> list[str]:
    if explicit and explicit.strip():
        return [explicit.strip()]
    return list_export_accounts(input_root, biz_date)
