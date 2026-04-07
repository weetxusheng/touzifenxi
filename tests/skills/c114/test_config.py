from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from c114.config import (
    collect_missing_c114_config,
    initialize_c114_local_config,
    load_c114_runtime_config,
    read_c114_local_config,
    runtime_example_path,
    runtime_local_path,
    write_c114_local_config,
)


class C114ConfigTests(unittest.TestCase):
    def test_initialize_c114_local_config_copies_example_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.example.json").write_text('{"keys":{"tavily_api_key":""}}\n', encoding="utf-8")

            local_path = initialize_c114_local_config(skill_root)
            local_text = local_path.read_text(encoding="utf-8")
            example_text = runtime_example_path(skill_root).read_text(encoding="utf-8")

        self.assertEqual(local_path, runtime_local_path(skill_root))
        self.assertEqual(local_text, example_text)

    def test_collect_missing_c114_config_reports_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "config").mkdir(parents=True, exist_ok=True)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")

            missing = collect_missing_c114_config(skill_root)

        self.assertIn("keys.search_provider_api_key", missing)
        self.assertIn("llm.api_key", missing)
        self.assertIn("llm.model", missing)
        self.assertIn("llm.base_url", missing)
        self.assertIn("search.recent_days", missing)
        self.assertIn("brief.role", missing)

    def test_collect_missing_c114_config_accepts_any_single_search_provider_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "keys": {
                            "baidu_api_key": "b",
                            "aliyun_iqs_api_key": "a",
                        },
                        "llm": {
                            "provider": "minimax",
                            "model": "MiniMax M2.7",
                            "api_key": "llm-key",
                            "base_url": "https://api.minimaxi.com/v1",
                        },
                        "search": {"recent_days": 30, "max_external_results": 5},
                        "content": {"fetch_keep_levels": ["strong", "weak"]},
                        "brief": {"role": "senior_researcher"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            missing = collect_missing_c114_config(skill_root)

        self.assertNotIn("keys.search_provider_api_key", missing)

    def test_write_c114_local_config_merges_existing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps({"keys": {"tavily_api_key": "old"}, "search": {"recent_days": 30}}, ensure_ascii=False),
                encoding="utf-8",
            )

            write_c114_local_config(
                skill_root,
                {
                    "keys": {"baidu_api_key": "new"},
                    "search": {"max_external_results": 5},
                    "brief": {"role": "senior_researcher"},
                },
            )

            written = read_c114_local_config(skill_root)
            written_path = runtime_local_path(skill_root)

        self.assertEqual(written["keys"]["tavily_api_key"], "old")
        self.assertEqual(written["keys"]["baidu_api_key"], "new")
        self.assertEqual(written["search"]["recent_days"], 30)
        self.assertEqual(written["search"]["max_external_results"], 5)
        self.assertEqual(written["brief"]["role"], "senior_researcher")
        self.assertTrue(str(written_path).endswith("config/runtime.local.json"))

    def test_load_c114_runtime_config_defaults_to_skill_output_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "keys": {
                            "tavily_api_key": "t",
                            "metaso_api_key": "m",
                            "baidu_api_key": "b",
                            "aliyun_iqs_api_key": "a",
                        },
                        "llm": {
                            "provider": "minimax",
                            "model": "MiniMax M2.7",
                            "api_key": "llm-key",
                            "base_url": "https://api.minimaxi.com/v1",
                            "timeout_seconds": 45,
                            "max_retries": 3,
                        },
                        "search": {"recent_days": 30, "max_external_results": 5},
                        "content": {"fetch_keep_levels": ["strong", "weak"]},
                        "brief": {"role": "senior_researcher"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_c114_runtime_config(skill_root)

        self.assertEqual(config.output_mode, "skill")
        self.assertEqual(config.llm_provider, "minimax")
        self.assertEqual(config.llm_model, "MiniMax M2.7")
        self.assertEqual(config.llm_api_key, "llm-key")
        self.assertEqual(config.llm_base_url, "https://api.minimaxi.com/v1")
        self.assertEqual(config.llm_timeout_seconds, 45.0)
        self.assertEqual(config.llm_max_retries, 3)

    def test_load_c114_runtime_config_normalizes_project_output_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "keys": {
                            "tavily_api_key": "t",
                            "metaso_api_key": "m",
                            "baidu_api_key": "b",
                            "aliyun_iqs_api_key": "a",
                        },
                        "llm": {
                            "provider": "minimax",
                            "model": "MiniMax M2.7",
                            "api_key": "llm-key",
                            "base_url": "https://api.minimaxi.com/v1",
                        },
                        "search": {"recent_days": 30, "max_external_results": 5},
                        "content": {"fetch_keep_levels": ["strong", "weak"]},
                        "brief": {"role": "senior_researcher"},
                        "paths": {"output_mode": "project_shared"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_c114_runtime_config(skill_root)

        self.assertEqual(config.output_mode, "project")

    def test_load_c114_runtime_config_enables_step7_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "keys": {
                            "tavily_api_key": "t",
                            "aliyun_iqs_api_key": "a",
                        },
                        "llm": {
                            "provider": "minimax",
                            "model": "MiniMax M2.7",
                            "api_key": "llm-key",
                            "base_url": "https://api.minimaxi.com/v1",
                        },
                        "search": {"recent_days": 30, "max_external_results": 5},
                        "content": {"fetch_keep_levels": ["strong", "weak"]},
                        "brief": {"role": "senior_researcher"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_c114_runtime_config(skill_root)

        self.assertTrue(config.review_enable_step7)

    def test_load_c114_runtime_config_reads_step7_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir)
            (skill_root / "SKILL.md").write_text("---\nname: demo\ndescription: 示例\n---\n", encoding="utf-8")
            config_dir = skill_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "runtime.local.json").write_text(
                json.dumps(
                    {
                        "keys": {
                            "tavily_api_key": "t",
                            "aliyun_iqs_api_key": "a",
                        },
                        "llm": {
                            "provider": "minimax",
                            "model": "MiniMax M2.7",
                            "api_key": "llm-key",
                            "base_url": "https://api.minimaxi.com/v1",
                        },
                        "search": {"recent_days": 30, "max_external_results": 5},
                        "content": {"fetch_keep_levels": ["strong", "weak"]},
                        "brief": {"role": "senior_researcher"},
                        "review": {"enable_step7": False},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_c114_runtime_config(skill_root)

        self.assertFalse(config.review_enable_step7)
