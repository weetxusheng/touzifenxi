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
from .instructions import load_compare_instructions
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

    def __init__(
        self,
        runtime_config: FileComparisonRuntimeConfig,
        *,
        compare_instructions: str | None = None,
        compare_instructions_path: Path | str | None = None,
    ) -> None:
        """根据运行配置初始化 provider 链、限流器和退避分类器。

        compare_instructions 非空时直接使用；否则若 compare_instructions_path 非空则从该路径读取；
        二者皆空时读取 skill 默认文件 prompts/compare-structured-instructions.md。
        """
        self.runtime_config = runtime_config
        self.providers = runtime_config.llm.providers
        self._compare_instructions_text = self._resolve_compare_instructions(
            compare_instructions=compare_instructions,
            compare_instructions_path=compare_instructions_path,
        )
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
        self._next_allowed_at_by_provider = {provider.provider: 0.0 for provider in self.providers}

    @staticmethod
    def _resolve_compare_instructions(
        *,
        compare_instructions: str | None,
        compare_instructions_path: Path | str | None,
    ) -> str:
        if compare_instructions is not None:
            return compare_instructions
        if compare_instructions_path is not None:
            return load_compare_instructions(path=Path(compare_instructions_path))
        return load_compare_instructions()

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
        compare_blocks = [
            {
                "block_id": block.block_id,
                "chapter_number": block.chapter_number,
                "chapter_title": block.chapter_title,
                "parent_path": block.parent_path,
                "old_items": [{"item_id": item.item_id, "text": item.text} for item in block.old_items],
                "new_items": [{"item_id": item.item_id, "text": item.text} for item in block.new_items],
            }
            for block in getattr(batch, "compare_blocks", ())
        ]
        return build_compare_request_payload(
            selected_provider.provider,
            model=selected_provider.model,
            schema_name=FILE_COMPARISON_SCHEMA["name"],
            schema=FILE_COMPARISON_SCHEMA["schema"],
            strict=FILE_COMPARISON_SCHEMA["strict"],
            instructions=self._compare_instructions_text,
            input_payload={
                "pair_id": pair_id,
                "batch_id": batch.batch_id,
                "chapter_numbers": list(batch.chapter_numbers),
                "old_sections": old_sections,
                "new_sections": new_sections,
                "compare_blocks": compare_blocks,
            },
        )

    def provider_chain_for_attempts(self) -> tuple[LLMProviderConfig, ...]:
        """返回当前客户端持有的 provider 链顺序。"""
        return self.providers

    def post(self, payload: dict[str, Any], *, provider_config: LLMProviderConfig | None = None) -> dict[str, Any]:
        """向指定 provider 发起请求，并返回归一化响应。"""
        _raw_body, normalized_body = self.post_with_raw(payload, provider_config=provider_config)
        return normalized_body

    def post_with_raw(self, payload: dict[str, Any], *, provider_config: LLMProviderConfig | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """向指定 provider 发起请求，并同时返回原始响应和归一化响应。"""
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
                return body, normalize_provider_response(selected_provider.provider, body)
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
                self.record_provider_unavailable(selected_provider, reason=last_error.retry_class)
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
                self.record_provider_unavailable(selected_provider, reason="fatal")
                raise last_error from error
            except (URLError, socket.timeout, TimeoutError, ConnectionResetError) as error:
                last_error = ProviderRequestError(
                    selected_provider.provider,
                    f"{selected_provider.provider} 请求失败: {error}",
                    infrastructure_error=True,
                    retry_class="infra",
                )
                self.record_provider_unavailable(selected_provider, reason="infra")
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

    def record_provider_unavailable(self, provider_config: LLMProviderConfig, *, reason: str = "") -> None:
        """记录 provider 本次不可用，并设置下一次同 provider 请求的冷却时间。"""
        cooldown_seconds = provider_config.failure_cooldown_seconds
        if cooldown_seconds <= 0:
            return
        with self._rate_limit_lock:
            next_allowed_at = time.time() + cooldown_seconds
            current_next_allowed_at = self._next_allowed_at_by_provider.get(provider_config.provider, 0.0)
            self._next_allowed_at_by_provider[provider_config.provider] = max(current_next_allowed_at, next_allowed_at)

    def _respect_min_interval(self, provider_config: LLMProviderConfig) -> None:
        """按 provider 的最小发起间隔和失败冷却限制请求节奏。"""
        min_interval_seconds = provider_config.min_interval_seconds
        with self._rate_limit_lock:
            now = time.time()
            last_started_at = self._last_request_started_at_by_provider.get(provider_config.provider, 0.0)
            next_allowed_at = self._next_allowed_at_by_provider.get(provider_config.provider, 0.0)
            wait_until = max(last_started_at + min_interval_seconds, next_allowed_at)
            wait_seconds = max(0.0, wait_until - now)
            scheduled_started_at = now + wait_seconds
            self._last_request_started_at_by_provider[provider_config.provider] = scheduled_started_at
        if wait_seconds > 0:
            time.sleep(wait_seconds)
