"""定义文件对照模型返回时必须满足的 JSON Schema。"""

from __future__ import annotations

FILE_COMPARISON_SCHEMA = {
    "name": "file_comparison_result",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["units"],
        "properties": {
            "units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "unit_id",
                        "chapter",
                        "subchapter",
                        "change_type",
                        "display_strategy",
                        "numbering_only",
                        "unchanged_lines",
                        "old_focus_text",
                        "new_focus_text",
                        "confidence",
                    ],
                    "properties": {
                        "unit_id": {"type": "string"},
                        "chapter": {"type": "string"},
                        "subchapter": {"type": "string"},
                        "change_type": {
                            "type": "string",
                            "enum": [
                                "replace",
                                "rewrite",
                                "add",
                                "delete",
                                "add_item",
                                "delete_item",
                                "numbering_only",
                                "equal",
                            ],
                        },
                        "display_strategy": {
                            "type": "string",
                            "enum": [
                                "compare_changed_only",
                                "whole_replace",
                                "delete_old_only",
                                "add_new_only",
                                "skip",
                            ],
                        },
                        "numbering_only": {"type": "boolean"},
                        "unchanged_lines": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "old_focus_text": {"type": "string"},
                        "new_focus_text": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            }
        },
    },
    "strict": True,
}
