"""聚合解析阶段错误类型。"""

from __future__ import annotations


class StrictLlmFailure(Exception):
    """``GZH_PARSE_STRICT_LLM`` 开启时，预筛 / 四维度 / 审稿任一步调用或解析失败。"""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        self.message = message
        super().__init__(f"{step}: {message}")
