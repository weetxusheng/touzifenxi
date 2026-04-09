"""统一 LLM client，对外暴露结构化调用接口。"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import LLMProviderRuntimeConfig, load_c114_runtime_config
from .providers import (
    build_chat_payload,
    classify_http_error,
    extract_provider_content,
    extract_streaming_content,
    provider_endpoint,
    provider_headers,
)
from .runtime import compute_backoff_delay, create_provider_semaphores, normalize_provider_chain
from .structured_output import StructuredLLMError, parse_json_payload


class ProviderRequestError(RuntimeError):
    """表示某个 provider 的请求失败。"""

    def __init__(
        self,
        provider: LLMProviderRuntimeConfig,
        message: str,
        *,
        infrastructure_error: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.infrastructure_error = infrastructure_error
        self.retry_after_seconds = retry_after_seconds


class StructuredChatClient:
    """面向 skill 内部步骤的统一结构化模型客户端。"""

    def __init__(
        self,
        *,
        primary: LLMProviderRuntimeConfig | None = None,
        fallback: LLMProviderRuntimeConfig | None = None,
        fallbacks: list[LLMProviderRuntimeConfig] | tuple[LLMProviderRuntimeConfig, ...] | None = None,
        providers: list[LLMProviderRuntimeConfig] | tuple[LLMProviderRuntimeConfig, ...] | None = None,
        failover_enabled: bool,
        failover_consecutive_failures: int,
        retry_honor_retry_after: bool = True,
        retry_jitter_seconds: float = 0.5,
        stream_enabled: bool = True,
        stream_steps: tuple[str, ...] = ("step_5", "step_6", "step_7"),
    ) -> None:
        """保存当前 skill 运行所需的 provider 链和运行策略。"""

        self.providers = normalize_provider_chain(
            primary=primary,
            fallback=fallback,
            fallbacks=fallbacks,
            providers=providers,
        )
        self.primary = self.providers[0]
        self.fallback = self.providers[-1] if len(self.providers) > 1 else None
        self.failover_enabled = failover_enabled and len(self.providers) > 1
        self.failover_consecutive_failures = max(1, failover_consecutive_failures)
        self.retry_honor_retry_after = retry_honor_retry_after
        self.retry_jitter_seconds = max(0.0, retry_jitter_seconds)
        self.stream_enabled = stream_enabled
        self.stream_steps = tuple(stream_steps)
        self._lock = threading.Lock()
        self._current_provider_index = 0
        self._consecutive_failures = 0
        self._current_step = ""
        self._provider_semaphores = create_provider_semaphores(self.providers)

    @classmethod
    def from_runtime_config(cls, base_path: Path | None = None) -> "StructuredChatClient":
        """从 skill 本地配置中构造统一的大模型客户端。"""

        runtime_config = load_c114_runtime_config(base_path)
        if not runtime_config.llm_providers or not runtime_config.llm_providers[0].api_key:
            raise RuntimeError("未配置 llm 主模型 api_key，无法执行需要模型思考的 C114 步骤。")
        if runtime_config.llm_failover.enabled and len(runtime_config.llm_providers) < 2:
            raise RuntimeError("已启用 llm.failover，但缺少备用 provider 配置。")
        if runtime_config.llm_failover.enabled and any(
            not provider.api_key for provider in runtime_config.llm_providers[1:]
        ):
            raise RuntimeError("已启用 llm.failover，但备用 provider 缺少 api_key。")
        return cls(
            providers=runtime_config.llm_providers,
            failover_enabled=runtime_config.llm_failover.enabled,
            failover_consecutive_failures=runtime_config.llm_failover.consecutive_failures,
            retry_honor_retry_after=runtime_config.llm_retry.honor_retry_after,
            retry_jitter_seconds=runtime_config.llm_retry.jitter_seconds,
            stream_enabled=runtime_config.llm_streaming.enabled,
            stream_steps=runtime_config.llm_streaming.steps,
        )

    def begin_step(self, step_name: str) -> None:
        """在每个需要模型的步骤开始前重置主备状态。"""

        with self._lock:
            self._current_step = step_name
            self._current_provider_index = 0
            self._consecutive_failures = 0

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> Any:
        """调用模型并返回解析后的 JSON。"""

        last_error: Exception | None = None
        repair_hint = ""
        remaining_parse_attempts = max(provider.max_retries for provider in self.providers) + 1
        while True:
            try:
                provider, content = self._post_chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + repair_hint,
                    json_mode=True,
                )
                self._record_success(provider)
                return parse_json_payload(content)
            except ProviderRequestError as error:
                last_error = error
                if error.infrastructure_error:
                    switched = self._record_failure(error.provider)
                    if switched:
                        continue
                raise StructuredLLMError(str(error)) from error
            except Exception as error:  # noqa: BLE001
                last_error = error
                remaining_parse_attempts -= 1
                if remaining_parse_attempts <= 0:
                    break
                repair_hint = (
                    "\n\n上一次输出未能被解析为合法 JSON。"
                    "这一次请只返回单个 JSON 对象或 JSON 数组，不要返回 Markdown，不要返回解释。"
                )
        raise StructuredLLMError(f"模型输出无法解析为 JSON：{last_error}") from last_error

    def complete_text(self, *, system_prompt: str, user_prompt: str) -> str:
        """调用模型并返回文本内容。"""

        provider, content = self._post_chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=False,
        )
        self._record_success(provider)
        return content

    def _post_chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool,
    ) -> tuple[LLMProviderRuntimeConfig, str]:
        """向当前生效的 provider 发送一次对话请求。"""

        provider = self._active_provider()
        return provider, self._request_with_provider(
            provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=json_mode,
        )

    def _request_with_provider(
        self,
        provider: LLMProviderRuntimeConfig,
        *,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool,
    ) -> str:
        """向单个 provider 发送请求，并在 provider 内部完成基础重试。"""

        stream = self._should_stream()
        payload = build_chat_payload(
            provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_mode=json_mode,
            stream=stream,
        )
        request = Request(
            url=provider_endpoint(provider),
            data=json.dumps(payload).encode("utf-8"),
            headers=provider_headers(provider),
            method="POST",
        )
        semaphore = self._provider_semaphores[provider.provider]
        last_error: ProviderRequestError | None = None
        for attempt in range(provider.max_retries + 1):
            try:
                with semaphore:
                    with urlopen(request, timeout=provider.timeout_seconds) as response:
                        raw_body = response.read().decode("utf-8")
                        if stream:
                            return extract_streaming_content(provider, raw_body)
                        body = json.loads(raw_body)
                        return extract_provider_content(provider, body)
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="ignore")
                infrastructure_error, message, retry_after_seconds = classify_http_error(provider, error, detail)
                last_error = ProviderRequestError(
                    provider,
                    message,
                    infrastructure_error=infrastructure_error,
                    retry_after_seconds=retry_after_seconds,
                )
                if not infrastructure_error or attempt >= provider.max_retries:
                    raise last_error from error
            except (URLError, socket.timeout, TimeoutError, ConnectionResetError) as error:
                last_error = ProviderRequestError(
                    provider,
                    f"{provider.provider} 请求失败: {error}",
                    infrastructure_error=True,
                )
                if attempt >= provider.max_retries:
                    raise last_error from error
            delay_seconds = compute_backoff_delay(
                base_delay_seconds=provider.retry_backoff_seconds,
                attempt_index=attempt,
                retry_after_seconds=(
                    last_error.retry_after_seconds
                    if self.retry_honor_retry_after and last_error is not None
                    else None
                ),
                jitter_seconds=self.retry_jitter_seconds,
            )
            time.sleep(delay_seconds)
        raise last_error or ProviderRequestError(
            provider,
            f"{provider.provider} 请求失败。",
            infrastructure_error=True,
        )

    def _active_provider(self) -> LLMProviderRuntimeConfig:
        """返回当前应使用的 provider。"""

        with self._lock:
            return self.providers[self._current_provider_index]

    def _record_success(self, provider: LLMProviderRuntimeConfig) -> None:
        """记录一次成功请求，必要时清零失败计数。"""

        with self._lock:
            current = self.providers[self._current_provider_index]
            if provider.provider == current.provider:
                self._consecutive_failures = 0

    def _record_failure(self, provider: LLMProviderRuntimeConfig) -> bool:
        """记录一次基础设施失败，并在达到阈值时切到后续 provider。"""

        if not self.failover_enabled:
            return False
        with self._lock:
            current_provider = self.providers[self._current_provider_index]
            if provider.provider != current_provider.provider:
                return False
            self._consecutive_failures += 1
            if self._consecutive_failures < self.failover_consecutive_failures:
                return False
            if self._current_provider_index >= len(self.providers) - 1:
                return False
            self._current_provider_index += 1
            self._consecutive_failures = 0
            return True

    def _should_stream(self) -> bool:
        """判断当前步骤是否应启用流式接收。"""

        return self.stream_enabled and self._current_step in self.stream_steps
