"""正文分析阶段的标题匹配能力。

本模块只负责把模型返回标题和原始标题归一到同一匹配口径。
它不调用 LLM、不读写文件，避免匹配规则和 step 编排互相耦合。
"""

from __future__ import annotations

import re
import unicodedata


def normalize_analysis_title_key(title: str) -> str:
    """生成 step 5 标题匹配 key，用于兼容模型轻微格式改写。"""

    normalized = unicodedata.normalize("NFKC", str(title)).casefold()
    return re.sub(r"\s+", "", normalized)
