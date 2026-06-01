from __future__ import annotations

from pathlib import Path

from chip.settings import AppPaths, ensure_directories, resolve_paths


def test_resolve_paths_returns_appPaths_dataclass():
    paths = resolve_paths()
    assert isinstance(paths, AppPaths)
    assert paths.data_dir.name == "data"
    assert paths.raw_dir == paths.data_dir / "raw"
    assert paths.reports_dir.name == "reports"


def test_ensure_directories_creates_all(tmp_path):
    paths = AppPaths(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        reports_dir=tmp_path / "reports",
        state_dir=tmp_path / "state",
    )
    ensure_directories(paths)
    assert (tmp_path / "data" / "raw").is_dir()
    assert (tmp_path / "reports").is_dir()
    assert (tmp_path / "state").is_dir()
