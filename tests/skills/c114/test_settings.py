from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from c114.settings import resolve_override_path


class OverridePathTests(unittest.TestCase):
    def test_resolve_override_path_uses_project_root_for_managed_directories(self) -> None:
        resolved = resolve_override_path(Path("/repo/skills/websearch/output"), "reports/c114_report/run/file.yaml")
        self.assertEqual(
            resolved,
            Path("/repo/skills/websearch/output/reports/c114_report/run/file.yaml"),
        )

    def test_resolve_override_path_uses_cwd_for_repo_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            original_cwd = Path.cwd()
            try:
                os.chdir(repo_root)
                resolved = resolve_override_path(
                    repo_root / "skills" / "websearch" / "output",
                    "skills/websearch/output/reports/c114_report/run/file.yaml",
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(
            resolved,
            (repo_root / "skills" / "websearch" / "output" / "reports" / "c114_report" / "run" / "file.yaml").resolve(),
        )
