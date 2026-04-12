from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from c114.checkpoint import (
    StepCheckpointStore,
    checkpoint_path_for_step,
)


class CheckpointPathTests(unittest.TestCase):
    def test_builds_checkpoint_path_under_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "c114_search_202604102139"
            output_path = run_dir / "c114_step_2_search_checklist_20260410.yaml"

            checkpoint_path = checkpoint_path_for_step(
                output_path=output_path,
                step_name="step_2",
                report_date="2026-04-10",
            )

            self.assertEqual(
                checkpoint_path,
                (run_dir / "checkpoints" / "c114_step_2_checkpoint_20260410.json").resolve(),
            )

    def test_supports_custom_prefix_for_other_sites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "infoq_search_202604121200"
            output_path = run_dir / "infoq_step_2_search_checklist_20260412.yaml"

            checkpoint_path = checkpoint_path_for_step(
                output_path=output_path,
                step_name="step_2",
                report_date="2026-04-12",
                prefix="infoq",
            )

            self.assertEqual(
                checkpoint_path,
                (run_dir / "checkpoints" / "infoq_step_2_checkpoint_20260412.json").resolve(),
            )


class StepCheckpointStoreTests(unittest.TestCase):
    def test_records_entries_and_reloads_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "checkpoints" / "c114_step_2_checkpoint_20260410.json"
            store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path,
                step_name="step_2",
                report_date="2026-04-10",
                input_path=Path("/tmp/input.csv"),
                output_path=Path("/tmp/output.yaml"),
            )

            store.record_entry(
                entry_id="article::Cloud&AI::标题A",
                status="success",
                provider="kimi-code",
                request_context={"topic": "Cloud&AI", "original_title": "标题A"},
                result={"keywords": ["关键词1", "关键词2"]},
            )

            reloaded = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path,
                step_name="step_2",
                report_date="2026-04-10",
                input_path=Path("/tmp/input.csv"),
                output_path=Path("/tmp/output.yaml"),
            )

            entry = reloaded.get_entry("article::Cloud&AI::标题A")
            assert entry is not None
            self.assertEqual(entry["status"], "success")
            self.assertEqual(entry["provider"], "kimi-code")
            self.assertEqual(entry["result"]["keywords"], ["关键词1", "关键词2"])

    def test_updates_status_summary_from_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "checkpoints" / "c114_step_3_checkpoint_20260410.json"
            store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path,
                step_name="step_3",
                report_date="2026-04-10",
                input_path=Path("/tmp/input.yaml"),
                output_path=Path("/tmp/output.yaml"),
            )

            store.record_entry(entry_id="q1", status="success", result={"value": 1})
            store.record_entry(entry_id="q2", status="error", error={"message": "network"})
            store.record_entry(entry_id="q3", status="postprocess_error", error={"message": "invalid"})

            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["status_summary"]["success"], 1)
            self.assertEqual(payload["status_summary"]["error"], 1)
            self.assertEqual(payload["status_summary"]["postprocess_error"], 1)
            self.assertEqual(payload["status_summary"]["total"], 3)

    def test_keeps_attempt_count_when_re_recording_same_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = Path(tmp_dir) / "checkpoints" / "c114_step_3_checkpoint_20260410.json"
            store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path,
                step_name="step_3",
                report_date="2026-04-10",
                input_path=Path("/tmp/input.yaml"),
                output_path=Path("/tmp/output.yaml"),
            )

            store.record_entry(entry_id="review::新闻::batch_1", status="error", error={"message": "timeout"})
            store.record_entry(entry_id="review::新闻::batch_1", status="success", result={"ok": True})

            entry = store.get_entry("review::新闻::batch_1")
            assert entry is not None
            self.assertEqual(entry["attempt_count"], 2)
            self.assertEqual(entry["status"], "success")
