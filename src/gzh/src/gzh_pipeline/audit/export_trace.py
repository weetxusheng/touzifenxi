"""大佳啦导出（抓取）留痕：与解析侧 ``aggregate`` 同结构的 per-account ``.trace.json``。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gzh_pipeline.audit.paths import build_audit_trace_path
from gzh_pipeline.audit.trace import TraceRecorder, write_trace_json


def default_export_audit_root() -> Path:
    import os

    raw = os.getenv("EXPORT_AUDIT_JSON_ROOT", "").strip() or os.getenv("PARSE_AUDIT_JSON_ROOT", "audit_traces").strip()
    return Path(raw or "audit_traces")


def write_export_account_trace(
    audit_root: Path,
    run_id: str,
    biz_date: str,
    account: str,
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
) -> Path:
    path = build_audit_trace_path(
        audit_root, biz_date, phase="export", run_id=run_id, account=account
    )
    doc_summary = {**summary, "kind": "export_fetch", "account": account, "biz_date": biz_date, "run_id": run_id}
    write_trace_json(path, doc_summary, steps)
    return path


def article_urls_preview(articles: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in articles[:limit]:
        url = str(item.get("url") or item.get("link") or item.get("content_url") or "")
        rows.append({"title": str(item.get("title") or item.get("digest") or ""), "url": url})
    return rows
