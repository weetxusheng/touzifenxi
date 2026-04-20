from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .settings import ensure_directories, resolve_paths
from .skill_packaging import (
    package_skill_directory,
    resolve_skill_directory,
    skill_archive_name,
    validate_skill_directory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the multi-agent A-share research team.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize SQLite storage.")
    subparsers.add_parser("paths", help="Show important system paths.")
    subparsers.add_parser("db-info", help="Show active/configured database backend status.")
    migrate_parser = subparsers.add_parser(
        "migrate-to-postgres", help="Migrate all SQLite history into the configured PostgreSQL database."
    )
    migrate_parser.add_argument(
        "--reset-target",
        action="store_true",
        help="Truncate the PostgreSQL target before importing SQLite history.",
    )
    serve_web_parser = subparsers.add_parser("serve-web", help="Start the local research dashboard.")
    serve_web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind the local dashboard server.")
    serve_web_parser.add_argument("--port", type=int, default=8787, help="Port to bind the local dashboard server.")
    subparsers.add_parser("performance", help="Show recommendation performance coverage and basic win-rate stats.")
    validate_skill_parser = subparsers.add_parser(
        "validate-skill", help="Validate one skill directory against the project packaging rules."
    )
    validate_skill_parser.add_argument("--skill", required=True, help="Skill directory name under skills/.")
    package_skill_parser = subparsers.add_parser(
        "package-skill", help="Package one skill directory into a zip archive under build/."
    )
    package_skill_parser.add_argument("--skill", required=True, help="Skill directory name under skills/.")
    package_skill_parser.add_argument(
        "--output", default=None, help="Optional zip output path. Defaults to build/<skill-name>-skill.zip."
    )
    send_email_parser = subparsers.add_parser("send-email", help="通过项目级 SMTP 渠道发送邮件。")
    send_email_parser.add_argument("--to", nargs="+", required=True, help="一个或多个收件人邮箱地址。")
    send_email_parser.add_argument("--subject", required=True, help="邮件主题。")
    send_email_parser.add_argument("--body", default="", help="邮件正文。")
    send_email_parser.add_argument("--body-file", default=None, help="从文件读取邮件正文。")
    send_email_parser.add_argument("--html-file", default=None, help="从文件读取 HTML 正文。")
    send_email_parser.add_argument(
        "--attach",
        nargs="*",
        default=[],
        help="一个或多个附件路径，支持相对项目根目录传入。",
    )
    c114_runner_parser = subparsers.add_parser(
        "run-c114-daily-brief",
        help="以项目内统一 runner 运行 C114 当日简报，并在成功后发送正文版邮件。",
    )
    c114_runner_parser.add_argument(
        "--date",
        default=None,
        help="可选日期，格式 YYYY-MM-DD；不传时使用 Asia/Shanghai 的 T-1 日期。",
    )
    c114_runner_parser.add_argument(
        "--no-email",
        action="store_true",
        help="仅跑流水线并生成本地 Step6 与邮件版 HTML/TXT，不发送邮件。",
    )
    c114_runner_parser.add_argument(
        "--to",
        nargs="+",
        default=["zx944532395@sina.com"],
        help="邮件收件人列表。",
    )
    send_c114_latest_parser = subparsers.add_parser(
        "send-c114-latest-brief-email",
        help="仅根据 Step6 简报渲染并发送邮件，不重新跑流水线；可与 --t1-gate 联动早间跑数后再发信。",
    )
    send_c114_latest_parser.add_argument(
        "--to",
        nargs="+",
        default=["zx944532395@sina.com"],
        help="邮件收件人列表。",
    )
    send_c114_latest_parser.add_argument(
        "--t1-gate",
        action="store_true",
        help=(
            "按 Asia/Shanghai 的 T-1 统计日选取 Step6，并要求其在「上海」今日不早于 --gate-time 生成"
            "（用于定时：先跑流水线，再在稍后任务中校验产物后再发邮件）。"
        ),
    )
    send_c114_latest_parser.add_argument(
        "--gate-time",
        default="10:00",
        help="与 --t1-gate 联用：Step6 的 mtime 不得早于上海当日的该时刻（默认 10:00）。",
    )
    send_c114_latest_parser.add_argument(
        "--report-date",
        default=None,
        help="指定统计日 YYYY-MM-DD；仅发送该日的 c114_step_6_brief_YYYYMMDD.md（不要与 --t1-gate 同用）。",
    )
    send_c114_latest_parser.add_argument(
        "--require-built-not-before",
        dest="require_built_not_before",
        default=None,
        help="与 --report-date 联用：要求 Step6 在上海「今日」不早于 HH:MM 写盘。",
    )
    coverage_parser = subparsers.add_parser(
        "coverage", help="Show current candidate coverage for industries and daily factors."
    )
    coverage_parser.add_argument("--min-amount", type=float, default=100_000_000, help="Minimum成交额 filter.")
    coverage_parser.add_argument(
        "--include-bse",
        action="store_true",
        help="Include BSE stocks instead of excluding them.",
    )
    sync_parser = subparsers.add_parser("sync-universe", help="Sync full A-share universe snapshot.")
    sync_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    sync_industry_parser = subparsers.add_parser(
        "sync-industries", help="Sync industry dictionaries from available sources."
    )
    sync_industry_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    sync_stock_industry_parser = subparsers.add_parser(
        "sync-stock-industries", help="Sync formal stock-industry mappings for current candidates."
    )
    sync_stock_industry_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    sync_stock_industry_parser.add_argument("--limit", type=int, default=200, help="Maximum candidate stocks to map.")
    sync_stock_industry_parser.add_argument(
        "--offset", type=int, default=0, help="Start offset within the filtered candidate pool."
    )
    sync_stock_industry_parser.add_argument(
        "--all",
        action="store_true",
        help="Sync from the full filtered candidate pool instead of only unmapped stocks.",
    )
    sync_financials_parser = subparsers.add_parser(
        "sync-financials", help="Sync real financial profiles for current candidates."
    )
    sync_financials_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    sync_financials_parser.add_argument("--limit", type=int, default=100, help="Maximum candidate stocks to sync.")
    sync_financials_parser.add_argument(
        "--offset", type=int, default=0, help="Start offset within the filtered candidate pool."
    )
    sync_financials_parser.add_argument(
        "--all",
        action="store_true",
        help="Sync from the full filtered candidate pool instead of only missing financials.",
    )
    sync_factors_parser = subparsers.add_parser(
        "sync-factors", help="Sync daily technical and fundamental factors for current candidates."
    )
    sync_factors_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    sync_factors_parser.add_argument(
        "--candidate-limit",
        type=int,
        default=300,
        help="Maximum number of synced-universe candidates to fetch factors for.",
    )
    sync_factors_parser.add_argument(
        "--offset", type=int, default=0, help="Start offset within the filtered candidate pool."
    )
    sync_factors_parser.add_argument(
        "--all",
        action="store_true",
        help="Sync from the full filtered candidate pool instead of only missing factor rows.",
    )
    sync_foundation_parser = subparsers.add_parser(
        "sync-foundation", help="Batch sync industries, financials, and daily factors for the candidate pool."
    )
    sync_foundation_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    sync_foundation_parser.add_argument("--batch-size", type=int, default=10, help="Batch size per sync step.")
    sync_foundation_parser.add_argument(
        "--batches", type=int, default=2, help="Number of batches to run in this invocation."
    )
    sync_foundation_parser.add_argument(
        "--offset", type=int, default=None, help="Starting batch offset within the filtered candidate pool."
    )
    sync_foundation_parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="Reset the stored foundation sync cursor before running.",
    )
    build_weekly_pool_parser = subparsers.add_parser(
        "build-weekly-pool", help="Build the weekly prefiltered 50-stock pool."
    )
    build_weekly_pool_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for event data or inherit system proxy settings.",
    )
    refresh_weekly_pool_parser = subparsers.add_parser(
        "refresh-weekly-pool", help="Lightly refresh the latest weekly pool by replacing up to 5 names."
    )
    refresh_weekly_pool_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for event data or inherit system proxy settings.",
    )
    refresh_weekly_pool_parser.add_argument(
        "--refresh-limit", type=int, default=5, help="Maximum number of new names to add into the weekly pool."
    )
    refresh_weekly_pool_parser.add_argument(
        "--candidate-limit",
        type=int,
        default=200,
        help="Maximum number of broad candidates to inspect during the light refresh.",
    )
    subparsers.add_parser("show-weekly-pool", help="Show the latest weekly prefiltered pool summary.")
    daily_cycle_parser = subparsers.add_parser(
        "daily-cycle",
        help="Run the daily system cycle: update returns, sync foundation data, and generate recommendations.",
    )
    daily_cycle_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    daily_cycle_parser.add_argument("--batch-size", type=int, default=10, help="Foundation sync batch size.")
    daily_cycle_parser.add_argument(
        "--batches", type=int, default=2, help="Number of foundation batches to run before recommendation."
    )
    daily_cycle_parser.add_argument("--top-n", type=int, default=5, help="Number of recommendations to output.")
    daily_cycle_parser.add_argument(
        "--candidate-limit",
        type=int,
        default=50,
        help="Number of database candidates to feed into the recommendation run.",
    )
    daily_cycle_parser.add_argument("--max-per-sector", type=int, default=1, help="Maximum picks per sector.")
    daily_cycle_parser.add_argument(
        "--max-per-style", type=int, default=2, help="Maximum picks per dominant style tag."
    )
    update_returns_parser = subparsers.add_parser(
        "update-returns", help="Update forward returns for stored recommendations."
    )
    update_returns_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    candidates_parser = subparsers.add_parser(
        "list-candidates", help="List filtered research candidates from the synced universe."
    )
    candidates_parser.add_argument("--limit", type=int, default=30, help="Maximum number of candidates to show.")
    candidates_parser.add_argument("--min-amount", type=float, default=100_000_000, help="Minimum成交额 filter.")
    candidates_parser.add_argument(
        "--include-bse",
        action="store_true",
        help="Include BSE stocks instead of excluding them.",
    )

    run_parser = subparsers.add_parser("run", help="Generate daily top stock recommendations.")
    run_parser.add_argument("--top-n", type=int, default=None, help="Number of stocks to recommend.")
    run_parser.add_argument("--max-per-sector", type=int, default=1, help="Maximum picks per sector.")
    run_parser.add_argument("--max-per-style", type=int, default=2, help="Maximum picks per dominant style tag.")
    run_parser.add_argument(
        "--data-source",
        choices=["auto", "sample", "akshare"],
        default="auto",
        help="Choose sample data or akshare real market data.",
    )
    run_parser.add_argument(
        "--report",
        action="store_true",
        help="Export a Markdown daily report to the reports directory.",
    )
    run_parser.add_argument(
        "--network-mode",
        choices=["direct", "inherit"],
        default="direct",
        help="Use direct connection for market data or inherit system proxy settings.",
    )
    run_parser.add_argument(
        "--use-synced-universe",
        action="store_true",
        help="Force the research candidate pool to come from synced universe_stocks.",
    )
    run_parser.add_argument(
        "--candidate-limit",
        type=int,
        default=50,
        help="Maximum number of weekly-pool candidates to feed into the research pipeline.",
    )
    run_parser.add_argument(
        "--disable-theme-router",
        action="store_true",
        help="Bypass the theme/event router and use the synced universe directly.",
    )
    run_parser.add_argument(
        "--disable-weekly-pool",
        action="store_true",
        help="Debug only: bypass the weekly prefiltered pool and fall back to the broader synced universe.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    paths = resolve_paths()
    ensure_directories(paths)

    if args.command == "init-db":
        from .storage import ResearchStore

        store = ResearchStore(paths.db_path, database_url=paths.database_url)
        store.init_db()
        status = store.get_database_status()
        print(f"数据库已初始化: active={status['active_backend']} | tables={status['table_count']}")
        return

    if args.command == "paths":
        print(f"project_root: {paths.project_root}")
        print(f"data_dir: {paths.data_dir}")
        print(f"reports_dir: {paths.reports_dir}")
        print(f"state_dir: {paths.state_dir}")
        print(f"db_path: {paths.db_path}")
        print(f"database_url: {paths.database_url or ''}")
        print(f"theme_config_path: {paths.theme_config_path}")
        return

    if args.command == "db-info":
        from .storage import ResearchStore

        store = ResearchStore(paths.db_path, database_url=paths.database_url)
        store.init_db()
        status = store.get_database_status()
        print(f"active_backend: {status['active_backend']}")
        print(f"configured_backend: {status['configured_backend']}")
        print(f"db_path: {status['db_path']}")
        print(f"configured_url: {status['configured_url']}")
        print(f"sqlite_url: {status['sqlite_url']}")
        print(f"postgres_schema_sql: {status['postgres_schema_sql']}")
        print(f"table_count: {status['table_count']}")
        return

    if args.command == "validate-skill":
        skill_dir = resolve_skill_directory(paths.project_root, args.skill)
        issues = validate_skill_directory(skill_dir)
        if issues:
            print(f"Skill 校验失败: {skill_dir}")
            for issue in issues:
                print(f"- {issue}")
            raise SystemExit(1)
        print(f"Skill 校验通过: {skill_dir}")
        return

    if args.command == "package-skill":
        skill_dir = resolve_skill_directory(paths.project_root, args.skill)
        output_path = (
            (paths.project_root / args.output).resolve()
            if args.output
            else (paths.project_root / "build" / skill_archive_name(skill_dir)).resolve()
        )
        result = package_skill_directory(skill_dir, output_path)
        print(f"Skill 打包完成: {result.output_path}")
        print(f"文件数: {len(result.archived_files)}")
        return

    if args.command == "send-email":
        from .channels.email import send_email

        def resolve_mail_path(raw_path: str) -> Path:
            path = Path(raw_path)
            return path if path.is_absolute() else (paths.project_root / path)

        if args.body_file:
            body_path = resolve_mail_path(args.body_file).resolve()
            body_text = body_path.read_text(encoding="utf-8")
        else:
            body_text = args.body
        if args.html_file:
            html_path = resolve_mail_path(args.html_file).resolve()
            body_html = html_path.read_text(encoding="utf-8")
        else:
            body_html = None
        attachments = [resolve_mail_path(attachment).resolve() for attachment in args.attach]
        send_email(
            recipient_emails=args.to,
            subject=args.subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )
        print(f"邮件发送完成: {', '.join(args.to)}")
        return

    if args.command == "run-c114-daily-brief":
        from datetime import date as date_cls

        from .c114_automation import run_c114_daily_brief, shanghai_yesterday

        report_date = date_cls.fromisoformat(args.date) if args.date else shanghai_yesterday()
        result = run_c114_daily_brief(
            project_root=paths.project_root,
            report_date=report_date,
            recipients=args.to,
            send_mail=not args.no_email,
        )
        if result.succeeded:
            print(f"运行目录: {result.run_dir}")
            print(f"Step 6 文件: {result.step6_path}")
            if args.no_email:
                print("已跳过邮件发送（--no-email）。")
            else:
                print(f"邮件发送完成: {', '.join(args.to)}")
            return
        print(f"失败步骤: {result.failure_step}")
        print(f"失败原因: {result.failure_reason}")
        if result.run_dir:
            print(f"运行目录: {result.run_dir}")
        if result.log_path:
            print(f"日志路径: {result.log_path}")
        raise SystemExit(1)

    if args.command == "send-c114-latest-brief-email":
        from datetime import date as date_cls

        from .c114_automation import (
            parse_clock_hh_mm,
            send_latest_c114_brief_email,
            shanghai_local_today_at,
            shanghai_yesterday,
        )

        report_date = None
        require_dt = None
        if args.t1_gate:
            if args.report_date:
                print("--t1-gate 不能与 --report-date 同时使用。", file=sys.stderr)
                raise SystemExit(2)
            try:
                gh, gm = parse_clock_hh_mm(args.gate_time)
            except ValueError as exc:
                print(f"--gate-time 无效: {exc}", file=sys.stderr)
                raise SystemExit(2) from exc
            report_date = shanghai_yesterday()
            require_dt = shanghai_local_today_at(gh, gm)
        elif args.report_date:
            report_date = date_cls.fromisoformat(args.report_date)
            if args.require_built_not_before:
                try:
                    bh, bm = parse_clock_hh_mm(args.require_built_not_before)
                except ValueError as exc:
                    print(f"--require-built-not-before 无效: {exc}", file=sys.stderr)
                    raise SystemExit(2) from exc
                require_dt = shanghai_local_today_at(bh, bm)
        elif args.require_built_not_before:
            print("--require-built-not-before 需要同时指定 --report-date。", file=sys.stderr)
            raise SystemExit(2)

        result = send_latest_c114_brief_email(
            project_root=paths.project_root,
            recipients=args.to,
            report_date=report_date,
            require_modified_not_before=require_dt,
        )
        if not result.succeeded:
            print(result.error_detail or "发送失败。")
            raise SystemExit(1)
        print(f"Step 6 文件: {result.step6_path}")
        print(f"邮件发送完成: {', '.join(args.to)}")
        return

    from .dashboard import serve_dashboard
    from .fundamentals import sync_candidate_financial_profiles
    from .industry import fetch_industry_dictionary
    from .industry_mapping import sync_candidate_stock_industries
    from .migration import migrate_sqlite_to_postgres
    from .models import UniverseFilter
    from .pipeline import DailyResearchPipeline, PipelineConfig
    from .storage import ResearchStore
    from .tracking import fetch_forward_returns
    from .universe import fetch_full_universe

    store = ResearchStore(paths.db_path, database_url=paths.database_url)

    if args.command == "migrate-to-postgres":
        if not paths.database_url:
            raise RuntimeError("TOUZIFENXI_DATABASE_URL 未配置，无法迁移到 PostgreSQL。")
        counts = migrate_sqlite_to_postgres(paths.db_path, paths.database_url, reset_target=bool(args.reset_target))
        total_rows = sum(counts.values())
        print(f"SQLite -> PostgreSQL 迁移完成: tables={len(counts)} | rows={total_rows}")
        for table_name in sorted(counts):
            print(f"- {table_name}: {counts[table_name]}")
        return

    if args.command == "serve-web":
        store.init_db()
        serve_dashboard(host=args.host, port=args.port)
        return

    if args.command == "performance":
        store.init_db()
        summary = store.get_performance_summary()
        print(f"研究运行数: {summary['runs']}")
        print(f"推荐记录数: {summary['recommendations']}")
        print(f"待更新收益记录: {summary['pending_returns']}")
        print(
            f"收益状态: updatable={summary['return_status']['updatable']} | "
            f"waiting={summary['return_status']['waiting']} | "
            f"failed={summary['return_status']['failed']} | "
            f"completed={summary['return_status']['completed']}"
        )
        print("基本面来源分布:")
        for source, count in summary["fundamental_sources"]:
            print(f"- {source}: {count}")
        print("路由模式分布:")
        for router_mode, count in summary["router_modes"]:
            print(f"- {router_mode}: {count}")
        print("主题分层分布:")
        for bucket, count in summary["theme_buckets"]:
            print(f"- {bucket}: {count}")
        print("主题分层收益(1d):")
        for bucket_metrics in summary["theme_bucket_returns"]:
            avg_return = (
                "N/A" if bucket_metrics["avg_return_1d"] is None else f"{bucket_metrics['avg_return_1d'] * 100:.2f}%"
            )
            win_rate = "N/A" if bucket_metrics["win_rate_1d"] is None else f"{bucket_metrics['win_rate_1d'] * 100:.2f}%"
            print(
                f"- {bucket_metrics['theme_bucket']}: count={bucket_metrics['count']} | "
                f"avg_return={avg_return} | win_rate={win_rate}"
            )
        print("收益窗口统计:")
        for horizon, metrics in summary["horizons"].items():
            avg_return = "N/A" if metrics["avg_return"] is None else f"{metrics['avg_return'] * 100:.2f}%"
            win_rate = "N/A" if metrics["win_rate"] is None else f"{metrics['win_rate'] * 100:.2f}%"
            print(f"- {horizon}: count={metrics['count']} | avg_return={avg_return} | win_rate={win_rate}")
        latest_weekly_pool = summary["latest_weekly_pool"]
        if latest_weekly_pool:
            print(
                f"最新周度池: id={latest_weekly_pool['id']} | week={latest_weekly_pool['prefilter_week']} | "
                f"themes={latest_weekly_pool['theme_count']} | pool_size={latest_weekly_pool['pool_size']} | "
                f"wildcard={latest_weekly_pool['wildcard_count']} | mode={latest_weekly_pool['build_mode']}"
            )
            if latest_weekly_pool.get("bucket_counts"):
                bucket_view = " | ".join(
                    f"{name}={count}" for name, count in latest_weekly_pool["bucket_counts"].items()
                )
                print(f"最新周度池分层: {bucket_view}")
            if latest_weekly_pool["build_mode"] == "daily_refresh":
                print(
                    f"最新周度池刷新: base_run_id={latest_weekly_pool['base_run_id']} | "
                    f"added={latest_weekly_pool['refresh_added']} | removed={latest_weekly_pool['refresh_removed']}"
                )
        latest_run = summary["latest_run"]
        if latest_run:
            print(
                f"最新运行: id={latest_run['id']} | run_at={latest_run['run_at']} | "
                f"data_source={latest_run['data_source']} | style={latest_run['dominant_style']} "
                f"({latest_run['style_confidence']:.2f}) | universe={latest_run['universe_size']}"
            )
            print(
                f"最新运行池质量: ready_pool={latest_run['ready_pool_size']} | "
                f"fallback_pool={latest_run['fallback_pool_size']} | coverage={latest_run['coverage_ratio']:.2%} | "
                f"real_financial_ratio={latest_run['recommendation_real_financial_ratio']:.2%}"
            )
            print(
                f"最新运行主题路由: mode={latest_run['router_mode']} | "
                f"active_themes={latest_run['active_theme_count']} | bypass_count={latest_run['bypass_count']}"
            )
            print("最新运行基本面来源:")
            for source, count in latest_run["fundamental_sources"]:
                print(f"- {source}: {count}")
            if latest_run["theme_counts"]:
                print("最新运行主题分布:")
                for theme_name, count in latest_run["theme_counts"]:
                    print(f"- {theme_name}: {count}")
            if latest_run["theme_buckets"]:
                print("最新运行分层分布:")
                for bucket, count in latest_run["theme_buckets"]:
                    print(f"- {bucket}: {count}")
                all_theme = all(bucket != "wildcard" for bucket, _ in latest_run["theme_buckets"])
                print(f"最新运行是否全部来自主题池: {'是' if all_theme else '否'}")
        print("最近运行:")
        for run in summary["recent_runs"]:
            print(
                f"- id={run['id']} | run_at={run['run_at']} | data_source={run['data_source']} | "
                f"style={run['dominant_style']} ({run['style_confidence']:.2f}) | "
                f"ready_pool={run['ready_pool_size']} | fallback_pool={run['fallback_pool_size']} | coverage={run['coverage_ratio']:.2%} | "
                f"router_mode={run['router_mode']} | active_themes={run['active_theme_count']} | bypass={run['bypass_count']}"
            )
        return

    if args.command == "coverage":
        store.init_db()
        summary = store.get_coverage_summary(
            snapshot_date=store.get_latest_factor_snapshot_date() or "1970-01-01",
            universe_filter=UniverseFilter(
                min_amount=args.min_amount,
                exclude_boards=[] if args.include_bse else ["BSE"],
                limit=1,
            ),
        )
        latest_snapshot = store.get_latest_factor_snapshot_date() or "N/A"
        print(f"因子快照日期: {latest_snapshot}")
        print(f"候选池总数: {summary['total_candidates']}")
        print(f"正式行业覆盖: {summary['industry_covered']}")
        print(f"财务覆盖: {summary['financial_covered']}")
        print(f"日因子覆盖: {summary['factor_covered']}")
        print(f"Ready Pool 数量: {summary['ready_candidates']}")
        print(f"Fallback Pool 数量: {summary['fallback_candidates']}")
        ready_ratio = (
            (summary["ready_candidates"] / summary["total_candidates"]) if summary["total_candidates"] else 0.0
        )
        print(f"Ready Pool 覆盖率: {ready_ratio:.2%}")
        print(f"基础同步游标: {store.get_sync_state('foundation_sync_offset', '0')}")
        return

    if args.command == "build-weekly-pool":
        store.init_db()
        pipeline = DailyResearchPipeline(paths=paths, store=store)
        run_id, result = pipeline.build_weekly_pool(
            PipelineConfig(
                data_source="akshare",
                top_n=5,
                network_mode=args.network_mode,
                max_per_sector=1,
                max_per_style=2,
                write_report_file=False,
                use_synced_universe=True,
                candidate_limit=50,
                use_theme_router=True,
                use_weekly_pool=False,
            )
        )
        selected_themes = [item.theme_name for item in result.theme_scores if item.selected]
        print(
            f"weekly_pool_run_id: {run_id} | week={result.prefilter_week} | "
            f"themes={len(selected_themes)} | pool_size={len(result.pool_members)} | wildcard={result.wildcard_count}"
        )
        print(f"入选主题: {', '.join(selected_themes) if selected_themes else 'N/A'}")
        return

    if args.command == "show-weekly-pool":
        store.init_db()
        summary = store.get_latest_weekly_pool_summary()
        if not summary:
            print("最新周度池: N/A")
            return
        print(
            f"最新周度池: id={summary['id']} | week={summary['prefilter_week']} | built_at={summary['built_at']} | "
            f"themes={summary['theme_count']} | pool_size={summary['pool_size']} | wildcard={summary['wildcard_count']} | "
            f"mode={summary['build_mode']}"
        )
        if summary.get("bucket_counts"):
            bucket_view = " | ".join(f"{name}={count}" for name, count in summary["bucket_counts"].items())
            print(f"周度池分层: {bucket_view}")
        if summary["build_mode"] == "daily_refresh":
            print(
                f"周度池刷新: base_run_id={summary['base_run_id']} | "
                f"added={summary['refresh_added']} | removed={summary['refresh_removed']}"
            )
        print("主题得分:")
        for item in summary["themes"]:
            status = "selected" if item["selected"] else "reserve"
            print(
                f"- {item['theme_name']}: total={item['total_score']:.4f} | policy={item['policy_score']:.4f} | "
                f"valuation={item['valuation_score']:.4f} | performance={item['performance_score']:.4f} | "
                f"performance_source={item['performance_source']} | {status}"
            )
        return

    if args.command == "refresh-weekly-pool":
        store.init_db()
        pipeline = DailyResearchPipeline(paths=paths, store=store)
        run_id, result = pipeline.refresh_weekly_pool(
            PipelineConfig(
                data_source="akshare",
                top_n=5,
                network_mode=args.network_mode,
                max_per_sector=1,
                max_per_style=2,
                write_report_file=False,
                use_synced_universe=True,
                candidate_limit=50,
                use_theme_router=True,
                use_weekly_pool=True,
            ),
            refresh_limit=args.refresh_limit,
            candidate_limit=args.candidate_limit,
        )
        print(
            f"weekly_pool_refresh_run_id: {run_id} | week={result.prefilter_week} | "
            f"pool_size={len(result.pool_members)} | added={result.refresh_added} | removed={result.refresh_removed}"
        )
        return

    if args.command == "sync-universe":
        store.init_db()
        snapshot = fetch_full_universe(network_mode=args.network_mode)
        count = store.save_universe_snapshot(snapshot)
        print(
            f"股票池已同步: {count} | source: {snapshot.source} | at: {snapshot.captured_at.isoformat(timespec='seconds')}"
        )
        return

    if args.command == "sync-industries":
        store.init_db()
        rows = fetch_industry_dictionary(network_mode=args.network_mode)
        count = store.save_industry_dictionary(rows)
        print(f"行业字典已同步: {count}")
        return

    if args.command == "sync-stock-industries":
        store.init_db()
        count = sync_candidate_stock_industries(
            store=store,
            network_mode=args.network_mode,
            limit=args.limit,
            offset=args.offset,
            only_missing=not args.all,
        )
        print(f"个股行业映射已同步: {count}")
        return

    if args.command == "sync-financials":
        store.init_db()
        count = sync_candidate_financial_profiles(
            store=store,
            network_mode=args.network_mode,
            limit=args.limit,
            offset=args.offset,
            only_missing=not args.all,
        )
        print(f"财务画像已同步: {count}")
        return

    if args.command == "sync-factors":
        store.init_db()
        pipeline = DailyResearchPipeline(paths=paths, store=store)
        data_source, count = pipeline.sync_factors(
            PipelineConfig(
                data_source="akshare",
                top_n=0,
                network_mode=args.network_mode,
                max_per_sector=1,
                max_per_style=1,
                write_report_file=False,
                use_synced_universe=True,
                candidate_limit=args.candidate_limit,
                candidate_offset=args.offset,
                only_missing_factors=not args.all,
            )
        )
        print(f"日因子已同步: {count} | data_source: {data_source}")
        return

    if args.command == "sync-foundation":
        store.init_db()
        pipeline = DailyResearchPipeline(paths=paths, store=store)
        total_industries = 0
        total_financials = 0
        total_factors = 0
        factor_source = "akshare"
        state_key = "foundation_sync_offset"
        if args.reset_cursor:
            store.set_sync_state(state_key, "0")
        base_offset = args.offset
        if base_offset is None:
            base_offset = int(store.get_sync_state(state_key, "0") or "0")
        for batch_index in range(args.batches):
            batch_offset = base_offset + batch_index * args.batch_size
            print(f"批次 {batch_index + 1}/{args.batches} | offset={batch_offset} | batch_size={args.batch_size}")
            total_industries += sync_candidate_stock_industries(
                store=store,
                network_mode=args.network_mode,
                limit=args.batch_size,
                offset=batch_offset,
                only_missing=True,
            )
            total_financials += sync_candidate_financial_profiles(
                store=store,
                network_mode=args.network_mode,
                limit=args.batch_size,
                offset=batch_offset,
                only_missing=True,
            )
            factor_source, factor_count = pipeline.sync_factors(
                PipelineConfig(
                    data_source="akshare",
                    top_n=0,
                    network_mode=args.network_mode,
                    max_per_sector=1,
                    max_per_style=1,
                    write_report_file=False,
                    use_synced_universe=True,
                    candidate_limit=args.batch_size,
                    candidate_offset=batch_offset,
                    only_missing_factors=True,
                )
            )
            total_factors += factor_count
        store.set_sync_state(state_key, str(base_offset + args.batches * args.batch_size))
        print(
            f"基础数据批同步完成: industries={total_industries} | financials={total_financials} | "
            f"factors={total_factors} | factor_source={factor_source} | next_offset={base_offset + args.batches * args.batch_size}"
        )
        return

    if args.command == "daily-cycle":
        store.init_db()
        pending = store.get_due_return_updates()
        updated_returns = 0
        for recommendation_id, ticker, run_at, base_price in pending:
            returns = fetch_forward_returns(
                ticker=ticker,
                run_date=run_at,
                base_price=base_price,
                network_mode=args.network_mode,
            )
            store.update_recommendation_returns(
                recommendation_id=recommendation_id,
                horizon_1d=returns["horizon_1d"],
                horizon_5d=returns["horizon_5d"],
                horizon_20d=returns["horizon_20d"],
                horizon_60d=returns["horizon_60d"],
            )
            updated_returns += 1

        pipeline = DailyResearchPipeline(paths=paths, store=store)
        total_industries = 0
        total_financials = 0
        total_factors = 0
        factor_source = "akshare"
        state_key = "foundation_sync_offset"
        base_offset = int(store.get_sync_state(state_key, "0") or "0")
        for batch_index in range(args.batches):
            batch_offset = base_offset + batch_index * args.batch_size
            total_industries += sync_candidate_stock_industries(
                store=store,
                network_mode=args.network_mode,
                limit=args.batch_size,
                offset=batch_offset,
                only_missing=True,
            )
            total_financials += sync_candidate_financial_profiles(
                store=store,
                network_mode=args.network_mode,
                limit=args.batch_size,
                offset=batch_offset,
                only_missing=True,
            )
            factor_source, factor_count = pipeline.sync_factors(
                PipelineConfig(
                    data_source="akshare",
                    top_n=0,
                    network_mode=args.network_mode,
                    max_per_sector=1,
                    max_per_style=1,
                    write_report_file=False,
                    use_synced_universe=True,
                    candidate_limit=args.batch_size,
                    candidate_offset=batch_offset,
                    only_missing_factors=True,
                )
            )
            total_factors += factor_count
        store.set_sync_state(state_key, str(base_offset + args.batches * args.batch_size))

        latest_weekly_pool = store.get_latest_weekly_pool_summary()
        refresh_today = False
        if latest_weekly_pool is None or datetime.now().weekday() == 4:
            weekly_run_id, weekly_result = pipeline.build_weekly_pool(
                PipelineConfig(
                    data_source="akshare",
                    top_n=args.top_n,
                    network_mode=args.network_mode,
                    max_per_sector=args.max_per_sector,
                    max_per_style=args.max_per_style,
                    write_report_file=False,
                    use_synced_universe=True,
                    candidate_limit=50,
                    use_theme_router=True,
                    use_weekly_pool=False,
                )
            )
        elif datetime.fromisoformat(str(latest_weekly_pool["built_at"])).date() != datetime.now().date():
            weekly_run_id, weekly_result = pipeline.refresh_weekly_pool(
                PipelineConfig(
                    data_source="akshare",
                    top_n=args.top_n,
                    network_mode=args.network_mode,
                    max_per_sector=args.max_per_sector,
                    max_per_style=args.max_per_style,
                    write_report_file=False,
                    use_synced_universe=True,
                    candidate_limit=50,
                    use_theme_router=True,
                    use_weekly_pool=True,
                ),
                refresh_limit=5,
                candidate_limit=max(120, args.candidate_limit * 4),
            )
            refresh_today = True
        else:
            weekly_run_id = int(latest_weekly_pool["id"])
            weekly_result = None

        result, report_path, run_id = pipeline.run(
            PipelineConfig(
                data_source="akshare",
                top_n=args.top_n,
                network_mode=args.network_mode,
                max_per_sector=args.max_per_sector,
                max_per_style=args.max_per_style,
                write_report_file=True,
                use_synced_universe=True,
                candidate_limit=args.candidate_limit,
                candidate_offset=0,
                only_missing_factors=False,
                use_theme_router=True,
                use_weekly_pool=True,
            )
        )
        coverage = store.get_coverage_summary(
            snapshot_date=store.get_latest_factor_snapshot_date() or "1970-01-01",
            universe_filter=UniverseFilter(limit=1),
        )
        perf = store.get_performance_summary()

        print(f"daily_cycle_run_id: {run_id}")
        print(f"收益更新: {updated_returns}")
        print(
            f"基础同步: industries={total_industries} | financials={total_financials} | "
            f"factors={total_factors} | factor_source={factor_source}"
        )
        print(f"周度池: id={weekly_run_id}")
        if weekly_result is not None:
            if refresh_today:
                print(
                    f"周度池刷新: week={weekly_result.prefilter_week} | pool_size={len(weekly_result.pool_members)} | "
                    f"added={weekly_result.refresh_added} | removed={weekly_result.refresh_removed}"
                )
            else:
                print(
                    f"周度池摘要: week={weekly_result.prefilter_week} | pool_size={len(weekly_result.pool_members)} | "
                    f"wildcard={weekly_result.wildcard_count}"
                )
        print(
            f"覆盖状态: industry={coverage['industry_covered']} | financial={coverage['financial_covered']} | "
            f"factors={coverage['factor_covered']} | ready={coverage['ready_candidates']} | "
            f"fallback={coverage['fallback_candidates']} | cursor={store.get_sync_state(state_key, '0')}"
        )
        print(
            f"推荐数据源: {result.data_source} | 样本数: {result.universe_size} | 风格: {result.style_view.dominant_style}"
        )
        print(
            f"候选池质量: ready_pool={result.ready_pool_size} | fallback_pool={result.fallback_pool_size} | "
            f"coverage={result.coverage_ratio:.2%}"
        )
        print(
            f"主题路由: mode={result.router_mode} | active_themes={','.join(result.active_themes) if result.active_themes else 'N/A'} | "
            f"bypass_count={result.bypass_count}"
        )
        print("推荐结果:")
        for index, rec in enumerate(result.recommendations, start=1):
            print(
                f"{index}. {rec.stock.ticker} {rec.stock.name} | score={rec.total_score:.4f} | "
                f"stage={rec.stage} | source={rec.stock.fundamental_source} | "
                f"industry_ready={int(rec.stock.industry_ready)} | ready_pool={int(rec.stock.ready_pool)} | "
                f"theme={rec.stock.theme_name or 'N/A'} | bucket={rec.stock.theme_bucket}"
            )
            print(
                f"   weekly_pool={rec.stock.prefilter_week or 'N/A'} | prefilter_theme={rec.stock.prefilter_theme or 'N/A'} | "
                f"prefilter_bucket={rec.stock.prefilter_bucket or 'N/A'} | prefilter_score={rec.stock.prefilter_score:.2f}"
            )
        print(
            f"绩效概览: runs={perf['runs']} | recs={perf['recommendations']} | pending_returns={perf['pending_returns']}"
        )
        if report_path:
            print(f"日报已生成: {report_path}")
        return

    if args.command == "update-returns":
        store.init_db()
        pending = store.get_due_return_updates()
        updated = 0
        for recommendation_id, ticker, run_at, base_price in pending:
            returns = fetch_forward_returns(
                ticker=ticker,
                run_date=run_at,
                base_price=base_price,
                network_mode=args.network_mode,
            )
            store.update_recommendation_returns(
                recommendation_id=recommendation_id,
                horizon_1d=returns["horizon_1d"],
                horizon_5d=returns["horizon_5d"],
                horizon_20d=returns["horizon_20d"],
                horizon_60d=returns["horizon_60d"],
            )
            updated += 1
        print(f"收益更新完成: {updated}")
        return

    if args.command == "list-candidates":
        store.init_db()
        universe_filter = UniverseFilter(
            min_amount=args.min_amount,
            exclude_boards=[] if args.include_bse else ["BSE"],
            limit=args.limit,
        )
        rows = store.list_universe_candidates(universe_filter)
        coverage = store.get_coverage_summary(
            snapshot_date=store.get_latest_factor_snapshot_date() or "1970-01-01",
            universe_filter=UniverseFilter(
                min_amount=args.min_amount,
                exclude_boards=[] if args.include_bse else ["BSE"],
                limit=1,
            ),
        )
        ready_ratio = (
            (coverage["ready_candidates"] / coverage["total_candidates"]) if coverage["total_candidates"] else 0.0
        )
        print(
            f"候选池摘要: ready={coverage['ready_candidates']} | fallback={coverage['fallback_candidates']} | "
            f"coverage={ready_ratio:.2%}"
        )
        print(f"候选池数量: {len(rows)}")
        for row in rows:
            print(
                f"{row['ticker']} {row['name']} | board={row['board']} | latest={row['latest_price']:.2f} | "
                f"chg={row['change_percent']:.2f}% | amount={row['amount']:.0f} | "
                f"industry_ready={row['industry_ready']} | financial_ready={row['financial_ready']} | "
                f"factor_ready={row['factor_ready']} | ready_pool={row['ready_pool']} | source={row['source']}"
            )
        return

    if args.command == "run":
        store.init_db()
        pipeline = DailyResearchPipeline(paths=paths, store=store)
        latest_weekly_pool = store.get_latest_weekly_pool_summary()
        if not args.disable_weekly_pool and latest_weekly_pool is None:
            raise RuntimeError("weekly pool missing; run build-weekly-pool first")
        if not args.disable_weekly_pool and latest_weekly_pool is not None and args.data_source in {"akshare", "auto"}:
            built_at = datetime.fromisoformat(str(latest_weekly_pool["built_at"]))
            if built_at.date() != datetime.now().date():
                if datetime.now().weekday() == 4:
                    weekly_run_id, weekly_result = pipeline.build_weekly_pool(
                        PipelineConfig(
                            data_source="akshare",
                            top_n=args.top_n or 5,
                            network_mode=args.network_mode,
                            max_per_sector=args.max_per_sector,
                            max_per_style=args.max_per_style,
                            write_report_file=False,
                            use_synced_universe=True,
                            candidate_limit=50,
                            use_theme_router=True,
                            use_weekly_pool=False,
                        )
                    )
                    print(
                        f"周度池重建: id={weekly_run_id} | week={weekly_result.prefilter_week} | "
                        f"pool_size={len(weekly_result.pool_members)} | wildcard={weekly_result.wildcard_count}"
                    )
                else:
                    weekly_run_id, weekly_result = pipeline.refresh_weekly_pool(
                        PipelineConfig(
                            data_source="akshare",
                            top_n=args.top_n or 5,
                            network_mode=args.network_mode,
                            max_per_sector=args.max_per_sector,
                            max_per_style=args.max_per_style,
                            write_report_file=False,
                            use_synced_universe=True,
                            candidate_limit=50,
                            use_theme_router=True,
                            use_weekly_pool=True,
                        ),
                        refresh_limit=5,
                        candidate_limit=max(120, args.candidate_limit * 4),
                    )
                    print(
                        f"周度池刷新: id={weekly_run_id} | week={weekly_result.prefilter_week} | "
                        f"added={weekly_result.refresh_added} | removed={weekly_result.refresh_removed}"
                    )
        top_n = args.top_n or 5
        result, report_path, run_id = pipeline.run(
            PipelineConfig(
                data_source=args.data_source,
                top_n=top_n,
                network_mode=args.network_mode,
                max_per_sector=args.max_per_sector,
                max_per_style=args.max_per_style,
                write_report_file=args.report,
                use_synced_universe=args.use_synced_universe,
                candidate_limit=args.candidate_limit,
                candidate_offset=0,
                only_missing_factors=False,
                use_theme_router=not args.disable_theme_router,
                use_weekly_pool=not args.disable_weekly_pool,
            )
        )
        style_view = result.style_view
        recommendations = result.recommendations
        print(f"run_id: {run_id}")
        print(f"数据源: {result.data_source} | 样本数: {result.universe_size}")
        print(
            f"候选池质量: ready_pool={result.ready_pool_size} | fallback_pool={result.fallback_pool_size} | "
            f"coverage={result.coverage_ratio:.2%}"
        )
        print(
            f"主题路由: mode={result.router_mode} | active_themes={','.join(result.active_themes) if result.active_themes else 'N/A'} | "
            f"bypass_count={result.bypass_count}"
        )
        if args.data_source in {"akshare", "auto"}:
            entry_mode = "weekly_pool" if not args.disable_weekly_pool else "synced_universe_debug"
            print(f"研究入口: {entry_mode}")
        print(f"风格判断: {style_view.dominant_style} | 置信度: {style_view.confidence:.2f}")
        print("投委会约束:")
        for note in result.portfolio_notes:
            print(f"- {note}")
        all_theme = all(rec.stock.prefilter_bucket != "wildcard" for rec in recommendations)
        print(f"- 本轮推荐是否全部来自主题池: {'是' if all_theme else '否'}")
        print(
            f"- 本轮是否触发 wildcard: {'是' if any(rec.stock.prefilter_bucket == 'wildcard' for rec in recommendations) else '否'}"
        )
        print("")
        print(f"每日推荐前 {top_n}:")
        for index, rec in enumerate(recommendations, start=1):
            print(
                f"{index}. {rec.stock.ticker} {rec.stock.name} | 综合得分: {rec.total_score:.4f} | 阶段: {rec.stage} | 现价: {rec.stock.last_price:.2f}"
            )
            print(f"   基本面数据源: {rec.stock.fundamental_source}")
            print(
                f"   行业映射: {'formal' if rec.stock.industry_ready else 'fallback'} | Ready Pool: {int(rec.stock.ready_pool)}"
            )
            print(
                f"   主题路由: {rec.stock.router_mode} | 主题: {rec.stock.theme_name or 'N/A'} | "
                f"分层: {rec.stock.theme_bucket} | 来源: {rec.stock.theme_source or 'N/A'} | 强度: {rec.stock.theme_strength:.2f}"
            )
            print(
                f"   周度池: {rec.stock.prefilter_week or 'N/A'} | 预筛主题: {rec.stock.prefilter_theme or 'N/A'} | "
                f"来源: {rec.stock.prefilter_bucket or 'N/A'} | 预筛分: {rec.stock.prefilter_score:.2f}"
            )
            print(
                f"   Agent得分: 基本面 {rec.agent_scores['fundamental'].score:.2f}, 技术面 {rec.agent_scores['technical'].score:.2f}, 风险 {rec.agent_scores['risk'].score:.2f}, 事件 {rec.agent_scores['event'].score:.2f}"
            )
            print(f"   理由: {'；'.join(rec.reasons)}")
            print(f"   风险: {'；'.join(rec.risks)}")
            print("")
        if report_path:
            print(f"日报已生成: {report_path}")


if __name__ == "__main__":
    main()
