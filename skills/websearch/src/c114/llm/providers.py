"""封装不同 provider 的接口差异、错误分类与响应提取。"""

from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError

from ..runtime.config import LLMProviderRuntimeConfig
from .structured_output import StructuredLLMError

RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 520, 529}
MINIMAX_RETRYABLE_STATUS_CODES = {1000, 1001, 1002, 1024, 1033, 1039}


def build_chat_payload(
    provider: LLMProviderRuntimeConfig,
    *,
    system_prompt: str,
    user_prompt: str,
    json_mode: bool,
    stream: bool,
) -> dict[str, Any]:
    """按 provider 规则构造请求体。"""

    payload: dict[str, Any] = {"model": provider.model}
    normalized_provider = provider.provider.lower()
    if normalized_provider == "kimi-code":
        payload["system"] = system_prompt
        payload["messages"] = [{"role": "user", "content": user_prompt}]
        payload["max_tokens"] = 4096
        if stream:
            payload["stream"] = True
        return payload

    if normalized_provider == "volc-ark":
        # 火山方舟 Responses API：input 为多轮消息；json_mode 仍依赖提示词约束（与旧链路一致）。
        payload["input"] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload["stream"] = stream
        return payload

    payload["messages"] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if normalized_provider != "kimi":
        payload["temperature"] = 0.2
    if stream:
        payload["stream"] = True
    return payload


def provider_endpoint(provider: LLMProviderRuntimeConfig) -> str:
    """返回不同 provider 的请求地址。"""

    base_url = provider.base_url.rstrip("/")
    if provider.provider.lower() == "kimi-code":
        if base_url.endswith("/v1"):
            return f"{base_url}/messages"
        return f"{base_url}/v1/messages"
    if provider.provider.lower() == "volc-ark":
        if base_url.endswith("/responses"):
            return base_url
        return f"{base_url}/responses"
    return f"{base_url}/chat/completions"


def provider_headers(provider: LLMProviderRuntimeConfig) -> dict[str, str]:
    """返回不同 provider 的请求头。"""

    if provider.provider.lower() == "kimi-code":
        return {
            "Content-Type": "application/json",
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider.api_key}",
    }


def extract_provider_content(provider: LLMProviderRuntimeConfig, body: dict[str, Any]) -> str:
    """从常规 JSON 响应中提取文本内容。"""

    if provider.provider.lower() == "kimi-code":
        blocks = body["content"]
        if not isinstance(blocks, list):
            raise StructuredLLMError(f"{provider.provider} 返回结构不符合预期：{body}")
        texts = [
            str(block.get("text", "")).strip()
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        content = "\n".join(text for text in texts if text)
        if not content:
            raise StructuredLLMError(f"{provider.provider} 返回了空内容。")
        return content

    if provider.provider.lower() == "volc-ark":
        return _extract_volc_ark_assistant_text(body)

    return str(body["choices"][0]["message"]["content"]).strip()


def extract_streaming_content(provider: LLMProviderRuntimeConfig, raw_text: str) -> str:
    """从 SSE 或流式文本响应中还原模型文本。"""

    stripped = raw_text.strip()
    if not stripped:
        raise StructuredLLMError(f"{provider.provider} 流式响应为空。")
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return extract_provider_content(provider, parsed)

    if provider.provider.lower() == "volc-ark":
        raise StructuredLLMError(f"{provider.provider} 当前未实现 Responses API 流式解析。")

    if provider.provider.lower() == "kimi-code":
        content = _extract_anthropic_stream_text(stripped)
    else:
        content = _extract_openai_stream_text(stripped)
    if not content:
        raise StructuredLLMError(f"{provider.provider} 流式响应未提取出有效文本。")
    return content


def classify_http_error(provider: LLMProviderRuntimeConfig, error: HTTPError, detail: str) -> tuple[bool, str, float | None]:
    """把 HTTP 错误归类为基础设施错误或立即失败错误。"""

    retry_after_seconds = parse_retry_after(error.headers.get("Retry-After") if error.headers else None)
    provider_name = provider.provider.lower()
    detail_json = _try_parse_json(detail)

    if error.code in {400, 401, 403}:
        if provider_name == "minimax":
            status_code = _read_minimax_status_code(detail_json)
            if status_code in MINIMAX_RETRYABLE_STATUS_CODES:
                return True, f"{provider.provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds
        return False, f"{provider.provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds

    if error.code in RETRYABLE_HTTP_CODES:
        return True, f"{provider.provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds

    if provider_name == "minimax":
        status_code = _read_minimax_status_code(detail_json)
        if status_code in MINIMAX_RETRYABLE_STATUS_CODES:
            return True, f"{provider.provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds

    return False, f"{provider.provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds


def parse_retry_after(value: str | None) -> float | None:
    """解析 Retry-After 头，支持秒数与 HTTP 时间。"""

    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return max(0.0, float(stripped))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError):
        return None
    now = retry_at.tzinfo and retry_at.now(retry_at.tzinfo) or None
    if now is None:
        return None
    return max(0.0, (retry_at - now).total_seconds())


