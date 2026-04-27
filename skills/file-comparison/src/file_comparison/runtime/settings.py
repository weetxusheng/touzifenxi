"""解析 file-comparison skill 的目录布局与运行输出路径。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import FileComparisonRuntimeConfig, load_file_comparison_runtime_config, project_root


@dataclass(frozen=True, slots=True)
class AppPaths:
    """描述 skill 运行时会用到的关键路径集合。"""

    project_root: Path
    output_root: Path
    runs_root: Path
    logs_root: Path


def resolve_paths(base_path: Path | None = None) -> AppPaths:
    """根据运行配置解析 skill 的输出与日志目录。"""
    root = project_root(base_path)
    config = load_file_comparison_runtime_config(base_path)
    if config.paths.output_mode == "project":
        output_root = (root / config.paths.output_root).resolve()
    else:
        output_root = (root / "output" / "file-comparison").resolve()
    return AppPaths(
        project_root=root,
        output_root=output_root,
        runs_root=output_root,
        logs_root=(output_root / "logs").resolve(),
    )


def ensure_directories(paths: AppPaths) -> None:
    """确保输出、运行与日志目录存在。"""
    for path in [paths.output_root, paths.runs_root, paths.logs_root]:
        path.mkdir(parents=True, exist_ok=True)


def prepare_run_dir(root_dir: str | Path, *, now: datetime | None = None) -> Path:
    """为一次新任务创建唯一的运行目录骨架。"""
    base_dir = Path(root_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    current = now or datetime.now()
    timestamp = current.strftime("%Y%m%d-%H%M%S")
    run_dir = base_dir / timestamp
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{timestamp}-{suffix:02d}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    for subdir in ("pairs", "checkpoints", "logs", "artifacts"):
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)
    return run_dir
