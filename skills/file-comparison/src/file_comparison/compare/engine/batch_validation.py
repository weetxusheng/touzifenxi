
"""batch 结果完整性校验。

职责：`validate_complete_batch_results`、可复用 batch 状态常量。
不负责：发起 batch 重试或写 Word（见 `pair_compare`、`batch_processor`）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import ChapterBatch, ComparisonRow

USABLE_BATCH_FINAL_STATUSES = {"succeeded"}

def validate_complete_batch_results(
    *,
    batches: list[ChapterBatch],
    batch_results: dict[str, list[ComparisonRow]],
    llm_dir: Path,
) -> None:
    """确认每个 batch 都有可复用结果；否则禁止生成不完整对照文档。"""
    invalid_batches: list[str] = []
    for batch in batches:
        batch_dir = llm_dir / batch.batch_id
        rows = batch_results.get(batch.batch_id)
        final_status = _read_json_object(batch_dir / "final_status.json")
        status = str(final_status.get("status", "")).strip()
        legacy_parsed_exists = not final_status and (batch_dir / "parsed.json").exists()
        has_usable_status = status in USABLE_BATCH_FINAL_STATUSES or legacy_parsed_exists
        if rows is None:
            invalid_batches.append(f"{batch.batch_id}(缺少结果)")
            continue
        if batch.compare_blocks and not rows:
            invalid_batches.append(f"{batch.batch_id}(结果为空)")
            continue
        if not has_usable_status:
            invalid_batches.append(f"{batch.batch_id}(状态不可用:{status or 'missing'})")
    if invalid_batches:
        raise RuntimeError("存在未完成或无可用结果的 batch，已停止生成文档: " + "、".join(invalid_batches))

def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件缺失、损坏或不是对象时返回空字典。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}
