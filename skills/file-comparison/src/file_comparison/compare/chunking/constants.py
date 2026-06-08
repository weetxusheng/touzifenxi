"""切块与规则 diff 共享常量。

职责：标题正则、`OMITTED_EQUAL_MARKER`、compare block 邻域窗口等魔法值。
不负责：标题语义判断与块构建（见 `headings`、`compare_blocks`）。
"""

from __future__ import annotations

import re


INNER_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（(?:[一二三四五六七八九十]+|\d+)）)")
# 与 INNER_HEADING_RE 的差别：不匹配 `（数字）`。
# 供 `section_items_for_blocks` 在父项块内启用，使行首为 `（1）`、`（2）` 等的行不切分条目。
NESTED_ITEM_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（[一二三四五六七八九十]+）)")
CHINESE_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
PAREN_CHINESE_HEADING_RE = re.compile(r"^（[一二三四五六七八九十]+）")
DISPLAY_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|\d+、|（[一二三四五六七八九十]+）)")
DECIMAL_HEADING_RE = re.compile(r"^\d+、")
CHINESE_CONTEXT_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")
MAX_PARENT_HEADING_CHARS = 40
COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW = 5
OMITTED_EQUAL_MARKER = "......"
