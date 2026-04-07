from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from c114.llm import (
    DEFAULT_LLM_MAX_CONCURRENCY,
    MiniMaxChatClient,
    StructuredLLMError,
    _parse_json_payload,
    run_parallel_ordered,
)


class LLMParsingTests(unittest.TestCase):
    def test_parse_json_payload_accepts_plain_json_object(self) -> None:
        payload = _parse_json_payload('{"keywords":["a","b"]}')
        self.assertEqual(payload["keywords"], ["a", "b"])

    def test_parse_json_payload_extracts_json_object_from_wrapped_text(self) -> None:
        payload = _parse_json_payload("```json\n{\"keep_level\":\"strong\"}\n```")
        self.assertEqual(payload["keep_level"], "strong")

    def test_parse_json_payload_rejects_non_json_text(self) -> None:
        with self.assertRaises(StructuredLLMError):
            _parse_json_payload("not-json")


class LLMConfigTests(unittest.TestCase):
    def test_client_from_runtime_config_reads_skill_local_llm_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "llm": {
                            "provider": "minimax",
                            "model": "MiniMax M2.7",
                            "api_key": "demo-key",
                            "base_url": "https://api.minimaxi.com/v1",
                            "timeout_seconds": 30,
                            "max_retries": 1,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            client = MiniMaxChatClient.from_runtime_config(skill_root)

        self.assertEqual(client.model, "MiniMax M2.7")
        self.assertEqual(client.api_key, "demo-key")


class LLMConcurrencyTests(unittest.TestCase):
    def test_run_parallel_ordered_preserves_order_and_caps_workers(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()

        def worker(value: int) -> int:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return value * 10

        result = run_parallel_ordered(list(range(8)), worker)

        self.assertEqual(result, [0, 10, 20, 30, 40, 50, 60, 70])
        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, DEFAULT_LLM_MAX_CONCURRENCY)
