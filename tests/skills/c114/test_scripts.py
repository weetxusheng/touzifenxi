from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

from c114.cli import build_parser

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "skills" / "c114-daily-hot-topics" / "scripts" / "c114.py"


class ScriptEntrypointTests(unittest.TestCase):
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
