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
    db_path: Path
    database_url: str | None
    sample_universe_path: Path
    watchlist_path: Path
    theme_config_path: Path


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
    cwd = Path.cwd().resolve()
    package_root = Path(__file__).resolve().parents[2]
    project_root = cwd if (cwd / "data").exists() else package_root
    load_local_env(project_root)
    data_dir = project_root / "data"
    state_dir = project_root / "state"
    database_url = os.getenv("TOUZIFENXI_DATABASE_URL")
    return AppPaths(
        project_root=project_root,
        data_dir=data_dir,
        raw_dir=data_dir / "raw",
        processed_dir=data_dir / "processed",
        reports_dir=project_root / "reports",
        state_dir=state_dir,
        db_path=state_dir / "touzifenxi.db",
        database_url=database_url,
        sample_universe_path=data_dir / "universe_sample.json",
        watchlist_path=data_dir / "watchlist_v2.json",
        theme_config_path=data_dir / "themes_v1.json",
    )


def ensure_directories(paths: AppPaths) -> None:
    for path in [paths.data_dir, paths.raw_dir, paths.processed_dir, paths.reports_dir, paths.state_dir]:
        path.mkdir(parents=True, exist_ok=True)
