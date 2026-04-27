"""提供 provider 并发控制与统一退避计算。"""

from __future__ import annotations

import random
import threading

from ..runtime.config import LLMProviderConfig


def compute_backoff_delay(
    *,
    base_delay_seconds: float,
    attempt_index: int,
    retry_after_seconds: float | None,
    jitter_seconds: float,
) -> float:
    """统一计算带抖动和 Retry-After 的退避时长。"""
    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return retry_after_seconds + _random_jitter(jitter_seconds)
    multiplier = max(0, int(attempt_index))
    delay = max(0.0, float(base_delay_seconds)) * (2**multiplier)
    return delay + _random_jitter(jitter_seconds)


def create_provider_semaphores(providers: tuple[LLMProviderConfig, ...]) -> dict[str, threading.Semaphore]:
    """为每个 provider 创建独立的并发信号量。"""
    return {
        provider.provider: threading.Semaphore(max(1, int(provider.max_concurrency)))
        for provider in providers
    }


def _random_jitter(jitter_seconds: float) -> float:
    """返回一个不超过给定上限的随机抖动。"""
    if jitter_seconds <= 0:
        return 0.0
    return random.uniform(0.0, jitter_seconds)
