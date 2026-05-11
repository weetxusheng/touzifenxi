import json
import time
from urllib.error import URLError

import pytest

import file_comparison.llm.client as client_module
from file_comparison.compare.engine import (
    _process_llm_batch,
    _write_readable_response_file,
    compare_pair_with_llm,
    normalize_block_operations_payload,
    rows_from_block_operations_payload,
    rows_from_llm_payload,
    validate_complete_batch_results,
)
from file_comparison.compare.models import (
    ChapterBatch,
    CompareBlock,
    CompareBlockItem,
    CompareUnit,
    ComparisonRow,
    PairMatch,
    Section,
)
from file_comparison.compare.postprocess import (
    insert_section_title_change_rows,
    merge_pure_delete_and_add_runs,
    sort_block_operations_old_first,
)
from file_comparison.compare.rerender import output_paths
from file_comparison.llm.client import OpenAIResponsesClient, ProviderRequestError
from file_comparison.llm.parser import ResponseParseError, parse_response_payload
from file_comparison.llm.providers import provider_endpoint
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


class RawAwareStubClient(StubResponsesClient):
    def post_with_raw(self, payload, *, provider_config=None):
        response = self.post(payload, provider_config=provider_config)
        return response, response


class RawSequenceStubClient(StubResponsesClient):
    def post_with_raw(self, payload, *, provider_config=None):
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        if isinstance(response, tuple) and len(response) == 2:
            return response
        return response, response


class CooldownRecordingStubClient(StubResponsesClient):
    def __init__(self, responses, providers=None):
        super().__init__(responses, providers=providers)
        self.unavailable_providers = []

    def record_provider_unavailable(self, provider_config, *, reason=""):
        self.unavailable_providers.append((provider_config.provider, reason))


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


class RealPayloadStubClient(StubResponsesClient):
    def __init__(self, runtime_config, responses):
        super().__init__(responses=responses, providers=runtime_config.llm.providers)
        self.real_client = OpenAIResponsesClient(runtime_config)

    def build_request_payload(self, *, pair_id, batch, provider_config=None):
        return self.real_client.build_request_payload(pair_id=pair_id, batch=batch, provider_config=provider_config)


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


def test_parse_response_payload_supports_block_operations():
    parsed = parse_response_payload(
        {
            "output_text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": "block-001",
                            "chapter": "第七部分  基金合同当事人及权利义务",
                            "parent_path": "基金管理人的义务",
                            "operations": [
                                {
                                    "type": "delete",
                                    "old_item_ids": ["old-024"],
                                    "new_item_ids": [],
                                    "old_focus_text": "（24）基金在募集期间未能达到基金的备案条件……",
                                    "new_focus_text": "",
                                    "confidence": 0.92,
                                    "reason": "该条在新文中被删除，后续条目顺延。",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
    )

    assert parsed["blocks"][0]["block_id"] == "block-001"
    assert parsed["blocks"][0]["operations"][0]["type"] == "delete"
    assert parsed["blocks"][0]["operations"][0]["old_item_ids"] == ["old-024"]


def test_write_readable_response_file_expands_output_text_json(tmp_path):
    path = tmp_path / "response.readable.attempt-01.deepseek.json"
    _write_readable_response_file(
        path,
        {
            "output_text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": "block-001",
                            "chapter": "第二部分",
                            "parent_path": "",
                            "operations": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["partial"] is False
    assert payload["blocks"][0]["id"] == "block-001"
    assert "output_text" not in payload


def test_write_readable_response_file_extracts_partial_blocks_without_wrapping_raw_text(tmp_path):
    path = tmp_path / "response.readable.attempt-01.deepseek.json"
    truncated_output = (
        '{"blocks": ['
        '{"block_id": "block-001", "chapter": "第二部分", "parent_path": "", "operations": []},'
        '{"block_id": "block-002", "chapter": "第二部分", "parent_path": "", "operations": ['
    )

    _write_readable_response_file(path, {"output_text": truncated_output})

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["partial"] is True
    assert payload["blocks"][0]["id"] == "block-001"
    assert "normalized_response" not in payload
    assert "output_text" not in payload


def test_write_readable_response_file_extracts_partial_block_operations(tmp_path):
    path = tmp_path / "response.readable.attempt-01.deepseek.json"
    truncated_output = (
        '{"blocks": ['
        '{"block_id": "block-001", "chapter": "第一部分", "parent_path": "", "operations": []},'
        '{"block_id": "block-002", "chapter": "第二部分", "parent_path": "", "operations": ['
        '{"type": "replace", "old_item_ids": ["old-001"], "new_item_ids": ["new-001"], '
        '"old_focus_text": "旧", "new_focus_text": "新", "confidence": 0.9, "reason": "已完整"},'
        '{"type": "replace", "old_item_ids": ["old-002"], "new_item_ids": ["new-002"], '
        '"old_focus_text": "未闭合"'
    )

    _write_readable_response_file(path, {"output_text": truncated_output})

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["partial"] is True
    assert [block["id"] for block in payload["blocks"]] == ["block-001", "block-002"]
    assert payload["blocks"][1]["ops"] == [
        {
            "t": "replace",
            "o": ["old-001"],
            "n": ["new-001"],
            "old": "旧",
            "new": "新",
            "c": 0.9,
            "r": "已完整",
        }
    ]


def test_rows_from_block_operations_payload_expands_add_delete_and_replace():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第一部分",
        chapter_title="第一部分  前言",
        parent_path="",
        old_items=(
            CompareBlockItem("old-004", "四、基金管理人、基金托管人在本基金合同之外披露涉及本基金的信息，其内容涉及界定基金合同当事人之间权利义务关系的，如与基金合同有冲突，以基金合同为准。"),
            CompareBlockItem("old-005", "五、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。"),
            CompareBlockItem("old-006", "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。"),
        ),
        new_items=(
            CompareBlockItem("new-004", "四、基金管理人、基金托管人在本基金合同之外披露涉及本基金的信息，其内容涉及界定基金合同当事人之间权利义务关系的，如与基金合同有冲突，以基金合同为准。"),
            CompareBlockItem("new-005", "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。"),
            CompareBlockItem("new-006", "六、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第一部分  前言",
                "parent_path": "",
                "operations": [
                    {
                        "type": "add",
                        "old_item_ids": [],
                        "new_item_ids": ["new-005"],
                        "old_focus_text": "",
                        "new_focus_text": "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务",
                        "confidence": 0.98,
                        "reason": "新增一条风险提示。",
                    },
                    {
                        "type": "delete",
                        "old_item_ids": ["old-006"],
                        "new_item_ids": [],
                        "old_focus_text": "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容",
                        "new_focus_text": "",
                        "confidence": 0.97,
                        "reason": "原条款被删除。",
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["old-005"],
                        "new_item_ids": ["new-006"],
                        "old_focus_text": "五、本基金按照中国法律法规成立并运作",
                        "new_focus_text": "六、本基金按照中国法律法规成立并运作",
                        "confidence": 0.91,
                        "reason": "仅发生顺延，内容主体保持一致。",
                    },
                ],
            }
        ]
    }

    rows = rows_from_block_operations_payload(payload, compare_blocks=(source_block,))

    assert [(row.old_text, row.new_text) for row in rows] == [
        ("新增", "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。"),
        ("六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。", "删除"),
    ]


def test_rows_from_block_operations_payload_resolves_replace_across_blocks():
    """replace 引用其它 compare block 中的 new_item_id 时，应用全局 map 回查右侧正文。"""
    block_new_only = CompareBlock(
        block_id="第五部分-block-001",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="",
        old_items=(),
        new_items=(
            CompareBlockItem(
                "第五部分-new-001",
                "《新版基金合同》生效后满三年…并在6个月内召集基金份额持有人大会。",
            ),
        ),
    )
    block_old_only = CompareBlock(
        block_id="第五部分-block-004",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="三、基金存续期内的基金份额持有人数量和资产规模",
        old_items=(
            CompareBlockItem(
                "第五部分-old-006",
                "《基金合同》生效后满三年…并在6个月内召开基金份额持有人大会进行表决。",
            ),
        ),
        new_items=(),
    )
    payload = {
        "blocks": [
            {
                "block_id": "第五部分-block-004",
                "chapter": "第五部分  基金备案",
                "parent_path": "三、基金存续期内的基金份额持有人数量和资产规模",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["第五部分-old-006"],
                        "new_item_ids": ["第五部分-new-001"],
                        "old_focus_text": "",
                        "new_focus_text": "",
                        "confidence": 0.9,
                        "reason": "跨块引用 new-001。",
                    }
                ],
            }
        ]
    }
    rows = rows_from_block_operations_payload(
        payload, compare_blocks=(block_new_only, block_old_only)
    )
    assert len(rows) == 1
    assert "《基金合同》" in rows[0].old_text
    assert "《新版基金合同》" in rows[0].new_text
    assert rows[0].new_text != "删除"


def test_rows_from_block_operations_payload_merges_consecutive_deletes_same_subchapter():
    b = CompareBlock(
        block_id="b1",
        chapter_number="第七部分",
        chapter_title="第七部分  示例",
        parent_path="一、基金管理人",
        old_items=(
            CompareBlockItem("old-a", "甲段"),
            CompareBlockItem("old-b", "乙段"),
        ),
        new_items=(),
    )
    payload = {
        "blocks": [
            {
                "block_id": "b1",
                "operations": [
                    {
                        "type": "delete",
                        "old_item_ids": ["old-a"],
                        "new_item_ids": [],
                    },
                    {
                        "type": "delete",
                        "old_item_ids": ["old-b"],
                        "new_item_ids": [],
                    },
                ],
            }
        ]
    }
    rows = rows_from_block_operations_payload(payload, compare_blocks=(b,))
    assert len(rows) == 1
    assert rows[0].old_text == "一、基金管理人\n甲段\n乙段"
    assert rows[0].subchapter == ""
    assert rows[0].new_text == "删除"


def test_rows_from_llm_payload_drops_add_when_replace_already_uses_new_item_id():
    """模型同时给根下 add 与小标题下 replace（同一 new_item_id）时，不应再保留「无|新增」重复行。"""
    block_root = CompareBlock(
        block_id="第五部分-block-001",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="",
        old_items=(),
        new_items=(CompareBlockItem("第五部分-new-001", "新版条款全文"),),
    )
    block_rep = CompareBlock(
        block_id="第五部分-block-004",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="三、存续",
        old_items=(CompareBlockItem("第五部分-old-006", "旧版条款全文"),),
        new_items=(CompareBlockItem("第五部分-new-001", "新版条款全文"),),
    )
    compare_blocks = (block_root, block_rep)
    payload = {
        "blocks": [
            {
                "block_id": "第五部分-block-001",
                "chapter": "第五部分  基金备案",
                "operations": [
                    {"type": "add", "old_item_ids": [], "new_item_ids": ["第五部分-new-001"]},
                ],
            },
            {
                "block_id": "第五部分-block-004",
                "chapter": "第五部分  基金备案",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["第五部分-old-006"],
                        "new_item_ids": ["第五部分-new-001"],
                    }
                ],
            },
        ]
    }
    rows = rows_from_llm_payload(payload, compare_blocks=compare_blocks)
    fifth = [r for r in rows if r.chapter == "第五部分  基金备案"]
    assert len(fifth) == 1
    assert fifth[0].old_text != "新增"
    assert "旧版" in fifth[0].old_text or "旧版条款" in fifth[0].old_text
    assert "新版" in fifth[0].new_text


