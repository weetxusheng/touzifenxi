"""对照行正文拼接（连续多行合并时的左右列组装）。

职责：把多条 `ComparisonRow` 按小标题规则拼成一段 `old_text` 或 `new_text`；
供 `row_merges` 中「连续删除」「纯删+纯增」合并使用，不决定合并范围。
"""

from __future__ import annotations

from typing import Literal

from ..chunking import text_starts_with_subchapter
from ..models import ComparisonRow

_RowSide = Literal["old", "new"]


def combine_comparison_run_text(run_rows: list[ComparisonRow], *, side: _RowSide) -> str:
    """合并连续对照行在指定侧的正文；小标题不同时各自保留在段首，同小标题续段只拼正文。"""
    parts: list[str] = []
    last_subchapter: str | None = None
    for row in run_rows:
        sc = str(row.subchapter or "").strip()
        body = str(row.old_text if side == "old" else row.new_text or "").strip()
        if body in {"", "新增", "删除"}:
            if body:
                parts.append(body)
            last_subchapter = None
            continue
        if sc:
            if text_starts_with_subchapter(body, sc):
                segment = body
            else:
                segment = f"{sc}\n{body}"
            if last_subchapter == sc and parts:
                parts.append(body)
            else:
                parts.append(segment)
            last_subchapter = sc
        else:
            parts.append(body)
            last_subchapter = None
    return "\n".join(p for p in parts if p).strip()
