"""解析 file-comparison skill 的目录布局与运行输出路径。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import load_file_comparison_runtime_config, project_root


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
        output_root = (root / "output").resolve()
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
    (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
    return run_dir


def pair_dir_for(run_dir: Path, pair_id: str) -> Path:
    """返回 pair 工作目录路径（P0-④ 扁平布局：`<run_dir>/<pair_id>/`）。

    兼容历史命名：若新路径不存在但旧路径 `<run_dir>/pairs/<pair_id>/` 存在，则就地
    迁移到新位置（rename），保留断点续跑能力。新任务直接写入新路径。
    """
    new_path = run_dir / pair_id
    if not new_path.exists():
        legacy = run_dir / "pairs" / pair_id
        if legacy.exists():
            legacy.replace(new_path)
            try:
                legacy.parent.rmdir()
            except OSError:
                pass  # 同 run_dir 下还有其它 pair 未迁移，保留 pairs/ 目录
    return new_path


def iter_pair_dirs(run_dir: Path) -> list[Path]:
    """列出指定运行目录下所有 pair 工作目录。

    会顺手把仍位于旧 `<run_dir>/pairs/<pair_id>/` 的目录迁移到新扁平位置，避免后续
    枚举逻辑两边都要兼容。返回结果按目录名排序。
    """
    legacy_root = run_dir / "pairs"
    if legacy_root.is_dir():
        for legacy in list(legacy_root.iterdir()):
            if legacy.is_dir() and (legacy / "pair.json").exists():
                target = run_dir / legacy.name
                if not target.exists():
                    legacy.replace(target)
        try:
            legacy_root.rmdir()
        except OSError:
            pass
    if not run_dir.is_dir():
        return []
    return sorted(
        path for path in run_dir.iterdir()
        if path.is_dir() and path.name.startswith("pair-") and (path / "pair.json").exists()
    )
