import json

from file_comparison.compare.engine import TaskManager, compare_pair_with_llm
from file_comparison.compare.models import PairMatch, Section
from file_comparison.runtime.checkpoint import PairCheckpointStore, TaskCheckpointStore, atomic_write_json
from file_comparison.runtime.config import load_file_comparison_runtime_config, write_runtime_config
from file_comparison.runtime.settings import resolve_paths


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


def test_pair_checkpoint_can_remove_single_batch_for_rerun(tmp_path):
    store = PairCheckpointStore.load_or_create(tmp_path / "pair.json", pair_id="pair-001", task_id="task-001")
    store.record_entry(entry_id="batch-001", status="success", result={"row_count": 1}, provider="minimax")
    store.record_entry(entry_id="batch-002", status="success", result={"row_count": 1}, provider="kimi-code")

    removed = store.remove_entry("batch-001")

    payload = json.loads((tmp_path / "pair.json").read_text(encoding="utf-8"))
    assert removed is True
    assert [entry["entry_id"] for entry in payload["entries"]] == ["batch-002"]
    assert payload["status_summary"]["success"] == 1
    assert payload["status_summary"]["total"] == 1


def test_task_manager_rerun_batch_clears_only_target_batch_and_starts_resume(tmp_path, monkeypatch):
    write_runtime_config(tmp_path, {"paths": {"output_root": str(tmp_path / "runs")}})
    runtime_config = load_file_comparison_runtime_config(tmp_path)
    paths = resolve_paths(tmp_path)
    run_dir = paths.runs_root / "task-001"
    pair_dir = run_dir / "pairs" / "pair-001"
    old_path = tmp_path / "old.docx"
    new_path = tmp_path / "new.docx"
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")
    (pair_dir / "llm" / "batch-001").mkdir(parents=True)
    (pair_dir / "llm" / "batch-002").mkdir(parents=True)
    (pair_dir / "llm" / "batch-001" / "parsed.json").write_text("{}", encoding="utf-8")
    (pair_dir / "llm" / "batch-002" / "parsed.json").write_text("{}", encoding="utf-8")
    (pair_dir / "outputs").mkdir(parents=True)
    (pair_dir / "outputs" / "comparison.docx").write_bytes(b"old-result")
    (pair_dir / "outputs" / "comparison.doc").write_bytes(b"old-result")
    atomic_write_json(
        run_dir / "task.json",
        {"task_id": "task-001", "folder_path": str(tmp_path), "pair_count": 1},
    )
    atomic_write_json(
        run_dir / "status.json",
        {
            "task_id": "task-001",
            "pairs": [
                {
                    "pair_id": "pair-001",
                    "key": "demo",
                    "old_path": str(old_path),
                    "new_path": str(new_path),
                    "status": "failed",
                }
            ],
        },
    )
    pair_store = PairCheckpointStore.load_or_create(
        run_dir / "checkpoints" / "pair_pair-001_checkpoint.json",
        pair_id="pair-001",
        task_id="task-001",
    )
    pair_store.record_entry(entry_id="batch-001", status="success", result={"row_count": 1}, provider="minimax")
    pair_store.record_entry(entry_id="batch-002", status="success", result={"row_count": 1}, provider="kimi-code")
    started = {}

    def fake_run_background(self, folder_path, rerun_dir, pairs):
        started["folder_path"] = folder_path
        started["run_dir"] = rerun_dir
        started["pairs"] = pairs

    monkeypatch.setattr(TaskManager, "_run_background", fake_run_background)

    payload = TaskManager(runtime_config, paths).rerun_batch("task-001", "pair-001", "batch-001")

    checkpoint_payload = json.loads((run_dir / "checkpoints" / "pair_pair-001_checkpoint.json").read_text(encoding="utf-8"))
    assert payload["status"] == "running"
    assert not (pair_dir / "llm" / "batch-001").exists()
    assert (pair_dir / "llm" / "batch-002" / "parsed.json").exists()
    assert not (pair_dir / "outputs" / "comparison.docx").exists()
    assert [entry["entry_id"] for entry in checkpoint_payload["entries"]] == ["batch-002"]
    assert started["run_dir"] == run_dir
    assert [pair.pair_id for pair in started["pairs"]] == ["pair-001"]


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
