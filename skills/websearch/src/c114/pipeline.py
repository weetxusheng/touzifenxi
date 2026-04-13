"""统一承载 `run` 主流程的调度入口。"""

from __future__ import annotations

import argparse
from typing import Any

from touzifenxi.settings import AppPaths

from .pipeline_steps import run_builtin_daily_pipeline, run_controller_daily_pipeline


def run_daily_pipeline(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 `run --source c114` 的完整日报流程。"""

    execution_mode = facade.resolve_requested_execution_mode(args)
    target_dates = facade.resolve_c114_date_range(args, default_to_today=True)
    runtime_config = facade.load_c114_runtime_config()
    llm_client = facade.require_llm_client() if execution_mode == "builtin" else None
    if execution_mode == "builtin":
        if len(target_dates) > 1:
            day_dirs = facade.create_c114_range_day_directories(paths, target_dates)
        else:
            run_dir = facade.create_search_run_directory(paths.reports_dir)
            day_dirs = {target_dates[0]: run_dir}
    else:
        day_dirs = {target_date: facade.controller_run_directory(paths, target_date) for target_date in target_dates}

    for target_date in target_dates:
        day_dir = day_dirs[target_date]
        facade.bind_llm_trace_log(
            llm_client,
            run_dir=day_dir,
            target_date=target_date,
            reset_file=execution_mode == "builtin",
        )
        if execution_mode == "controller-agent":
            run_controller_daily_pipeline(
                args=args,
                paths=paths,
                facade=facade,
                runtime_config=runtime_config,
                target_date=target_date,
                day_dir=day_dir,
            )
            continue
        run_builtin_daily_pipeline(
            args=args,
            paths=paths,
            facade=facade,
            runtime_config=runtime_config,
            llm_client=llm_client,
            target_date=target_date,
            day_dir=day_dir,
        )
