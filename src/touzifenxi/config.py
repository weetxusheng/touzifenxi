from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    universe_path: Path
    watchlist_path: Path
    reports_dir: Path
    top_n: int = 5


def load_config() -> AppConfig:
    cwd = Path.cwd().resolve()
    package_base = Path(__file__).resolve().parents[2]
    if (cwd / "data").exists():
        base_dir = cwd
    else:
        base_dir = package_base
    return AppConfig(
        base_dir=base_dir,
        universe_path=base_dir / "data" / "universe_sample.json",
        watchlist_path=base_dir / "data" / "watchlist_v2.json",
        reports_dir=base_dir / "reports",
    )
