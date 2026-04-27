"""定义模型调用错误的重试分类与判定逻辑。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryClassifierConfig:
    """描述各类错误允许的总尝试次数。"""

    infra_max_attempts: int = 3
    parse_max_attempts: int = 3
    postprocess_max_attempts: int = 2
    fatal_max_attempts: int = 1


class RetryClassifier:
    """根据错误类别判断允许的重试上限。"""

    def __init__(self, config: RetryClassifierConfig) -> None:
        """保存各类错误的最大尝试次数配置。"""
        self._max_attempts_by_class = {
            "infra": max(1, int(config.infra_max_attempts)),
            "parse": max(1, int(config.parse_max_attempts)),
            "postprocess": max(1, int(config.postprocess_max_attempts)),
            "fatal": max(1, int(config.fatal_max_attempts)),
        }

    def max_attempts_for(self, retry_class: str, *, default: int = 1) -> int:
        """返回某类错误允许的总尝试次数。"""
        normalized = str(retry_class).strip().lower()
        if normalized not in self._max_attempts_by_class:
            return max(1, int(default))
        return self._max_attempts_by_class[normalized]

    def should_retry(self, retry_class: str, *, attempt_index: int, default: int = 1) -> bool:
        """判断在当前尝试序号下是否还允许继续重试。"""
        return int(attempt_index) + 1 < self.max_attempts_for(retry_class, default=default)


def classify_retry_class(*, infrastructure_error: bool) -> str:
    """按是否为基础设施错误返回对应的重试分类。"""
    return "infra" if infrastructure_error else "fatal"
