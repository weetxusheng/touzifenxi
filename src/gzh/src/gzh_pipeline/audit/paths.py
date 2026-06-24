"""审计留痕路径：``{根}/{业务日}/{export|parse}_{run_id}/{业务日}_{公众号}.trace.json``。"""

from __future__ import annotations

from pathlib import Path

from gzh_pipeline.util.text import biz_date_for_path, safe_filename


def build_audit_trace_path(
    audit_root: Path,
    biz_date: str,
    *,
    phase: str,
    run_id: str,
    account: str,
) -> Path:
    """
    phase 为 ``export``（抓取）或 ``parse``（解析），目录名为 ``{phase}_{run_id}``。
    """
    biz_key = biz_date_for_path(biz_date)
    account_safe = safe_filename(account)
    return audit_root / biz_key / f"{phase}_{run_id}" / f"{biz_key}_{account_safe}.trace.json"
