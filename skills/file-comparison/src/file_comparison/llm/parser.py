"""解析不同 provider 返回的结构化 JSON，并做最小修复。"""

from __future__ import annotations

import json
import re
from typing import Any

from .schema import FILE_COMPARISON_SCHEMA

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
CODE_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.I)
OBJECT_WRAPPER_KEYS = ("analysis", "result", "output", "data", "response", "payload")
OBJECT_META_KEYS = ("usage", "meta", "metadata", "id", "request_id", "model", "provider")

class ResponseParseError(ValueError):
    """Raised when the model output cannot be parsed into the expected schema."""


def parse_response_payload(response: dict[str, Any]) -> dict[str, Any]:
    """从统一响应体里提取并校验结构化比较结果。"""
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return validate_payload(coerce_json_object_payload(output_text))
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return validate_payload(coerce_json_object_payload(content["text"]))
    raise ResponseParseError("model response did not contain parseable structured output")


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验模型返回是否满足 file-comparison 的最小结构约束。"""
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        raise ResponseParseError("payload missing chapters")
    for chapter in chapters:
        if not isinstance(chapter, dict) or "chapter" not in chapter or "subsections" not in chapter:
            raise ResponseParseError("chapter entry missing required fields")
        if not isinstance(chapter["subsections"], list):
            raise ResponseParseError("subsections must be a list")
        for subsection in chapter["subsections"]:
            for key in FILE_COMPARISON_SCHEMA["schema"]["properties"]["chapters"]["items"]["properties"]["subsections"]["items"]["required"]:
                if key not in subsection:
                    raise ResponseParseError(f"subsection missing required field: {key}")
            subsection["fully_equal_lines"] = normalize_string_list(subsection.get("fully_equal_lines"))
            subsection["subchapter"] = str(subsection.get("subchapter", "")).strip()
            subsection["old_text"] = str(subsection.get("old_text", "")).strip()
            subsection["new_text"] = str(subsection.get("new_text", "")).strip()
            subsection["change_type"] = str(subsection.get("change_type", "")).strip()
    return payload


def coerce_json_object_payload(payload: Any) -> dict[str, Any]:
    """把字符串或包装对象规整成 JSON 对象。"""
    if isinstance(payload, str):
        payload = parse_json_payload(payload)
    normalized = unwrap_json_object_payload(payload)
    if isinstance(normalized, dict):
        return normalized
    raise ResponseParseError("parsed payload was not a JSON object")


def parse_json_payload(content: str) -> Any:
    """从混杂文本中尽量提取出合法 JSON。"""
    stripped = strip_json_noise(content)
    if not stripped:
        raise ResponseParseError("model response text was empty")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fragments = extract_json_fragments(stripped)
    if fragments:
        return merge_json_fragments(fragments)

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

    raise ResponseParseError(f"could not extract JSON from model response: {content[:200]}")


def unwrap_json_object_payload(payload: Any, depth: int = 0) -> Any:
    """递归拆开常见的 output/result/data 包装层。"""
    if depth > 4:
        return payload
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        merged = merge_json_fragments(payload)
        return unwrap_json_object_payload(merged, depth + 1)
    if not isinstance(payload, dict):
        return payload

    candidate_keys = [
        key
        for key, value in payload.items()
        if key not in OBJECT_META_KEYS and value not in (None, "", [], {})
    ]
    if len(candidate_keys) == 1:
        only_key = candidate_keys[0]
        wrapped_value = payload.get(only_key)
        should_unwrap_dict = isinstance(wrapped_value, dict) and (
            only_key in OBJECT_WRAPPER_KEYS or len(payload) == 1
        )
        should_unwrap_list = (
            isinstance(wrapped_value, list)
            and only_key in OBJECT_WRAPPER_KEYS
            and wrapped_value
            and all(isinstance(item, dict) for item in wrapped_value)
        )
        if should_unwrap_dict or should_unwrap_list:
            return unwrap_json_object_payload(wrapped_value, depth + 1)
    return payload


def normalize_string_list(value: Any) -> list[str]:
    """把任意值规整成字符串列表。"""
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    normalized.append(stripped)
                continue
            stripped = str(item).strip()
            if stripped:
                normalized.append(stripped)
        return normalized
    if value is None:
        return []
    stripped = str(value).strip()
    return [stripped] if stripped else []


def strip_json_noise(content: str) -> str:
    """去掉 `<think>` 和代码围栏等非 JSON 噪音。"""
    without_think = THINK_BLOCK_RE.sub(" ", content)
    without_fence = CODE_FENCE_RE.sub(" ", without_think)
    return without_fence.strip()


def extract_json_fragments(content: str) -> list[Any]:
    """从长文本中扫描并提取多个顶层 JSON 片段。"""
    decoder = json.JSONDecoder()
    fragments: list[Any] = []
    index = 0
    while index < len(content):
        current = content[index]
        if current not in "{[":
            index += 1
            continue
        previous = previous_significant_char(content, index)
        if previous in {"{", "[", ":", ","}:
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


def merge_json_fragments(fragments: list[Any]) -> Any:
    """把多个同类型 JSON 片段合并成一个对象。"""
    if len(fragments) == 1:
        return fragments[0]
    kinds = {type(fragment) for fragment in fragments}
    if len(kinds) != 1:
        raise ResponseParseError("multiple JSON fragments had inconsistent top-level types")
    first = fragments[0]
    if isinstance(first, dict):
        merged: dict[str, Any] = {}
        for fragment in fragments:
            merged = merge_dicts(merged, fragment)
        return merged
    if isinstance(first, list):
        merged_list: list[Any] = []
        for fragment in fragments:
            merged_list = merge_lists(merged_list, fragment)
        return merged_list
    raise ResponseParseError("multiple JSON scalar fragments could not be merged")


def merge_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个 JSON 对象。"""
    merged = dict(base)
    for key, value in incoming.items():
        if key in merged:
            merged[key] = merge_json_values(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_json_values(left: Any, right: Any) -> Any:
    """按 JSON 值类型决定合并策略。"""
    if isinstance(left, dict) and isinstance(right, dict):
        return merge_dicts(left, right)
    if isinstance(left, list) and isinstance(right, list):
        return merge_lists(left, right)
    if isinstance(left, list):
        return merge_lists(left, [right])
    if isinstance(right, list):
        return merge_lists([left], right)
    return right


def merge_lists(left: list[Any], right: list[Any]) -> list[Any]:
    """按稳定 JSON 键去重合并两个列表。"""
    merged = list(left)
    seen = {stable_json_key(item) for item in merged}
    for item in right:
        key = stable_json_key(item)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def stable_json_key(value: Any) -> str:
    """把任意 JSON 值转成可稳定比较的字符串键。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def previous_significant_char(content: str, index: int) -> str | None:
    """返回指定位置之前最近的非空白字符。"""
    cursor = index - 1
    while cursor >= 0:
        current = content[cursor]
        if not current.isspace():
            return current
        cursor -= 1
    return None