def _extract_openai_stream_text(raw_text: str) -> str:
    """从 OpenAI 兼容 SSE 中拼出完整文本。"""

    parts: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if isinstance(delta, dict):
            parts.extend(_flatten_content(delta.get("content")))
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            parts.extend(_flatten_content(message.get("content")))
    return "".join(parts).strip()


def _extract_anthropic_stream_text(raw_text: str) -> str:
    """从 Anthropic 兼容 SSE 中拼出完整文本。"""

    parts: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = body.get("type")
        if event_type == "content_block_delta":
            delta = body.get("delta", {})
            if isinstance(delta, dict):
                text = str(delta.get("text", "")).strip()
                if text:
                    parts.append(text)
            continue
        if event_type == "content_block_start":
            block = body.get("content_block", {})
            if isinstance(block, dict):
                text = str(block.get("text", "")).strip()
                if text:
                    parts.append(text)
            continue
        if event_type == "message_start":
            message = body.get("message", {})
            if isinstance(message, dict):
                parts.extend(_flatten_content(message.get("content")))
    return "".join(parts).strip()


def _flatten_content(value: Any) -> list[str]:
    """把 content 的常见字符串或列表块展开为文本片段。"""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return parts
    return []


def _extract_volc_ark_assistant_text(body: dict[str, Any]) -> str:
    """从火山方舟 Responses API 的 response 对象中提取助手可见文本。"""

    err = body.get("error")
    if isinstance(err, dict) and err.get("message"):
        raise StructuredLLMError(f"volc-ark API 错误: {err}")
    status = body.get("status")
    if status == "failed":
        raise StructuredLLMError(f"volc-ark 响应失败: {body}")
    if status in {"in_progress", "incomplete"}:
        raise StructuredLLMError(f"volc-ark 响应未完成（status={status}）：{body}")
    output = body.get("output")
    if not isinstance(output, list):
        raise StructuredLLMError(f"volc-ark 返回结构不符合预期：{body}")
    texts = _collect_volc_ark_output_texts(output)
    content = "\n".join(texts).strip()
    if not content:
        raise StructuredLLMError("volc-ark 返回了空内容。")
    return content


def _collect_volc_ark_output_texts(node: Any) -> list[str]:
    """从 Responses `output` 树中收集助手文本块（兼容 message / output_text 等形态）。"""

    texts: list[str] = []
    if isinstance(node, dict):
        ntype = node.get("type")
        if ntype in {"output_text", "input_text"} and isinstance(node.get("text"), str):
            t = str(node["text"]).strip()
            if t:
                texts.append(t)
        elif ntype == "message" and isinstance(node.get("content"), list):
            for block in node["content"]:
                texts.extend(_collect_volc_ark_output_texts(block))
        elif isinstance(node.get("content"), list):
            for block in node["content"]:
                texts.extend(_collect_volc_ark_output_texts(block))
    elif isinstance(node, list):
        for item in node:
            texts.extend(_collect_volc_ark_output_texts(item))
    return texts


def _try_parse_json(detail: str) -> Any:
    """尽量把错误详情解析成 JSON。"""

    detail = detail.strip()
    if not detail:
        return None
    try:
        return json.loads(detail)
    except json.JSONDecodeError:
        return None


def _read_minimax_status_code(detail_json: Any) -> int | None:
    """读取 MiniMax 错误体中的业务状态码。"""

    if not isinstance(detail_json, dict):
        return None
    base_resp = detail_json.get("base_resp")
    if not isinstance(base_resp, dict):
        return None
    status_code = base_resp.get("status_code")
    try:
        return int(status_code)
    except (TypeError, ValueError):
        return None
