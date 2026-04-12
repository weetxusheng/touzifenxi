from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from c114.cli import build_parser, infer_trace_run_dir, run_with_args
from touzifenxi.settings import AppPaths

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "skills" / "c114-daily-hot-topics" / "scripts" / "c114.py"


class ScriptEntrypointTests(unittest.TestCase):
    def test_infer_trace_run_dir_prefers_run_directory_from_input_path(self) -> None:
        run_dir = Path("/repo/reports/c114_report/c114_search_202604101832")
        resolved = infer_trace_run_dir(
            run_dir / "c114_step_2_search_checklist_20260410.yaml",
            Path("/repo/reports/c114_report/c114_step_3_search_results_20260410.yaml"),
        )

        self.assertEqual(resolved, run_dir)

    def test_skill_uses_single_script_entrypoint(self) -> None:
        self.assertTrue(SCRIPT_PATH.exists())
        spec = importlib.util.spec_from_file_location("c114_script", SCRIPT_PATH)
        self.assertIsNotNone(spec)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.bootstrap))
        self.assertTrue(callable(module.main))

    def test_skill_parser_exposes_c114_subcommands(self) -> None:
        parser = build_parser()
        commands = parser._subparsers._group_actions[0].choices
        self.assertIn("c114-hot-topics", commands)
        self.assertIn("c114-search", commands)
        self.assertIn("c114-review-brief", commands)
        self.assertIn("c114-config-status", commands)
        self.assertIn("c114-config-init", commands)
        self.assertIn("run", commands)

    def test_skill_script_runs_as_real_entrypoint(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "c114-config-status",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("C114 配置文件", completed.stdout)

    def test_run_skips_step7_when_disabled_in_runtime_config(self) -> None:
        args = Namespace(
            command="run",
            date="2026-04-07",
            start_date=None,
            end_date=None,
            execution_mode=None,
            channels=["home"],
            candidate_limit=1,
            timeout=5.0,
            provider="auto",
            per_query_limit=1,
            per_article_limit=None,
            extract_limit=1,
        )
        paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )

        class FakeReport:
            pass

        with ExitStack() as stack:
            stack.enter_context(patch("c114.cli.ensure_directories"))
            stack.enter_context(patch("c114.cli.require_llm_client", return_value=object()))
            load_runtime = stack.enter_context(patch("c114.cli.load_c114_runtime_config"))
            stack.enter_context(patch("c114.cli.create_search_run_directory", return_value=Path("/repo/reports/run1")))
            stack.enter_context(patch("c114.cli.collect_daily_report", return_value=FakeReport()))
            stack.enter_context(
                patch("c114.cli.resolve_hot_topics_output_path", return_value=Path("/repo/data/raw/c114_hot_topics_20260407.json"))
            )
            stack.enter_context(patch("c114.cli.save_daily_report"))
            resolve_analysis = stack.enter_context(patch("c114.cli.resolve_analysis_output_paths"))
            stack.enter_context(patch("c114.cli.analyze_daily_articles", return_value=([{"title": "x"}], ["topic"])))
            stack.enter_context(patch("c114.cli.write_analysis_outputs", return_value=[{"topic": "AI"}]))
            resolve_search = stack.enter_context(patch("c114.cli.resolve_search_output_paths"))
            stack.enter_context(patch("c114.cli.build_search_trace_logger", return_value=object()))
            stack.enter_context(patch("c114.cli.run_search_workflow", return_value=object()))
            stack.enter_context(patch("c114.cli.save_search_results"))
            resolve_content = stack.enter_context(patch("c114.cli.resolve_content_output_paths"))
            stack.enter_context(patch("c114.cli.run_content_fetch_workflow", return_value=object()))
            stack.enter_context(patch("c114.cli.save_content_results"))
            resolve_content_analysis = stack.enter_context(patch("c114.cli.resolve_content_analysis_output_paths"))
            stack.enter_context(patch("c114.cli.load_content_analysis_inputs", return_value=object()))
            stack.enter_context(patch("c114.cli.auto_complete_content_analysis", return_value=object()))
            stack.enter_context(patch("c114.cli.save_content_analysis_yaml"))
            stack.enter_context(patch("c114.cli.generate_layer_issues", return_value=[]))
            stack.enter_context(patch("c114.cli.save_layer_issues_yaml"))
            stack.enter_context(patch("c114.cli.collect_missing_analysis_fields", return_value=[]))
            stack.enter_context(patch("c114.cli.generate_brief_markdown", return_value="# brief"))
            stack.enter_context(patch("pathlib.Path.mkdir"))
            stack.enter_context(patch("pathlib.Path.write_text"))
            resolve_review = stack.enter_context(patch("c114.cli.resolve_brief_review_output_paths"))
            build_review = stack.enter_context(patch("c114.cli.build_brief_review_report_with_llm"))
            save_review = stack.enter_context(patch("c114.cli.save_brief_review_yaml"))
            load_runtime.return_value = type(
                "Cfg",
                (),
                {
                    "search_max_external_results": 5,
                    "review_enable_step7": False,
                },
            )()
            resolve_analysis.return_value = type(
                "AnalysisPaths",
                (),
                {
                    "input_path": Path("/repo/data/raw/c114_hot_topics_20260407.json"),
                    "analysis_output": Path("/repo/reports/run1/c114_step_1_analysis_20260407.csv"),
                    "checklist_output": Path("/repo/reports/run1/c114_step_2_search_checklist_20260407.yaml"),
                },
            )()
            resolve_search.return_value = type(
                "SearchPaths",
                (),
                {
                    "input_path": Path("/repo/reports/run1/c114_step_2_search_checklist_20260407.yaml"),
                    "output_path": Path("/repo/reports/run1/c114_step_3_search_results_20260407.yaml"),
                    "provider": "auto",
                },
            )()
            resolve_content.return_value = type(
                "ContentPaths",
                (),
                {
                    "input_path": Path("/repo/reports/run1/c114_step_3_search_results_20260407.yaml"),
                    "output_path": Path("/repo/reports/run1/c114_step_4_content_20260407.yaml"),
                },
            )()
            resolve_content_analysis.return_value = type(
                "ContentAnalysisPaths",
                (),
                {
                    "input_path": Path("/repo/reports/run1/c114_step_4_content_20260407.yaml"),
                    "analysis_output": Path("/repo/reports/run1/c114_step_5_content_analysis_20260407.yaml"),
                    "issues_output": Path("/repo/reports/run1/c114_layer_issues_20260407.yaml"),
                    "brief_output": Path("/repo/reports/run1/c114_step_6_brief_20260407.md"),
                },
            )()
            resolve_review.return_value = type(
                "ReviewPaths",
                (),
                {
                    "brief_input_path": Path("/repo/reports/run1/c114_step_6_brief_20260407.md"),
                    "analysis_input_path": Path("/repo/reports/run1/c114_step_5_content_analysis_20260407.yaml"),
                    "content_input_path": Path("/repo/reports/run1/c114_step_4_content_20260407.yaml"),
                    "review_output_path": Path("/repo/reports/run1/c114_step_7_brief_review_20260407.yaml"),
                },
            )()

            run_with_args(args, paths=paths)

        build_review.assert_not_called()
        save_review.assert_not_called()

    def test_controller_agent_search_command_recognizes_completed_step3(self) -> None:
        args = Namespace(
            command="c114-search",
            date="2026-04-07",
            start_date=None,
            end_date=None,
            execution_mode="controller-agent",
            input=None,
            output=None,
            provider="auto",
            per_query_limit=1,
            per_article_limit=None,
            extract_limit=1,
        )
        paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )
        with ExitStack() as stack:
            stack.enter_context(patch("c114.cli.ensure_directories"))
            stack.enter_context(
                patch(
                    "c114.cli.load_c114_runtime_config",
                    return_value=type("Cfg", (), {"search_max_external_results": 5, "execution_mode": "controller-agent"})(),
                )
            )
            resolve_search = stack.enter_context(patch("c114.cli.resolve_search_output_paths"))
            step3_done = stack.enter_context(patch("c114.cli.step3_review_completed", return_value=True))
            write_manifest = stack.enter_context(patch("c114.cli.write_controller_agent_manifest"))
            run_search = stack.enter_context(patch("c114.cli.run_search_workflow"))
            build_search_trace = stack.enter_context(patch("c114.cli.build_search_trace_logger"))

            resolve_search.return_value = type(
                "SearchPaths",
                (),
                {
                    "input_path": Path("/repo/reports/run1/c114_step_2_search_checklist_20260407.yaml"),
                    "output_path": Path("/repo/reports/run1/c114_step_3_search_results_20260407.yaml"),
                    "provider": "auto",
                },
            )()

            with patch.object(Path, "exists", return_value=True):
                run_with_args(args, paths=paths)

        step3_done.assert_called_once()
        write_manifest.assert_not_called()
        run_search.assert_not_called()
        build_search_trace.assert_not_called()

    def test_search_command_binds_logs_to_inferred_run_directory(self) -> None:
        args = Namespace(
            command="c114-search",
            date="2026-04-10",
            start_date=None,
            end_date=None,
            execution_mode="builtin",
            input="/repo/reports/c114_report/c114_search_202604101832/c114_step_2_search_checklist_20260410.yaml",
            output="/repo/reports/c114_report/c114_step_3_search_results_20260410.yaml",
            provider="auto",
            per_query_limit=1,
            per_article_limit=None,
            extract_limit=1,
        )
        paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )
        with ExitStack() as stack:
            stack.enter_context(patch("c114.cli.ensure_directories"))
            stack.enter_context(patch("c114.cli.require_llm_client", return_value=object()))
            stack.enter_context(
                patch(
                    "c114.cli.load_c114_runtime_config",
                    return_value=type("Cfg", (), {"search_max_external_results": 5, "execution_mode": "builtin"})(),
                )
            )
            resolve_search = stack.enter_context(patch("c114.cli.resolve_search_output_paths"))
            bind_trace = stack.enter_context(patch("c114.cli.bind_llm_trace_log"))
            build_search_trace = stack.enter_context(patch("c114.cli.build_search_trace_logger", return_value=object()))
            stack.enter_context(patch("c114.cli.run_search_workflow", return_value=object()))
            stack.enter_context(patch("c114.cli.save_search_results"))

            resolve_search.return_value = type(
                "SearchPaths",
                (),
                {
                    "input_path": Path("/repo/reports/c114_report/c114_search_202604101832/c114_step_2_search_checklist_20260410.yaml"),
                    "output_path": Path("/repo/reports/c114_report/c114_step_3_search_results_20260410.yaml"),
                    "provider": "auto",
                },
            )()

            run_with_args(args, paths=paths)

        expected_run_dir = Path("/repo/reports/c114_report/c114_search_202604101832")
        bind_trace.assert_called_once()
        self.assertEqual(bind_trace.call_args.kwargs["run_dir"], expected_run_dir)
        build_search_trace.assert_called_once()
        self.assertEqual(build_search_trace.call_args.args[0], expected_run_dir)

    def test_controller_agent_review_command_reuses_existing_template_without_crashing(self) -> None:
        args = Namespace(
            command="c114-review-brief",
            date="2026-04-07",
            start_date=None,
            end_date=None,
            execution_mode="controller-agent",
            input=None,
            analysis_input=None,
            content_input=None,
            output=None,
        )
        paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )
        with ExitStack() as stack:
            stack.enter_context(patch("c114.cli.ensure_directories"))
            stack.enter_context(
                patch(
                    "c114.cli.load_c114_runtime_config",
                    return_value=type("Cfg", (), {"execution_mode": "controller-agent"})(),
                )
            )
            resolve_review = stack.enter_context(patch("c114.cli.resolve_brief_review_output_paths"))
            validate_review = stack.enter_context(
                patch("c114.cli.validate_brief_review_yaml_for_agent", side_effect=ValueError("template"))
            )
            write_manifest = stack.enter_context(patch("c114.cli.write_controller_agent_manifest", return_value=Path("/repo/reports/run1/manifest.yaml")))
            write_text = stack.enter_context(patch("pathlib.Path.write_text"))

            resolve_review.return_value = type(
                "ReviewPaths",
                (),
                {
                    "brief_input_path": Path("/repo/reports/run1/c114_step_6_brief_20260407.md"),
                    "analysis_input_path": Path("/repo/reports/run1/c114_step_5_content_analysis_20260407.yaml"),
                    "content_input_path": Path("/repo/reports/run1/c114_step_4_content_20260407.yaml"),
                    "review_output_path": Path("/repo/reports/run1/c114_step_7_brief_review_20260407.yaml"),
                },
            )()

            with patch.object(Path, "exists", return_value=True):
                run_with_args(args, paths=paths)

        validate_review.assert_called_once()
        write_manifest.assert_called_once()
        write_text.assert_not_called()

    def test_run_controller_agent_stops_at_step2_with_manifest(self) -> None:
        args = Namespace(
            command="run",
            date="2026-04-07",
            start_date=None,
            end_date=None,
            execution_mode="controller-agent",
            channels=["home"],
            candidate_limit=1,
            timeout=5.0,
            provider="auto",
            per_query_limit=1,
            per_article_limit=None,
            extract_limit=1,
        )
        paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )

        with ExitStack() as stack:
            stack.enter_context(patch("c114.cli.ensure_directories"))
            stack.enter_context(
                patch(
                    "c114.cli.load_c114_runtime_config",
                    return_value=type(
                        "Cfg",
                        (),
                        {
                            "execution_mode": "controller-agent",
                            "search_max_external_results": 5,
                            "review_enable_step7": False,
                        },
                    )(),
                )
            )
            stack.enter_context(patch("c114.cli.controller_run_directory", return_value=Path("/repo/reports/run1")))
            stack.enter_context(patch("c114.cli.collect_daily_report", return_value=object()))
            stack.enter_context(
                patch("c114.cli.resolve_hot_topics_output_path", return_value=Path("/repo/data/raw/c114_hot_topics_20260407.json"))
            )
            stack.enter_context(patch("c114.cli.save_daily_report"))
            resolve_analysis = stack.enter_context(patch("c114.cli.resolve_analysis_output_paths"))
            stack.enter_context(patch("c114.cli.analyze_daily_articles", return_value=([{"title": "x"}], ["topic"])))
            stack.enter_context(patch("c114.cli.write_analysis_outputs", return_value=[{"topic": "AI"}]))
            write_manifest = stack.enter_context(
                patch("c114.cli.write_controller_agent_manifest", return_value=Path("/repo/reports/run1/c114_execution_manifest_20260407.yaml"))
            )

            resolve_analysis.return_value = type(
                "AnalysisPaths",
                (),
                {
                    "input_path": Path("/repo/data/raw/c114_hot_topics_20260407.json"),
                    "analysis_output": Path("/repo/reports/run1/c114_step_1_analysis_20260407.csv"),
                    "checklist_output": Path("/repo/reports/run1/c114_step_2_search_checklist_20260407.yaml"),
                },
            )()

            with patch.object(Path, "exists", return_value=False):
                run_with_args(args, paths=paths)

        write_manifest.assert_called_once()
        self.assertEqual(write_manifest.call_args.kwargs["current_step"], "step_2")

    def test_run_controller_agent_advances_to_step3_after_step2_completion(self) -> None:
        args = Namespace(
            command="run",
            date="2026-04-07",
            start_date=None,
            end_date=None,
            execution_mode="controller-agent",
            channels=["home"],
            candidate_limit=1,
            timeout=5.0,
            provider="auto",
            per_query_limit=1,
            per_article_limit=None,
            extract_limit=1,
        )
        paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )

        def fake_exists(path_self: Path) -> bool:
            return path_self.name in {
                "c114_step_1_analysis_20260407.csv",
                "c114_step_2_search_checklist_20260407.yaml",
            }

        with ExitStack() as stack:
            stack.enter_context(patch("c114.cli.ensure_directories"))
            stack.enter_context(
                patch(
                    "c114.cli.load_c114_runtime_config",
                    return_value=type(
                        "Cfg",
                        (),
                        {
                            "execution_mode": "controller-agent",
                            "search_max_external_results": 5,
                            "review_enable_step7": False,
                        },
                    )(),
                )
            )
            stack.enter_context(patch("c114.cli.controller_run_directory", return_value=Path("/repo/reports/run1")))
            step2_done = stack.enter_context(patch("c114.cli.step2_keywords_completed", return_value=True))
            resolve_search = stack.enter_context(patch("c114.cli.resolve_search_output_paths"))
            stack.enter_context(patch("c114.cli.build_search_trace_logger", return_value=object()))
            run_search = stack.enter_context(patch("c114.cli.run_search_workflow", return_value=object()))
            stack.enter_context(patch("c114.cli.save_search_results"))
            write_manifest = stack.enter_context(
                patch("c114.cli.write_controller_agent_manifest", return_value=Path("/repo/reports/run1/c114_execution_manifest_20260407.yaml"))
            )

            resolve_search.return_value = type(
                "SearchPaths",
                (),
                {
                    "input_path": Path("/repo/reports/run1/c114_step_2_search_checklist_20260407.yaml"),
                    "output_path": Path("/repo/reports/run1/c114_step_3_search_results_20260407.yaml"),
                    "provider": "auto",
                },
            )()

            with patch.object(Path, "exists", fake_exists):
                run_with_args(args, paths=paths)

        step2_done.assert_called_once()
        run_search.assert_called_once()
        write_manifest.assert_called_once()
        self.assertEqual(write_manifest.call_args.kwargs["current_step"], "step_3")
