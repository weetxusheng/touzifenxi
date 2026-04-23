"""统一 LLM client，对外暴露结构化调用接口。"""

from __future__ import annotations

import atexit
import json
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .providers import LLMProviderRuntimeConfig
from .parallel import compute_backoff_delay, create_provider_semaphores, normalize_provider_chain
from .providers import (
    build_chat_payload,
    classify_http_error,
    extract_provider_content,
    extract_streaming_content,
    provider_endpoint,
    provider_headers,
)
from .retry import RetryClassifier, RetryClassifierConfig, classify_retry_class
from .structured_output import StructuredLLMError, parse_json_payload


def _normalize_trace_source_prefix(source_prefix: str | None) -> str:
    """规范化站点日志前缀，避免新站点继续写死 c114。"""

    normalized = str(source_prefix or "c114").strip().lower().replace("-", "_")
    return normalized or "c114"


def _llm_trace_log_name_for_step(step_name: str, report_date: str, *, source_prefix: str | None = None) -> str:
    """生成按步骤拆分的 LLM 日志文件名。"""

    normalized_step = str(step_name).strip() or "unknown"
    normalized_date = report_date.replace("-", "")
    normalized_prefix = _normalize_trace_source_prefix(source_prefix)
    return f"{normalized_prefix}_llm_trace_{normalized_step}_{normalized_date}.jsonl"


def _trace_phase_for_status(status: str) -> str:
    """把旧状态映射成更明确的请求阶段。"""

    return {
        "started": "request_started",
        "success": "response_received",
        "error": "request_failed",
        "parse_error": "parse_failed",
        "postprocess_error": "postprocess_failed",
        "aborted": "request_aborted",
    }.get(status, "unknown")


def _trace_response_kind_for_status(
    status: str,
    *,
    response_text_present: bool,
    infrastructure_error: bool | None,
) -> str:
    """把响应内容形态转成更易读的分类。"""

    if status == "started":
        return "not_received_yet"
    if status == "success":
        return "provider_text"
    if status == "parse_error":
        return "provider_text_unparseable_json"
    if status == "postprocess_error":
        return "postprocessed_payload"
    if status == "aborted":
        return "not_received_before_abort"
    if status == "error":
        if response_text_present:
            return "provider_error_body"
        if infrastructure_error:
            return "no_response_body"
        return "provider_error_without_body"
    return "unknown"


