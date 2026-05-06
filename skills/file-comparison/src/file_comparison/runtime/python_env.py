"""统一选择 skill 推荐的 Python 解释器。"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path


def project_root_from_skill_root(skill_root: Path) -> Path:
    """从 skill 根目录推导项目根目录。"""
    return skill_root.parent.parent


def candidate_project_python_paths(skill_root: Path) -> Iterable[Path]:
    """按优先级列出项目内可用的 Python 解释器。"""
    project_root = project_root_from_skill_root(skill_root)
    yield project_root / ".venv" / "bin" / "python3"
    yield project_root / ".venv" / "bin" / "python"


def resolve_python_executable(
    *,
    skill_root: Path,
    explicit_python_executable: str | None = None,
    current_executable: str | None = None,
) -> str:
    """解析当前 skill 应优先使用的 Python 解释器。"""
    if explicit_python_executable:
        return explicit_python_executable
    for candidate in candidate_project_python_paths(skill_root):
        if candidate.exists():
            return str(candidate)
    return current_executable or sys.executable


def _same_executable(left: str, right: str) -> bool:
    """判断两个解释器路径是否指向同一个文件。"""
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def maybe_reexec_into_project_python(
    *,
    skill_root: Path,
    script_path: Path,
    argv: list[str] | None = None,
    current_executable: str | None = None,
    execv: Callable[[str, list[str]], object] = os.execv,
) -> bool:
    """必要时切换到项目 `.venv` 的 Python 重新执行当前脚本。"""
    target_executable = resolve_python_executable(
        skill_root=skill_root,
        current_executable=current_executable,
    )
    effective_current = current_executable or sys.executable
    if _same_executable(target_executable, effective_current):
        return False
    execv(target_executable, [target_executable, str(script_path), *(argv or [])])
    return True
