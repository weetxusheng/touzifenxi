import json
import time

from file_comparison.compare.engine import compare_pair_with_llm
from file_comparison.compare.models import PairMatch, Section
from file_comparison.llm.client import OpenAIResponsesClient, ProviderRequestError
from file_comparison.llm.parser import ResponseParseError, parse_response_payload
from file_comparison.llm.providers import build_compare_request_payload, provider_endpoint
from file_comparison.runtime.checkpoint import PairCheckpointStore
from file_comparison.runtime.config import load_file_comparison_runtime_config, write_runtime_config


class StubResponsesClient:
    def __init__(self, responses, providers=None):
        self.responses = list(responses)
        self.calls = 0
        self.providers = tuple(providers or [])

    def build_request_payload(self, *, pair_id, batch, provider_config=None):
        return {
            "pair_id": pair_id,
            "batch_id": batch.batch_id,
            "provider": getattr(provider_config, "provider", ""),
        }

    def write_request(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def provider_chain_for_attempts(self):
        return self.providers

    def post(self, payload, *, provider_config=None):
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    def write_response(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class DelayedRoutingClient(StubResponsesClient):
    def __init__(self, response_factory, providers=None, delay_seconds=0.0):
        super().__init__(responses=[], providers=providers)
        self.response_factory = response_factory
        self.delay_seconds = delay_seconds
        self.provider_calls = []

    def post(self, payload, *, provider_config=None):
        self.calls += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        provider_name = getattr(provider_config, "provider", "")
        self.provider_calls.append((payload["batch_id"], provider_name))
        response = self.response_factory(payload["batch_id"], provider_name, self.calls)
        if isinstance(response, Exception):
            raise response
        return response


def test_parse_response_payload_supports_output_text():
    parsed = parse_response_payload(
        {
            "output_text": json.dumps(
                {
                    "chapters": [
                        {
                            "chapter": "第一部分  前言",
                            "subsections": [
                                {
                                    "subchapter": "一、总则",
                                    "old_text": "旧",
                                    "new_text": "新",
                                    "change_type": "replace",
                                    "numbering_only": False,
                                    "fully_equal_lines": [],
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
    )

    assert parsed["chapters"][0]["chapter"] == "第一部分  前言"


def test_parse_response_payload_strips_think_and_extracts_json():
    parsed = parse_response_payload(
        {
            "output_text": (
                "<think>\n先分析差异\n</think>\n"
                + json.dumps(
                    {
                        "chapters": [
                            {
                                "chapter": "第二部分  释义",
                                "subsections": [
                                    {
                                        "subchapter": "1、基金或本基金：",
                                        "old_text": "旧基金",
                                        "new_text": "新基金",
                                        "change_type": "replace",
                                        "numbering_only": False,
                                        "fully_equal_lines": [],
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        }
    )

    assert parsed["chapters"][0]["chapter"] == "第二部分  释义"


def test_parse_response_payload_unwraps_output_wrapper():
    parsed = parse_response_payload(
        {
            "output_text": json.dumps(
                {
                    "output": {
                        "chapters": [
                            {
                                "chapter": "第三部分  基金的基本情况",
                                "subsections": [
                                    {
                                        "subchapter": "一、基金名称",
                                        "old_text": "旧名称",
                                        "new_text": "新名称",
                                        "change_type": "replace",
                                        "numbering_only": False,
                                        "fully_equal_lines": "完全一致行",
                                    }
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            )
        }
    )

    assert parsed["chapters"][0]["chapter"] == "第三部分  基金的基本情况"
    assert parsed["chapters"][0]["subsections"][0]["fully_equal_lines"] == ["完全一致行"]


def test_parse_response_payload_extracts_json_inside_code_fence():
    parsed = parse_response_payload(
        {
            "output_text": "```json\n"
            + json.dumps(
                {
                    "chapters": [
                        {
                            "chapter": "第四部分  历史沿革",
                            "subsections": [
                                {
                                    "subchapter": "沿革",
                                    "old_text": "旧",
                                    "new_text": "新",
                                    "change_type": "replace",
                                    "numbering_only": False,
                                    "fully_equal_lines": [],
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
            + "\n```",
        }
    )

    assert parsed["chapters"][0]["chapter"] == "第四部分  历史沿革"


def test_parse_response_payload_merges_multiple_json_fragments():
    fragment_one = json.dumps(
        {
            "chapters": [
                {
                    "chapter": "第一部分  前言",
                    "subsections": [
                        {
                            "subchapter": "三、设立方式",
                            "old_text": "旧A",
                            "new_text": "新A",
                            "change_type": "replace",
                            "numbering_only": False,
                            "fully_equal_lines": [],
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    fragment_two = json.dumps(
        {
            "chapters": [
                {
                    "chapter": "第二部分  释义",
                    "subsections": [
                        {
                            "subchapter": "1、基金或本基金：",
                            "old_text": "旧B",
                            "new_text": "新B",
                            "change_type": "replace",
                            "numbering_only": False,
                            "fully_equal_lines": [],
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    parsed = parse_response_payload({"output_text": fragment_one + "\n补充如下：\n" + fragment_two})

    assert len(parsed["chapters"]) == 2


def test_parse_response_payload_raises_parse_error_for_missing_structure():
    try:
        parse_response_payload({"output": []})
    except ResponseParseError as exc:
        assert "structured output" in str(exc)
    else:
        raise AssertionError("expected ResponseParseError")


def test_compare_pair_with_llm_retries_and_logs_files(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 3,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "secret",
                        "base_url": "https://api.minimaxi.com/v1",
                        "max_retries": 3,
                    }
                ],
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    pair_store = PairCheckpointStore.load_or_create(tmp_path / "pair_checkpoint.json", pair_id="pair-001", task_id="task-001")
    pair = PairMatch(
        pair_id="pair-001",
        key="demo",
        old_path=tmp_path / "old.docx",
        new_path=tmp_path / "new.docx",
        old_label="old.docx",
        new_label="new.docx",
    )
    old_sections = [Section(number="第一部分", title="第一部分  前言", body="第一部分  前言\n一、总则\n旧内容")]
    new_sections = [Section(number="第一部分", title="第一部分  前言", body="第一部分  前言\n一、总则\n新内容")]
    client = StubResponsesClient(
        [
            {"output_text": "{bad json"},
            {
                "output_text": json.dumps(
                    {
                        "chapters": [
                            {
                                "chapter": "第一部分  前言",
                                "subsections": [
                                    {
                                        "subchapter": "一、总则",
                                        "old_text": "旧内容",
                                        "new_text": "新内容",
                                        "change_type": "replace",
                                        "numbering_only": False,
                                        "fully_equal_lines": [],
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        ],
        providers=runtime_config.llm.providers,
    )

    rows = compare_pair_with_llm(
        pair=pair,
        old_sections=old_sections,
        new_sections=new_sections,
        llm_dir=tmp_path / "llm",
        runtime_config=runtime_config,
        pair_store=pair_store,
        client=client,
    )

    batch_dir = tmp_path / "llm" / "batch-001"
    assert len(rows) == 1
    assert client.calls == 2
    assert (batch_dir / "request.json").exists()
    assert (batch_dir / "response.json").exists()
    assert (batch_dir / "parsed.json").exists()
    payload = pair_store.payload()
    assert payload["entries"][0]["status"] == "success"
    assert payload["entries"][0]["attempt_count"] == 2


def test_compare_pair_with_llm_switches_provider_after_request_error(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "infra_max_attempts": 1,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "minimax-key",
                        "base_url": "https://api.minimaxi.com/v1",
                    },
                    {
                        "provider": "kimi-code",
                        "model": "kimi-for-coding",
                        "api_key": "kimi-key",
                        "base_url": "https://api.kimi.com/coding",
                    },
                ],
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    pair_store = PairCheckpointStore.load_or_create(tmp_path / "pair_checkpoint.json", pair_id="pair-001", task_id="task-001")
    pair = PairMatch(
        pair_id="pair-001",
        key="demo",
        old_path=tmp_path / "old.docx",
        new_path=tmp_path / "new.docx",
        old_label="old.docx",
        new_label="new.docx",
    )
    old_sections = [Section(number="第一部分", title="第一部分  前言", body="第一部分  前言\n一、总则\n旧内容")]
    new_sections = [Section(number="第一部分", title="第一部分  前言", body="第一部分  前言\n一、总则\n新内容")]
    client = StubResponsesClient(
        [
            ProviderRequestError("minimax", "minimax timeout", infrastructure_error=True, retry_class="infra"),
            {
                "output_text": json.dumps(
                    {
                        "chapters": [
                            {
                                "chapter": "第一部分  前言",
                                "subsections": [
                                    {
                                        "subchapter": "一、总则",
                                        "old_text": "旧内容",
                                        "new_text": "新内容",
                                        "change_type": "replace",
                                        "numbering_only": False,
                                        "fully_equal_lines": [],
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        ],
        providers=runtime_config.llm.providers,
    )

    rows = compare_pair_with_llm(
        pair=pair,
        old_sections=old_sections,
        new_sections=new_sections,
        llm_dir=tmp_path / "llm",
        runtime_config=runtime_config,
        pair_store=pair_store,
        client=client,
    )

    assert len(rows) == 1
    assert client.calls == 2
    payload = pair_store.payload()
    assert payload["entries"][0]["provider"] == "kimi-code"
    assert payload["entries"][0]["attempt_count"] == 2
    assert (tmp_path / "llm" / "batch-001" / "request.attempt-02.kimi-code.json").exists()


def test_provider_endpoint_uses_chat_completions_for_minimax():
    assert provider_endpoint("minimax", "https://api.minimaxi.com/v1") == "https://api.minimaxi.com/v1/chat/completions"
    assert provider_endpoint("openai-responses", "https://api.openai.com") == "https://api.openai.com/v1/responses"
    assert provider_endpoint("kimi-code", "https://api.kimi.com/coding") == "https://api.kimi.com/coding/v1/messages"
    assert provider_endpoint("deepseek-ark", "https://ark.cn-beijing.volces.com/api/v3") == "https://ark.cn-beijing.volces.com/api/v3/responses"


def test_build_request_payload_uses_chat_completions_shape_for_minimax(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "provider": "minimax",
                "model": "MiniMax-M2.7",
                "api_key": "secret",
                "base_url": "https://api.minimaxi.com/v1",
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    client = OpenAIResponsesClient(runtime_config)
    batch = type(
        "Batch",
        (),
        {
            "batch_id": "batch-001",
            "chapter_numbers": ("第一部分",),
            "old_sections": (Section(number="第一部分", title="第一部分  前言", body="旧"),),
            "new_sections": (Section(number="第一部分", title="第一部分  前言", body="新"),),
        },
    )()

    payload = client.build_request_payload(pair_id="pair-001", batch=batch)

    assert payload["model"] == "MiniMax-M2.7"
    assert "messages" in payload
    assert payload["response_format"] == {"type": "json_object"}
    assert "text" not in payload


def test_build_request_payload_uses_messages_shape_for_kimi_code(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "providers": [
                    {
                        "provider": "kimi-code",
                        "model": "kimi-for-coding",
                        "api_key": "secret",
                        "base_url": "https://api.kimi.com/coding",
                    }
                ]
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    client = OpenAIResponsesClient(runtime_config)
    batch = type(
        "Batch",
        (),
        {
            "batch_id": "batch-001",
            "chapter_numbers": ("第一部分",),
            "old_sections": (Section(number="第一部分", title="第一部分  前言", body="旧"),),
            "new_sections": (Section(number="第一部分", title="第一部分  前言", body="新"),),
        },
    )()

    payload = client.build_request_payload(
        pair_id="pair-001",
        batch=batch,
        provider_config=runtime_config.llm.providers[0],
    )

    assert payload["model"] == "kimi-for-coding"
    assert payload["system"]
    assert payload["messages"][0]["role"] == "user"
    assert "response_format" not in payload
    assert "temperature" not in payload


def test_build_request_payload_uses_responses_shape_for_deepseek_ark(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "providers": [
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-20260415114137-55jp7",
                        "api_key": "ark-key",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    }
                ]
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    client = OpenAIResponsesClient(runtime_config)
    batch = type(
        "Batch",
        (),
        {
            "batch_id": "batch-001",
            "chapter_numbers": ("第一部分",),
            "old_sections": (Section(number="第一部分", title="第一部分  前言", body="旧"),),
            "new_sections": (Section(number="第一部分", title="第一部分  前言", body="新"),),
        },
    )()

    payload = client.build_request_payload(
        pair_id="pair-001",
        batch=batch,
        provider_config=runtime_config.llm.providers[0],
    )

    assert payload["model"] == "ep-20260415114137-55jp7"
    assert payload["instructions"]
    assert payload["input"]
    assert payload["text"]["format"]["type"] == "json_schema"


def test_compare_pair_with_llm_routes_batches_round_robin_and_keeps_order(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "chapter_batch_size": 1,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "minimax-key",
                        "base_url": "https://api.minimaxi.com/v1",
                    },
                    {
                        "provider": "kimi-code",
                        "model": "kimi-for-coding",
                        "api_key": "kimi-key",
                        "base_url": "https://api.kimi.com/coding",
                    },
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-20260415114137-55jp7",
                        "api_key": "ark-key",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    },
                ],
                "task_routing": {
                    "enabled": True,
                    "batch_compare": ["minimax", "kimi-code", "deepseek-ark"],
                },
            },
            "execution": {"per_pair_max_workers": 2},
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    pair_store = PairCheckpointStore.load_or_create(tmp_path / "pair_checkpoint.json", pair_id="pair-001", task_id="task-001")
    pair = PairMatch(
        pair_id="pair-001",
        key="demo",
        old_path=tmp_path / "old.docx",
        new_path=tmp_path / "new.docx",
        old_label="old.docx",
        new_label="new.docx",
    )
    old_sections = [
        Section(number="第一部分", title="第一部分  前言", body="第一部分  前言\n一、总则\n旧内容1"),
        Section(number="第二部分", title="第二部分  释义", body="第二部分  释义\n一、定义\n旧内容2"),
        Section(number="第三部分", title="第三部分  基本情况", body="第三部分  基本情况\n一、情况\n旧内容3"),
    ]
    new_sections = [
        Section(number="第一部分", title="第一部分  前言", body="第一部分  前言\n一、总则\n新内容1"),
        Section(number="第二部分", title="第二部分  释义", body="第二部分  释义\n一、定义\n新内容2"),
        Section(number="第三部分", title="第三部分  基本情况", body="第三部分  基本情况\n一、情况\n新内容3"),
    ]

    def response_factory(batch_id, provider_name, _call_index):
        suffix = batch_id.split("-")[-1]
        return {
            "output_text": json.dumps(
                {
                    "chapters": [
                        {
                            "chapter": f"章节{suffix}",
                            "subsections": [
                                {
                                    "subchapter": "一、总则",
                                    "old_text": f"旧内容{suffix}",
                                    "new_text": f"新内容{suffix}",
                                    "change_type": "replace",
                                    "numbering_only": False,
                                    "fully_equal_lines": [],
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }

    client = DelayedRoutingClient(response_factory, providers=runtime_config.llm.providers, delay_seconds=0.2)

    started_at = time.perf_counter()
    rows = compare_pair_with_llm(
        pair=pair,
        old_sections=old_sections,
        new_sections=new_sections,
        llm_dir=tmp_path / "llm",
        runtime_config=runtime_config,
        pair_store=pair_store,
        client=client,
    )
    elapsed = time.perf_counter() - started_at

    assert [row.chapter for row in rows] == ["章节001", "章节002", "章节003"]
    assert dict(client.provider_calls) == {
        "batch-001": "minimax",
        "batch-002": "kimi-code",
        "batch-003": "deepseek-ark",
    }
    assert elapsed < 0.55
