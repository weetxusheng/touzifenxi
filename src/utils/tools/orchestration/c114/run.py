"""`run` 命令入口。

这里负责 source 分流，真正的 C114 主流程由 `pipeline.py` 承接。
"""

from __future__ import annotations

import argparse
from typing import Any

from utils.tools.settings import AppPaths

from c114.pipeline import run_daily_pipeline
from utils.tools.analysis.step5_recovery_notes import clear_step5_recovery_messages, drain_step5_recovery_messages


def handle_run_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """处理 `run` 命令，并按 source 选择后续流程。"""

    if getattr(args, "source", "c114") == "36kr":
        from kr36.cli import run_with_args as run_kr36_with_args

        target_dates = facade.resolve_c114_date_range(args, default_to_today=True)
        run_kr36_with_args(
            argparse.Namespace(
                command="run",
                date=target_dates[0].isoformat(),
                provider=getattr(args, "provider", "auto"),
                per_query_limit=getattr(args, "per_query_limit", 5),
                per_article_limit=getattr(args, "per_article_limit", None),
                extract_limit=getattr(args, "extract_limit", 5),
                external_search=bool(getattr(args, "external_search", False)),
            )
        )
        return

    if getattr(args, "source", "c114") == "infoq":
        from infoq.cli import run_with_args as run_infoq_with_args

        infoq_dates = facade.resolve_c114_date_range(args, default_to_today=True)
        run_infoq_with_args(argparse.Namespace(command="run", date=infoq_dates[0].isoformat()))
        return

    clear_step5_recovery_messages()
    try:
        run_daily_pipeline(args, paths=paths, facade=facade)
    except BaseException as exc:
        soft = drain_step5_recovery_messages()
        from c114.ops_alerts import send_c114_pipeline_failure_alert

        send_c114_pipeline_failure_alert(project_root=paths.project_root, exc=exc, soft_notes=soft)
        raise
    else:
        soft = drain_step5_recovery_messages()
        if soft:
            from c114.ops_alerts import send_c114_pipeline_soft_digest

            send_c114_pipeline_soft_digest(project_root=paths.project_root, messages=soft)
