"""处理不同模型 provider 的请求协议差异与错误分类。"""

from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError

from ..runtime.config import LLMProviderConfig

RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 520, 529}
MINIMAX_RETRYABLE_STATUS_CODES = {1000, 1001, 1002, 1024, 1033, 1039}


def append_endpoint(base_url: str, path: str) -> str:
    """把 endpoint 追加到基础地址，并避免重复拼接。"""
    normalized_base = base_url.rstrip("/")
    normalized_path = path.lstrip("/")
    if normalized_base.endswith(f"/{normalized_path}"):
        return normalized_base
    return f"{normalized_base}/{normalized_path}"


def provider_endpoint(provider: str, base_url: str) -> str:
    """按 provider 类型返回最终请求地址。"""
    normalized_provider = str(provider).strip().lower()
    if normalized_provider == "kimi-code":
        return append_endpoint(base_url, "v1/messages")
    if normalized_provider in {"openai-responses", "deepseek-ark"}:
        return append_endpoint(base_url, "v1/responses" if normalized_provider == "openai-responses" else "responses")
    return append_endpoint(base_url, "chat/completions")


def provider_headers(provider: str, api_key: str) -> dict[str, str]:
    """按 provider 类型生成请求头。"""
    if str(provider).strip().lower() == "kimi-code":
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def build_compare_request_payload(
    provider: str,
    *,
    model: str,
    schema_name: str,
    schema: dict[str, Any],
    strict: bool,
    instructions: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """按 provider 协议构造结构化文件对照请求体。"""
    normalized_provider = str(provider).strip().lower()
    if normalized_provider == "kimi-code":
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        user_prompt = (
            "请严格输出一个 JSON 对象，不要输出 Markdown，不要解释。\n"
            f"必须符合以下 JSON Schema：\n{schema_text}\n\n"
            "输入数据如下：\n"
            f"{json.dumps(input_payload, ensure_ascii=False, indent=2)}"
        )
        return {
            "model": model,
            "system": instructions,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": 4096,
        }
    if normalized_provider in {"openai-responses", "deepseek-ark"}:
        return {
            "model": model,
            "instructions": instructions,
            "input": json.dumps(input_payload, ensure_ascii=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": strict,
                }
            },
        }
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    user_prompt = (
        "请严格输出一个 JSON 对象，不要输出 Markdown，不要解释。\n"
        f"必须符合以下 JSON Schema：\n{schema_text}\n\n"
        "输入数据如下：\n"
        f"{json.dumps(input_payload, ensure_ascii=False, indent=2)}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    if normalized_provider == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    return payload


def normalize_provider_response(provider: str, body: dict[str, Any]) -> dict[str, Any]:
    """把不同 provider 的响应体归一化成统一解析入口。"""
    normalized_provider = str(provider).strip().lower()
    if normalized_provider in {"openai-responses", "deepseek-ark"}:
        return body
    return {"output_text": extract_chat_content(body)}


def extract_chat_content(body: dict[str, Any]) -> str:
    """从 chat/completions 或 messages 风格响应中提取文本。"""
    if isinstance(body.get("content"), list):
        texts: list[str] = []
        for block in body["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
        if texts:
            return "\n".join(texts).strip()
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"provider response missing choices: {body}")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
        return "\n".join(texts).strip()
    raise ValueError(f"provider response missing message content: {body}")


def classify_http_error(provider: str, error: HTTPError, detail: str) -> tuple[bool, str, float | None]:
    """根据 HTTP 响应判断是否可重试，并提取退避信息。"""
    retry_after_seconds = parse_retry_after(error.headers.get("Retry-After") if error.headers else None)
    provider_name = str(provider).strip().lower()
    detail_json = _try_parse_json(detail)

    if error.code in {400, 401, 403}:
        if provider_name == "minimax":
            status_code = _read_minimax_status_code(detail_json)
            if status_code in MINIMAX_RETRYABLE_STATUS_CODES:
                return True, f"{provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds
        return False, f"{provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds

    if error.code in RETRYABLE_HTTP_CODES:
        return True, f"{provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds

    if provider_name == "minimax":
        status_code = _read_minimax_status_code(detail_json)
        if status_code in MINIMAX_RETRYABLE_STATUS_CODES:
            return True, f"{provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds

    return False, f"{provider} 请求失败: {error.code} {detail}".strip(), retry_after_seconds


def provider_label(provider: LLMProviderConfig) -> str:
    """返回便于日志使用的 provider:model 标签。"""
    return f"{provider.provider}:{provider.model}"


def parse_retry_after(value: str | None) -> float | None:
    """把 Retry-After 头解析为秒数。"""
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


def _try_parse_json(detail: str) -> Any:
    """尝试把错误详情字符串解析成 JSON。"""
    stripped = detail.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _read_minimax_status_code(detail_json: Any) -> int | None:
    """读取 MiniMax 错误体中的内部状态码。"""
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
