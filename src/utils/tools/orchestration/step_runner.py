"""Source step runner adapters.

This module centralizes orchestration entrypoints so source packages can keep
thin wrappers while gradually migrating concrete step logic into `utils`.
"""

from __future__ import annotations

import argparse
from typing import Any

from utils.tools.settings import AppPaths


def run_c114_controller_daily_pipeline(
    *,
    args: argparse.Namespace,
    paths: AppPaths,
    facade: Any,
    runtime_config: object,
    target_date: object,
    day_dir: object,
) -> None:
    from utils.tools.orchestration.c114.pipeline_steps import run_controller_daily_pipeline

    run_controller_daily_pipeline(
        args=args,
        paths=paths,
        facade=facade,
        runtime_config=runtime_config,
        target_date=target_date,
        day_dir=day_dir,
    )


def run_c114_builtin_daily_pipeline(
    *,
    args: argparse.Namespace,
    paths: AppPaths,
    facade: Any,
    runtime_config: object,
    llm_client: object,
    target_date: object,
    day_dir: object,
) -> None:
    from utils.tools.orchestration.c114.pipeline_steps import run_builtin_daily_pipeline

    run_builtin_daily_pipeline(
        args=args,
        paths=paths,
        facade=facade,
        runtime_config=runtime_config,
        llm_client=llm_client,
        target_date=target_date,
        day_dir=day_dir,
    )

