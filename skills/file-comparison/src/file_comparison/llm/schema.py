"""定义文件对照模型返回时必须满足的 JSON Schema。"""

from __future__ import annotations

FILE_COMPARISON_SCHEMA = {
    "name": "file_comparison_result",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["blocks"],
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block_id", "chapter", "parent_path", "operations"],
                    "properties": {
                        "block_id": {"type": "string"},
                        "chapter": {"type": "string"},
                        "parent_path": {"type": "string"},
                        "operations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "type",
                                    "old_item_ids",
                                    "new_item_ids",
                                    "old_focus_text",
                                    "new_focus_text",
                                    "confidence",
                                    "reason",
                                ],
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["add", "delete", "replace"],
                                    },
                                    "old_item_ids": {"type": "array", "items": {"type": "string"}},
                                    "new_item_ids": {"type": "array", "items": {"type": "string"}},
                                    "old_focus_text": {"type": "string"},
                                    "new_focus_text": {"type": "string"},
                                    "confidence": {"type": "number"},
                                    "reason": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        },
    },
    "strict": True,
}
