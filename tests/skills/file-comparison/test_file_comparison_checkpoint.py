import json

from file_comparison.compare.engine import compare_pair_with_llm
from file_comparison.compare.models import PairMatch, Section
from file_comparison.runtime.checkpoint import PairCheckpointStore, TaskCheckpointStore, atomic_write_json
from file_comparison.runtime.config import load_file_comparison_runtime_config


class FailIfCalledClient:
    def build_request_payload(self, *, pair_id, batch):
        raise AssertionError("client should not be called for recovered batches")

    def write_request(self, path, payload):
        raise AssertionError("client should not be called for recovered batches")

    def post(self, payload):
        raise AssertionError("client should not be called for recovered batches")

    def write_response(self, path, payload):
        raise AssertionError("client should not be called for recovered batches")


def test_atomic_write_json_replaces_file(tmp_path):
    path = tmp_path / "status.json"
    atomic_write_json(path, {"status": "pending"})
    atomic_write_json(path, {"status": "completed", "count": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "completed", "count": 2}


def test_pair_checkpoint_attempt_count_and_summary(tmp_path):
    store = PairCheckpointStore.load_or_create(tmp_path / "pair.json", pair_id="pair-001", task_id="task-001")

    store.record_entry(
        entry_id="batch-001",
        status="parse_error",
        error={"message": "bad json"},
        provider="minimax",
        duration_ms=1200,
        provider_available=False,
    )
    store.record_entry(
        entry_id="batch-001",
        status="success",
        result={"row_count": 1},
        provider="kimi-code",
        duration_ms=800,
        provider_available=True,
    )

    payload = store.payload()
    assert payload["status_summary"]["success"] == 1
    assert payload["status_summary"]["parse_error"] == 0
    assert payload["entries"][0]["attempt_count"] == 2
    assert payload["entries"][0]["total_duration_ms"] == 2000
    assert payload["entries"][0]["attempts"][0]["provider_available"] is False
    assert payload["entries"][0]["attempts"][0]["duration_ms"] == 1200
    assert payload["entries"][0]["attempts"][1]["provider"] == "kimi-code"
    assert payload["total_duration_ms"] == 2000


def test_task_checkpoint_records_pair_status(tmp_path):
    store = TaskCheckpointStore.load_or_create(tmp_path / "task.json", task_id="task-001")
    store.record_pair(pair_id="pair-001", status="completed", summary={"success": 2}, duration_ms=3456)

    payload = store.payload()
    assert payload["pairs"][0]["pair_id"] == "pair-001"
    assert payload["pairs"][0]["status"] == "completed"
    assert payload["pairs"][0]["summary"] == {"success": 2}
    assert payload["pairs"][0]["duration_ms"] == 3456
    assert payload["total_duration_ms"] == 3456


def test_compare_pair_with_llm_skips_successful_batches_on_resume(tmp_path):
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
    llm_dir = tmp_path / "llm"
    batch_dir = llm_dir / "batch-001"
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
    (batch_dir / "parsed.json").write_text(json.dumps(parsed_payload, ensure_ascii=False), encoding="utf-8")
    pair_store = PairCheckpointStore.load_or_create(tmp_path / "pair_checkpoint.json", pair_id="pair-001", task_id="task-001")
    pair_store.record_entry(entry_id="batch-001", status="success", result={"row_count": 1}, provider="openai-responses")

    rows = compare_pair_with_llm(
        pair=pair,
        old_sections=old_sections,
        new_sections=new_sections,
        llm_dir=llm_dir,
        runtime_config=runtime_config,
        pair_store=pair_store,
        client=FailIfCalledClient(),
    )

    assert len(rows) == 1
    assert rows[0].old_text == "旧内容"
    assert rows[0].new_text == "新内容"
