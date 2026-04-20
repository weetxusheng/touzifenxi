"""统一管理 provider 链、并发限制和退避策略。"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from .providers import LLMProviderRuntimeConfig

DEFAULT_LLM_MAX_CONCURRENCY = 3
T = TypeVar("T")
R = TypeVar("R")


def normalize_provider_config(provider: LLMProviderRuntimeConfig) -> LLMProviderRuntimeConfig:
    """规整 provider 配置中的 URL 和重试参数。"""

    return LLMProviderRuntimeConfig(
        provider=provider.provider.strip(),
        model=provider.model.strip(),
        api_key=provider.api_key.strip(),
        base_url=provider.base_url.rstrip("/"),
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        retry_backoff_seconds=max(0.0, provider.retry_backoff_seconds),
        max_concurrency=max(1, provider.max_concurrency),
    )


def normalize_provider_chain(
    *,
    primary: LLMProviderRuntimeConfig | None,
    fallback: LLMProviderRuntimeConfig | None,
    fallbacks: list[LLMProviderRuntimeConfig] | tuple[LLMProviderRuntimeConfig, ...] | None,
    providers: list[LLMProviderRuntimeConfig] | tuple[LLMProviderRuntimeConfig, ...] | None,
) -> tuple[LLMProviderRuntimeConfig, ...]:
    """将主备或 provider 列表统一规整成有序链路。"""

    if providers is not None:
        raw_chain = list(providers)
    else:
        raw_chain = []
        if primary is not None:
            raw_chain.append(primary)
        if fallbacks:
            raw_chain.extend(list(fallbacks))
        elif fallback is not None:
            raw_chain.append(fallback)
    normalized = tuple(normalize_provider_config(provider) for provider in raw_chain if provider is not None)
    if not normalized:
        raise RuntimeError("至少需要一个可用的 LLM provider 配置。")
    return normalized


def compute_backoff_delay(
    *,
    base_delay_seconds: float,
    attempt_index: int,
    retry_after_seconds: float | None,
    jitter_seconds: float,
) -> float:
    """统一计算退避时长。"""

    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return retry_after_seconds + _random_jitter(jitter_seconds)
    multiplier = max(0, attempt_index)
    delay = max(0.0, base_delay_seconds) * (2**multiplier)
    return delay + _random_jitter(jitter_seconds)


def create_provider_semaphores(
    providers: tuple[LLMProviderRuntimeConfig, ...],
) -> dict[str, threading.Semaphore]:
    """为每个 provider 创建独立的并发信号量。"""

    return {
        provider.provider: threading.Semaphore(max(1, provider.max_concurrency))
        for provider in providers
    }


def run_parallel_ordered(
    items: list[T],
    worker: Callable[[T], R],
    *,
    max_workers: int = DEFAULT_LLM_MAX_CONCURRENCY,
) -> list[R]:
    """在受控并发下执行独立任务，并保持输出顺序不变。"""

    if not items:
        return []
    if len(items) == 1:
        return [worker(items[0])]
    bounded_workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=bounded_workers) as executor:
        futures = [executor.submit(worker, item) for item in items]
        return [future.result() for future in futures]


def _random_jitter(jitter_seconds: float) -> float:
    """返回退避抖动。"""

    if jitter_seconds <= 0:
        return 0.0
    return random.uniform(0.0, jitter_seconds)