def test_rows_from_llm_payload_coerces_root_add_and_similar_delete_to_replace():
    """根块 add 与另一块 delete 正文高度重合时，应合并为 replace，且连续删除不含「三」段。"""
    block_root_new = CompareBlock(
        block_id="第五部分-block-001",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="",
        old_items=(),
        new_items=(CompareBlockItem("第五部分-new-001", "《新版合同》条款正文甲乙丙丁"),),
    )
    block_del1 = CompareBlock(
        block_id="第五部分-block-002",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="一、条件",
        old_items=(CompareBlockItem("第五部分-old-001", "旧侧一段"),),
        new_items=(),
    )
    block_del2 = CompareBlock(
        block_id="第五部分-block-003",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="二、处理",
        old_items=(CompareBlockItem("第五部分-old-002", "旧侧二段"),),
        new_items=(),
    )
    block_del3 = CompareBlock(
        block_id="第五部分-block-004",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="三、存续",
        old_items=(CompareBlockItem("第五部分-old-006", "《基金合同》条款正文甲乙丙丁"),),
        new_items=(),
    )
    compare_blocks = (block_root_new, block_del1, block_del2, block_del3)
    payload = {
        "blocks": [
            {
                "block_id": "第五部分-block-001",
                "chapter": "第五部分  基金备案",
                "operations": [
                    {
                        "type": "add",
                        "old_item_ids": [],
                        "new_item_ids": ["第五部分-new-001"],
                    }
                ],
            },
            {
                "block_id": "第五部分-block-002",
                "chapter": "第五部分  基金备案",
                "operations": [
                    {"type": "delete", "old_item_ids": ["第五部分-old-001"], "new_item_ids": []},
                ],
            },
            {
                "block_id": "第五部分-block-003",
                "chapter": "第五部分  基金备案",
                "operations": [
                    {"type": "delete", "old_item_ids": ["第五部分-old-002"], "new_item_ids": []},
                ],
            },
            {
                "block_id": "第五部分-block-004",
                "chapter": "第五部分  基金备案",
                "operations": [
                    {"type": "delete", "old_item_ids": ["第五部分-old-006"], "new_item_ids": []},
                ],
            },
        ]
    }
    rows = rows_from_llm_payload(payload, compare_blocks=compare_blocks)
    fifth = [r for r in rows if r.chapter == "第五部分  基金备案"]
    assert len(fifth) == 2
    deletes = [r for r in fifth if r.new_text == "删除"]
    replaces = [r for r in fifth if r.new_text not in {"删除", "新增"} and r.old_text not in {"新增", "删除"}]
    assert len(deletes) == 1
    assert "一、条件" in deletes[0].old_text and "二、处理" in deletes[0].old_text
    assert "三、存续" not in deletes[0].old_text
    assert len(replaces) == 1
    assert "《基金合同》" in replaces[0].old_text
    assert "《新版合同》" in replaces[0].new_text


def test_rows_from_block_operations_payload_merges_similar_delete_add_alongside_replace():
    """同块内除多条 replace 外仅一条 delete、一条 add，且删增正文高度相似时，仍应并成一行（与第四部分标题+正文同块场景一致）。"""
    block = CompareBlock(
        block_id="第四部分-block-001",
        chapter_number="第四部分",
        chapter_title="第四部分  基金份额的发售",
        parent_path="",
        old_items=(
            CompareBlockItem("第四部分-old-001", "第四部分  基金份额的发售"),
            CompareBlockItem("第四部分-old-002", "一、下面某条款旧文"),
        ),
        new_items=(
            CompareBlockItem("第四部分-new-001", "第四部分  基金份额的发售历史沿革"),
            CompareBlockItem("第四部分-new-002", "一、下面某条款新文"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "第四部分-block-001",
                "chapter": "第四部分  基金份额的发售",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["第四部分-old-002"],
                        "new_item_ids": ["第四部分-new-002"],
                        "confidence": 0.9,
                        "reason": "正文修订",
                    },
                    {
                        "type": "delete",
                        "old_item_ids": ["第四部分-old-001"],
                        "new_item_ids": [],
                        "confidence": 0.9,
                    },
                    {
                        "type": "add",
                        "old_item_ids": [],
                        "new_item_ids": ["第四部分-new-001"],
                        "confidence": 0.9,
                    },
                ],
            }
        ]
    }
    rows = rows_from_block_operations_payload(payload, compare_blocks=(block,))
    assert len(rows) == 2
    titles = [r for r in rows if "历史沿革" in r.new_text]
    assert len(titles) == 1
    assert "发售" in titles[0].old_text and titles[0].old_text != "新增"
    bodies = [r for r in rows if "条款新文" in r.new_text]
    assert len(bodies) == 1
    assert "条款旧文" in bodies[0].old_text


def test_rows_from_llm_payload_merges_same_block_lone_delete_add_to_single_row():
    """同块内仅一条 delete、一条 add、无 replace，且两侧正文相似度够高时，应对照为一行（左右正文）。"""
    block = CompareBlock(
        block_id="第四部分-block-001",
        chapter_number="第四部分",
        chapter_title="第四部分  基金份额的发售",
        parent_path="",
        old_items=(CompareBlockItem("old-ch", "第四部分  基金份额的发售"),),
        new_items=(CompareBlockItem("new-ch", "第四部分  基金份额的发售历史沿革"),),
    )
    payload = {
        "blocks": [
            {
                "block_id": "第四部分-block-001",
                "chapter": "第四部分  基金份额的发售",
                "parent_path": "",
                "operations": [
                    {
                        "type": "delete",
                        "old_item_ids": ["old-ch"],
                        "new_item_ids": [],
                        "confidence": 0.9,
                        "reason": "标题删除",
                    },
                    {
                        "type": "add",
                        "old_item_ids": [],
                        "new_item_ids": ["new-ch"],
                        "confidence": 0.9,
                        "reason": "标题新增",
                    },
                ],
            }
        ]
    }
    rows = rows_from_llm_payload(payload, compare_blocks=(block,))
    assert len(rows) == 1
    assert rows[0].old_text not in {"新增", "删除"}
    assert rows[0].new_text not in {"新增", "删除"}
    assert "发售" in rows[0].old_text
    assert "历史沿革" in rows[0].new_text


def test_sort_block_operations_old_first_orders_by_minimum_old_index():
    block = CompareBlock(
        block_id="第七部分-block-001",
        chapter_number="第七部分",
        chapter_title="第七部分  示例",
        parent_path="",
        old_items=(
            CompareBlockItem("第七部分-old-001", "先"),
            CompareBlockItem("第七部分-old-002", "后"),
        ),
        new_items=(
            CompareBlockItem("第七部分-new-001", "先新"),
            CompareBlockItem("第七部分-new-002", "后新"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "第七部分-block-001",
                "chapter": "第七部分  示例",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["第七部分-old-002"],
                        "new_item_ids": ["第七部分-new-002"],
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["第七部分-old-001"],
                        "new_item_ids": ["第七部分-new-001"],
                    },
                ],
            }
        ]
    }
    sort_block_operations_old_first(payload, compare_blocks=(block,))
    ordered = payload["blocks"][0]["operations"]
    assert [op["old_item_ids"][0] for op in ordered] == ["第七部分-old-001", "第七部分-old-002"]


