"""定义 batch 调用的重试、降级和 failover 策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import FileComparisonRuntimeConfig, LLMProviderConfig

MIN_FAILED_ATTEMPTS_BEFORE_FALLBACK = 6


@dataclass(slots=True)
class FailureCounters:
    """记录单个 batch 在不同失败分类上的累计次数。"""

    parse: int = 0
    infra: int = 0
    postprocess: int = 0
    fatal: int = 0

    def increment(self, retry_class: str) -> None:
        """按 retry class 增加对应计数。"""
        normalized = retry_class.strip().lower()
        if normalized == "parse":
            self.parse += 1
        elif normalized == "postprocess":
            self.postprocess += 1
        elif normalized == "fatal":
            self.fatal += 1
        else:
            self.infra += 1


@dataclass(slots=True)
class CallPolicy:
    """封装五层治理里的尝试次数、provider 路由和 prompt 降级规则。"""

    runtime_config: FileComparisonRuntimeConfig
    provider_chain: tuple[LLMProviderConfig, ...]
    parse_limit: int
    infra_limit: int
    postprocess_limit: int
    fatal_limit: int
    allow_repair: bool = True
    allow_fallback: bool = True
    degraded_after_parse_failure: bool = True
    counters: FailureCounters = field(default_factory=FailureCounters)

    @classmethod
    def from_runtime(
        cls,
        runtime_config: FileComparisonRuntimeConfig,
        provider_chain: tuple[LLMProviderConfig, ...],
    ) -> CallPolicy:
        """根据运行配置生成单个 batch 的调用策略。"""
        return cls(
            runtime_config=runtime_config,
            provider_chain=provider_chain,
            parse_limit=runtime_config.llm.parse_max_attempts,
            infra_limit=runtime_config.llm.infra_max_attempts * max(1, len(provider_chain)),
            postprocess_limit=runtime_config.llm.postprocess_max_attempts,
            fatal_limit=runtime_config.llm.fatal_max_attempts * max(1, len(provider_chain)),
        )

    def provider_for_attempt(self, attempt_index: int) -> LLMProviderConfig:
        """返回指定尝试序号应使用的 provider。"""
        return self.provider_chain[(attempt_index - 1) % len(self.provider_chain)]

    def prompt_mode_for_attempt(self, attempt_index: int) -> str:
        """返回当前尝试应使用的 prompt 模式。"""
        if self.degraded_after_parse_failure and self.counters.parse > 0:
            return "minimal"
        return "standard"

    def record_failure(self, retry_class: str) -> None:
        """记录一次失败，用于后续判断是否继续尝试。"""
        self.counters.increment(retry_class)

    def should_fallback(self, attempt_index: int) -> bool:
        """判断当前失败累计是否应进入 fallback。"""
        if attempt_index < MIN_FAILED_ATTEMPTS_BEFORE_FALLBACK:
            return False
        if self.counters.parse >= self.parse_limit:
            return True
        if self.counters.infra >= self.infra_limit:
            return True
        if self.counters.postprocess >= self.postprocess_limit:
            return True
        if self.counters.fatal >= self.fatal_limit:
            return True
        return attempt_index >= self.max_total_attempts()

    def max_total_attempts(self) -> int:
        """返回 batch 最大总尝试次数。"""
        return max(
            MIN_FAILED_ATTEMPTS_BEFORE_FALLBACK,
            self.parse_limit,
            self.infra_limit,
            self.postprocess_limit,
            self.fatal_limit,
        )
