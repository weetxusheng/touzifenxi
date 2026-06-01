"""Skill-local path and environment bootstrap helpers.

By default the C114 skill writes into its own ``output/`` directory so the
package can be zipped and used in a standalone workspace. A caller can opt into
shared project directories through the skill-local runtime config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import C114RuntimeConfig, load_c114_runtime_config, skill_root


@dataclass(frozen=True)
class AppPaths:
    """Resolved project paths used by all C114 workflow steps."""

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
    """Load `.env.local` values once without overriding existing environment vars."""

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
    """Resolve writable directories for either standalone skill mode or project mode."""

    cwd = Path.cwd().resolve()
    current_skill_root = skill_root()
    runtime_config = load_c114_runtime_config(current_skill_root)
    load_local_env(current_skill_root)

    if runtime_config.output_mode == "project":
        base_root = cwd
        load_local_env(base_root)
    else:
        base_root = current_skill_root / "output"

    data_dir = base_root / "data"
    state_dir = base_root / "state"
    database_url = os.getenv("TOUZIFENXI_DATABASE_URL")
    return AppPaths(
        project_root=base_root,
        data_dir=data_dir,
        raw_dir=data_dir / "raw",
        processed_dir=data_dir / "processed",
        reports_dir=base_root / "reports",
        state_dir=state_dir,
        db_path=state_dir / "touzifenxi.db",
        database_url=database_url,
        sample_universe_path=data_dir / "universe_sample.json",
        watchlist_path=data_dir / "watchlist_v2.json",
        theme_config_path=data_dir / "themes_v1.json",
    )


def ensure_directories(paths: AppPaths) -> None:
    """Create shared writable directories required by the C114 workflow."""

    for path in [paths.data_dir, paths.raw_dir, paths.processed_dir, paths.reports_dir, paths.state_dir]:
        path.mkdir(parents=True, exist_ok=True)


def resolve_override_path(project_root: Path, override: str) -> Path:
    """解析命令行覆盖路径。

    规则：
    1. 绝对路径原样使用。
    2. `reports/`、`data/`、`state/` 开头的相对路径按 skill 输出根目录解析。
    3. 其它相对路径按当前工作目录解析，兼容从仓库根目录传入
       `skills/.../output/...` 这类路径。
    """

    candidate = Path(override).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    managed_roots = {"reports", "data", "state"}
    if candidate.parts and candidate.parts[0] in managed_roots:
        return (project_root / candidate).resolve()
    return (Path.cwd().resolve() / candidate).resolve()


__all__ = [
    "AppPaths",
    "C114RuntimeConfig",
    "ensure_directories",
    "load_c114_runtime_config",
    "load_local_env",
    "resolve_override_path",
    "resolve_paths",
]