def test_sort_block_operations_old_first_pure_add_uses_new_order():
    block = CompareBlock(
        block_id="b-add",
        chapter_number="章",
        chapter_title="章  题",
        parent_path="",
        old_items=(),
        new_items=(
            CompareBlockItem("n1", "甲"),
            CompareBlockItem("n2", "乙"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "b-add",
                "operations": [
                    {"type": "add", "old_item_ids": [], "new_item_ids": ["n2"]},
                    {"type": "add", "old_item_ids": [], "new_item_ids": ["n1"]},
                ],
            }
        ]
    }
    sort_block_operations_old_first(payload, compare_blocks=(block,))
    assert [op["new_item_ids"][0] for op in payload["blocks"][0]["operations"]] == ["n1", "n2"]


def test_merge_pure_delete_and_add_runs_combines_consecutive_delete_and_add():
    ch = "第四部分  基金份额的发售"
    rows = [
        ComparisonRow(chapter=ch, subchapter="一、节", old_text="旧段A", new_text="删除"),
        ComparisonRow(chapter=ch, subchapter="", old_text="新增", new_text="新说明B"),
    ]
    out = merge_pure_delete_and_add_runs(rows)
    assert len(out) == 1
    assert "旧段A" in out[0].old_text
    assert "新说明B" in out[0].new_text
    assert out[0].new_text != "删除"
    assert out[0].old_text != "新增"


def test_merge_pure_delete_and_add_runs_inserts_after_title_row_still_merges_body():
    ch = "第四部分  基金份额的发售"
    rows = [
        ComparisonRow(chapter=ch, subchapter="", old_text=ch, new_text=ch + "历史沿革"),
        ComparisonRow(chapter=ch, subchapter="一、小节", old_text="删文", new_text="删除"),
        ComparisonRow(chapter=ch, subchapter="", old_text="新增", new_text="增文"),
    ]
    out = merge_pure_delete_and_add_runs(rows)
    assert len(out) == 2
    assert "历史沿革" in out[0].new_text
    assert "删文" in out[1].old_text and "增文" in out[1].new_text


def test_merge_pure_delete_and_add_runs_does_not_span_replace_like_row():
    ch = "示例章"
    rows = [
        ComparisonRow(chapter=ch, subchapter="", old_text="删1", new_text="删除"),
        ComparisonRow(chapter=ch, subchapter="", old_text="左替", new_text="右替"),
        ComparisonRow(chapter=ch, subchapter="", old_text="新增", new_text="增"),
    ]
    out = merge_pure_delete_and_add_runs(rows)
    assert len(out) == 3


def test_insert_section_title_change_rows_prepends_when_old_new_titles_differ():
    old_sections = [Section("第四部分", "第四部分  基金份额的发售", "body")]
    new_sections = [Section("第四部分", "第四部分  基金份额的发售历史沿革", "body")]
    rows = [
        ComparisonRow(chapter="第四部分  基金份额的发售", subchapter="一、小节", old_text="正文左", new_text="正文右"),
    ]
    out = insert_section_title_change_rows(rows, old_sections=old_sections, new_sections=new_sections)
    assert len(out) == 2
    assert out[0].subchapter == ""
    assert out[0].old_text == "第四部分  基金份额的发售"
    assert out[0].new_text == "第四部分  基金份额的发售历史沿革"
    assert out[1].old_text == "正文左"


def test_insert_section_title_change_rows_skips_when_row_already_has_title_pair():
    title_old = "第四部分  基金份额的发售"
    title_new = "第四部分  基金份额的发售历史沿革"
    old_sections = [Section("第四部分", title_old, "body")]
    new_sections = [Section("第四部分", title_new, "body")]
    rows = [
        ComparisonRow(chapter=title_old, subchapter="", old_text=title_old, new_text=title_new),
        ComparisonRow(chapter=title_old, subchapter="一、", old_text="a", new_text="b"),
    ]
    out = insert_section_title_change_rows(rows, old_sections=old_sections, new_sections=new_sections)
    assert len(out) == 2


def test_rows_from_block_operations_payload_skips_merging_lone_delete_add_when_dissimilar():
    """单删+单增但正文相似度不足时不合并，避免误把两条无关操作挤成一行。"""
    block = CompareBlock(
        block_id="b1",
        chapter_number="示例",
        chapter_title="示例章",
        parent_path="",
        old_items=(CompareBlockItem("o1", "完全不同的旧文案甲"),),
        new_items=(CompareBlockItem("n1", "完全不同的新文案乙"),),
    )
    payload = {
        "blocks": [
            {
                "block_id": "b1",
                "chapter": "示例章",
                "operations": [
                    {"type": "delete", "old_item_ids": ["o1"], "new_item_ids": []},
                    {"type": "add", "old_item_ids": [], "new_item_ids": ["n1"]},
                ],
            }
        ]
    }
    rows = rows_from_block_operations_payload(payload, compare_blocks=(block,))
    assert len(rows) == 2
    assert rows[0].new_text == "删除"
    assert rows[1].old_text == "新增"


def test_rows_from_block_operations_payload_merges_consecutive_deletes_same_chapter_different_subchapter():
    b1 = CompareBlock(
        block_id="第五部分-block-002",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="一、基金备案的条件",
        old_items=(CompareBlockItem("o1", "旧一段"),),
        new_items=(),
    )
    b2 = CompareBlock(
        block_id="第五部分-block-003",
        chapter_number="第五部分",
        chapter_title="第五部分  基金备案",
        parent_path="二、基金合同不能生效时募集资金的处理方式",
        old_items=(CompareBlockItem("o2", "旧二段"),),
        new_items=(),
    )
    payload = {
        "blocks": [
            {
                "block_id": "第五部分-block-002",
                "operations": [
                    {"type": "delete", "old_item_ids": ["o1"], "new_item_ids": []},
                ],
            },
            {
                "block_id": "第五部分-block-003",
                "operations": [
                    {"type": "delete", "old_item_ids": ["o2"], "new_item_ids": []},
                ],
            },
        ]
    }
    rows = rows_from_block_operations_payload(payload, compare_blocks=(b1, b2))
    assert len(rows) == 1
    assert "一、基金备案的条件" in rows[0].old_text
    assert "二、基金合同不能生效时募集资金的处理方式" in rows[0].old_text
    assert rows[0].new_text == "删除"


def test_rows_from_block_operations_payload_keeps_ellipsis_for_equal_middle_lines():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第三部分",
        chapter_title="第三部分  基金管理人",
        parent_path="一、基本情况",
        old_items=(
            CompareBlockItem(
                "old-001",
                "\n".join(
                    [
                        "设立日期：2014年7月9日",
                        "组织形式：有限责任公司",
                        "注册资本：2.33亿元人民币",
                        "存续期限：持续经营",
                        "联系电话：0755-23838000",
                    ]
                ),
            ),
        ),
        new_items=(
            CompareBlockItem(
                "new-001",
                "\n".join(
                    [
                        "设立日期：2014年7月9日",
                        "组织形式：股份有限公司",
                        "注册资本：2.33亿元人民币",
                        "存续期限：持续经营",
                        "联系电话：0755-23839000",
                    ]
                ),
            ),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第三部分",
                "parent_path": "一、基本情况",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["old-001"],
                        "new_item_ids": ["new-001"],
                        "old_focus_text": "组织形式：有限责任公司",
                        "new_focus_text": "组织形式：股份有限公司",
                        "confidence": 1.0,
                        "reason": "中间有未变化内容。",
                    }
                ],
            }
        ]
    }

    rows = rows_from_block_operations_payload(payload, compare_blocks=(source_block,))

    assert rows[0].old_text == "组织形式：有限责任公司\n......\n联系电话：0755-23838000"
    assert rows[0].new_text == "组织形式：股份有限公司\n......\n联系电话：0755-23839000"


def test_normalize_block_operations_payload_repairs_shifted_item_misreplace():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第一部分",
        chapter_title="第一部分  前言",
        parent_path="",
        old_items=(
            CompareBlockItem("old-004", "四、基金管理人、基金托管人在本基金合同之外披露涉及本基金的信息，其内容涉及界定基金合同当事人之间权利义务关系的，如与基金合同有冲突，以基金合同为准。"),
            CompareBlockItem("old-005", "五、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。"),
            CompareBlockItem("old-006", "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。"),
        ),
        new_items=(
            CompareBlockItem("new-004", "四、基金管理人、基金托管人在本基金合同之外披露涉及本基金的信息，其内容涉及界定基金合同当事人之间权利义务关系的，如与基金合同有冲突，以基金合同为准。"),
            CompareBlockItem("new-005", "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。"),
            CompareBlockItem("new-006", "六、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第一部分  前言",
                "parent_path": "",
                "operations": [
                    {
                        "type": "delete",
                        "old_item_ids": ["old-006"],
                        "new_item_ids": [],
                        "old_focus_text": "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。",
                        "new_focus_text": "",
                        "confidence": 1.0,
                        "reason": "旧版第六项关于基金产品资料概要的内容已被删除。",
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["old-005"],
                        "new_item_ids": ["new-005"],
                        "old_focus_text": "五、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。",
                        "new_focus_text": "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。",
                        "confidence": 1.0,
                        "reason": "模型误判为替换。",
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["old-006"],
                        "new_item_ids": ["new-006"],
                        "old_focus_text": "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。",
                        "new_focus_text": "六、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的规定为准。",
                        "confidence": 1.0,
                        "reason": "模型误判为替换。",
                    },
                ],
            }
        ]
    }

    normalized = normalize_block_operations_payload(payload, compare_blocks=(source_block,))
    rows = rows_from_block_operations_payload(normalized, compare_blocks=(source_block,))

    assert [(row.old_text, row.new_text) for row in rows] == [
        ("六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。", "删除"),
        ("新增", "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。"),
    ]


