from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    reports_dir: Path
    state_dir: Path


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_local_env(project_root: Path) -> None:
    env_path = project_root / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip()


def resolve_paths() -> AppPaths:
    root = skill_root() / "output"
    load_local_env(Path.cwd().resolve())
    return AppPaths(
        project_root=root,
        data_dir=root / "data",
        raw_dir=root / "data" / "raw",
        processed_dir=root / "data" / "processed",
        reports_dir=root / "reports",
        state_dir=root / "state",
    )


def ensure_directories(paths: AppPaths) -> None:
    for path in (paths.data_dir, paths.raw_dir, paths.processed_dir, paths.reports_dir, paths.state_dir):
        path.mkdir(parents=True, exist_ok=True)
