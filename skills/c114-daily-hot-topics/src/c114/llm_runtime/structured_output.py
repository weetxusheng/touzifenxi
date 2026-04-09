"""统一处理模型结构化输出中的噪音、分片与轻微格式偏差。"""

from __future__ import annotations

import json
import re
from typing import Any

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
CODE_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```", re.I)


class StructuredLLMError(RuntimeError):
    """表示模型返回结果无法整理成预期结构。"""


def parse_json_payload(content: str) -> Any:
    """从模型文本输出中提取 JSON 对象或数组。"""

    stripped = strip_json_noise(content)
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

    raise StructuredLLMError(f"无法从模型输出中提取 JSON：{content[:200]}")


def coerce_json_object_payload(payload: Any, context: str) -> dict[str, Any]:
    """把模型返回规整成 JSON 对象。"""

    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        merged = merge_json_fragments(payload)
        if isinstance(merged, dict):
            return merged
    raise StructuredLLMError(f"{context}不是 JSON 对象。")


def normalize_string_list(value: Any) -> list[str]:
    """把单字符串、字符串列表或混合列表统一规整为字符串列表。"""

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
    """移除模型常见的思考块和 Markdown 代码围栏。"""

    without_think = THINK_BLOCK_RE.sub(" ", content)
    without_fence = CODE_FENCE_RE.sub(" ", without_think)
    return without_fence.strip()


def extract_json_fragments(content: str) -> list[Any]:
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


def merge_json_fragments(fragments: list[Any]) -> Any:
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
            merged = merge_dicts(merged, fragment)
        return merged
    if isinstance(first, list):
        merged_list: list[Any] = []
        for fragment in fragments:
            merged_list = merge_lists(merged_list, fragment)
        return merged_list
    raise StructuredLLMError("模型返回了多个 JSON 标量值，无法自动合并。")


def merge_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """按“后者覆盖，列表去重合并”规则合并对象。"""

    merged = dict(base)
    for key, value in incoming.items():
        if key in merged:
            merged[key] = merge_json_values(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_json_values(left: Any, right: Any) -> Any:
    """合并同名字段的值。"""

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
    """拼接两个列表，并按值去重。"""

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
    """把任意 JSON 值转成稳定的去重键。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
