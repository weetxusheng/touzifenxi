from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from c114.c114_brief_review import (
    render_brief_review_template_yaml,
    validate_brief_review_yaml_for_agent,
)
from c114.runtime.execution import (
    StepInstruction,
    build_checkpoint_sequence,
    create_controller_agent_bridge,
    execution_manifest_name,
    normalize_execution_mode,
    render_controller_agent_bridge,
)


class ExecutionModeTests(unittest.TestCase):
    def test_normalize_execution_mode_defaults_to_builtin(self) -> None:
        self.assertEqual(normalize_execution_mode(None), "builtin")
        self.assertEqual(normalize_execution_mode("unknown"), "builtin")
        self.assertEqual(normalize_execution_mode("controller-agent"), "controller-agent")

    def test_render_controller_agent_bridge_contains_manifest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / execution_manifest_name(date(2026, 4, 9))
            checkpoints = build_checkpoint_sequence(
                ready_step="step_3",
                output_paths={
                    "step_1": root / "c114_step_1.csv",
                    "step_2": root / "c114_step_2.yaml",
                    "step_3": root / "c114_step_3.yaml",
                },
                prompt_paths={"step_3": root / "search-review-agent.md"},
                input_paths={"step_3": (root / "c114_step_2.yaml", root / "c114_step_3.yaml")},
                required_fields={"step_3": ("keep_level", "reason")},
            )
            bridge = create_controller_agent_bridge(
                report_date="2026-04-09",
                manifest_path=manifest_path,
                current_step="step_3",
                checkpoints=checkpoints,
                instruction=StepInstruction(
                    step_name="step_3",
                    prompt_path=root / "search-review-agent.md",
                    input_paths=(root / "c114_step_2.yaml", root / "c114_step_3.yaml"),
                    output_path=root / "c114_step_3.yaml",
                    required_fields=("keep_level", "reason"),
                ),
                next_action="补完 step 3 后再继续。",
            )

            rendered = render_controller_agent_bridge(bridge)

        self.assertIn("execution_mode: 'controller-agent'", rendered)
        self.assertIn("current_step: 'step_3'", rendered)
        self.assertIn("status: 'ready_for_agent'", rendered)
        self.assertIn("required_fields:", rendered)


class BriefReviewTemplateTests(unittest.TestCase):
    def test_validate_brief_review_yaml_for_agent_rejects_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "review.yaml"
            path.write_text(
                render_brief_review_template_yaml(
                    "2026-04-09",
                    Path("/tmp/brief.md"),
                    Path("/tmp/analysis.yaml"),
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "仍是模板"):
                validate_brief_review_yaml_for_agent(path)

    def test_validate_brief_review_yaml_for_agent_accepts_completed_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "review.yaml"
            path.write_text(
                "\n".join(
                    [
                        "report_date: '2026-04-09'",
                        "brief_path: '/tmp/brief.md'",
                        "analysis_path: '/tmp/analysis.yaml'",
                        "review_prompt_path: '/tmp/prompt.md'",
                        "overall_decision: 'pass'",
                        "summary: '审查通过。'",
                        "findings:",
                        "  []",
                        "strengths:",
                        "  - '结构完整。'",
                    ]
                ),
                encoding="utf-8",
            )

            validate_brief_review_yaml_for_agent(path)
