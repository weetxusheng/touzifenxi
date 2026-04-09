from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from c114.llm import (
    DEFAULT_LLM_MAX_CONCURRENCY,
    LLMProviderConfig,
    StructuredChatClient,
    StructuredLLMError,
    _parse_json_payload,
    coerce_json_object_payload,
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

    def test_parse_json_payload_strips_think_wrapper(self) -> None:
        payload = _parse_json_payload("<think>\n先想一想\n</think>\n{\"keep_level\":\"strong\"}")
        self.assertEqual(payload["keep_level"], "strong")

    def test_parse_json_payload_merges_multiple_json_objects(self) -> None:
        payload = _parse_json_payload('{"summary":"旧值","core_points":["a"]}\n{"summary":"新值","core_points":["b","a"]}')
        self.assertEqual(payload["summary"], "新值")
        self.assertEqual(payload["core_points"], ["a", "b"])

    def test_parse_json_payload_merges_multiple_json_objects_with_text_noise(self) -> None:
        payload = _parse_json_payload(
            '这里是解释\n```json\n{"entities":["中国联通"]}\n```\n补充如下\n{"entities":["TM Forum"],"signals":"行业信号"}'
        )
        self.assertEqual(payload["entities"], ["中国联通", "TM Forum"])
        self.assertEqual(payload["signals"], "行业信号")

    def test_parse_json_payload_merges_step7_style_findings_lists(self) -> None:
        payload = _parse_json_payload(
            '{"overall_decision":"pass","findings":[{"topic":"AI","severity":"low"}]}\n'
            '{"overall_decision":"revise","findings":[{"topic":"量子","severity":"medium"}],"strengths":["结构完整"]}'
        )
        self.assertEqual(payload["overall_decision"], "revise")
        self.assertEqual(
            payload["findings"],
            [
                {"topic": "AI", "severity": "low"},
                {"topic": "量子", "severity": "medium"},
            ],
        )
        self.assertEqual(payload["strengths"], ["结构完整"])

    def test_parse_json_payload_rejects_mixed_object_and_array_fragments(self) -> None:
        with self.assertRaises(StructuredLLMError):
            _parse_json_payload('{"summary":"一"}\n["二"]')

    def test_coerce_json_object_payload_merges_object_list(self) -> None:
        payload = coerce_json_object_payload(
            [
                {"summary": "旧值"},
                {"summary": "新值", "core_points": ["甲"]},
            ],
            "step 5 分析结果",
        )

        self.assertEqual(payload["summary"], "新值")
        self.assertEqual(payload["core_points"], ["甲"])


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
                            "primary": {
                                "provider": "kimi",
                                "model": "kimi-k2.5",
                                "api_key": "demo-key",
                                "base_url": "https://api.moonshot.cn/v1",
                                "timeout_seconds": 30,
                                "max_retries": 1,
                                "retry_backoff_seconds": 1.0,
                            },
                            "failover": {
                                "enabled": False,
                            },
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            client = StructuredChatClient.from_runtime_config(skill_root)

        self.assertEqual(client.primary.model, "kimi-k2.5")
        self.assertEqual(client.primary.api_key, "demo-key")


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


class LLMRetryTests(unittest.TestCase):
    def test_post_chat_completion_retries_on_529(self) -> None:
        attempts = 0

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"keywords":["a","b"]}',
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(_request, timeout=30.0):  # type: ignore[no-untyped-def]
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise HTTPError(
                    url="https://api.minimaxi.com/v1/chat/completions",
                    code=529,
                    msg="overloaded",
                    hdrs=None,
                    fp=BytesIO('{"base_resp":{"status_code":529,"status_msg":"当前服务集群负载较高，请稍后重试"}}'.encode("utf-8")),
                )
            return FakeResponse()

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="minimax",
                model="MiniMax M2.7",
                api_key="demo",
                base_url="https://api.minimaxi.com/v1",
                timeout_seconds=30.0,
                max_retries=1,
                retry_backoff_seconds=0.0,
            ),
            fallback=None,
            failover_enabled=False,
            failover_consecutive_failures=3,
        )

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            payload = client.complete_json(system_prompt="系统", user_prompt="用户")

        self.assertEqual(attempts, 2)
        self.assertEqual(payload["keywords"], ["a", "b"])

    def test_client_failsover_to_fallback_after_three_infra_failures(self) -> None:
        attempts: list[str] = []

        class FakeResponse:
            def __init__(self, content: str) -> None:
                self._content = content

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": self._content}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=30.0):  # type: ignore[no-untyped-def]
            provider = "kimi" if "moonshot" in request.full_url else "minimax"
            attempts.append(provider)
            if provider == "kimi":
                raise HTTPError(
                    url=request.full_url,
                    code=529,
                    msg="overloaded",
                    hdrs=None,
                    fp=BytesIO('{"error":"busy"}'.encode("utf-8")),
                )
            return FakeResponse('{"keywords":["a","b"]}')

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi",
                model="kimi-k2.5",
                api_key="kimi",
                base_url="https://api.moonshot.cn/v1",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            fallback=LLMProviderConfig(
                provider="minimax",
                model="MiniMax M2.7",
                api_key="minimax",
                base_url="https://api.minimaxi.com/v1",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            failover_enabled=True,
            failover_consecutive_failures=3,
        )
        client.begin_step("step_2")

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="一次")
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="二次")
            client.complete_json(system_prompt="系统", user_prompt="三次")
            payload = client.complete_json(system_prompt="系统", user_prompt="四次")

        self.assertEqual(attempts, ["kimi", "kimi", "kimi", "minimax", "minimax"])
        self.assertEqual(payload["keywords"], ["a", "b"])

    def test_client_resets_to_primary_on_new_step(self) -> None:
        attempts: list[str] = []

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": '{"keywords":["a","b"]}'}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=30.0):  # type: ignore[no-untyped-def]
            provider = "kimi" if "moonshot" in request.full_url else "minimax"
            attempts.append(provider)
            if provider == "kimi" and len(attempts) <= 3:
                raise HTTPError(
                    url=request.full_url,
                    code=529,
                    msg="overloaded",
                    hdrs=None,
                    fp=BytesIO(b'{"error":"busy"}'),
                )
            return FakeResponse()

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi",
                model="kimi-k2.5",
                api_key="kimi",
                base_url="https://api.moonshot.cn/v1",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            fallback=LLMProviderConfig(
                provider="minimax",
                model="MiniMax M2.7",
                api_key="minimax",
                base_url="https://api.minimaxi.com/v1",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            failover_enabled=True,
            failover_consecutive_failures=3,
        )

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            client.begin_step("step_2")
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="一次")
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="二次")
            client.complete_json(system_prompt="系统", user_prompt="三次")
            client.complete_json(system_prompt="系统", user_prompt="四次")
            client.begin_step("step_3")
            client.complete_json(system_prompt="系统", user_prompt="五次")

        self.assertEqual(attempts[-1], "kimi")

    def test_kimi_request_payload_omits_temperature(self) -> None:
        captured_payload: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": '{"ping":"pong"}'}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=30.0):  # type: ignore[no-untyped-def]
            nonlocal captured_payload
            captured_payload = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi",
                model="kimi-k2.5",
                api_key="kimi",
                base_url="https://api.moonshot.cn/v1",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            fallback=None,
            failover_enabled=False,
            failover_consecutive_failures=3,
        )

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            payload = client.complete_json(system_prompt="系统", user_prompt="用户")

        self.assertEqual(payload["ping"], "pong")
        self.assertNotIn("temperature", captured_payload)

    def test_kimi_code_uses_anthropic_messages_endpoint(self) -> None:
        captured_url = ""
        captured_headers: dict[str, str] = {}
        captured_payload: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": '{"ping":"pong"}',
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout=30.0):  # type: ignore[no-untyped-def]
            nonlocal captured_url, captured_headers, captured_payload
            captured_url = request.full_url
            captured_headers = dict(request.header_items())
            captured_payload = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi-code",
                model="kimi-for-coding",
                api_key="kimi-code-key",
                base_url="https://api.kimi.com/coding",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            fallback=None,
            failover_enabled=False,
            failover_consecutive_failures=3,
        )

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            payload = client.complete_json(system_prompt="系统", user_prompt="用户")

        self.assertEqual(payload["ping"], "pong")
        self.assertEqual(captured_url, "https://api.kimi.com/coding/v1/messages")
        self.assertEqual(captured_headers["X-api-key"], "kimi-code-key")
        self.assertIn("Anthropic-version", captured_headers)
        self.assertEqual(captured_payload["model"], "kimi-for-coding")
        self.assertEqual(captured_payload["system"], "系统")
        self.assertEqual(captured_payload["messages"][0]["content"], "用户")
        self.assertNotIn("response_format", captured_payload)

    def test_client_failsover_across_three_provider_chain(self) -> None:
        attempts: list[str] = []

        class FakeResponse:
            def __init__(self, content: str) -> None:
                self._content = content

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                if "content" in self._content:
                    return self._content.encode("utf-8")
                return json.dumps({"choices": [{"message": {"content": self._content}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=30.0):  # type: ignore[no-untyped-def]
            full_url = request.full_url
            if "api.kimi.com/coding" in full_url:
                provider = "kimi-code"
            elif "moonshot" in full_url:
                provider = "kimi"
            else:
                provider = "minimax"
            attempts.append(provider)
            if provider in {"kimi-code", "kimi"}:
                raise HTTPError(
                    url=full_url,
                    code=529,
                    msg="overloaded",
                    hdrs=None,
                    fp=BytesIO(b'{"error":"busy"}'),
                )
            return FakeResponse('{"choices":[{"message":{"content":"{\\"keywords\\":[\\"a\\",\\"b\\"]}"}}]}')

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi-code",
                model="kimi-for-coding",
                api_key="code-key",
                base_url="https://api.kimi.com/coding",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            fallbacks=[
                LLMProviderConfig(
                    provider="kimi",
                    model="kimi-k2.5",
                    api_key="kimi-key",
                    base_url="https://api.moonshot.cn/v1",
                    timeout_seconds=30.0,
                    max_retries=0,
                    retry_backoff_seconds=0.0,
                ),
                LLMProviderConfig(
                    provider="minimax",
                    model="MiniMax M2.7",
                    api_key="minimax-key",
                    base_url="https://api.minimaxi.com/v1",
                    timeout_seconds=30.0,
                    max_retries=0,
                    retry_backoff_seconds=0.0,
                ),
            ],
            failover_enabled=True,
            failover_consecutive_failures=3,
        )
        client.begin_step("step_2")

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="一")
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="二")
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="三")
            with self.assertRaises(StructuredLLMError):
                client.complete_json(system_prompt="系统", user_prompt="四")
            payload = client.complete_json(system_prompt="系统", user_prompt="五")
            payload = client.complete_json(system_prompt="系统", user_prompt="六")

        self.assertEqual(
            attempts,
            ["kimi-code", "kimi-code", "kimi-code", "kimi", "kimi", "kimi", "minimax", "minimax"],
        )
        self.assertEqual(payload["keywords"], ["a", "b"])

    def test_retry_after_header_takes_priority_over_local_backoff(self) -> None:
        sleep_calls: list[float] = []

        def fake_urlopen(_request, timeout=30.0):  # type: ignore[no-untyped-def]
            raise HTTPError(
                url="https://api.moonshot.cn/v1/chat/completions",
                code=429,
                msg="rate_limited",
                hdrs={"Retry-After": "7"},
                fp=BytesIO(b'{"error":"rate limited"}'),
            )

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi",
                model="kimi-k2.5",
                api_key="kimi",
                base_url="https://api.moonshot.cn/v1",
                timeout_seconds=30.0,
                max_retries=1,
                retry_backoff_seconds=0.0,
            ),
            fallback=None,
            failover_enabled=False,
            failover_consecutive_failures=3,
            retry_honor_retry_after=True,
            retry_jitter_seconds=0.0,
        )

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            with patch("c114.llm_runtime.client.time.sleep", side_effect=lambda value: sleep_calls.append(value)):
                with self.assertRaises(StructuredLLMError):
                    client.complete_json(system_prompt="系统", user_prompt="用户")

        self.assertEqual(sleep_calls, [7.0])

    def test_streaming_steps_use_stream_payload_and_parse_sse(self) -> None:
        captured_payload: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def read(self) -> bytes:
                return (
                    'data: {"choices":[{"delta":{"content":"{\\"summary\\":\\"一\\""}}]}\n'
                    'data: {"choices":[{"delta":{"content":"}"}}]}\n'
                    "data: [DONE]\n"
                ).encode("utf-8")

        def fake_urlopen(request, timeout=30.0):  # type: ignore[no-untyped-def]
            nonlocal captured_payload
            captured_payload = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = StructuredChatClient(
            primary=LLMProviderConfig(
                provider="kimi",
                model="kimi-k2.5",
                api_key="kimi",
                base_url="https://api.moonshot.cn/v1",
                timeout_seconds=30.0,
                max_retries=0,
                retry_backoff_seconds=0.0,
            ),
            fallback=None,
            failover_enabled=False,
            failover_consecutive_failures=3,
            stream_enabled=True,
            stream_steps=("step_5",),
        )
        client.begin_step("step_5")

        with patch("c114.llm_runtime.client.urlopen", side_effect=fake_urlopen):
            payload = client.complete_json(system_prompt="系统", user_prompt="用户")

        self.assertTrue(captured_payload["stream"])
        self.assertEqual(payload["summary"], "一")