def test_normalize_block_operations_payload_splits_replace_crossed_by_unreported_shift():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第一部分",
        chapter_title="第一部分  前言",
        parent_path="",
        old_items=(
            CompareBlockItem("old-011", "四、基金管理人、基金托管人在本基金合同之外披露涉及本基金的信息，其内容涉及界定基金合同当事人之间权利义务关系的，如与基金合同有冲突，以基金合同为准。"),
            CompareBlockItem("old-012", "五、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。"),
            CompareBlockItem("old-013", "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。"),
        ),
        new_items=(
            CompareBlockItem("new-014", "四、基金管理人、基金托管人在本基金合同之外披露涉及本基金的信息，其内容涉及界定基金合同当事人之间权利义务关系的，如与基金合同有冲突，以基金合同为准。"),
            CompareBlockItem("new-015", "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。"),
            CompareBlockItem("new-016", "六、本基金按照中国法律法规成立并运作，若基金合同的内容与届时有效的法律法规的强制性规定不一致，应当以届时有效的法律法规的规定为准。"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第一部分  前言",
                "parent_path": "",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["old-013"],
                        "new_item_ids": ["new-015"],
                        "old_focus_text": "六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。",
                        "new_focus_text": "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。",
                        "confidence": 1.0,
                        "reason": "模型误判为替换，并漏报旧五到新六的编号顺延。",
                    }
                ],
            }
        ]
    }

    normalized = normalize_block_operations_payload(payload, compare_blocks=(source_block,))
    rows = rows_from_block_operations_payload(normalized, compare_blocks=(source_block,))

    assert [operation["type"] for operation in normalized["blocks"][0]["operations"]] == ["delete", "add"]
    assert [(row.old_text, row.new_text) for row in rows] == [
        ("六、本基金合同关于基金产品资料概要的编制、披露及更新等内容，将不晚于2020年9月1日起执行。", "删除"),
        ("新增", "五、本基金可根据法律法规和基金合同的约定参与转融通证券出借业务，可能存在流动性风险、市场风险和信用风险等转融通业务特有风险。"),
    ]


def test_normalize_block_operations_payload_repairs_item_ids_from_focus_text():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        parent_path="",
        old_items=(
            CompareBlockItem("old-015", "14、中国证监会：指中国证券监督管理委员会"),
            CompareBlockItem("old-016", "15、银行业监督管理机构：指中国人民银行和/或中国银行业监督管理委员会"),
            CompareBlockItem("old-019", "18、机构投资者：指依法可以投资证券投资基金的、在中华人民共和国境内合法登记并存续或经有关政府部门批准设立并存续的企业法人、事业法人、社会团体或其他组织"),
            CompareBlockItem("old-020", "19、合格境外机构投资者：指符合相关法律法规规定可以投资于在中国境内依法募集的证券投资基金的中国境外的机构投资者"),
            CompareBlockItem("old-051", "50、基金资产估值：指计算评估基金资产和负债的价值，以确定基金资产净值和基金份额净值的过程"),
            CompareBlockItem("old-052", "51、指定媒介：指中国证监会指定的用以进行信息披露的全国性报刊及指定互联网网站（包括基金管理人网站、基金托管人网站、中国证监会基金电子披露网站）等媒介"),
        ),
        new_items=(
            CompareBlockItem("new-076", "14、中国证监会：指中国证券监督管理委员会"),
            CompareBlockItem("new-077", "15、银行业监督管理机构：指中国人民银行和/或中国银行保险监督管理委员会"),
            CompareBlockItem("new-080", "18、机构投资者：指依法可以投资证券投资基金的、在中华人民共和国境内合法登记并存续或经有关政府部门批准设立并存续的企业法人、事业法人、社会团体或其他组织"),
            CompareBlockItem("new-081", "19、合格境外机构投资者：指符合《合格境外机构投资者境内证券投资管理办法》（包括其不时修订）及相关法律法规规定可以投资于在中国境内依法募集的证券投资基金的中国境外的机构投资者"),
            CompareBlockItem("new-114", "52、规定媒介：指符合中国证监会规定条件的用以进行信息披露的全国性报刊及《信息披露办法》规定的互联网网站（包括基金管理人网站、基金托管人网站、中国证监会基金电子披露网站）等媒介"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第二部分  释义",
                "parent_path": "",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["old-015"],
                        "new_item_ids": ["new-077"],
                        "old_focus_text": "15、银行业监督管理机构：指中国人民银行和/或中国银行业监督管理委员会",
                        "new_focus_text": "15、银行业监督管理机构：指中国人民银行和/或中国银行保险监督管理委员会",
                        "confidence": 1.0,
                        "reason": "模型把可见编号误当成 old item_id。",
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["old-019"],
                        "new_item_ids": ["new-081"],
                        "old_focus_text": "19、合格境外机构投资者：指符合相关法律法规规定可以投资于在中国境内依法募集的证券投资基金的中国境外的机构投资者",
                        "new_focus_text": "19、合格境外机构投资者：指符合《合格境外机构投资者境内证券投资管理办法》（包括其不时修订）及相关法律法规规定可以投资于在中国境内依法募集的证券投资基金的中国境外的机构投资者",
                        "confidence": 1.0,
                        "reason": "模型把可见编号误当成 old item_id。",
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["old-051"],
                        "new_item_ids": ["new-114"],
                        "old_focus_text": "52、指定媒介：指中国证监会指定的用以进行信息披露的全国性报刊及指定互联网网站（包括基金管理人网站、基金托管人网站、中国证监会基金电子披露网站）等媒介",
                        "new_focus_text": "52、规定媒介：指符合中国证监会规定条件的用以进行信息披露的全国性报刊及《信息披露办法》规定的互联网网站（包括基金管理人网站、基金托管人网站、中国证监会基金电子披露网站）等媒介",
                        "confidence": 1.0,
                        "reason": "模型把可见编号误当成 old item_id，且 focus 编号也顺延。",
                    },
                ],
            }
        ]
    }

    normalized = normalize_block_operations_payload(payload, compare_blocks=(source_block,))
    rows = rows_from_block_operations_payload(normalized, compare_blocks=(source_block,))

    assert rows[0].old_text == "15、银行业监督管理机构：指中国人民银行和/或中国银行业监督管理委员会"
    assert rows[0].new_text == "15、银行业监督管理机构：指中国人民银行和/或中国银行保险监督管理委员会"
    assert rows[1].old_text == "19、合格境外机构投资者：指符合相关法律法规规定可以投资于在中国境内依法募集的证券投资基金的中国境外的机构投资者"
    assert rows[1].new_text == (
        "19、合格境外机构投资者：指符合《合格境外机构投资者境内证券投资管理办法》（包括其不时修订）及相关法律法规规定"
        "可以投资于在中国境内依法募集的证券投资基金的中国境外的机构投资者"
    )
    assert rows[2].old_text == "51、指定媒介：指中国证监会指定的用以进行信息披露的全国性报刊及指定互联网网站（包括基金管理人网站、基金托管人网站、中国证监会基金电子披露网站）等媒介"
    assert rows[2].new_text == "52、规定媒介：指符合中国证监会规定条件的用以进行信息披露的全国性报刊及《信息披露办法》规定的互联网网站（包括基金管理人网站、基金托管人网站、中国证监会基金电子披露网站）等媒介"


def test_normalize_block_operations_payload_fills_missing_uncovered_delete():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        parent_path="",
        old_items=(
            CompareBlockItem("old-007", "6、招募说明书：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）招募说明书》及其更新"),
            CompareBlockItem("old-008", "7、基金份额发售公告：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金份额发售公告》"),
            CompareBlockItem("old-009", "8、基金产品资料概要：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金产品资料概要》及其更新"),
        ),
        new_items=(
            CompareBlockItem("new-081", "6、招募说明书：指《创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）招募说明书》及其更新"),
            CompareBlockItem("new-082", "7、基金产品资料概要：指《创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）基金产品资料概要》及其更新"),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第二部分  释义",
                "parent_path": "",
                "operations": [
                    {
                        "type": "replace",
                        "old_item_ids": ["old-007"],
                        "new_item_ids": ["new-081"],
                        "old_focus_text": "6、招募说明书",
                        "new_focus_text": "6、招募说明书",
                        "confidence": 0.95,
                        "reason": "基金名称从3个月改为6个月。",
                    },
                    {
                        "type": "replace",
                        "old_item_ids": ["old-009"],
                        "new_item_ids": ["new-082"],
                        "old_focus_text": "8、基金产品资料概要",
                        "new_focus_text": "7、基金产品资料概要",
                        "confidence": 0.95,
                        "reason": "同名定义项编号变化且基金名称从3个月改为6个月。",
                    },
                ],
            }
        ]
    }

    normalized = normalize_block_operations_payload(payload, compare_blocks=(source_block,))
    rows = rows_from_block_operations_payload(normalized, compare_blocks=(source_block,))

    assert [operation["type"] for operation in normalized["blocks"][0]["operations"]] == ["replace", "delete", "replace"]
    assert rows[1].old_text == "7、基金份额发售公告：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金份额发售公告》"
    assert rows[1].new_text == "删除"
    assert rows[2].old_text == "8、基金产品资料概要：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金产品资料概要》及其更新"
    assert rows[2].new_text == "7、基金产品资料概要：指《创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）基金产品资料概要》及其更新"


