"""CLI entrypoints for the C114 skill.

This module is the single command surface for the C114 skill and should be
invoked via ``skills/c114-daily-hot-topics/scripts/c114.py``.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .c114_brief_review import (
    build_brief_review_report,
    resolve_brief_review_output_paths,
    save_brief_review_yaml,
)
from .c114_content import resolve_content_output_paths, run_content_fetch_workflow, save_content_results
from .c114_content_analysis import (
    CONTENT_ANALYSIS_PROMPT_PATH,
    collect_missing_analysis_fields,
    generate_layer_issues,
    load_content_analysis_inputs,
    resolve_content_analysis_output_paths,
    save_brief_markdown,
    save_content_analysis_yaml,
    save_layer_issues_yaml,
)
from .c114_hot_topics import (
    collect_daily_report,
    render_daily_report,
    resolve_hot_topics_output_path,
    save_daily_report,
)
from .c114_intelligence import (
    analyze_daily_articles,
    create_search_range_directory,
    create_search_run_directory,
    find_latest_c114_step_file,
    layer_issues_name,
    resolve_analysis_output_paths,
    step_1_analysis_name,
    step_2_checklist_name,
    step_3_results_name,
    step_4_content_name,
    step_5_content_analysis_name,
    step_6_brief_name,
    step_7_brief_review_name,
    write_analysis_outputs,
)
from .c114_search import provider_stats_name, resolve_search_output_paths, run_search_workflow, save_search_results
from .config import (
    collect_missing_c114_config,
    initialize_c114_local_config,
    load_c114_runtime_config,
    read_c114_local_config,
    runtime_local_path,
    write_c114_local_config,
)
from .settings import AppPaths, ensure_directories, resolve_paths


def add_c114_date_arguments(parser: argparse.ArgumentParser, *, default_to_today: bool = False) -> None:
    """Attach the shared single-day / date-range arguments used by C114 steps."""

    default_hint = " Defaults to today." if default_to_today else ""
    parser.add_argument("--date", default=None, help=f"Target article date in YYYY-MM-DD format.{default_hint}")
    parser.add_argument("--start-date", default=None, help="Start date in YYYY-MM-DD format for a closed date range.")
    parser.add_argument("--end-date", default=None, help="End date in YYYY-MM-DD format for a closed date range.")


def resolve_c114_date_range(args: argparse.Namespace, *, default_to_today: bool = False) -> list[date]:
    """Normalize `--date` or `--start-date/--end-date` into a concrete date list."""

    single_date = getattr(args, "date", None)
    start_date = getattr(args, "start_date", None)
    end_date = getattr(args, "end_date", None)
    if single_date and (start_date or end_date):
        raise ValueError("不能同时使用 --date 和 --start-date/--end-date。")
    if start_date or end_date:
        if not start_date or not end_date:
            raise ValueError("必须同时提供 --start-date 和 --end-date。")
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError("--start-date 不能晚于 --end-date。")
        total_days = (end - start).days + 1
        return [start + timedelta(days=offset) for offset in range(total_days)]
    if single_date:
        return [date.fromisoformat(single_date)]
    if default_to_today:
        return [datetime.now().date()]
    raise ValueError("必须提供 --date，或者同时提供 --start-date 和 --end-date。")


def create_c114_range_day_directories(
    paths: AppPaths,
    dates: list[date],
    *,
    run_started_at: datetime | None = None,
) -> dict[date, Path]:
    """Create a range run directory with one child directory per target date."""

    range_dir = create_search_range_directory(
        paths.reports_dir,
        start_date=dates[0],
        end_date=dates[-1],
        run_started_at=run_started_at,
    )
    day_dirs: dict[date, Path] = {}
    for target_date in dates:
        day_dir = range_dir / target_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=False)
        day_dirs[target_date] = day_dir
    return day_dirs


def find_required_step_input(paths: AppPaths, target_date: date, file_name: str, label: str) -> Path:
    """Find the latest upstream step file for one date or fail with a readable error."""

    input_path = find_latest_c114_step_file(paths.reports_dir, target_date, file_name)
    if input_path is None:
        raise FileNotFoundError(f"未找到 {target_date.isoformat()} 的{label}：{file_name}")
    return input_path


def build_parser() -> argparse.ArgumentParser:
    """Build the C114-only command parser used by the skill scripts and root proxy."""

    parser = argparse.ArgumentParser(description="Run the C114 daily hot-topics skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    c114_parser = subparsers.add_parser("c114-hot-topics", help="Fetch daily hot topics from C114.")
    add_c114_date_arguments(c114_parser, default_to_today=True)
    c114_parser.add_argument(
        "--channels",
        nargs="+",
        default=["home", "quantum", "satellite", "la", "ai"],
        choices=["home", "quantum", "satellite", "la", "ai"],
        help="Channels to fetch.",
    )
    c114_parser.add_argument("--candidate-limit", type=int, default=30, help="Maximum candidates per channel.")
    c114_parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds.")
    c114_parser.add_argument("--output", default=None, help="Optional JSON output path.")

    c114_analyze_parser = subparsers.add_parser("c114-analyze", help="Analyze C114 daily articles.")
    add_c114_date_arguments(c114_analyze_parser)
    c114_analyze_parser.add_argument("--input", default=None, help="Optional input CSV path.")
    c114_analyze_parser.add_argument("--analysis-output", default=None, help="Optional step 1 output path.")
    c114_analyze_parser.add_argument("--checklist-output", default=None, help="Optional step 2 output path.")

    c114_search_parser = subparsers.add_parser("c114-search", help="Search the web for each C114 article.")
    add_c114_date_arguments(c114_search_parser)
    c114_search_parser.add_argument("--input", default=None, help="Optional step 2 input path.")
    c114_search_parser.add_argument("--output", default=None, help="Optional step 3 output path.")
    c114_search_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google"],
        help="Search provider.",
    )
    c114_search_parser.add_argument("--per-query-limit", type=int, default=5)
    c114_search_parser.add_argument("--per-article-limit", type=int, default=None)
    c114_search_parser.add_argument("--extract-limit", type=int, default=5)

    c114_content_parser = subparsers.add_parser("c114-fetch-content", help="Fetch content for selected URLs.")
    add_c114_date_arguments(c114_content_parser)
    c114_content_parser.add_argument("--input", default=None, help="Optional step 3 input path.")
    c114_content_parser.add_argument("--output", default=None, help="Optional step 4 output path.")

    c114_content_analysis_parser = subparsers.add_parser(
        "c114-analyze-content", help="Generate step 5, step 6 and layer issues."
    )
    add_c114_date_arguments(c114_content_analysis_parser)
    c114_content_analysis_parser.add_argument("--input", default=None, help="Optional step 4 input path.")
    c114_content_analysis_parser.add_argument("--output", default=None, help="Optional step 5 output path.")
    c114_content_analysis_parser.add_argument("--issues-output", default=None, help="Optional issues output path.")

    c114_brief_review_parser = subparsers.add_parser("c114-review-brief", help="Review the final brief.")
    add_c114_date_arguments(c114_brief_review_parser)
    c114_brief_review_parser.add_argument("--input", default=None, help="Optional step 6 input path.")
    c114_brief_review_parser.add_argument("--analysis-input", default=None, help="Optional step 5 input path.")
    c114_brief_review_parser.add_argument("--content-input", default=None, help="Optional step 4 input path.")
    c114_brief_review_parser.add_argument("--output", default=None, help="Optional step 7 output path.")

    c114_config_status_parser = subparsers.add_parser("c114-config-status", help="Show missing runtime config fields.")
    c114_config_status_parser.add_argument("--json", action="store_true")

    c114_config_apply_parser = subparsers.add_parser("c114-config-apply", help="Merge config into runtime.local.json.")
    c114_config_apply_parser.add_argument("--payload-json", required=True)

    c114_config_init_parser = subparsers.add_parser(
        "c114-config-init", help="Create runtime.local.json from the example template."
    )
    c114_config_init_parser.add_argument("--force", action="store_true")

    c114_run_parser = subparsers.add_parser(
        "run", help="Run deterministic C114 steps until the next agent checkpoint."
    )
    add_c114_date_arguments(c114_run_parser, default_to_today=True)
    c114_run_parser.add_argument(
        "--channels",
        nargs="+",
        default=["home", "quantum", "satellite", "la", "ai"],
        choices=["home", "quantum", "satellite", "la", "ai"],
        help="Channels to fetch.",
    )
    c114_run_parser.add_argument("--candidate-limit", type=int, default=30, help="Maximum candidates per channel.")
    c114_run_parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds.")
    c114_run_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google"],
        help="Search provider.",
    )
    c114_run_parser.add_argument("--per-query-limit", type=int, default=5)
    c114_run_parser.add_argument("--per-article-limit", type=int, default=None)
    c114_run_parser.add_argument("--extract-limit", type=int, default=5)
    return parser


def run_with_args(args: argparse.Namespace, paths: AppPaths | None = None) -> None:
    """Execute one C114 workflow command against the shared project directories."""

    resolved_paths = paths or resolve_paths()
    ensure_directories(resolved_paths)

    if args.command == "c114-hot-topics":
        target_dates = resolve_c114_date_range(args, default_to_today=True)
        for target_date in target_dates:
            reports = collect_daily_report(
                report_date=target_date,
                channel_keys=args.channels,
                timeout=float(args.timeout),
                candidate_limit=int(args.candidate_limit),
            )
            output_path = resolve_hot_topics_output_path(
                project_root=resolved_paths.project_root,
                raw_dir=resolved_paths.raw_dir,
                report_date=target_date,
                output_override=args.output,
            )
            save_daily_report(output_path, target_date, reports)
            print(render_daily_report(reports, target_date))
            print(f"\nJSON 已写入: {output_path}\n")
        return

    if args.command == "c114-analyze":
        target_dates = resolve_c114_date_range(args)
        range_day_dirs = create_c114_range_day_directories(resolved_paths, target_dates) if len(target_dates) > 1 else {}
        for target_date in target_dates:
            output_paths = resolve_analysis_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=args.input,
                analysis_output_override=(
                    str((range_day_dirs[target_date] / f"c114_step_1_analysis_{target_date.strftime('%Y%m%d')}.csv").resolve())
                    if range_day_dirs and not args.analysis_output
                    else args.analysis_output
                ),
                checklist_output_override=(
                    str((range_day_dirs[target_date] / f"c114_step_2_search_checklist_{target_date.strftime('%Y%m%d')}.yaml").resolve())
                    if range_day_dirs and not args.checklist_output
                    else args.checklist_output
                ),
            )
            analyses, briefs = analyze_daily_articles(output_paths.input_path, target_date.isoformat())
            checklist_items = write_analysis_outputs(output_paths, target_date.isoformat(), analyses)
            print(f"C114 分析完成 {target_date.isoformat()}")
            print(f"文章数: {len(analyses)}")
            print(f"主题数: {len(briefs)}")
            print(f"\n逐篇分析 CSV: {output_paths.analysis_output}")
            if checklist_items:
                print(f"搜索清单 YAML 模板: {output_paths.checklist_output}\n")
            else:
                print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
        return

    if args.command == "c114-search":
        target_dates = resolve_c114_date_range(args)
        runtime_config = load_c114_runtime_config()
        range_day_dirs = create_c114_range_day_directories(resolved_paths, target_dates) if len(target_dates) > 1 else {}
        for target_date in target_dates:
            input_override = args.input
            output_override = args.output
            if range_day_dirs and not input_override:
                input_override = str(find_required_step_input(resolved_paths, target_date, step_2_checklist_name(target_date), "搜索清单 YAML"))
            if range_day_dirs and not output_override:
                output_override = str((range_day_dirs[target_date] / step_3_results_name(target_date)).resolve())
            output_paths = resolve_search_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=input_override,
                output_override=output_override,
                provider=args.provider,
            )
            payload = run_search_workflow(
                input_path=output_paths.input_path,
                report_date=target_date.isoformat(),
                per_query_limit=int(args.per_query_limit),
                per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
                extract_limit=int(args.extract_limit),
                provider_name=output_paths.provider,
            )
            save_search_results(output_paths.output_path, payload)
            print(f"C114 搜索完成 {target_date.isoformat()}")
            print(f"\n输入清单 YAML: {output_paths.input_path}")
            print(f"搜索结果 YAML: {output_paths.output_path}")
            print(f"Provider 用量统计: {output_paths.output_path.parent / provider_stats_name(target_date)}\n")
        return

    if args.command == "c114-fetch-content":
        target_dates = resolve_c114_date_range(args)
        range_day_dirs = create_c114_range_day_directories(resolved_paths, target_dates) if len(target_dates) > 1 else {}
        for target_date in target_dates:
            input_override = args.input
            output_override = args.output
            if range_day_dirs and not input_override:
                input_override = str(find_required_step_input(resolved_paths, target_date, step_3_results_name(target_date), "搜索结果 YAML"))
            if range_day_dirs and not output_override:
                output_override = str((range_day_dirs[target_date] / step_4_content_name(target_date)).resolve())
            output_paths = resolve_content_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=input_override,
                output_override=output_override,
            )
            payload = run_content_fetch_workflow(input_path=output_paths.input_path, report_date=target_date.isoformat())
            save_content_results(output_paths.output_path, payload)
            print(f"C114 正文抓取完成 {target_date.isoformat()}")
            print(f"正文内容 YAML: {output_paths.output_path}\n")
        return

    if args.command == "c114-analyze-content":
        target_dates = resolve_c114_date_range(args)
        range_day_dirs = create_c114_range_day_directories(resolved_paths, target_dates) if len(target_dates) > 1 else {}
        for target_date in target_dates:
            input_override = args.input
            output_override = args.output
            issues_output = args.issues_output
            if range_day_dirs and not input_override:
                input_override = str(find_required_step_input(resolved_paths, target_date, step_4_content_name(target_date), "正文 YAML"))
            if range_day_dirs and not output_override:
                output_override = str((range_day_dirs[target_date] / step_5_content_analysis_name(target_date)).resolve())
            if range_day_dirs and not issues_output:
                issues_output = str((range_day_dirs[target_date] / layer_issues_name(target_date)).resolve())
            output_paths = resolve_content_analysis_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=input_override,
                output_override=output_override,
                issues_output_override=issues_output,
            )
            payload = load_content_analysis_inputs(output_paths.input_path)
            save_content_analysis_yaml(output_paths.analysis_output, payload)
            run_dir = output_paths.input_path.parent
            issues = generate_layer_issues(
                report_date=target_date.isoformat(),
                raw_csv_path=resolved_paths.raw_dir / "c114_hot_topics.csv",
                checklist_path=run_dir / step_2_checklist_name(target_date),
                search_results_path=run_dir / step_3_results_name(target_date),
                content_path=output_paths.input_path,
            )
            save_layer_issues_yaml(output_paths.issues_output, target_date.isoformat(), issues)
            missing_analysis = collect_missing_analysis_fields(payload)
            print(f"C114 正文分析模板生成完成 {target_date.isoformat()}")
            print(f"正文分析 YAML: {output_paths.analysis_output}")
            print(f"层问题 YAML: {output_paths.issues_output}")
            if missing_analysis:
                print("step 5 尚未补全，step 6 不继续生成。")
                print(f"请先读取提示词: {CONTENT_ANALYSIS_PROMPT_PATH}")
                print("并基于 step 4 正文内容补齐以下字段后，再继续运行 c114-analyze-content：")
                for issue in missing_analysis[:10]:
                    fields = "、".join(issue["missing_fields"])
                    print(f"- [{issue['topic']}] {issue['original_title']}: {fields}")
                if len(missing_analysis) > 10:
                    print(f"- 其余 {len(missing_analysis) - 10} 篇请查看 step 5 YAML 继续补齐")
                print("")
                continue
            save_brief_markdown(output_paths.brief_output, payload)
            print(f"主题简报 MD: {output_paths.brief_output}\n")
        return

    if args.command == "c114-review-brief":
        target_dates = resolve_c114_date_range(args)
        range_day_dirs = create_c114_range_day_directories(resolved_paths, target_dates) if len(target_dates) > 1 else {}
        for target_date in target_dates:
            brief_input = args.input
            analysis_input = args.analysis_input
            content_input = args.content_input
            output_override = args.output
            if range_day_dirs and not brief_input:
                brief_input = str(find_required_step_input(resolved_paths, target_date, step_6_brief_name(target_date), "主题简报 MD"))
            if range_day_dirs and not analysis_input:
                analysis_input = str(find_required_step_input(resolved_paths, target_date, step_5_content_analysis_name(target_date), "正文分析 YAML"))
            if range_day_dirs and not content_input:
                content_input = str(find_required_step_input(resolved_paths, target_date, step_4_content_name(target_date), "正文内容 YAML"))
            if range_day_dirs and not output_override:
                output_override = str((range_day_dirs[target_date] / step_7_brief_review_name(target_date)).resolve())
            output_paths = resolve_brief_review_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                brief_input_override=brief_input,
                analysis_input_override=analysis_input,
                content_input_override=content_input,
                output_override=output_override,
            )
            report = build_brief_review_report(
                report_date=target_date.isoformat(),
                brief_path=output_paths.brief_input_path,
                analysis_path=output_paths.analysis_input_path,
                content_path=output_paths.content_input_path,
            )
            save_brief_review_yaml(output_paths.review_output_path, report)
            print(f"C114 简报审查完成 {target_date.isoformat()}")
            print(f"总评: {report.overall_decision}")
            print(f"问题数: {len(report.findings)}")
            print(f"审查报告 YAML: {output_paths.review_output_path}\n")
        return

    if args.command == "c114-config-status":
        current = read_c114_local_config()
        missing = collect_missing_c114_config()
        config_path = runtime_local_path()
        if args.json:
            print(json.dumps({"config_path": str(config_path), "current": current, "missing": missing}, ensure_ascii=False, indent=2))
            return
        print(f"C114 配置文件: {config_path}")
        if missing:
            print("缺失配置项:")
            for item in missing:
                print(f"- {item}")
            print("说明：step 1-2 可先不配 API key；若要继续跑 step 3-7，搜索侧只需先配置至少一个搜索 provider key（Tavily / Metaso / Baidu 三选一），再补齐其余 search/content/brief 基础配置。")
        else:
            print("配置已完整。")
        return

    if args.command == "c114-config-apply":
        payload = json.loads(args.payload_json)
        output_path = write_c114_local_config(None, payload)
        missing = collect_missing_c114_config()
        print(f"C114 配置已写入: {output_path}")
        if missing:
            print("仍缺少以下配置项:")
            for item in missing:
                print(f"- {item}")
        else:
            print("配置已完整。")
        return

    if args.command == "c114-config-init":
        output_path = initialize_c114_local_config(None, overwrite=bool(args.force))
        print(f"C114 配置模板已初始化: {output_path}")
        if collect_missing_c114_config():
            print("请编辑 runtime.local.json，补齐 keys/search/content/brief 配置后再运行。")
        return

    if args.command == "run":
        target_dates = resolve_c114_date_range(args, default_to_today=True)
        if len(target_dates) > 1:
            day_dirs = create_c114_range_day_directories(resolved_paths, target_dates)
        else:
            run_dir = create_search_run_directory(resolved_paths.reports_dir)
            day_dirs = {target_dates[0]: run_dir}

        for target_date in target_dates:
            day_dir = day_dirs[target_date]
            report = collect_daily_report(
                report_date=target_date,
                channel_keys=args.channels,
                timeout=float(args.timeout),
                candidate_limit=int(args.candidate_limit),
            )
            raw_output = resolve_hot_topics_output_path(
                project_root=resolved_paths.project_root,
                raw_dir=resolved_paths.raw_dir,
                report_date=target_date,
                output_override=None,
            )
            save_daily_report(raw_output, target_date, report)

            analysis_output = (day_dir / step_1_analysis_name(target_date)).resolve()
            checklist_output = (day_dir / step_2_checklist_name(target_date)).resolve()
            analysis_paths = resolve_analysis_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                analysis_output_override=str(analysis_output),
                checklist_output_override=str(checklist_output),
            )
            analyses, briefs = analyze_daily_articles(analysis_paths.input_path, target_date.isoformat())
            checklist_items = write_analysis_outputs(analysis_paths, target_date.isoformat(), analyses)
            print(f"C114 全流程 step 1-2 完成 {target_date.isoformat()}")
            print(f"文章数: {len(analyses)}")
            print(f"主题数: {len(briefs)}")
            print(f"Step 1 CSV: {analysis_paths.analysis_output}")
            if not checklist_items:
                print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
                continue
            print(f"Step 2 YAML: {analysis_paths.checklist_output}")
            print(
                "run 命令已停在 step 2。请先按 step 2 契约补全每条标题的 2 组 keywords，"
                "再继续 c114-search；完成 step 3 后，再按 review_prompt_path 补全 "
                "ai_review.review_status 与 ai_review.keep_level，然后再继续 step 4-7。\n"
            )
        return

    raise ValueError(f"未知命令: {args.command}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_with_args(args)


if __name__ == "__main__":
    main()
