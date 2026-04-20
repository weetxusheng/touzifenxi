"""Shared daily pipeline orchestration loop.

This module keeps source-agnostic control flow (date expansion, execution-mode
branching, run-dir allocation, and per-day dispatch) out of source-specific
packages such as `c114`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from utils.tools.settings import AppPaths


def run_source_daily_pipeline(
    *,
    args: argparse.Namespace,
    paths: AppPaths,
    facade: Any,
    resolve_dates: Callable[[argparse.Namespace], list[object]],
    run_builtin_for_day: Callable[[object, object, object, object], None],
    run_controller_for_day: Callable[[object, object, object], None],
) -> None:
    """Run a source pipeline across one or multiple dates.

    The caller provides source-specific day executors while this function
    handles shared scheduling mechanics.
    """

    execution_mode = facade.resolve_requested_execution_mode(args)
    target_dates = resolve_dates(args)
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
            run_controller_for_day(target_date, day_dir, runtime_config)
            continue
        run_builtin_for_day(target_date, day_dir, runtime_config, llm_client)
