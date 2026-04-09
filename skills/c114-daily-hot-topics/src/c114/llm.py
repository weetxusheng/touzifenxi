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
