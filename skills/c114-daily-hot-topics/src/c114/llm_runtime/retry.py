"""统一管理 LLM 请求、解析与后处理的重试分类。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryClassifierConfig:
    """按错误类型控制总尝试次数。"""

    infra_max_attempts: int = 3
    parse_max_attempts: int = 3
    postprocess_max_attempts: int = 3
    fatal_max_attempts: int = 2


class RetryClassifier:
    """把不同错误映射成统一重试口径。"""

    def __init__(self, config: RetryClassifierConfig) -> None:
        self._max_attempts_by_class = {
            "infra": max(1, int(config.infra_max_attempts)),
            "parse": max(1, int(config.parse_max_attempts)),
            "postprocess": max(1, int(config.postprocess_max_attempts)),
            "fatal": max(1, int(config.fatal_max_attempts)),
        }

    def max_attempts_for(self, retry_class: str, *, default: int = 1) -> int:
        """返回某类错误的总尝试次数。"""

        normalized = str(retry_class).strip().lower()
        if normalized not in self._max_attempts_by_class:
            return max(1, int(default))
        return self._max_attempts_by_class[normalized]

    def should_retry(self, retry_class: str, *, attempt_index: int, default: int = 1) -> bool:
        """判断当前是否还应继续尝试。"""

        total_attempts = self.max_attempts_for(retry_class, default=default)
        return int(attempt_index) + 1 < total_attempts


def classify_retry_class(*, infrastructure_error: bool) -> str:
    """把 provider 级异常归类到统一重试类别。"""

    return "infra" if infrastructure_error else "fatal"
