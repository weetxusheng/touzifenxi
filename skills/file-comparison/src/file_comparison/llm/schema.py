"""定义文件对照模型返回时必须满足的 JSON Schema。"""

from __future__ import annotations

FILE_COMPARISON_SCHEMA = {
    "name": "file_comparison_result",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["chapters"],
        "properties": {
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["chapter", "subsections"],
                    "properties": {
                        "chapter": {"type": "string"},
                        "subsections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "subchapter",
                                    "old_text",
                                    "new_text",
                                    "change_type",
                                    "numbering_only",
                                    "fully_equal_lines",
                                ],
                                "properties": {
                                    "subchapter": {"type": "string"},
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                    "change_type": {
                                        "type": "string",
                                        "enum": ["replace", "add", "delete"],
                                    },
                                    "numbering_only": {"type": "boolean"},
                                    "fully_equal_lines": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
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
