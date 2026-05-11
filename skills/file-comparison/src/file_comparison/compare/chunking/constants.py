"""切块与规则 diff 共享常量。

职责：标题正则、`OMITTED_EQUAL_MARKER`、compare block 邻域窗口等魔法值。
不负责：标题语义判断与块构建（见 `headings`、`compare_blocks`）。
"""

from __future__ import annotations

import re


INNER_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（(?:[一二三四五六七八九十]+|\d+)）)")
CHINESE_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
PAREN_CHINESE_HEADING_RE = re.compile(r"^（[一二三四五六七八九十]+）")
DISPLAY_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（[一二三四五六七八九十]+）)")
DECIMAL_HEADING_RE = re.compile(r"^\d+、")
CHINESE_CONTEXT_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")
MAX_PARENT_HEADING_CHARS = 40
COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW = 5
OMITTED_EQUAL_MARKER = "......"
