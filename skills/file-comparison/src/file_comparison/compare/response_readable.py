"""把模型原始响应转换成人工可读诊断文件。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..llm.parser import coerce_json_object_payload
from ..runtime.checkpoint import atomic_write_json


def write_readable_response_file(path: Path, normalized_response: dict[str, Any]) -> None:
    """把 normalized response 中的大字符串 JSON 展开，便于人工排查。"""
    try:
        readable = readable_response_payload(normalized_response)
    except Exception as exc:  # noqa: BLE001
        readable = {
            "parse_error": f"{type(exc).__name__}: {exc}",
            "raw_keys": sorted(normalized_response.keys()),
        }
    atomic_write_json(path, readable)


def readable_response_payload(normalized_response: dict[str, Any]) -> dict[str, Any]:
    """从 normalized response 提取模型结构化输出。"""
    output_text = normalized_response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return compact_readable_payload(output_text)
    for item in normalized_response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text" and content.get("text"):
                return compact_readable_payload(str(content["text"]))
    return {"partial": False, "blocks": [], "raw_keys": sorted(normalized_response.keys())}


def compact_readable_payload(output_text: str) -> dict[str, Any]:
    """把模型输出转换成便于人工阅读的短字段结构。"""
    try:
        payload = coerce_json_object_payload(output_text)
        return {"partial": False, "blocks": compact_readable_blocks(payload.get("blocks", []))}
    except Exception as exc:  # noqa: BLE001
        partial_blocks, tail = extract_complete_blocks_from_partial_json(output_text)
        return {
            "partial": True,
            "error": f"{type(exc).__name__}: {exc}",
            "blocks": compact_readable_blocks(partial_blocks),
            "tail": tail,
        }


def compact_readable_blocks(blocks: Any) -> list[dict[str, Any]]:
    """把 blocks/operations 字段名压短，减少排查噪音。"""
    if not isinstance(blocks, list):
        return []
    compact_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        compact_blocks.append(
            {
                "id": str(block.get("block_id", "")).strip(),
                "ch": str(block.get("chapter", "")).strip(),
                "p": str(block.get("parent_path", "")).strip(),
                "ops": compact_readable_operations(block.get("operations", [])),
            }
        )
    return compact_blocks


def compact_readable_operations(operations: Any) -> list[dict[str, Any]]:
    """把单条 operation 转成短字段结构。"""
    if not isinstance(operations, list):
        return []
    compact_operations: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        compact_operations.append(
            {
                "t": str(operation.get("type", "")).strip(),
                "o": operation.get("old_item_ids", []),
                "n": operation.get("new_item_ids", []),
                "old": str(operation.get("old_focus_text", "")).strip(),
                "new": str(operation.get("new_focus_text", "")).strip(),
                "c": operation.get("confidence", 0),
                "r": str(operation.get("reason", "")).strip(),
            }
        )
    return compact_operations


def extract_complete_blocks_from_partial_json(output_text: str) -> tuple[list[dict[str, Any]], str]:
    """从被截断的 blocks JSON 中尽量提取已经完整闭合的 block 对象。"""
    blocks_key_index = output_text.find('"blocks"')
    if blocks_key_index == -1:
        return [], output_text[-2000:]
    array_start = output_text.find("[", blocks_key_index)
    if array_start == -1:
        return [], output_text[-2000:]
    decoder = json.JSONDecoder()
    blocks: list[dict[str, Any]] = []
    index = array_start + 1
    while index < len(output_text):
        while index < len(output_text) and output_text[index] in " \r\n\t,":
            index += 1
        if index >= len(output_text) or output_text[index] == "]":
            break
        try:
            block, next_index = decoder.raw_decode(output_text, index)
        except json.JSONDecodeError:
            break
        if isinstance(block, dict):
            blocks.append(block)
        index = next_index
    partial_block = extract_partial_block_from_json(output_text, index)
    if partial_block is not None:
        blocks.append(partial_block)
    return blocks, output_text[index:][-2000:]


def extract_partial_block_from_json(output_text: str, start_index: int) -> dict[str, Any] | None:
    """从未闭合的 block 片段中提取 block 元信息和完整 operations。"""
    block_start = output_text.find("{", start_index)
    if block_start == -1:
        return None
    fragment = output_text[block_start:]
    block_id = extract_json_string_field(fragment, "block_id")
    chapter = extract_json_string_field(fragment, "chapter")
    parent_path = extract_json_string_field(fragment, "parent_path")
    operations = extract_complete_operations_from_partial_block(fragment)
    if not block_id and not chapter and not parent_path and not operations:
        return None
    return {
        "block_id": block_id,
        "chapter": chapter,
        "parent_path": parent_path,
        "operations": operations,
    }


def extract_json_string_field(fragment: str, field_name: str) -> str:
    """从 JSON 片段中读取一个已闭合的字符串字段。"""
    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"((?:\\.|[^"\\])*)"', fragment)
    if not match:
        return ""
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1)


def extract_complete_operations_from_partial_block(fragment: str) -> list[dict[str, Any]]:
    """从未闭合 block 的 operations 数组中提取完整 operation 对象。"""
    operations_key_index = fragment.find('"operations"')
    if operations_key_index == -1:
        return []
    array_start = fragment.find("[", operations_key_index)
    if array_start == -1:
        return []
    decoder = json.JSONDecoder()
    operations: list[dict[str, Any]] = []
    index = array_start + 1
    while index < len(fragment):
        while index < len(fragment) and fragment[index] in " \r\n\t,":
            index += 1
        if index >= len(fragment) or fragment[index] == "]":
            break
        try:
            operation, next_index = decoder.raw_decode(fragment, index)
        except json.JSONDecodeError:
            break
        if isinstance(operation, dict):
            operations.append(operation)
        index = next_index
    return operations
