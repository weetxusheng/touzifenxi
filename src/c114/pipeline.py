"""统一承载 `run` 主流程的调度入口。"""

from __future__ import annotations

import argparse
from typing import Any

from utils.tools.settings import AppPaths
from utils.tools.orchestration import (
    run_c114_builtin_daily_pipeline,
    run_c114_controller_daily_pipeline,
    run_source_daily_pipeline,
)


def run_daily_pipeline(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 `run --source c114` 的完整日报流程。"""

    def _resolve_dates(cli_args: argparse.Namespace) -> list[object]:
        return facade.resolve_c114_date_range(cli_args, default_to_today=True)

    def _run_controller_for_day(target_date: object, day_dir: object, runtime_config: object) -> None:
        run_c114_controller_daily_pipeline(
            args=args,
            paths=paths,
            facade=facade,
            runtime_config=runtime_config,
            target_date=target_date,
            day_dir=day_dir,
        )

    def _run_builtin_for_day(target_date: object, day_dir: object, runtime_config: object, llm_client: object) -> None:
        run_c114_builtin_daily_pipeline(
            args=args,
            paths=paths,
            facade=facade,
            runtime_config=runtime_config,
            llm_client=llm_client,
            target_date=target_date,
            day_dir=day_dir,
        )

    run_source_daily_pipeline(
        args=args,
        paths=paths,
        facade=facade,
        resolve_dates=_resolve_dates,
        run_builtin_for_day=_run_builtin_for_day,
        run_controller_for_day=_run_controller_for_day,
    )