class ProviderRequestError(RuntimeError):
    """表示某个 provider 的请求失败。"""

    def __init__(
        self,
        provider: LLMProviderRuntimeConfig,
        message: str,
        *,
        infrastructure_error: bool,
        retry_class: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.infrastructure_error = infrastructure_error
        self.retry_class = retry_class
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
        retry_classifier_config: RetryClassifierConfig | None = None,
        stream_enabled: bool = True,
        stream_steps: tuple[str, ...] = ("step_5", "step_6", "step_7"),
        step_min_interval_seconds: dict[str, float] | None = None,
        step_task_routing: dict[str, tuple[str, ...]] | None = None,
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
        self._retry_classifier = RetryClassifier(retry_classifier_config) if retry_classifier_config else None
        self.stream_enabled = stream_enabled
        self.stream_steps = tuple(stream_steps)
        self.step_min_interval_seconds = {
            str(step).strip(): max(0.0, float(value))
            for step, value in (step_min_interval_seconds or {}).items()
            if str(step).strip()
        }
        self._providers_by_name = {provider.provider: provider for provider in self.providers}
        self.step_task_routing = {
            str(step).strip(): tuple(
                provider_name
                for provider_name in provider_names
                if provider_name in self._providers_by_name
            )
            for step, provider_names in (step_task_routing or {}).items()
            if str(step).strip()
        }
        self._lock = threading.Lock()
        self._current_provider_index = 0
        self._consecutive_failures = 0
        self._current_step = ""
        self._step_task_route_index_by_step: dict[str, int] = {}
        self._provider_semaphores = create_provider_semaphores(self.providers)
        self._trace_log_path: Path | None = None
        self._trace_log_dir: Path | None = None
        self._trace_log_date: str | None = None
        self._trace_log_source_prefix = "c114"
        self._trace_log_lock = threading.Lock()
        self._step_rate_limit_lock = threading.Lock()
        self._last_request_started_at_by_step: dict[str, float] = {}
        self._thread_local = threading.local()
        self._pending_trace_records: dict[str, dict[str, Any]] = {}
        self._http_round_counter = 0
        atexit.register(self._flush_pending_trace_records_on_exit)

    @classmethod
    def from_runtime_config(cls, base_path: Path | None = None) -> "StructuredChatClient":
        """从 skill 本地配置中构造统一的大模型客户端。"""

        from c114.runtime.config import load_c114_runtime_config

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
            retry_classifier_config=RetryClassifierConfig(
                infra_max_attempts=runtime_config.llm_retry_classifier.infra_max_attempts,
                parse_max_attempts=runtime_config.llm_retry_classifier.parse_max_attempts,
                postprocess_max_attempts=runtime_config.llm_retry_classifier.postprocess_max_attempts,
                fatal_max_attempts=runtime_config.llm_retry_classifier.fatal_max_attempts,
            ),
            stream_enabled=runtime_config.llm_streaming.enabled,
            stream_steps=runtime_config.llm_streaming.steps,
            step_min_interval_seconds=runtime_config.llm_step_rate_limits.min_interval_seconds_by_step,
            step_task_routing=runtime_config.llm_step_task_routing.providers_by_step,
        )

    def begin_step(self, step_name: str) -> None:
        """在每个需要模型的步骤开始前重置主备状态。"""

        with self._lock:
            self._current_step = step_name
            self._current_provider_index = 0
            self._consecutive_failures = 0
            self._step_task_route_index_by_step[step_name] = 0
            self._http_round_counter = 0

    def take_http_round_count(self) -> int:
        """返回本步内 provider HTTP 尝试次数（含失败后重试的轮次），读完后清零。"""

        with self._lock:
            total = self._http_round_counter
            self._http_round_counter = 0
            return total

    def set_trace_log_path(self, trace_log_path: Path | None, *, reset_file: bool = False) -> None:
        """配置当前运行的 LLM 调用日志文件。"""

        with self._trace_log_lock:
            self._trace_log_path = trace_log_path.resolve() if trace_log_path else None
            self._trace_log_dir = None
            self._trace_log_date = None
            self._trace_log_source_prefix = "c114"
            if self._trace_log_path is None:
                return
            self._trace_log_path.parent.mkdir(parents=True, exist_ok=True)
            if reset_file:
                self._trace_log_path.write_text("", encoding="utf-8")

    def set_trace_log_directory(
        self,
        trace_log_dir: Path | None,
        *,
        report_date: str,
        source_prefix: str | None = None,
        reset_files: bool = False,
    ) -> None:
        """配置按步骤拆分的 LLM 日志目录。"""

        with self._trace_log_lock:
            self._trace_log_dir = trace_log_dir.resolve() if trace_log_dir else None
            self._trace_log_date = report_date
            self._trace_log_source_prefix = _normalize_trace_source_prefix(source_prefix)
            self._trace_log_path = None
            if self._trace_log_dir is None:
                return
            self._trace_log_dir.mkdir(parents=True, exist_ok=True)
            if reset_files:
                pattern = _llm_trace_log_name_for_step("*", report_date, source_prefix=self._trace_log_source_prefix)
                for existing in self._trace_log_dir.glob(pattern):
                    existing.unlink(missing_ok=True)

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> Any:
        """调用模型并返回解析后的 JSON。"""

        last_error: Exception | None = None
        repair_hint = ""
        remaining_parse_attempts = self.max_attempts_for_retry_class(
            "parse",
            default=max(provider.max_retries for provider in self.providers) + 1,
        )
        while True:
            try:
                provider, content = self._post_chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + repair_hint,
                    json_mode=True,
                )
                self._record_success(provider)
                try:
                    parsed_payload = parse_json_payload(content)
                except Exception as error:  # noqa: BLE001
                    request_context = getattr(self._thread_local, "last_request_context", {})
                    self._write_trace_record(
                        request_id=request_context.get("request_id"),
                        provider=provider,
                        attempt=int(request_context.get("attempt", 0)),
                        json_mode=True,
                        stream=self._should_stream(provider),
                        system_prompt=system_prompt,
                        user_prompt=user_prompt + repair_hint,
                        duration_ms=0.0,
                        status="parse_error",
                        response_text=content,
                        error_message=str(error),
                        infrastructure_error=False,
                    )
                    raise
                self._remember_last_completion(
                    provider=provider,
                    json_mode=True,
                    stream=self._should_stream(provider),
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + repair_hint,
                    response_text=content,
                    parsed_payload=parsed_payload,
                )
                return parsed_payload
            except ProviderRequestError as error:
                last_error = error
                if error.retry_class == "infra":
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
        self._remember_last_completion(
            provider=provider,
            json_mode=False,
            stream=self._should_stream(provider),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_text=content,
            parsed_payload=content,
        )
        return content

    def record_postprocess_error(self, *, error: Exception, response_payload: Any | None = None) -> None:
        """记录模型返回已成功收到，但在后置校验阶段失败的情况。"""

        context = getattr(self._thread_local, "last_completion_context", None)
        if not isinstance(context, dict):
            return
        payload_to_log = response_payload if response_payload is not None else context.get("parsed_payload")
        response_text = self._serialize_trace_payload(payload_to_log)
        self._write_trace_record(
            request_id=context.get("request_id"),
            provider=context["provider"],
            attempt=int(context["attempt"]),
            json_mode=bool(context["json_mode"]),
            stream=bool(context["stream"]),
            system_prompt=str(context["system_prompt"]),
            user_prompt=str(context["user_prompt"]),
            duration_ms=0.0,
            status="postprocess_error",
            response_text=response_text,
            error_message=str(error),
            infrastructure_error=False,
        )

    def max_attempts_for_retry_class(self, retry_class: str, *, default: int = 1) -> int:
        """返回某类错误的总尝试次数。"""

        if self._retry_classifier is None:
            return max(1, int(default))
        return self._retry_classifier.max_attempts_for(retry_class, default=default)

    def _post_chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool,
    ) -> tuple[LLMProviderRuntimeConfig, str]:
        """向当前生效的 provider 发送一次对话请求。"""

        provider = self._active_provider()
        if not self._uses_step_task_routing():
            return provider, self._request_with_provider(
                provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_mode=json_mode,
            )

        last_error: ProviderRequestError | None = None
        for routed_provider in self._routed_provider_chain():
            try:
                return routed_provider, self._request_with_provider(
                    routed_provider,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    json_mode=json_mode,
                )
            except ProviderRequestError as error:
                last_error = error
                if error.retry_class != "infra":
                    raise
                continue
        raise last_error or ProviderRequestError(
            provider,
            f"{provider.provider} 请求失败。",
            infrastructure_error=True,
            retry_class="infra",
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

        stream = self._should_stream(provider)
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
        max_provider_attempts = max(
            self.max_attempts_for_retry_class("infra", default=provider.max_retries + 1),
            self.max_attempts_for_retry_class("fatal", default=1),
        )
        for attempt in range(max_provider_attempts):
            with self._lock:
                self._http_round_counter += 1
            self._respect_step_rate_limit()
            started_at = time.time()
            request_id = self._next_request_id()
            self._write_trace_record(
                request_id=request_id,
                provider=provider,
                attempt=attempt,
                json_mode=json_mode,
                stream=stream,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                duration_ms=0.0,
                status="started",
                response_text=None,
            )
            try:
                with semaphore:
                    with urlopen(request, timeout=provider.timeout_seconds) as response:
                        raw_body = response.read().decode("utf-8")
                        if stream:
                            content = extract_streaming_content(provider, raw_body)
                            self._write_trace_record(
                                request_id=request_id,
                                provider=provider,
                                attempt=attempt,
                                json_mode=json_mode,
                                stream=stream,
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                duration_ms=(time.time() - started_at) * 1000.0,
                                status="success",
                                response_text=content,
                            )
                            self._thread_local.last_request_context = {
                                "request_id": request_id,
                                "attempt": attempt,
                            }
                            return content
                        body = json.loads(raw_body)
                        content = extract_provider_content(provider, body)
                        self._write_trace_record(
                            request_id=request_id,
                            provider=provider,
                            attempt=attempt,
                            json_mode=json_mode,
                            stream=stream,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            duration_ms=(time.time() - started_at) * 1000.0,
                            status="success",
                            response_text=content,
                        )
                        self._thread_local.last_request_context = {
                            "request_id": request_id,
                            "attempt": attempt,
                        }
                        return content
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="ignore")
                infrastructure_error, message, retry_after_seconds = classify_http_error(provider, error, detail)
                last_error = ProviderRequestError(
                    provider,
                    message,
                    infrastructure_error=infrastructure_error,
                    retry_class=classify_retry_class(infrastructure_error=infrastructure_error),
                    retry_after_seconds=retry_after_seconds,
                )
                self._write_trace_record(
                    request_id=request_id,
                    provider=provider,
                    attempt=attempt,
                    json_mode=json_mode,
                    stream=stream,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    duration_ms=(time.time() - started_at) * 1000.0,
                    status="error",
                    response_text=detail or None,
                    error_message=message,
                    infrastructure_error=infrastructure_error,
                )
                if not self.max_attempts_for_retry_class(
                    last_error.retry_class,
                    default=(provider.max_retries + 1 if infrastructure_error else 1),
                ) > attempt + 1:
                    raise last_error from error
            except (URLError, socket.timeout, TimeoutError, ConnectionResetError) as error:
                last_error = ProviderRequestError(
                    provider,
                    f"{provider.provider} 请求失败: {error}",
                    infrastructure_error=True,
                    retry_class="infra",
                )
                self._write_trace_record(
                    request_id=request_id,
                    provider=provider,
                    attempt=attempt,
                    json_mode=json_mode,
                    stream=stream,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    duration_ms=(time.time() - started_at) * 1000.0,
                    status="error",
                    response_text=None,
                    error_message=str(last_error),
                    infrastructure_error=True,
                )
                if not self.max_attempts_for_retry_class("infra", default=provider.max_retries + 1) > attempt + 1:
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
            retry_class="infra",
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

    def _should_stream(self, provider: LLMProviderRuntimeConfig | None = None) -> bool:
        """判断当前步骤是否应启用流式接收。"""

        if not self.stream_enabled:
            return False
        if provider is not None and provider.provider.lower() == "volc-ark":
            return False
        return self._current_step in self.stream_steps

    def _remember_last_completion(
        self,
        *,
        provider: LLMProviderRuntimeConfig,
        json_mode: bool,
        stream: bool,
        system_prompt: str,
        user_prompt: str,
        response_text: str,
        parsed_payload: Any,
    ) -> None:
        """保存当前线程最近一次成功收到的模型返回，供后置校验失败时落日志。"""

        self._thread_local.last_completion_context = {
            "provider": provider,
            "attempt": getattr(self._thread_local, "last_request_context", {}).get("attempt", 0),
            "request_id": getattr(self._thread_local, "last_request_context", {}).get("request_id"),
            "json_mode": json_mode,
            "stream": stream,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_text": response_text,
            "parsed_payload": parsed_payload,
        }

    def _serialize_trace_payload(self, payload: Any) -> str | None:
        """把任意返回载荷转成适合写入 jsonl 的字符串。"""

        if payload is None:
            return None
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload, ensure_ascii=False)
        except TypeError:
            return str(payload)

    def _resolved_trace_log_path(self, step_name: str) -> Path | None:
        """根据当前步骤解析最终的日志文件路径。"""

        if self._trace_log_path is not None:
            return self._trace_log_path
        if self._trace_log_dir is None or not self._trace_log_date:
            return None
        return self._trace_log_dir / _llm_trace_log_name_for_step(
            step_name,
            self._trace_log_date,
            source_prefix=self._trace_log_source_prefix,
        )

    def _uses_step_task_routing(self) -> bool:
        """判断当前步骤是否启用了任务分片路由。"""

        routed_providers = self.step_task_routing.get(self._current_step, ())
        return len(routed_providers) > 1

    def _routed_provider_chain(self) -> tuple[LLMProviderRuntimeConfig, ...]:
        """按当前步骤的轮转顺序返回本次请求的 provider 链。"""

        with self._lock:
            provider_names = self.step_task_routing.get(self._current_step, ())
            if not provider_names:
                return (self.providers[self._current_provider_index],)
            route_index = self._step_task_route_index_by_step.get(self._current_step, 0) % len(provider_names)
            ordered_names = provider_names[route_index:] + provider_names[:route_index]
            self._step_task_route_index_by_step[self._current_step] = route_index + 1
        return tuple(self._providers_by_name[name] for name in ordered_names)

    def _respect_step_rate_limit(self) -> None:
        """按步骤限制模型请求的最小发起间隔。"""

        step_name = self._current_step.strip()
        if not step_name:
            return
        min_interval_seconds = self.step_min_interval_seconds.get(step_name, 0.0)
        if min_interval_seconds <= 0:
            return
        with self._step_rate_limit_lock:
            now = time.time()
            last_started_at = self._last_request_started_at_by_step.get(step_name)
            wait_seconds = 0.0
            if last_started_at is not None:
                wait_seconds = max(0.0, last_started_at + min_interval_seconds - now)
            scheduled_started_at = now + wait_seconds
            self._last_request_started_at_by_step[step_name] = scheduled_started_at
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _write_trace_record(
        self,
        *,
        request_id: str | None,
        provider: LLMProviderRuntimeConfig,
        attempt: int,
        json_mode: bool,
        stream: bool,
        system_prompt: str,
        user_prompt: str,
        duration_ms: float,
        status: str,
        response_text: str | None,
        error_message: str | None = None,
        infrastructure_error: bool | None = None,
    ) -> None:
        """把一次大模型请求的入参与出参落到单次运行日志。"""

        with self._trace_log_lock:
            step_name = self._current_step or "unknown"
            trace_path = self._resolved_trace_log_path(step_name)
            if trace_path is None:
                return
            response_text_present = response_text is not None and response_text != ""
            record = {
                "request_id": request_id,
                "timestamp": datetime.now().astimezone().isoformat(),
                "step": self._current_step,
                "provider": provider.provider,
                "model": provider.model,
                "attempt": attempt + 1,
                "json_mode": json_mode,
                "stream": stream,
                "status": status,
                "phase": _trace_phase_for_status(status),
                "duration_ms": round(duration_ms, 2),
                "request": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                "response": response_text,
                "response_text_present": response_text_present,
                "response_kind": _trace_response_kind_for_status(
                    status,
                    response_text_present=response_text_present,
                    infrastructure_error=infrastructure_error,
                ),
            }
            if error_message is not None:
                record["error"] = {
                    "message": error_message,
                    "infrastructure_error": infrastructure_error,
                }
            is_terminal = status in {"success", "error", "parse_error", "postprocess_error", "aborted"}
            if request_id:
                if status == "started":
                    self._pending_trace_records[request_id] = {
                        "provider": provider,
                        "attempt": attempt,
                        "json_mode": json_mode,
                        "stream": stream,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "step": self._current_step,
                    }
                elif is_terminal:
                    self._pending_trace_records.pop(request_id, None)
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _next_request_id(self) -> str:
        """生成单次模型请求的唯一日志标识。"""

        return f"{self._current_step or 'unknown'}-{threading.get_ident()}-{time.time_ns()}"

    def _flush_pending_trace_records_on_exit(self) -> None:
        """在进程退出时，把仍未收尾的请求补记为 aborted。"""

        with self._trace_log_lock:
            pending_items = list(self._pending_trace_records.items())
        for request_id, context in pending_items:
            provider = context.get("provider")
            if not isinstance(provider, LLMProviderRuntimeConfig):
                continue
            self._write_trace_record(
                request_id=request_id,
                provider=provider,
                attempt=int(context.get("attempt", 0)),
                json_mode=bool(context.get("json_mode", False)),
                stream=bool(context.get("stream", False)),
                system_prompt=str(context.get("system_prompt", "")),
                user_prompt=str(context.get("user_prompt", "")),
                duration_ms=0.0,
                status="aborted",
                response_text=None,
                error_message="进程退出时该请求仍未完成。",
                infrastructure_error=False,
            )
