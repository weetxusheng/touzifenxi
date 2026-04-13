"""C114 skill 对外暴露的统一 LLM 门面。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import LLMProviderRuntimeConfig
from .llm_runtime.client import ProviderRequestError as _ProviderRequestError
from .llm_runtime.client import StructuredChatClient
from .llm_runtime.runtime import DEFAULT_LLM_MAX_CONCURRENCY as _DEFAULT_LLM_MAX_CONCURRENCY
from .llm_runtime.runtime import run_parallel_ordered as _run_parallel_ordered
from .llm_runtime.structured_output import (
    StructuredLLMError as _StructuredLLMError,
)
from .llm_runtime.structured_output import (
    coerce_json_object_payload as _coerce_json_object_payload,
)
from .llm_runtime.structured_output import (
    normalize_string_list as _normalize_string_list,
)
from .llm_runtime.structured_output import (
    parse_json_payload as _parse_json_payload,
)

MiniMaxChatClient = StructuredChatClient
LLMProviderConfig = LLMProviderRuntimeConfig
StructuredLLMError = _StructuredLLMError
ProviderRequestError = _ProviderRequestError
DEFAULT_LLM_MAX_CONCURRENCY = _DEFAULT_LLM_MAX_CONCURRENCY
run_parallel_ordered = _run_parallel_ordered
coerce_json_object_payload = _coerce_json_object_payload
normalize_string_list = _normalize_string_list

__all__ = [
    "DEFAULT_LLM_MAX_CONCURRENCY",
    "LLMProviderConfig",
    "MiniMaxChatClient",
    "ProviderRequestError",
    "StructuredChatClient",
    "StructuredLLMError",
    "_parse_json_payload",
    "begin_llm_step",
    "complete_json_with_postprocess_retry",
    "coerce_json_object_payload",
    "load_prompt_text",
    "normalize_string_list",
    "run_parallel_ordered",
]


def begin_llm_step(llm_client: Any, step_name: str) -> None:
    """在步骤开始前重置主备状态；测试桩未实现时静默跳过。"""

    begin_step = getattr(llm_client, "begin_step", None)
    if callable(begin_step):
        begin_step(step_name)


def load_prompt_text(prompt_path: Path) -> str:
    """以 UTF-8 方式读取 prompt 文件内容。"""

    return prompt_path.read_text(encoding="utf-8")


def complete_json_with_postprocess_retry(
    *,
    llm_client: Any,
    system_prompt: str,
    user_prompt: str,
    normalize_response: Any,
    response_label: str,
    default_max_attempts: int = 1,
) -> Any:
    """统一执行 complete_json，并对 postprocess_error 做重试。"""

    max_attempts = max(1, int(default_max_attempts))
    configured_attempts = getattr(llm_client, "max_attempts_for_retry_class", None)
    if callable(configured_attempts):
        max_attempts = max(max_attempts, int(configured_attempts("postprocess", default=max_attempts)))
    last_error: StructuredLLMError | None = None
    for _ in range(max_attempts):
        response = llm_client.complete_json(system_prompt=system_prompt, user_prompt=user_prompt)
        try:
            return normalize_response(response)
        except StructuredLLMError as error:
            last_error = error
            record_postprocess_error = getattr(llm_client, "record_postprocess_error", None)
            if callable(record_postprocess_error):
                record_postprocess_error(error=error, response_payload=response)
    raise last_error or StructuredLLMError(f"{response_label} 后处理失败。")
