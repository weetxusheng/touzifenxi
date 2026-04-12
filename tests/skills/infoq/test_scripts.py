from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "skills" / "infoq-daily-hot-topics" / "scripts" / "infoq.py"


class InfoQScriptEntrypointTests(unittest.TestCase):
    def test_skill_uses_single_script_entrypoint(self) -> None:
        self.assertTrue(SCRIPT_PATH.exists())
        spec = importlib.util.spec_from_file_location("infoq_script", SCRIPT_PATH)
        self.assertIsNotNone(spec)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.bootstrap))
        self.assertTrue(callable(module.main))

    def test_skill_parser_exposes_infoq_commands(self) -> None:
        spec = importlib.util.spec_from_file_location("infoq_script", SCRIPT_PATH)
        self.assertIsNotNone(spec)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.bootstrap()

        from infoq.cli import build_parser

        parser = build_parser()
        commands = parser._subparsers._group_actions[0].choices
        self.assertIn("infoq-hot-topics", commands)
        self.assertIn("run", commands)


if __name__ == "__main__":
    unittest.main()
