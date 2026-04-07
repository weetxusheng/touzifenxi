"""C114 skill 统一使用的 MiniMax 大模型调用封装。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import socket
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import load_c114_runtime_config

DEFAULT_LLM_MAX_CONCURRENCY = 4
T = TypeVar("T")
R = TypeVar("R")


class StructuredLLMError(RuntimeError):
    """表示模型返回结果无法整理成预期结构。"""


class MiniMaxChatClient:
    """面向 skill 内部步骤的轻量 MiniMax 兼容客户端。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        """保存当前 skill 运行所需的固定模型配置。"""
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @classmethod
    def from_runtime_config(cls, base_path: Path | None = None) -> "MiniMaxChatClient":
        """从 skill 本地配置中构造统一的大模型客户端。"""
        runtime_config = load_c114_runtime_config(base_path)
        if not runtime_config.llm_api_key:
            raise RuntimeError("未配置 llm.api_key，无法执行需要模型思考的 C114 步骤。")
        return cls(
            api_key=runtime_config.llm_api_key,
            model=runtime_config.llm_model,
            base_url=runtime_config.llm_base_url,
            timeout_seconds=runtime_config.llm_timeout_seconds,
            max_retries=runtime_config.llm_max_retries,
        )

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> Any:
        """调用模型并返回解析后的 JSON，必要时自动重试。"""
        last_error: Exception | None = None
        repair_hint = ""
        for _ in range(self.max_retries + 1):
            try:
                content = self._post_chat_completion(system_prompt=system_prompt, user_prompt=user_prompt + repair_hint)
                return _parse_json_payload(content)
            except Exception as error:  # noqa: BLE001
                last_error = error
                repair_hint = (
                    "\n\n上一次输出未能被解析为合法 JSON。"
                    "这一次请只返回单个 JSON 对象或 JSON 数组，不要返回 Markdown，不要返回解释。"
                )
        raise StructuredLLMError(f"模型输出无法解析为 JSON：{last_error}") from last_error

    def _post_chat_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        """向配置好的 MiniMax 兼容接口发送一次对话请求。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"MiniMax 请求失败: {error.code} {detail}") from error
        except URLError as error:
            raise RuntimeError(f"MiniMax 请求失败: {error.reason}") from error
        except socket.timeout as error:
            raise RuntimeError("MiniMax 请求超时。") from error

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise StructuredLLMError(f"MiniMax 返回结构不符合预期：{body}") from error
        if not isinstance(content, str) or not content.strip():
            raise StructuredLLMError("MiniMax 返回了空内容。")
        return content.strip()


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
    stripped = content.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start != -1 and object_end != -1 and object_start < object_end:
        return json.loads(stripped[object_start : object_end + 1])

    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if array_start != -1 and array_end != -1 and array_start < array_end:
        return json.loads(stripped[array_start : array_end + 1])

    raise StructuredLLMError(f"无法从模型输出中提取 JSON：{content[:200]}")
