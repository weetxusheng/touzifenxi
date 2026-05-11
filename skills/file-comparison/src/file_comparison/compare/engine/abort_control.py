"""任务级协作式暂停：通过运行目录下的标记文件在文件对之间停止执行。"""

from __future__ import annotations

from pathlib import Path

ABORT_REQUESTED_FILENAME = "abort_requested"
USER_ABORT_PAIR_MESSAGE = "用户已暂停当次任务"


def abort_requested_path(run_dir: Path) -> Path:
    return run_dir / ABORT_REQUESTED_FILENAME


def clear_abort_requested_flag(run_dir: Path) -> None:
    path = abort_requested_path(run_dir)
    if path.exists():
        path.unlink()


def touch_abort_requested_flag(run_dir: Path) -> None:
    abort_requested_path(run_dir).write_text("", encoding="utf-8")


def is_abort_requested(run_dir: Path) -> bool:
    return abort_requested_path(run_dir).exists()
