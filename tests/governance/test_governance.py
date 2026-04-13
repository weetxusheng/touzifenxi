from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib

from c114.c114_hot_topics import resolve_hot_topics_output_path
from c114.c114_intelligence import create_search_range_directory, resolve_analysis_output_paths
from c114.cli import build_parser as build_c114_parser
from c114.cli import resolve_c114_date_range
from c114.search.workflow import resolve_search_output_paths
from touzifenxi.cli import build_parser
from touzifenxi.settings import AppPaths
from touzifenxi.skill_packaging import package_skill_directory, validate_skill_directory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class GovernanceDocumentTests(unittest.TestCase):
    def test_agents_is_the_governance_entrypoint(self) -> None:
        content = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("docs/project-conventions.md", content)
        self.assertIn("开发前自查", content)

    def test_required_governance_documents_and_templates_exist(self) -> None:
        required_paths = [
            PROJECT_ROOT / "docs" / "project-conventions.md",
            PROJECT_ROOT / "docs" / "development-workflow.md",
            PROJECT_ROOT / "docs" / "skill-packaging.md",
            PROJECT_ROOT / "docs" / "templates" / "prompt-template.md",
            PROJECT_ROOT / "docs" / "templates" / "module-template.md",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "SKILL.md",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "agents" / "agent.yaml",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "config" / ".gitkeep",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "prompts" / ".gitkeep",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "scripts" / ".gitkeep",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "src" / ".gitkeep",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "output" / ".gitkeep",
            PROJECT_ROOT / ".pre-commit-config.yaml",
            PROJECT_ROOT / ".gitignore",
        ]
        for path in required_paths:
            self.assertTrue(path.exists(), f"Missing required governance path: {path}")

    def test_pyproject_includes_governance_tooling(self) -> None:
        payload = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = payload["project"]
        self.assertIn("optional-dependencies", project)
        self.assertIn("dev", project["optional-dependencies"])
        self.assertIn("pytest", " ".join(project["optional-dependencies"]["dev"]))
        self.assertIn("ruff", " ".join(project["optional-dependencies"]["dev"]))
        self.assertIn("pre-commit", " ".join(project["optional-dependencies"]["dev"]))
        self.assertIn("tool", payload)
        self.assertIn("ruff", payload["tool"])
        self.assertIn("pytest", payload["tool"])

    def test_cli_exposes_skill_validation_and_packaging_commands(self) -> None:
        parser = build_parser()
        commands = parser._subparsers._group_actions[0].choices
        self.assertIn("validate-skill", commands)
        self.assertIn("package-skill", commands)
        self.assertNotIn("c114-search", commands)
        self.assertNotIn("c114-review-brief", commands)

    def test_skill_cli_exposes_c114_commands(self) -> None:
        parser = build_c114_parser()
        commands = parser._subparsers._group_actions[0].choices
        self.assertIn("c114-search", commands)
        self.assertIn("c114-review-brief", commands)

    def test_skill_frontmatter_uses_slug_name_and_chinese_description(self) -> None:
        skill_docs = [
            PROJECT_ROOT / "skills" / "websearch" / "SKILL.md",
            PROJECT_ROOT / "docs" / "templates" / "skill-template" / "SKILL.md",
        ]
        for path in skill_docs:
            content = path.read_text(encoding="utf-8")
            self.assertIn("name: ", content)
            self.assertIn("description: ", content)
            self.assertNotIn("description: Use when", content)

    def test_c114_requirements_document_records_step_7_review_layer(self) -> None:
        content = (PROJECT_ROOT / "skills" / "websearch" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("当前 C114 业务链路共定义 `7` 个步骤", content)
        self.assertIn("Step 7：简报审查 YAML", content)
        self.assertIn("若某一天在 `Step 1` 分析后文章数为 `0`", content)

    def test_step_6_contract_uses_industry_researcher_language(self) -> None:
        requirements = (PROJECT_ROOT / "skills" / "websearch" / "SKILL.md").read_text(encoding="utf-8")
        brief_prompt = (PROJECT_ROOT / "skills" / "websearch" / "prompts" / "brief-agent.md").read_text(
            encoding="utf-8"
        )
        agent_config = (PROJECT_ROOT / "skills" / "websearch" / "agents" / "agent.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("行业研究员", requirements)
        self.assertIn("行业研究员", brief_prompt)
        self.assertIn("事实", brief_prompt)
        self.assertIn("推断", brief_prompt)
        self.assertIn("不确定性", brief_prompt)
        self.assertIn("不要直接写成市场评论", brief_prompt)
        self.assertIn("按产业研究员口径深挖", agent_config)

    def test_skill_readme_records_skill_private_code_and_runtime_config(self) -> None:
        content = (PROJECT_ROOT / "skills" / "websearch" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("src/c114", content)
        self.assertIn("runtime.example.json", content)
        self.assertIn("runtime.local.json", content)
        self.assertIn("config/README.md", content)

    def test_websearch_scripts_use_single_entrypoint(self) -> None:
        scripts_dir = PROJECT_ROOT / "skills" / "websearch" / "scripts"
        script_names = sorted(path.name for path in scripts_dir.glob("*.py"))
        self.assertEqual(script_names, ["websearch.py"])

    def test_websearch_is_the_only_skill_for_this_workflow(self) -> None:
        self.assertTrue((PROJECT_ROOT / "skills" / "websearch").exists())
        self.assertFalse((PROJECT_ROOT / "skills" / "infoq-daily-hot-topics").exists())
        self.assertFalse((PROJECT_ROOT / "skills" / "c114-daily-hot-topics").exists())

    def test_websearch_skill_uses_single_document_and_no_embedded_tests(self) -> None:
        skill_dir = PROJECT_ROOT / "skills" / "websearch"
        self.assertFalse((skill_dir / "README.md").exists())
        self.assertFalse((skill_dir / "tests").exists())


class C114OutputPathTests(unittest.TestCase):
    def make_app_paths(self, project_root: Path) -> AppPaths:
        return AppPaths(
            project_root=project_root,
            data_dir=project_root / "data",
            raw_dir=project_root / "data" / "raw",
            processed_dir=project_root / "data" / "processed",
            reports_dir=project_root / "reports",
            state_dir=project_root / "state",
            db_path=project_root / "state" / "touzifenxi.db",
            database_url=None,
            sample_universe_path=project_root / "data" / "universe_sample.json",
            watchlist_path=project_root / "data" / "watchlist_v2.json",
            theme_config_path=project_root / "data" / "themes_v1.json",
        )

    def test_resolve_hot_topics_output_path_uses_raw_dir_default(self) -> None:
        output_path = resolve_hot_topics_output_path(
            project_root=PROJECT_ROOT,
            raw_dir=PROJECT_ROOT / "data" / "raw",
            report_date=date(2026, 3, 31),
            output_override=None,
        )
        self.assertEqual(output_path, PROJECT_ROOT / "data" / "raw" / "c114_hot_topics_20260331.json")

    def test_resolve_analysis_output_paths_returns_consistent_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            app_paths = self.make_app_paths(project_root)

            resolved = resolve_analysis_output_paths(
                app_paths,
                date(2026, 3, 27),
                run_started_at=datetime(2026, 4, 1, 13, 30),
            )

            self.assertEqual(resolved.input_path, (project_root / "data" / "raw" / "c114_hot_topics.csv").resolve())
            self.assertEqual(
                resolved.analysis_output,
                (
                    project_root
                    / "reports"
                    / "c114_report"
                    / "c114_search_202604011330"
                    / "c114_step_1_analysis_20260327.csv"
                ).resolve(),
            )
            self.assertEqual(
                resolved.checklist_output,
                (
                    project_root
                    / "reports"
                    / "c114_report"
                    / "c114_search_202604011330"
                    / "c114_step_2_search_checklist_20260327.yaml"
                ).resolve(),
            )

    def test_resolve_search_output_paths_returns_consistent_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            app_paths = self.make_app_paths(project_root)

            run_dir = project_root / "reports" / "c114_report" / "c114_search_202604011330"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "c114_step_2_search_checklist_20260327.yaml").write_text(
                "report_date: '2026-03-27'\n", encoding="utf-8"
            )

            resolved = resolve_search_output_paths(app_paths, date(2026, 3, 27))

            self.assertEqual(
                resolved.input_path,
                (
                    project_root
                    / "reports"
                    / "c114_report"
                    / "c114_search_202604011330"
                    / "c114_step_2_search_checklist_20260327.yaml"
                ).resolve(),
            )
            self.assertEqual(
                resolved.output_path,
                (
                    project_root
                    / "reports"
                    / "c114_report"
                    / "c114_search_202604011330"
                    / "c114_step_3_search_results_20260327.yaml"
                ).resolve(),
            )

    def test_resolve_search_output_paths_uses_input_parent_for_default_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            app_paths = self.make_app_paths(project_root)
            custom_dir = project_root / "reports" / "c114_report" / "c114_search_202604011330_custom"
            custom_dir.mkdir(parents=True, exist_ok=True)
            input_path = custom_dir / "c114_step_2_search_checklist_20260327.yaml"
            input_path.write_text("report_date: '2026-03-27'\n", encoding="utf-8")

            resolved = resolve_search_output_paths(
                app_paths,
                date(2026, 3, 27),
                input_override=str(input_path.relative_to(project_root)),
            )

            self.assertEqual(resolved.input_path, input_path.resolve())
            self.assertEqual(resolved.output_path, (custom_dir / "c114_step_3_search_results_20260327.yaml").resolve())

    def test_resolve_c114_date_range_supports_single_day_and_closed_range(self) -> None:
        single = resolve_c114_date_range(Namespace(date="2026-04-04", start_date=None, end_date=None))
        closed = resolve_c114_date_range(Namespace(date=None, start_date="2026-04-04", end_date="2026-04-05"))

        self.assertEqual(single, [date(2026, 4, 4)])
        self.assertEqual(closed, [date(2026, 4, 4), date(2026, 4, 5)])

    def test_resolve_c114_date_range_rejects_mixed_or_incomplete_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能同时使用 --date 和 --start-date/--end-date"):
            resolve_c114_date_range(Namespace(date="2026-04-04", start_date="2026-04-04", end_date="2026-04-05"))
        with self.assertRaisesRegex(ValueError, "必须同时提供 --start-date 和 --end-date"):
            resolve_c114_date_range(Namespace(date=None, start_date="2026-04-04", end_date=None))

    def test_create_search_range_directory_uses_dedicated_range_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports_dir = Path(tmp_dir) / "reports"
            run_dir = create_search_range_directory(
                reports_dir,
                start_date=date(2026, 4, 4),
                end_date=date(2026, 4, 5),
                run_started_at=datetime(2026, 4, 6, 9, 27),
            )

            self.assertEqual(
                run_dir,
                reports_dir / "c114_report" / "c114_range_20260404_20260405_202604060927",
            )


class SkillPackagingTests(unittest.TestCase):
    def test_validate_skill_directory_accepts_current_c114(self) -> None:
        skill_dir = PROJECT_ROOT / "skills" / "websearch"
        shutil.rmtree(skill_dir / "scripts" / "__pycache__", ignore_errors=True)
        issues = validate_skill_directory(skill_dir)
        self.assertEqual(issues, [])

    def test_validate_skill_directory_rejects_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_dir = Path(tmp_dir) / "demo-skill"
            (skill_dir / "agents").mkdir(parents=True)
            issues = validate_skill_directory(skill_dir)
        self.assertTrue(any("SKILL.md" in issue for issue in issues))

    def test_package_skill_directory_excludes_pycache(self) -> None:
        skill_dir = PROJECT_ROOT / "skills" / "websearch"
        shutil.rmtree(skill_dir / "scripts" / "__pycache__", ignore_errors=True)
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "skill.zip"
            package_skill_directory(skill_dir, output_path)
            with zipfile.ZipFile(output_path) as archive:
                names = archive.namelist()
        self.assertTrue(any(name.endswith("SKILL.md") for name in names))
        self.assertFalse(any("__pycache__" in name for name in names))

    def test_package_skill_directory_excludes_runtime_outputs(self) -> None:
        skill_dir = PROJECT_ROOT / "skills" / "websearch"
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "skill.zip"
            package_skill_directory(skill_dir, output_path)
            with zipfile.ZipFile(output_path) as archive:
                names = archive.namelist()

        self.assertIn("websearch/output/.gitkeep", names)
        self.assertFalse(
            any(
                name.startswith("websearch/output/reports/")
                or name.startswith("websearch/output/data/")
                or name.startswith("websearch/output/state/")
                for name in names
            )
        )