def test_normalize_block_operations_payload_collapses_identical_add_delete_into_renumber_only():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        parent_path="",
        old_items=(
            CompareBlockItem(
                "old-029",
                "28、基金合同生效日：指基金募集达到法律法规规定及基金合同规定的条件，基金管理人向中国证监会办理基金备案手续完毕，并获得中国证监会书面确认的日期",
            ),
            CompareBlockItem(
                "old-030",
                "29、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
            ),
        ),
        new_items=(
            CompareBlockItem(
                "new-091",
                "29、基金合同生效日：指基金募集达到法律法规规定及基金合同规定的条件，基金管理人向中国证监会办理基金备案手续完毕，并获得中国证监会书面确认的日期",
            ),
            CompareBlockItem(
                "new-092",
                "30、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
            ),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001",
                "chapter": "第二部分  释义",
                "parent_path": "",
                "operations": [
                    {
                        "type": "renumber_only",
                        "old_item_ids": ["old-029"],
                        "new_item_ids": ["new-091"],
                        "old_focus_text": "28、基金合同生效日",
                        "new_focus_text": "29、基金合同生效日",
                        "confidence": 1.0,
                        "reason": "内容完全相同，仅编号顺延",
                    },
                    {
                        "type": "delete",
                        "old_item_ids": ["old-030"],
                        "new_item_ids": [],
                        "old_focus_text": "29、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
                        "new_focus_text": "",
                        "confidence": 1.0,
                        "reason": "旧版有该条，新版没有对应条目",
                    },
                    {
                        "type": "add",
                        "old_item_ids": [],
                        "new_item_ids": ["new-092"],
                        "old_focus_text": "",
                        "new_focus_text": "30、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
                        "confidence": 1.0,
                        "reason": "新增定义，旧版无此条目",
                    },
                ],
            }
        ]
    }

    normalized = normalize_block_operations_payload(payload, compare_blocks=(source_block,))
    operations = normalized["blocks"][0]["operations"]

    assert [operation["type"] for operation in operations] == ["renumber_only", "renumber_only"]
    assert operations[1]["old_item_ids"] == ["old-030"]
    assert operations[1]["new_item_ids"] == ["new-092"]
    rows = rows_from_block_operations_payload(normalized, compare_blocks=(source_block,))
    assert rows == []


def test_normalize_block_operations_payload_collapses_identical_add_delete_across_split_parts():
    source_block = CompareBlock(
        block_id="block-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        parent_path="",
        old_items=(
            CompareBlockItem(
                "old-029",
                "28、基金合同生效日：指基金募集达到法律法规规定及基金合同规定的条件，基金管理人向中国证监会办理基金备案手续完毕，并获得中国证监会书面确认的日期",
            ),
            CompareBlockItem(
                "old-030",
                "29、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
            ),
        ),
        new_items=(
            CompareBlockItem(
                "new-091",
                "29、基金合同生效日：指基金募集达到法律法规规定及基金合同规定的条件，基金管理人向中国证监会办理基金备案手续完毕，并获得中国证监会书面确认的日期",
            ),
            CompareBlockItem(
                "new-092",
                "30、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
            ),
        ),
    )
    payload = {
        "blocks": [
            {
                "block_id": "block-001-part-001",
                "chapter": "第二部分  释义",
                "parent_path": "",
                "operations": [
                    {
                        "type": "renumber_only",
                        "old_item_ids": ["old-029"],
                        "new_item_ids": ["new-091"],
                        "old_focus_text": "28、基金合同生效日",
                        "new_focus_text": "29、基金合同生效日",
                        "confidence": 1.0,
                        "reason": "内容完全相同，仅编号顺延",
                    },
                    {
                        "type": "delete",
                        "old_item_ids": ["old-030"],
                        "new_item_ids": [],
                        "old_focus_text": "29、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
                        "new_focus_text": "",
                        "confidence": 1.0,
                        "reason": "旧版有该条，新版没有对应条目",
                    },
                ],
            },
            {
                "block_id": "block-001-part-002",
                "chapter": "第二部分  释义",
                "parent_path": "",
                "operations": [
                    {
                        "type": "add",
                        "old_item_ids": [],
                        "new_item_ids": ["new-092"],
                        "old_focus_text": "",
                        "new_focus_text": "30、基金合同终止日：指基金合同规定的基金合同终止事由出现后，基金财产清算完毕，清算结果报中国证监会备案并予以公告的日期",
                        "confidence": 1.0,
                        "reason": "新增定义，旧版无此条目",
                    }
                ],
            },
        ]
    }

    normalized = normalize_block_operations_payload(payload, compare_blocks=(source_block,))
    first_ops = normalized["blocks"][0]["operations"]
    second_ops = normalized["blocks"][1]["operations"]

    assert [operation["type"] for operation in first_ops] == ["renumber_only", "renumber_only"]
    assert second_ops == []
    rows = rows_from_block_operations_payload(normalized, compare_blocks=(source_block,))
    assert rows == []


def test_output_paths_overwrite_ignores_office_temp_docx(tmp_path):
    pair = PairMatch(
        pair_id="pair-001",
        key="demo",
        old_path=tmp_path / "old.docx",
        new_path=tmp_path / "new.docx",
        old_label="old.docx",
        new_label="new.docx",
    )
    output_dir = tmp_path / "pair-001" / "outputs"
    output_dir.mkdir(parents=True)
    temp_docx = output_dir / ".~temp.docx"
    real_docx = output_dir / "real.docx"
    real_doc = output_dir / "real.doc"
    temp_docx.write_text("temp", encoding="utf-8")
    real_docx.write_text("docx", encoding="utf-8")
    real_doc.write_text("doc", encoding="utf-8")

    docx_path, doc_path = output_paths(output_dir.parent, pair=pair, overwrite=True)

    assert docx_path == real_docx
    assert doc_path == real_doc


def test_rows_from_unit_decisions_keeps_full_source_context_when_focus_text_exists():
    source_unit = CompareUnit(
        unit_id="unit-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        subchapter="4、基金合同、《基金合同》或本基金合同、本合同：",
        old_text="4、基金合同、《基金合同》或本基金合同、本合同：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金合同》及对本基金合同的任何有效修订和补充",
        new_text="4、基金合同、《基金合同》或本基金合同、本合同：指《创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）基金合同》及对本基金合同的任何有效修订和补充",
    )
    payload = {
        "units": [
            {
                "unit_id": "unit-001",
                "chapter": "第二部分",
                "subchapter": "4、基金合同、《基金合同》或本基金合同、本合同：",
                "change_type": "replace",
                "display_strategy": "replace",
                "numbering_only": False,
                "unchanged_lines": [],
                "old_focus_text": "3个月持有期",
                "new_focus_text": "6个月持有期",
                "confidence": 0.95,
            }
        ]
    }

    rows = rows_from_llm_payload(payload, compare_units=(source_unit,))

    assert len(rows) == 1
    assert rows[0].old_text == source_unit.old_text
    assert rows[0].new_text == source_unit.new_text
    assert rows[0].old_text != "3个月持有期"
    assert rows[0].new_text != "6个月持有期"


def test_rows_from_unit_decisions_keeps_deleted_item_when_model_marks_it_unchanged():
    source_unit = CompareUnit(
        unit_id="unit-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        subchapter="7、基金份额发售公告：",
        old_text="7、基金份额发售公告：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金份额发售公告》",
        new_text="删除",
    )
    payload = {
        "units": [
            {
                "unit_id": "unit-001",
                "chapter": "第二部分",
                "subchapter": "7、基金份额发售公告：",
                "change_type": "delete_item",
                "display_strategy": "delete_old_only",
                "numbering_only": False,
                "unchanged_lines": [
                    "7、基金份额发售公告：指《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金份额发售公告》"
                ],
                "old_focus_text": "",
                "new_focus_text": "",
                "confidence": 1.0,
            }
        ]
    }

    rows = rows_from_llm_payload(payload, compare_units=(source_unit,))

    assert len(rows) == 1
    assert rows[0].chapter == "第二部分  释义"
    assert rows[0].old_text == source_unit.old_text
    assert rows[0].new_text == "删除"


def test_rows_from_unit_decisions_rejects_model_delete_when_new_text_still_exists():
    source_unit = CompareUnit(
        unit_id="unit-001",
        chapter_number="第二部分",
        chapter_title="第二部分  释义",
        subchapter="24、基金销售业务：",
        old_text="25、基金销售业务：指基金管理人或销售机构宣传推介基金，发售基金份额，办理基金份额的申购、赎回、转换、转托管及定期定额投资等业务",
        new_text="24、基金销售业务：指基金管理人或销售机构宣传推介基金，办理基金份额的申购、赎回、转换、转托管及定期定额投资等业务",
    )
    payload = {
        "units": [
            {
                "unit_id": "unit-001",
                "chapter": "第二部分",
                "subchapter": "24、基金销售业务：",
                "change_type": "delete_item",
                "display_strategy": "delete_old_only",
                "numbering_only": False,
                "unchanged_lines": [
                    "24、基金销售业务：指基金管理人或销售机构宣传推介基金，办理基金份额的申购、赎回、转换、转托管及定期定额投资等业务"
                ],
                "old_focus_text": "25、基金销售业务",
                "new_focus_text": "24、基金销售业务",
                "confidence": 0.9,
            }
        ]
    }

    with pytest.raises(ValueError, match="模型误判为整项删除"):
        rows_from_llm_payload(payload, compare_units=(source_unit,))


