"""C114 skill 统一使用的结构化大模型调用封装。"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import LLMProviderRuntimeConfig, load_c114_runtime_config

DEFAULT_LLM_MAX_CONCURRENCY = 3
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
CODE_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.I)
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504, 520, 529}
T = TypeVar("T")
R = TypeVar("R")


class StructuredLLMError(RuntimeError):
    """表示模型返回结果无法整理成预期结构。"""


class ProviderRequestError(RuntimeError):
    """表示某个 provider 的基础设施请求失败。"""

    def __init__(self, provider: LLMProviderRuntimeConfig, message: str) -> None:
        super().__init__(message)
        self.provider = provider


class StructuredChatClient:
    """面向 skill 内部步骤的统一结构化模型客户端。"""

    def __init__(
        self,
        *,
        primary: LLMProviderRuntimeConfig,
        fallback: LLMProviderRuntimeConfig | None,
        failover_enabled: bool,
        failover_consecutive_failures: int,
    ) -> None:
        """保存当前 skill 运行所需的主备模型配置。"""

        self.primary = _normalize_provider_config(primary)
        self.fallback = _normalize_provider_config(fallback) if fallback is not None else None
        self.failover_enabled = failover_enabled and self.fallback is not None
        self.failover_consecutive_failures = max(1, failover_consecutive_failures)
        self._lock = threading.Lock()
        self._current_provider = self.primary.provider
        self._consecutive_primary_failures = 0
        self._current_step = ""

    @classmethod
    def from_runtime_config(cls, base_path: Path | None = None) -> "StructuredChatClient":
        """从 skill 本地配置中构造统一的大模型客户端。"""
        runtime_config = load_c114_runtime_config(base_path)
        if not runtime_config.llm_primary.api_key:
            raise RuntimeError("未配置 llm.primary.api_key，无法执行需要模型思考的 C114 步骤。")
        if runtime_config.llm_failover.enabled and runtime_config.llm_fallback is None:
            raise RuntimeError("已启用 llm.failover，但缺少 llm.fallback 配置。")
        if runtime_config.llm_failover.enabled and runtime_config.llm_fallback and not runtime_config.llm_fallback.api_key:
            raise RuntimeError("已启用 llm.failover，但未配置 llm.fallback.api_key。")
        return cls(
            primary=runtime_config.llm_primary,
            fallback=runtime_config.llm_fallback,
            failover_enabled=runtime_config.llm_failover.enabled,
            failover_consecutive_failures=runtime_config.llm_failover.consecutive_failures,
        )

    def begin_step(self, step_name: str) -> None:
        """在每个需要模型的步骤开始前重置主备状态。"""

        with self._lock:
            self._current_step = step_name
            self._current_provider = self.primary.provider
            self._consecutive_primary_failures = 0

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> Any:
        """调用模型并返回解析后的 JSON，必要时自动重试。"""
        last_error: Exception | None = None
        repair_hint = ""
        remaining_parse_attempts = max(self.primary.max_retries, self.fallback.max_retries if self.fallback else 0) + 1
        while True:
            try:
                provider, content = self._post_chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + repair_hint,
                )
                self._record_success(provider)
                return _parse_json_payload(content)
            except ProviderRequestError as error:
                last_error = error
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

    def _post_chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[LLMProviderRuntimeConfig, str]:
        """向当前生效的 provider 发送一次对话请求。"""

        provider = self._active_provider()
        return provider, self._request_with_provider(
            provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def _request_with_provider(
        self,
        provider: LLMProviderRuntimeConfig,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """向单个 provider 发送请求，并在 provider 内部做基础重试。"""

        payload = _build_chat_payload(provider, system_prompt=system_prompt, user_prompt=user_prompt)
        request = Request(
            url=f"{provider.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {provider.api_key}",
            },
            method="POST",
        )
        last_error: ProviderRequestError | None = None
        for attempt in range(provider.max_retries + 1):
            try:
                with urlopen(request, timeout=provider.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="ignore")
                last_error = ProviderRequestError(provider, f"{provider.provider} 请求失败: {error.code} {detail}")
                if error.code not in RETRYABLE_HTTP_CODES or attempt >= provider.max_retries:
                    raise last_error from error
            except URLError as error:
                last_error = ProviderRequestError(provider, f"{provider.provider} 请求失败: {error.reason}")
                if attempt >= provider.max_retries:
                    raise last_error from error
            except socket.timeout as error:
                last_error = ProviderRequestError(provider, f"{provider.provider} 请求超时。")
                if attempt >= provider.max_retries:
                    raise last_error from error
            time.sleep(provider.retry_backoff_seconds * (attempt + 1))
        else:  # pragma: no cover - defensive
            raise last_error or ProviderRequestError(provider, f"{provider.provider} 请求失败。")

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise StructuredLLMError(f"{provider.provider} 返回结构不符合预期：{body}") from error
        if not isinstance(content, str) or not content.strip():
            raise StructuredLLMError(f"{provider.provider} 返回了空内容。")
        return content.strip()

    def _active_provider(self) -> LLMProviderRuntimeConfig:
        """返回当前应使用的 provider。"""

        with self._lock:
            if self.failover_enabled and self.fallback and self._current_provider == self.fallback.provider:
                return self.fallback
            return self.primary

    def _record_success(self, provider: LLMProviderRuntimeConfig) -> None:
        """记录一次成功请求，必要时清零主模型失败计数。"""

        if provider.provider != self.primary.provider:
            return
        with self._lock:
            self._consecutive_primary_failures = 0

    def _record_failure(self, provider: LLMProviderRuntimeConfig) -> bool:
        """记录一次基础设施失败，并在达到阈值时切到备用模型。"""

        if not self.failover_enabled or self.fallback is None:
            return False
        if provider.provider != self.primary.provider:
            return False
        with self._lock:
            self._consecutive_primary_failures += 1
            if self._consecutive_primary_failures < self.failover_consecutive_failures:
                return False
            self._current_provider = self.fallback.provider
            return True


def _normalize_provider_config(provider: LLMProviderRuntimeConfig) -> LLMProviderRuntimeConfig:
    """规整 provider 配置中的 URL 和重试参数。"""

    return LLMProviderRuntimeConfig(
        provider=provider.provider.strip(),
        model=provider.model.strip(),
        api_key=provider.api_key.strip(),
        base_url=provider.base_url.rstrip("/"),
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        retry_backoff_seconds=max(0.0, provider.retry_backoff_seconds),
    )


def _build_chat_payload(
    provider: LLMProviderRuntimeConfig,
    *,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """按 provider 规则构造 chat/completions 请求体。"""

    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if provider.provider.lower() != "kimi":
        payload["temperature"] = 0.2
    return payload


MiniMaxChatClient = StructuredChatClient
LLMProviderConfig = LLMProviderRuntimeConfig


def begin_llm_step(llm_client: Any, step_name: str) -> None:
    """在步骤开始前重置主备状态；测试桩未实现时静默跳过。"""

    begin_step = getattr(llm_client, "begin_step", None)
    if callable(begin_step):
        begin_step(step_name)


def load_prompt_text(prompt_path: Path) -> str:
    """以 UTF-8 方式读取 prompt 文件内容。"""
    return prompt_path.read_text(encoding="utf-8")


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


def _parse_json_payload(content: str) -> Any:
    """从模型文本输出中提取 JSON 对象或数组。"""
    stripped = _strip_json_noise(content)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fragments = _extract_json_fragments(stripped)
    if fragments:
        return _merge_json_fragments(fragments)

    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start != -1 and object_end != -1 and object_start < object_end:
        candidate = stripped[object_start : object_end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if array_start != -1 and array_end != -1 and array_start < array_end:
        candidate = stripped[array_start : array_end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise StructuredLLMError(f"无法从模型输出中提取 JSON：{content[:200]}")


def _strip_json_noise(content: str) -> str:
    """移除模型常见的思考块和 Markdown 代码围栏。"""

    without_think = THINK_BLOCK_RE.sub(" ", content)
    without_fence = CODE_FENCE_RE.sub(" ", without_think)
    return without_fence.strip()


def _extract_json_fragments(content: str) -> list[Any]:
    """从夹杂说明文字的输出中提取多个顶层 JSON 片段。"""

    decoder = json.JSONDecoder()
    fragments: list[Any] = []
    index = 0
    while index < len(content):
        current = content[index]
        if current not in "{[":
            index += 1
            continue
        try:
            fragment, next_index = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            index += 1
            continue
        fragments.append(fragment)
        index = next_index
    return fragments


def _merge_json_fragments(fragments: list[Any]) -> Any:
    """按统一规则合并多个 JSON 片段。"""

    if len(fragments) == 1:
        return fragments[0]
    kinds = {type(fragment) for fragment in fragments}
    if len(kinds) != 1:
        raise StructuredLLMError("模型返回了多段 JSON，但顶层类型不一致，无法自动合并。")
    first = fragments[0]
    if isinstance(first, dict):
        merged: dict[str, Any] = {}
        for fragment in fragments:
            merged = _merge_dicts(merged, fragment)
        return merged
    if isinstance(first, list):
        merged_list: list[Any] = []
        for fragment in fragments:
            merged_list = _merge_lists(merged_list, fragment)
        return merged_list
    raise StructuredLLMError("模型返回了多个 JSON 标量值，无法自动合并。")


def _merge_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """按“后者覆盖，列表去重合并”规则合并对象。"""

    merged = dict(base)
    for key, value in incoming.items():
        if key in merged:
            merged[key] = _merge_json_values(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_json_values(left: Any, right: Any) -> Any:
    """合并同名字段的值。"""

    if isinstance(left, dict) and isinstance(right, dict):
        return _merge_dicts(left, right)
    if isinstance(left, list) and isinstance(right, list):
        return _merge_lists(left, right)
    if isinstance(left, list):
        return _merge_lists(left, [right])
    if isinstance(right, list):
        return _merge_lists([left], right)
    return right


def _merge_lists(left: list[Any], right: list[Any]) -> list[Any]:
    """拼接两个列表，并按值去重。"""

    merged = list(left)
    seen = {_stable_json_key(item) for item in merged}
    for item in right:
        key = _stable_json_key(item)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _stable_json_key(value: Any) -> str:
    """把任意 JSON 值转成稳定的去重键。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
