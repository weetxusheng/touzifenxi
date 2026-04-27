"""封装 file-comparison skill 的多 provider 结构化模型客户端。"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..compare.models import ChapterBatch
from ..runtime.config import FileComparisonRuntimeConfig, LLMProviderConfig, resolve_provider_api_key
from .parallel import compute_backoff_delay, create_provider_semaphores
from .providers import (
    build_compare_request_payload,
    classify_http_error,
    normalize_provider_response,
    provider_endpoint,
    provider_headers,
)
from .retry import RetryClassifier, RetryClassifierConfig, classify_retry_class
from .schema import FILE_COMPARISON_SCHEMA


class ProviderRequestError(RuntimeError):
    """表示一次 provider 请求失败及其重试分类。"""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        infrastructure_error: bool,
        retry_class: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        """保存 provider 名称、错误级别和可能的 Retry-After 信息。"""
        super().__init__(message)
        self.provider = provider
        self.infrastructure_error = infrastructure_error
        self.retry_class = retry_class
        self.retry_after_seconds = retry_after_seconds


class OpenAIResponsesClient:
    """负责构造请求、执行 provider 内重试并返回归一化响应。"""

    def __init__(self, runtime_config: FileComparisonRuntimeConfig) -> None:
        """根据运行配置初始化 provider 链、限流器和退避分类器。"""
        self.runtime_config = runtime_config
        self.providers = runtime_config.llm.providers
        self.current_provider_name = self.providers[0].provider
        self._retry_classifier = RetryClassifier(
            RetryClassifierConfig(
                infra_max_attempts=runtime_config.llm.infra_max_attempts,
                parse_max_attempts=runtime_config.llm.parse_max_attempts,
                postprocess_max_attempts=runtime_config.llm.postprocess_max_attempts,
                fatal_max_attempts=runtime_config.llm.fatal_max_attempts,
            )
        )
        self._provider_semaphores = create_provider_semaphores(self.providers)
        self._rate_limit_lock = threading.Lock()
        self._last_request_started_at_by_provider = {provider.provider: 0.0 for provider in self.providers}

    def build_request_payload(
        self,
        *,
        pair_id: str,
        batch: ChapterBatch,
        provider_config: LLMProviderConfig | None = None,
    ) -> dict[str, Any]:
        """为某个章节批次构造结构化比较请求体。"""
        selected_provider = provider_config or self.providers[0]
        old_sections = [
            {"number": section.number, "title": section.title, "body": section.body}
            for section in batch.old_sections
        ]
        new_sections = [
            {"number": section.number, "title": section.title, "body": section.body}
            for section in batch.new_sections
        ]
        return build_compare_request_payload(
            selected_provider.provider,
            model=selected_provider.model,
            schema_name=FILE_COMPARISON_SCHEMA["name"],
            schema=FILE_COMPARISON_SCHEMA["schema"],
            strict=FILE_COMPARISON_SCHEMA["strict"],
            instructions=(
                "你是文件修订对照助手。请只返回 JSON。"
                "按章节比较旧版和新版，识别需要展示的变更条目。"
                "如果某条仅编号变化、正文完全一致，则 numbering_only=true。"
                "如果某个小行在左右完全一致，则把该行写入 fully_equal_lines。"
            ),
            input_payload={
                "pair_id": pair_id,
                "batch_id": batch.batch_id,
                "chapter_numbers": list(batch.chapter_numbers),
                "old_sections": old_sections,
                "new_sections": new_sections,
            },
        )

    def provider_chain_for_attempts(self) -> tuple[LLMProviderConfig, ...]:
        """返回当前客户端持有的 provider 链顺序。"""
        return self.providers

    def post(self, payload: dict[str, Any], *, provider_config: LLMProviderConfig | None = None) -> dict[str, Any]:
        """向指定 provider 发起请求，并在 provider 内部完成基础设施重试。"""
        selected_provider = provider_config or self.providers[0]
        api_key = resolve_provider_api_key(selected_provider)
        if not api_key:
            raise RuntimeError(
                f"missing API key in provider {selected_provider.provider} config.api_key "
                f"or environment variable {selected_provider.api_key_env}"
            )
        request = Request(
            url=provider_endpoint(selected_provider.provider, selected_provider.base_url),
            data=json.dumps(payload).encode("utf-8"),
            headers=provider_headers(selected_provider.provider, api_key),
            method="POST",
        )
        last_error: ProviderRequestError | None = None
        max_attempts = max(
            self._retry_classifier.max_attempts_for("infra", default=selected_provider.max_retries + 1),
            self._retry_classifier.max_attempts_for("fatal", default=1),
        )
        for attempt in range(max_attempts):
            self._respect_min_interval(selected_provider)
            try:
                with self._provider_semaphores[selected_provider.provider]:
                    with urlopen(request, timeout=selected_provider.timeout_seconds) as response:  # noqa: S310
                        raw_body = response.read().decode("utf-8")
                body = json.loads(raw_body)
                self.current_provider_name = selected_provider.provider
                return normalize_provider_response(selected_provider.provider, body)
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="ignore")
                infrastructure_error, message, retry_after_seconds = classify_http_error(selected_provider.provider, error, detail)
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    message,
                    infrastructure_error=infrastructure_error,
                    retry_class=classify_retry_class(infrastructure_error=infrastructure_error),
                    retry_after_seconds=retry_after_seconds,
                )
                if not self._retry_classifier.should_retry(
                    last_error.retry_class,
                    attempt_index=attempt,
                    default=(selected_provider.max_retries + 1 if infrastructure_error else 1),
                ):
                    raise last_error from error
            except json.JSONDecodeError as error:
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    f"{selected_provider.provider} 响应不是合法 JSON: {error}",
                    infrastructure_error=False,
                    retry_class="fatal",
                )
                raise last_error from error
            except (URLError, socket.timeout, TimeoutError, ConnectionResetError) as error:
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    f"{selected_provider.provider} 请求失败: {error}",
                    infrastructure_error=True,
                    retry_class="infra",
                )
                if not self._retry_classifier.should_retry(
                    "infra",
                    attempt_index=attempt,
                    default=selected_provider.max_retries + 1,
                ):
                    raise last_error from error
            delay_seconds = compute_backoff_delay(
                base_delay_seconds=selected_provider.retry_backoff_seconds,
                attempt_index=attempt,
                retry_after_seconds=(
                    last_error.retry_after_seconds
                    if selected_provider.honor_retry_after and last_error is not None
                    else None
                ),
                jitter_seconds=selected_provider.jitter_seconds,
            )
            time.sleep(delay_seconds)
        raise last_error or ProviderRequestError(
            selected_provider.provider,
            f"{selected_provider.provider} 请求失败。",
            infrastructure_error=True,
            retry_class="infra",
        )

    def write_request(self, path: Path, payload: dict[str, Any]) -> None:
        """把单次请求体以 JSON 形式落盘。"""
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_response(self, path: Path, payload: dict[str, Any]) -> None:
        """把单次响应体以 JSON 形式落盘。"""
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _respect_min_interval(self, provider_config: LLMProviderConfig) -> None:
        """按 provider 的最小发起间隔限制请求节奏。"""
        min_interval_seconds = provider_config.min_interval_seconds
        if min_interval_seconds <= 0:
            return
        with self._rate_limit_lock:
            now = time.time()
            last_started_at = self._last_request_started_at_by_provider.get(provider_config.provider, 0.0)
            wait_seconds = max(0.0, last_started_at + min_interval_seconds - now)
            scheduled_started_at = now + wait_seconds
            self._last_request_started_at_by_provider[provider_config.provider] = scheduled_started_at
        if wait_seconds > 0:
            time.sleep(wait_seconds)
