"""编号顺延与条款搬迁用的文本规范化。

职责：剥除可见编号/空白差异，供 `normalize_payload`、`operation_repair`、`payload_coerce` 比较正文是否「仅编号变化」。
不负责：业务决策与行合并。
"""

from __future__ import annotations

import re

def normalize_line_for_shift_compare(line: str) -> str:
    """返回忽略常见编号前缀后的行文本，用于识别编号上移但正文未变。"""
    stripped = str(line).strip()
    stripped = re.sub(r"^（\d+）\s*", "", stripped)
    stripped = re.sub(r"^\(\d+\)\s*", "", stripped)
    stripped = re.sub(r"^\d+[、.．]\s*", "", stripped)
    stripped = re.sub(r"^[一二三四五六七八九十百]+、\s*", "", stripped)
    return stripped.strip()


def normalize_operation_text_for_numbering(text: str) -> str:
    """去掉常见编号前缀后返回正文，用于识别误判的 add/delete 顺延项。"""
    lines = [normalize_line_for_shift_compare(line) for line in str(text).splitlines()]
    return "\n".join(line for line in lines if line).strip()

