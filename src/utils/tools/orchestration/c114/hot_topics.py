"""热点抓取命令。"""

from __future__ import annotations

import argparse
from typing import Any

from utils.tools.settings import AppPaths


def handle_hot_topics_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 step 0 热点抓取。"""

    target_dates = facade.resolve_c114_date_range(args, default_to_today=True)
    for target_date in target_dates:
        reports = facade.collect_daily_report(
            report_date=target_date,
            channel_keys=args.channels,
            timeout=float(args.timeout),
            candidate_limit=int(args.candidate_limit),
        )
        output_path = facade.resolve_hot_topics_output_path(
            project_root=paths.project_root,
            raw_dir=paths.raw_dir,
            report_date=target_date,
            output_override=args.output,
        )
        facade.save_daily_report(output_path, target_date, reports)
        print(facade.render_daily_report(reports, target_date))
        print(f"\nJSON 已写入: {output_path}\n")
