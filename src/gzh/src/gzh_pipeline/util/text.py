from __future__ import annotations

import re
from datetime import date, datetime


def parse_accounts(raw: str) -> list[str]:
    seen: set[str] = set()
    accounts: list[str] = []
    for item in re.split(r"[,，\n\r]+", raw):
        account = item.strip()
        if account and account not in seen:
            seen.add(account)
            accounts.append(account)
    return accounts


def safe_filename(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", value).strip().strip(".")
    return cleaned or fallback


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def parse_biz_date(value: str) -> date:
    """解析业务日字符串（紧凑或 ISO）为 ``date``。"""
    v = value.strip()
    if re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v, "%Y%m%d").date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return datetime.strptime(v, "%Y-%m-%d").date()
    raise ValueError(f"业务日须为 yyyyMMdd 或 YYYY-MM-DD，收到: {value!r}")


def biz_date_for_path(value: str | date | None = None) -> str:
    """目录与文件名中的业务日：**yyyyMMdd**（抓取、解析、`summary.biz_date` 一致）。"""
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if value is None or value == "":
        return date.today().strftime("%Y%m%d")
    return parse_biz_date(value).strftime("%Y%m%d")


def biz_date_iso_for_display(path_key_or_input: str) -> str:
    """页面标题等可读展示：YYYY-MM-DD。"""
    return parse_biz_date(path_key_or_input).isoformat()