def test_compare_pair_with_llm_retries_next_provider_when_model_misclassifies_partial_delete(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "postprocess_max_attempts": 2,
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
    old_sections = [
        Section(
            number="第二部分",
            title="第二部分  释义",
            body="第二部分  释义\n25、基金销售业务：指基金管理人或销售机构宣传推介基金，发售基金份额，办理基金份额的申购、赎回、转换、转托管及定期定额投资等业务",
        )
    ]
    new_sections = [
        Section(
            number="第二部分",
            title="第二部分  释义",
            body="第二部分  释义\n24、基金销售业务：指基金管理人或销售机构宣传推介基金，办理基金份额的申购、赎回、转换、转托管及定期定额投资等业务",
        )
    ]
    client = StubResponsesClient(
        [
            {
                "output_text": json.dumps(
                    {
                        "blocks": [
                            {
                                "block_id": "第二部分-block-001",
                                "chapter": "第二部分",
                                "parent_path": "",
                                "operations": [
                                    {
                                        "type": "delete",
                                        "old_item_ids": ["第二部分-old-001"],
                                        "new_item_ids": [],
                                        "old_focus_text": "25、基金销售业务",
                                        "new_focus_text": "",
                                        "confidence": 0.9,
                                        # 故意缺少 reason，触发 schema 失败并切到下一 provider
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
            {
                "output_text": json.dumps(
                    {
                        "blocks": [
                            {
                                "block_id": "第二部分-block-001",
                                "chapter": "第二部分  释义",
                                "parent_path": "",
                                "operations": [
                                    {
                                        "type": "replace",
                                        "old_item_ids": ["第二部分-old-001"],
                                        "new_item_ids": ["第二部分-new-001"],
                                        "old_focus_text": "发售基金份额，",
                                        "new_focus_text": "",
                                        "confidence": 0.95,
                                        "reason": "仅删除了中间短语。",
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

    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    checkpoint = pair_store.payload()["entries"][0]
    assert client.calls == 2
    assert rows[0].new_text != "删除"
    assert [event["provider"] for event in timeline["events"] if event["status"] == "requesting"] == ["minimax", "kimi-code"]
    assert any(event["status"] == "parse_error" for event in timeline["events"])
    assert checkpoint["attempt_count"] == 2


def test_rows_from_unit_decisions_preserves_local_context_for_rewrite_even_if_model_marks_heading_unchanged():
    source_unit = CompareUnit(
        unit_id="unit-001",
        chapter_number="第一部分",
        chapter_title="第一部分  前言",
        subchapter="八、",
        old_text="八、\n相同风险说明\n投资者存在3个月最短持有期。",
        new_text="八、\n相同风险说明\n投资者存在6个月最短持有期。",
    )
    payload = {
        "units": [
            {
                "unit_id": "unit-001",
                "chapter": "第一部分  前言",
                "subchapter": "八、",
                "change_type": "rewrite",
                "display_strategy": "compare_changed_only",
                "numbering_only": False,
                "unchanged_lines": ["八、", "相同风险说明"],
                "old_focus_text": "3个月",
                "new_focus_text": "6个月",
                "confidence": 0.9,
            }
        ]
    }

    rows = rows_from_llm_payload(payload, compare_units=(source_unit,))

    assert len(rows) == 1
    assert rows[0].old_text == "八、\n投资者存在3个月最短持有期。"
    assert rows[0].new_text == "八、\n投资者存在6个月最短持有期。"


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


def test_process_llm_batch_locally_splits_only_truncated_batch(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "providers": [
                    {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "api_key": "deepseek-key",
                        "base_url": "https://api.deepseek.com/v1",
                    }
                ],
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    pair = PairMatch(
        pair_id="pair-001",
        key="demo",
        old_path=tmp_path / "old.docx",
        new_path=tmp_path / "new.docx",
        old_label="old.docx",
        new_label="new.docx",
    )
    block_1 = CompareBlock(
        block_id="第一部分-block-001",
        chapter_number="第一部分",
        chapter_title="第一部分  前言",
        parent_path="",
        old_items=(CompareBlockItem("old-001", "五、旧条目"),),
        new_items=(CompareBlockItem("new-001", "五、新条目"),),
    )
    block_2 = CompareBlock(
        block_id="第一部分-block-002",
        chapter_number="第一部分",
        chapter_title="第一部分  前言",
        parent_path="",
        old_items=(CompareBlockItem("old-002", "六、旧条目"),),
        new_items=(CompareBlockItem("new-002", "六、新条目"),),
    )
    batch = ChapterBatch(
        batch_id="batch-001",
        chapter_numbers=("第一部分",),
        old_sections=(Section(number="第一部分", title="第一部分  前言", body=""),),
        new_sections=(Section(number="第一部分", title="第一部分  前言", body=""),),
        compare_blocks=(block_1, block_2),
    )
    client = RawSequenceStubClient(
        [
            (
                {
                    "id": "resp-001",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "{\"blocks\": ["},
                            "finish_reason": "length",
                        }
                    ],
                },
                {"output_text": "{\"blocks\": ["},
            ),
            {
                "output_text": json.dumps(
                    {
                        "blocks": [
                            {
                                "block_id": "第一部分-block-001",
                                "chapter": "第一部分",
                                "parent_path": "",
                                "operations": [
                                    {
                                        "type": "replace",
                                        "old_item_ids": ["old-001"],
                                        "new_item_ids": ["new-001"],
                                        "old_focus_text": "五、旧条目",
                                        "new_focus_text": "五、新条目",
                                        "confidence": 0.95,
                                        "reason": "内容替换。",
                                    }
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
            {
                "output_text": json.dumps(
                    {
                        "blocks": [
                            {
                                "block_id": "第一部分-block-002",
                                "chapter": "第一部分",
                                "parent_path": "",
                                "operations": [
                                    {
                                        "type": "replace",
                                        "old_item_ids": ["old-002"],
                                        "new_item_ids": ["new-002"],
                                        "old_focus_text": "六、旧条目",
                                        "new_focus_text": "六、新条目",
                                        "confidence": 0.96,
                                        "reason": "内容替换。",
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

    batch_id, rows = _process_llm_batch(
        pair=pair,
        batch=batch,
        batch_index=0,
        llm_dir=tmp_path / "llm",
        runtime_config=runtime_config,
        pair_store=None,
        client=client,
        provider_chain=runtime_config.llm.providers,
    )

    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    final_status = json.loads((tmp_path / "llm" / "batch-001" / "final_status.json").read_text(encoding="utf-8"))
    combined_payload = json.loads((tmp_path / "llm" / "batch-001" / final_status["parsed_file"]).read_text(encoding="utf-8"))

    assert batch_id == "batch-001"
    assert client.calls == 3
    assert [(row.old_text, row.new_text) for row in rows] == [("五、旧条目", "五、新条目"), ("六、旧条目", "六、新条目")]
    assert any(event["status"] == "parse_error" for event in timeline["events"])
    assert final_status["status"] == "succeeded"
    assert final_status["next_resume_step"] == "reuse_parsed"
    assert [block["block_id"] for block in combined_payload["blocks"]] == ["第一部分-block-001", "第一部分-block-002"]


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
        process_dir=tmp_path / "process",
    )

    batch_dir = tmp_path / "llm" / "batch-001"
    assert len(rows) == 1
    assert client.calls == 2
    assert (tmp_path / "process" / "preprocess_summary.json").exists()
    assert (tmp_path / "process" / "compare_blocks.json").exists()
    assert (tmp_path / "process" / "batch_plan.json").exists()
    assert (batch_dir / "batch_input.json").exists()
    assert (batch_dir / "request.attempt-01.minimax.json").exists()
    assert (batch_dir / "request.attempt-02.minimax.json").exists()
    assert (batch_dir / "response.raw.attempt-02.minimax.json").exists()
    assert (batch_dir / "response.normalized.attempt-02.minimax.json").exists()
    assert (batch_dir / "parsed.attempt-02.minimax.json").exists()
    assert not (batch_dir / "request.json").exists()
    assert not (batch_dir / "response.json").exists()
    assert not (batch_dir / "parsed.json").exists()
    payload = pair_store.payload()
    assert payload["entries"][0]["status"] == "success"
    assert payload["entries"][0]["attempt_count"] == 2
    process_payload = json.loads((tmp_path / "process" / "compare_blocks.json").read_text(encoding="utf-8"))
    assert process_payload["items"][0]["parent_path"] == "一、总则"
    assert process_payload["items"][0]["old_items"][0]["text"] == "旧内容"


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


def test_compare_pair_with_llm_marks_parse_failure_provider_unavailable_for_cooldown(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 1,
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
    client = CooldownRecordingStubClient(
        [
            {"output_text": "not-json"},
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
    assert client.unavailable_providers == [("minimax", "parse_error")]


def test_compare_pair_with_llm_failovers_provider_after_fatal_request_error(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "fatal_max_attempts": 1,
                "task_routing": {
                    "enabled": True,
                    "batch_compare": ["deepseek-ark", "minimax"],
                },
                "providers": [
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-test",
                        "api_key": "ark-key",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    },
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "minimax-key",
                        "base_url": "https://api.minimaxi.com/v1",
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
            ProviderRequestError(
                "deepseek-ark",
                "deepseek-ark 请求失败: 400 unknown field",
                infrastructure_error=False,
                retry_class="fatal",
            ),
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

    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert client.calls == 2
    assert [event["provider"] for event in timeline["events"] if event["status"] == "requesting"] == [
        "deepseek-ark",
        "minimax",
    ]
    assert (tmp_path / "llm" / "batch-001" / "request.attempt-02.minimax.json").exists()


def test_compare_pair_with_llm_requires_six_failed_attempts_before_fallback(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 1,
                "infra_max_attempts": 1,
                "postprocess_max_attempts": 1,
                "fatal_max_attempts": 1,
                "providers": [
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-test",
                        "api_key": "ark-key",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    },
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
                "task_routing": {
                    "enabled": True,
                    "batch_compare": ["deepseek-ark", "minimax", "kimi-code"],
                },
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
            ProviderRequestError(
                "provider",
                f"第 {index} 次失败",
                infrastructure_error=False,
                retry_class="fatal",
            )
            for index in range(1, 7)
        ],
        providers=runtime_config.llm.providers,
    )

    with pytest.raises(RuntimeError, match="本地 fallback 仅用于诊断"):
        compare_pair_with_llm(
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            llm_dir=tmp_path / "llm",
            runtime_config=runtime_config,
            pair_store=pair_store,
            client=client,
        )

    final_status = json.loads((tmp_path / "llm" / "batch-001" / "final_status.json").read_text(encoding="utf-8"))
    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    request_providers = [event["provider"] for event in timeline["events"] if event["status"] == "requesting"]
    assert client.calls == 6
    assert final_status["status"] == "failed"
    assert final_status["attempt_count"] == 6
    assert final_status["fallback"] == "compare-blocks"
    assert (tmp_path / "llm" / "batch-001" / "fallback.diagnostic.json").exists()
    assert request_providers == ["deepseek-ark", "minimax", "kimi-code", "deepseek-ark", "minimax", "kimi-code"]


def test_deepseek_ark_unknown_field_error_downgrades_to_plain_json_prompt(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "fatal_max_attempts": 2,
                "providers": [
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-test",
                        "api_key": "ark-key",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    }
                ],
                "task_routing": {
                    "enabled": True,
                    "batch_compare": ["deepseek-ark"],
                },
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
    client = RealPayloadStubClient(
        runtime_config,
        [
            ProviderRequestError(
                "deepseek-ark",
                'deepseek-ark 请求失败: 400 {"error":{"code":"InvalidParameter","message":"json: unknown field \\"text\\""}}',
                infrastructure_error=False,
                retry_class="fatal",
            ),
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
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
                                ),
                            }
                        ],
                    }
                ]
            },
        ],
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

    first_request = json.loads((tmp_path / "llm" / "batch-001" / "request.attempt-01.deepseek-ark.json").read_text(encoding="utf-8"))
    second_request = json.loads((tmp_path / "llm" / "batch-001" / "request.attempt-02.deepseek-ark.json").read_text(encoding="utf-8"))
    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert "text" in first_request
    assert "text" not in second_request
    assert "instructions" not in second_request
    assert "请严格输出一个 JSON 对象" in second_request["input"]
    requesting_events = [event for event in timeline["events"] if event["status"] == "requesting"]
    assert requesting_events[1]["details"]["prompt_mode"] == "plain_json"


def test_compare_pair_with_llm_writes_timeline_for_parse_retry(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 2,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "secret",
                        "base_url": "https://api.minimaxi.com/v1",
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

    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    final_status = json.loads((tmp_path / "llm" / "batch-001" / "final_status.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert [event["status"] for event in timeline["events"]] == [
        "prepared",
        "requesting",
        "responded",
        "normalized",
        "parse_error",
        "requesting",
        "responded",
        "normalized",
        "succeeded",
    ]
    assert final_status["status"] == "succeeded"
    assert final_status["attempt_count"] == 2
    assert final_status["next_resume_step"] == "reuse_parsed"
    assert final_status["parsed_file"] == "parsed.attempt-02.minimax.json"
    assert not (tmp_path / "llm" / "batch-001" / "request.json").exists()
    assert not (tmp_path / "llm" / "batch-001" / "response.json").exists()
    assert not (tmp_path / "llm" / "batch-001" / "response.raw.json").exists()
    assert not (tmp_path / "llm" / "batch-001" / "response.normalized.json").exists()
    assert not (tmp_path / "llm" / "batch-001" / "parsed.json").exists()


def test_compare_pair_with_llm_forbids_compare_units_as_formal_result_when_models_fail(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 1,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "secret",
                        "base_url": "https://api.minimaxi.com/v1",
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
        [{"output_text": "{bad json"}],
        providers=runtime_config.llm.providers,
    )

    with pytest.raises(RuntimeError, match="本地 fallback 仅用于诊断"):
        compare_pair_with_llm(
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            llm_dir=tmp_path / "llm",
            runtime_config=runtime_config,
            pair_store=pair_store,
            client=client,
        )

    final_status = json.loads((tmp_path / "llm" / "batch-001" / "final_status.json").read_text(encoding="utf-8"))
    fallback_diagnostic = json.loads((tmp_path / "llm" / "batch-001" / "fallback.diagnostic.json").read_text(encoding="utf-8"))
    assert fallback_diagnostic["row_count"] == 1
    assert fallback_diagnostic["rows"][0]["subchapter"] == "一、总则"
    assert fallback_diagnostic["rows"][0]["old_text"] == "旧内容"
    assert fallback_diagnostic["rows"][0]["new_text"] == "新内容"
    assert final_status["status"] == "failed"
    assert final_status["fallback"] == "compare-blocks"


def test_compare_pair_with_llm_writes_raw_normalized_and_repair_files(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 1,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "secret",
                        "base_url": "https://api.minimaxi.com/v1",
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
    client = RawAwareStubClient(
        [
            {
                "output_text": json.dumps(
                    {
                        "subsections": [
                            {
                                "subchapter": "一、总则",
                                "old_text": "旧内容",
                                "new_text": "新内容",
                                "change_type": "replace",
                                "numbering_only": False,
                                "fully_equal_lines": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
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
    repair = json.loads((batch_dir / "repair.attempt-01.minimax.json").read_text(encoding="utf-8"))
    final_status = json.loads((batch_dir / "final_status.json").read_text(encoding="utf-8"))
    timeline = json.loads((batch_dir / "timeline.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert (batch_dir / "response.raw.attempt-01.minimax.json").exists()
    assert (batch_dir / "response.normalized.attempt-01.minimax.json").exists()
    assert repair["success"] is True
    assert repair["repair_type"] == "wrap_subsections"
    assert final_status["status"] == "succeeded"
    assert final_status["recoverable"] is False
    assert final_status["next_resume_step"] == "reuse_parsed"
    assert "repaired" in [event["status"] for event in timeline["events"]]


def test_compare_pair_with_llm_degrades_prompt_after_parse_error(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "parse_max_attempts": 2,
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "secret",
                        "base_url": "https://api.minimaxi.com/v1",
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
    client = RawAwareStubClient(
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

    request_two = json.loads((tmp_path / "llm" / "batch-001" / "request.attempt-02.minimax.json").read_text(encoding="utf-8"))
    timeline = json.loads((tmp_path / "llm" / "batch-001" / "timeline.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert "governance" not in request_two
    assert timeline["events"][5]["details"]["prompt_mode"] == "minimal"


def test_compare_pair_with_llm_records_every_error_in_timeline_without_error_txt(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "secret",
                        "base_url": "https://api.minimaxi.com/v1",
                        "failure_cooldown_seconds": 0,
                    },
                    {
                        "provider": "kimi-code",
                        "model": "kimi-for-coding",
                        "api_key": "secret",
                        "base_url": "https://api.kimi.com/coding",
                        "failure_cooldown_seconds": 0,
                    },
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-20260415114137-55jp7",
                        "api_key": "secret",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                        "failure_cooldown_seconds": 0,
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
    client = RawAwareStubClient(
        [
            {"output_text": "not-json-attempt-1"},
            {"output_text": "not-json-attempt-2"},
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
    timeline = json.loads((batch_dir / "timeline.json").read_text(encoding="utf-8"))
    error_events = [event for event in timeline["events"] if event["error"]]
    assert len(rows) == 1
    assert not (batch_dir / "error.txt").exists()
    assert [event["status"] for event in error_events] == ["parse_error", "parse_error"]
    assert [event["attempt_index"] for event in error_events] == [1, 2]
    assert [event["provider"] for event in error_events] == ["minimax", "kimi-code"]
    assert "not-json-attempt-1" in error_events[0]["error"]
    assert "not-json-attempt-2" in error_events[1]["error"]


def test_compare_pair_with_llm_reuses_final_status_parsed_file_without_parsed_alias(tmp_path):
    runtime_config = load_file_comparison_runtime_config(tmp_path)
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
    batch_dir = tmp_path / "llm" / "batch-001"
    batch_dir.mkdir(parents=True)
    parsed_payload = {
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
    }
    parsed_name = "parsed.attempt-01.kimi-code.json"
    (batch_dir / parsed_name).write_text(json.dumps(parsed_payload, ensure_ascii=False), encoding="utf-8")
    (batch_dir / "final_status.json").write_text(
        json.dumps(
            {"status": "succeeded", "next_resume_step": "reuse_parsed", "parsed_file": parsed_name},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pair_store = PairCheckpointStore.load_or_create(tmp_path / "pair_checkpoint.json", pair_id="pair-001", task_id="task-001")

    rows = compare_pair_with_llm(
        pair=pair,
        old_sections=old_sections,
        new_sections=new_sections,
        llm_dir=tmp_path / "llm",
        runtime_config=runtime_config,
        pair_store=pair_store,
        client=StubResponsesClient([], providers=runtime_config.llm.providers),
    )

    assert len(rows) == 1
    assert rows[0].old_text == "旧内容"
    assert rows[0].new_text == "新内容"


def test_compare_pair_with_llm_reuses_repair_on_resume(tmp_path):
    runtime_config = load_file_comparison_runtime_config(tmp_path)
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
    batch_dir = tmp_path / "llm" / "batch-001"
    batch_dir.mkdir(parents=True)
    repair_payload = {
        "success": True,
        "payload": {
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
    }
    (batch_dir / "repair.json").write_text(json.dumps(repair_payload, ensure_ascii=False), encoding="utf-8")
    (batch_dir / "final_status.json").write_text(
        json.dumps({"status": "succeeded", "next_resume_step": "reuse_repair"}, ensure_ascii=False),
        encoding="utf-8",
    )
    pair_store = PairCheckpointStore.load_or_create(tmp_path / "pair_checkpoint.json", pair_id="pair-001", task_id="task-001")

    rows = compare_pair_with_llm(
        pair=pair,
        old_sections=old_sections,
        new_sections=new_sections,
        llm_dir=tmp_path / "llm",
        runtime_config=runtime_config,
        pair_store=pair_store,
        client=StubResponsesClient([], providers=runtime_config.llm.providers),
    )

    assert len(rows) == 1
    assert rows[0].old_text == "旧内容"
    assert rows[0].new_text == "新内容"


def test_provider_endpoint_uses_chat_completions_for_minimax():
    assert provider_endpoint("minimax", "https://api.minimaxi.com/v1") == "https://api.minimaxi.com/v1/chat/completions"
    assert provider_endpoint("openai-responses", "https://api.openai.com") == "https://api.openai.com/v1/responses"
    assert provider_endpoint("kimi-code", "https://api.kimi.com/coding") == "https://api.kimi.com/coding/v1/messages"
    assert provider_endpoint("deepseek-ark", "https://ark.cn-beijing.volces.com/api/v3") == "https://ark.cn-beijing.volces.com/api/v3/responses"
    assert provider_endpoint("deepseek", "https://api.deepseek.com") == "https://api.deepseek.com/chat/completions"


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


def test_build_request_payload_uses_fast_deepseek_flash_without_thinking(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "providers": [
                    {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "api_key": "secret",
                        "base_url": "https://api.deepseek.com",
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

    assert payload["model"] == "deepseek-v4-flash"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert payload["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in payload
    assert payload["thinking"] == {"type": "disabled"}


def test_provider_failure_cools_down_next_same_provider_request(tmp_path, monkeypatch):
    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {
                "retry_classifier": {"infra_max_attempts": 1},
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "minimax-key",
                        "base_url": "https://api.minimaxi.com/v1",
                        "min_interval_seconds": 0,
                        "failure_cooldown_seconds": 40,
                    }
                ],
            },
        },
    )
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    client = OpenAIResponsesClient(runtime_config)
    sleep_calls = []
    current_time = 1000.0

    def fake_urlopen(*_args, **_kwargs):
        raise URLError("temporary unavailable")

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_module.time, "time", lambda: current_time)
    monkeypatch.setattr(client_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    with pytest.raises(ProviderRequestError):
        client.post_with_raw({"input": "first"})
    with pytest.raises(ProviderRequestError):
        client.post_with_raw({"input": "second"})

    assert sleep_calls == [40.0]


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


def test_build_request_payload_prefers_compare_blocks_when_present(tmp_path):
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
            "chapter_numbers": ("第三部分",),
            "old_sections": (),
            "new_sections": (),
            "compare_blocks": (
                type(
                    "Block",
                    (),
                    {
                        "block_id": "第三部分-block-001",
                        "chapter_number": "第三部分",
                        "chapter_title": "第三部分  基金的基本情况",
                        "parent_path": "",
                        "old_items": (type("Item", (), {"item_id": "old-001", "text": "一、基金名称\n旧名称"})(),),
                        "new_items": (type("Item", (), {"item_id": "new-001", "text": "一、基金名称\n新名称"})(),),
                    },
                )(),
            ),
        },
    )()

    payload = client.build_request_payload(pair_id="pair-001", batch=batch)
    input_payload = json.loads(payload["messages"][1]["content"].split("输入数据如下：\n", 1)[1])

    assert input_payload["compare_blocks"][0]["block_id"] == "第三部分-block-001"
    assert input_payload["compare_blocks"][0]["old_items"][0]["text"] == "一、基金名称\n旧名称"
    assert payload["response_format"] == {"type": "json_object"}


def test_build_request_payload_requests_block_operation_schema(tmp_path):
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
    batch = ChapterBatch(
        batch_id="batch-001",
        chapter_numbers=("第七部分",),
        compare_blocks=(
            CompareBlock(
                block_id="block-001",
                chapter_number="第七部分",
                chapter_title="第七部分  基金合同当事人及权利义务",
                parent_path="基金管理人的义务",
                old_items=(
                    CompareBlockItem("old-024", "（24）旧内容"),
                    CompareBlockItem("old-025", "（25）未变内容"),
                ),
                new_items=(CompareBlockItem("new-024", "（24）未变内容"),),
            ),
        ),
    )

    payload = client.build_request_payload(pair_id="pair-001", batch=batch, provider_config=runtime_config.llm.providers[0])
    schema = payload["text"]["format"]["schema"]

    assert schema["required"] == ["blocks"]
    assert "chapters" not in schema["properties"]
    operation_schema = schema["properties"]["blocks"]["items"]["properties"]["operations"]["items"]
    assert operation_schema["properties"]["type"]["enum"] == ["add", "delete", "replace"]
    assert "compare_blocks" in payload["instructions"]
    assert "## 工作步骤" in payload["instructions"]
    assert "## 匹配规则" in payload["instructions"]
    assert "## 覆盖规则" in payload["instructions"]
    assert "## 输出规则" in payload["instructions"]
    assert "## 返回结构示例" in payload["instructions"]
    assert "仅编号顺延的条目，这些条目不要输出" in payload["instructions"]
    assert "每个 compare block 必须做覆盖检查" in payload["instructions"]
    assert "仍未匹配的 old_item 必须返回 delete" in payload["instructions"]
    assert "仍未匹配的 new_item 必须返回 add" in payload["instructions"]
    assert "建立覆盖清单" in payload["instructions"]
    assert "定义项必须优先按冒号或中文冒号前的定义名称对齐" in payload["instructions"]
    assert "不同定义名称默认不要 replace" in payload["instructions"]
    assert "定义A" in payload["instructions"]
    assert "定义C" in payload["instructions"]
    assert "定义E" in payload["instructions"]
    assert "定义F" in payload["instructions"]
    assert "默认拆成 delete + add" in payload["instructions"]
    assert "条款G" in payload["instructions"]
    assert "后续正文未变的顺延条目不要输出" in payload["instructions"]
    assert "基金募集期" not in payload["instructions"]
    assert "输出前必须自检" in payload["instructions"]
    assert '"operations"' in payload["instructions"]
    assert '"old_item_ids"' in payload["instructions"]


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


def test_validate_complete_batch_results_blocks_partial_document(tmp_path):
    batches = [
        ChapterBatch(
            batch_id="batch-001",
            chapter_numbers=("第一部分",),
            compare_units=(
                CompareUnit(
                    unit_id="unit-001",
                    chapter_number="第一部分",
                    chapter_title="第一部分  前言",
                    subchapter="一、总则",
                    old_text="旧内容",
                    new_text="新内容",
                ),
            ),
        ),
        ChapterBatch(
            batch_id="batch-002",
            chapter_numbers=("第二部分",),
            compare_units=(
                CompareUnit(
                    unit_id="unit-002",
                    chapter_number="第二部分",
                    chapter_title="第二部分  释义",
                    subchapter="一、定义",
                    old_text="旧定义",
                    new_text="新定义",
                ),
            ),
        ),
    ]
    batch_one_dir = tmp_path / "llm" / "batch-001"
    batch_one_dir.mkdir(parents=True)
    (batch_one_dir / "final_status.json").write_text(
        json.dumps({"status": "succeeded"}, ensure_ascii=False),
        encoding="utf-8",
    )
    batch_results = {
        "batch-001": [ComparisonRow(chapter="第一部分  前言", subchapter="一、总则", old_text="旧内容", new_text="新内容")]
    }

    with pytest.raises(RuntimeError, match="batch-002"):
        validate_complete_batch_results(batches=batches, batch_results=batch_results, llm_dir=tmp_path / "llm")


def test_validate_complete_batch_results_blocks_fallback_status(tmp_path):
    batches = [
        ChapterBatch(
            batch_id="batch-001",
            chapter_numbers=("第一部分",),
            compare_units=(
                CompareUnit(
                    unit_id="unit-001",
                    chapter_number="第一部分",
                    chapter_title="第一部分  前言",
                    subchapter="一、总则",
                    old_text="旧内容",
                    new_text="新内容",
                ),
            ),
        )
    ]
    batch_dir = tmp_path / "llm" / "batch-001"
    batch_dir.mkdir(parents=True)
    (batch_dir / "final_status.json").write_text(
        json.dumps({"status": "fallback_succeeded"}, ensure_ascii=False),
        encoding="utf-8",
    )
    batch_results = {
        "batch-001": [ComparisonRow(chapter="第一部分  前言", subchapter="一、总则", old_text="旧内容", new_text="新内容")]
    }

    with pytest.raises(RuntimeError, match="状态不可用:fallback_succeeded"):
        validate_complete_batch_results(batches=batches, batch_results=batch_results, llm_dir=tmp_path / "llm")
