"""`run` 命令入口。

这里负责 source 分流，真正的 C114 主流程由 `pipeline.py` 承接。
"""

from __future__ import annotations

import argparse
from typing import Any

from touzifenxi.settings import AppPaths

from ..pipeline import run_daily_pipeline


def handle_run_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """处理 `run` 命令，并按 source 选择后续流程。"""

    if getattr(args, "source", "c114") == "infoq":
        from infoq.cli import run_with_args as run_infoq_with_args

        infoq_dates = facade.resolve_c114_date_range(args, default_to_today=True)
        run_infoq_with_args(argparse.Namespace(command="run", date=infoq_dates[0].isoformat()))
        return

    run_daily_pipeline(args, paths=paths, facade=facade)
