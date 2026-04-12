"""C114 skill 的统一命令行入口。"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .c114_brief_review import (
    BRIEF_REVIEW_PROMPT_PATH,
    build_brief_review_report_with_llm,
    render_brief_review_template_yaml,
    resolve_brief_review_output_paths,
    save_brief_review_yaml,
    validate_brief_markdown_for_agent,
    validate_brief_review_yaml_for_agent,
)
from .c114_content import (
    load_search_results_yaml,
    resolve_content_output_paths,
    run_content_fetch_workflow,
    save_content_results,
    validate_content_fetch_inputs,
)
from .c114_content_analysis import (
    BRIEF_PROMPT_PATH,
    CONTENT_ANALYSIS_PROMPT_PATH,
    auto_complete_content_analysis,
    collect_missing_analysis_fields,
    generate_brief_markdown,
    generate_layer_issues,
    load_content_analysis_inputs,
    render_brief_markdown,
    resolve_content_analysis_output_paths,
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
    LLM_TRACE_LOG_DIR_NAME,
    ArticleAnalysis,
    analyze_daily_articles,
    auto_group_analysis_topics,
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
from .c114_intelligence import (
    PROMPT_PATH as SEARCH_KEYWORD_PROMPT_PATH,
)
from .c114_search import (
    SEARCH_REVIEW_PROMPT_PATH,
    SearchTraceLogger,
    load_search_checklist_yaml,
    provider_stats_name,
    resolve_search_output_paths,
    run_search_workflow,
    save_search_results,
    search_trace_log_name_for_step,
    validate_search_checklist_items,
)
from .checkpoint import StepCheckpointStore, checkpoint_path_for_step
from .config import (
    collect_missing_c114_config,
    initialize_c114_local_config,
    load_c114_runtime_config,
    read_c114_local_config,
    runtime_local_path,
    write_c114_local_config,
)
from .execution import (
    StepInstruction,
    build_checkpoint_sequence,
    create_controller_agent_bridge,
    execution_manifest_name,
    normalize_execution_mode,
    save_controller_agent_bridge,
)
from .llm import StructuredChatClient
from .settings import AppPaths, ensure_directories, resolve_paths

RUN_DIR_NAME_RE = re.compile(r"^c114_search_\d{12}(?:_.*)?$")
RANGE_DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def add_c114_date_arguments(parser: argparse.ArgumentParser, *, default_to_today: bool = False) -> None:
    """为 C114 相关命令补充单日或区间日期参数。"""

    default_hint = " Defaults to today." if default_to_today else ""
    parser.add_argument("--date", default=None, help=f"Target article date in YYYY-MM-DD format.{default_hint}")
    parser.add_argument("--start-date", default=None, help="Start date in YYYY-MM-DD format for a closed date range.")
    parser.add_argument("--end-date", default=None, help="End date in YYYY-MM-DD format for a closed date range.")


def add_execution_mode_argument(parser: argparse.ArgumentParser) -> None:
    """为支持双执行模式的命令补充执行模式参数。"""

    parser.add_argument(
        "--execution-mode",
        default=None,
        choices=["builtin", "controller-agent"],
        help="执行模式：builtin 使用内置 provider 链；controller-agent 生成检查点并等待当前控制 agent 接力。",
    )


def resolve_c114_date_range(args: argparse.Namespace, *, default_to_today: bool = False) -> list[date]:
    """把单日或区间参数统一展开成具体日期列表。"""

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
    """为区间运行创建总目录，并按天生成子目录。"""

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
    """查找某一天最新的上游步骤文件，找不到时抛出可读错误。"""

    input_path = find_latest_c114_step_file(paths.reports_dir, target_date, file_name)
    if input_path is None:
        raise FileNotFoundError(f"未找到 {target_date.isoformat()} 的{label}：{file_name}")
    return input_path


def require_llm_client() -> StructuredChatClient:
    """构造统一的主备大模型客户端，缺配置时抛出可读错误。"""

    return StructuredChatClient.from_runtime_config()


def bind_llm_trace_log(
    llm_client: StructuredChatClient | None,
    *,
    run_dir: Path,
    target_date: date,
    reset_file: bool = False,
) -> None:
    """把当前步骤使用的 LLM 日志统一绑定到单次运行目录。"""

    if llm_client is None:
        return
    set_trace_log_directory = getattr(llm_client, "set_trace_log_directory", None)
    if callable(set_trace_log_directory):
        set_trace_log_directory(
            (run_dir / LLM_TRACE_LOG_DIR_NAME).resolve(),
            report_date=target_date.isoformat(),
            reset_files=reset_file,
        )
        return
    set_trace_log_path = getattr(llm_client, "set_trace_log_path", None)
    if not callable(set_trace_log_path):
        return
    set_trace_log_path((run_dir / LLM_TRACE_LOG_DIR_NAME / f"legacy_{target_date.isoformat()}.jsonl").resolve(), reset_file=reset_file)


def build_search_trace_logger(run_dir: Path, target_date: date, *, reset_file: bool = False) -> SearchTraceLogger:
    """构造 step 3 搜索请求日志器。"""

    trace_path = (run_dir / LLM_TRACE_LOG_DIR_NAME / search_trace_log_name_for_step("step_3", target_date)).resolve()
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    if reset_file:
        trace_path.write_text("", encoding="utf-8")
    return SearchTraceLogger(trace_path)


def infer_trace_run_dir(*paths: Path | None) -> Path:
    """从输入输出路径推断本次任务真正的 run 目录。"""

    first_existing_parent: Path | None = None
    for candidate in paths:
        if candidate is None:
            continue
        current = candidate if candidate.is_dir() else candidate.parent
        if first_existing_parent is None:
            first_existing_parent = current
        for parent in (current, *current.parents):
            if RUN_DIR_NAME_RE.match(parent.name):
                return parent
            if RANGE_DAY_DIR_RE.match(parent.name) and parent.parent.name.startswith("c114_range_"):
                return parent
    if first_existing_parent is not None:
        return first_existing_parent
    raise ValueError("无法推断 trace run 目录。")


def resolve_requested_execution_mode(args: argparse.Namespace) -> str:
    """综合 CLI 与配置文件得到本次命令的执行模式。"""

    runtime_config = load_c114_runtime_config()
    return normalize_execution_mode(
        getattr(args, "execution_mode", None) or getattr(runtime_config, "execution_mode", "builtin")
    )


def resolve_content_analysis_runtime(runtime_config: object) -> tuple[str, int]:
    """从运行配置或测试桩对象里取出 step 5 分析模式。"""

    content_analysis = getattr(runtime_config, "content_analysis", None)
    mode = getattr(content_analysis, "mode", "per_topic")
    retry_attempts = getattr(content_analysis, "batch_retry_attempts", 3)
    return str(mode or "per_topic"), max(1, int(retry_attempts))


def build_parser() -> argparse.ArgumentParser:
    """构造 C114 skill 专用的命令行解析器。"""

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
    c114_parser.add_argument("--timeout", type=float, default=45.0, help="Per-request timeout in seconds.")
    c114_parser.add_argument("--output", default=None, help="Optional JSON output path.")

    c114_analyze_parser = subparsers.add_parser("c114-analyze", help="Analyze C114 daily articles.")
    add_c114_date_arguments(c114_analyze_parser)
    add_execution_mode_argument(c114_analyze_parser)
    c114_analyze_parser.add_argument("--input", default=None, help="Optional input CSV path.")
    c114_analyze_parser.add_argument("--analysis-output", default=None, help="Optional step 1 output path.")
    c114_analyze_parser.add_argument("--checklist-output", default=None, help="Optional step 2 output path.")

    c114_search_parser = subparsers.add_parser("c114-search", help="Search the web for each C114 article.")
    add_c114_date_arguments(c114_search_parser)
    add_execution_mode_argument(c114_search_parser)
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
    add_execution_mode_argument(c114_content_analysis_parser)
    c114_content_analysis_parser.add_argument("--input", default=None, help="Optional step 4 input path.")
    c114_content_analysis_parser.add_argument("--output", default=None, help="Optional step 5 output path.")
    c114_content_analysis_parser.add_argument("--issues-output", default=None, help="Optional issues output path.")

    c114_brief_review_parser = subparsers.add_parser("c114-review-brief", help="Review the final brief.")
    add_c114_date_arguments(c114_brief_review_parser)
    add_execution_mode_argument(c114_brief_review_parser)
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

    c114_run_parser = subparsers.add_parser("run", help="Run the full C114 pipeline from step 1 to step 7.")
    add_c114_date_arguments(c114_run_parser, default_to_today=True)
    add_execution_mode_argument(c114_run_parser)
    c114_run_parser.add_argument(
        "--channels",
        nargs="+",
        default=["home", "quantum", "satellite", "la", "ai"],
        choices=["home", "quantum", "satellite", "la", "ai"],
        help="Channels to fetch.",
    )
    c114_run_parser.add_argument("--candidate-limit", type=int, default=30, help="Maximum candidates per channel.")
    c114_run_parser.add_argument("--timeout", type=float, default=45.0, help="Per-request timeout in seconds.")
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


def step_manifest_path(day_dir: Path, target_date: date) -> Path:
    """返回某一天运行目录下的执行 manifest 路径。"""

    return (day_dir / execution_manifest_name(target_date)).resolve()


def write_controller_agent_manifest(
    *,
    day_dir: Path,
    target_date: date,
    current_step: str,
    output_paths: dict[str, Path],
    prompt_paths: dict[str, Path | None],
    input_paths: dict[str, tuple[Path, ...]],
    required_fields: dict[str, tuple[str, ...]],
    instruction: StepInstruction,
    next_action: str,
    errors: dict[str, str] | None = None,
    notes: dict[str, tuple[str, ...]] | None = None,
) -> Path:
    """写出 controller-agent 模式的 manifest。"""

    manifest_path = step_manifest_path(day_dir, target_date)
    bridge = create_controller_agent_bridge(
        report_date=target_date.isoformat(),
        manifest_path=manifest_path,
        current_step=current_step,
        checkpoints=build_checkpoint_sequence(
            ready_step=current_step,
            output_paths=output_paths,
            prompt_paths=prompt_paths,
            input_paths=input_paths,
            required_fields=required_fields,
            errors=errors,
            notes=notes,
        ),
        instruction=instruction,
        next_action=next_action,
    )
    save_controller_agent_bridge(bridge)
    return manifest_path


def build_controller_output_paths(day_dir: Path, target_date: date) -> dict[str, Path]:
    """为 manifest 统一整理各步骤的目标输出路径。"""

    return {
        "step_1": (day_dir / step_1_analysis_name(target_date)).resolve(),
        "step_2": (day_dir / step_2_checklist_name(target_date)).resolve(),
        "step_3": (day_dir / step_3_results_name(target_date)).resolve(),
        "step_4": (day_dir / step_4_content_name(target_date)).resolve(),
        "step_5": (day_dir / step_5_content_analysis_name(target_date)).resolve(),
        "step_6": (day_dir / step_6_brief_name(target_date)).resolve(),
        "step_7": (day_dir / step_7_brief_review_name(target_date)).resolve(),
    }


def controller_run_directory(paths: AppPaths, target_date: date) -> Path:
    """为 controller-agent 模式复用最近的未完成目录，或创建新目录。"""

    latest = find_latest_c114_step_file(paths.reports_dir, target_date, execution_manifest_name(target_date))
    if latest is not None:
        return latest.parent.resolve()
    return create_search_run_directory(paths.reports_dir)


def step2_keywords_completed(checklist_path: Path) -> bool:
    """判断 step 2 是否已补齐两组关键词。"""

    if not checklist_path.exists():
        return False
    try:
        _report_date, items = load_search_checklist_yaml(checklist_path)
        validate_search_checklist_items(items)
    except Exception:
        return False
    return True


def step3_review_completed(search_results_path: Path) -> bool:
    """判断 step 3 是否已补齐 ai_review。"""

    if not search_results_path.exists():
        return False
    try:
        validate_content_fetch_inputs(load_search_results_yaml(search_results_path))
    except Exception:
        return False
    return True


def step5_analysis_completed(analysis_path: Path) -> bool:
    """判断 step 5 八个分析字段是否都已补齐。"""

    if not analysis_path.exists():
        return False
    try:
        payload = load_content_analysis_inputs(analysis_path)
    except Exception:
        return False
    return not collect_missing_analysis_fields(payload)


def step6_brief_completed(brief_path: Path, analysis_path: Path) -> bool:
    """判断 step 6 简报是否已由控制 agent 正式完成。"""

    if not brief_path.exists():
        return False
    try:
        validate_brief_markdown_for_agent(brief_path, analysis_path)
    except Exception:
        return False
    return True


def step7_review_completed(review_path: Path) -> bool:
    """判断 step 7 审查 YAML 是否已由控制 agent 正式完成。"""

    if not review_path.exists():
        return False
    try:
        validate_brief_review_yaml_for_agent(review_path)
    except Exception:
        return False
    return True


def run_with_args(args: argparse.Namespace, paths: AppPaths | None = None) -> None:
    """根据命令行参数执行一次 C114 工作流命令。"""

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
        execution_mode = resolve_requested_execution_mode(args)
        target_dates = resolve_c114_date_range(args)
        range_day_dirs = create_c114_range_day_directories(resolved_paths, target_dates) if len(target_dates) > 1 else {}
        llm_client = None
        if execution_mode == "builtin":
            try:
                llm_client = require_llm_client()
            except RuntimeError:
                llm_client = None
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
            trace_run_dir = infer_trace_run_dir(output_paths.analysis_output, output_paths.checklist_output)
            bind_llm_trace_log(
                llm_client,
                run_dir=trace_run_dir,
                target_date=target_date,
                reset_file=not (trace_run_dir / LLM_TRACE_LOG_DIR_NAME).exists(),
            )
            analyses, briefs = analyze_daily_articles(output_paths.input_path, target_date.isoformat())
            if execution_mode == "builtin" and analyses and all(isinstance(item, ArticleAnalysis) for item in analyses):
                topic_grouping_checkpoint_store = StepCheckpointStore.load_or_create(
                    checkpoint_path=checkpoint_path_for_step(
                        output_path=output_paths.analysis_output,
                        step_name="step_1_5",
                        report_date=target_date.isoformat(),
                    ),
                    step_name="step_1_5",
                    report_date=target_date.isoformat(),
                    input_path=output_paths.input_path,
                    output_path=output_paths.analysis_output,
                )
                analyses, briefs = auto_group_analysis_topics(
                    analyses,
                    llm_client,
                    report_date=target_date.isoformat(),
                    checkpoint_store=topic_grouping_checkpoint_store,
                )
            if execution_mode == "controller-agent":
                if output_paths.checklist_output.exists():
                    if step2_keywords_completed(output_paths.checklist_output):
                        print(f"C114 分析完成 {target_date.isoformat()}")
                        print(f"Step 2 已完成: {output_paths.checklist_output}\n")
                        continue
                    manifest_path = write_controller_agent_manifest(
                        day_dir=output_paths.checklist_output.parent,
                        target_date=target_date,
                        current_step="step_2",
                        output_paths=build_controller_output_paths(output_paths.checklist_output.parent, target_date),
                        prompt_paths={"step_2": SEARCH_KEYWORD_PROMPT_PATH},
                        input_paths={"step_2": (output_paths.analysis_output,)},
                        required_fields={"step_2": ("keywords[0]", "keywords[1]")},
                        instruction=StepInstruction(
                            step_name="step_2",
                            prompt_path=SEARCH_KEYWORD_PROMPT_PATH,
                            input_paths=(output_paths.analysis_output,),
                            output_path=output_paths.checklist_output,
                            required_fields=("keywords[0]", "keywords[1]"),
                            notes=("step 2 文件已存在；请继续在现有文件上补写或检查。",),
                        ),
                        next_action="step 2 文件已存在，请在现有 YAML 上继续补全后再继续。",
                    )
                    print(f"C114 分析完成 {target_date.isoformat()}")
                    print(f"Step 2 模板 YAML: {output_paths.checklist_output}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                checklist_items = write_analysis_outputs(
                    output_paths,
                    target_date.isoformat(),
                    analyses,
                    llm_client=None,
                    auto_fill_keywords=False,
                )
                if not checklist_items:
                    print(f"C114 分析完成 {target_date.isoformat()}")
                    print(f"文章数: {len(analyses)}")
                    print(f"主题数: {len(briefs)}")
                    print(f"\n逐篇分析 CSV: {output_paths.analysis_output}")
                    print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
                    continue
                run_dir = output_paths.checklist_output.parent
                output_map = build_controller_output_paths(run_dir, target_date)
                manifest_path = write_controller_agent_manifest(
                    day_dir=run_dir,
                    target_date=target_date,
                    current_step="step_2",
                    output_paths=output_map,
                    prompt_paths={"step_2": SEARCH_KEYWORD_PROMPT_PATH},
                    input_paths={
                        "step_2": (output_paths.analysis_output,),
                    },
                    required_fields={
                        "step_2": (
                            "categories[*].items[*].keywords[0]",
                            "categories[*].items[*].keywords[1]",
                        ),
                    },
                    instruction=StepInstruction(
                        step_name="step_2",
                        prompt_path=SEARCH_KEYWORD_PROMPT_PATH,
                        input_paths=(output_paths.analysis_output,),
                        output_path=output_paths.checklist_output,
                        required_fields=(
                            "categories[*].items[*].keywords[0]",
                            "categories[*].items[*].keywords[1]",
                        ),
                        notes=(
                            "控制 agent 只补关键词，不改原标题、栏目、日期和链接。",
                            "每篇文章必须补足两组关键词，补完后重新运行后续步骤。",
                        ),
                    ),
                    next_action="当前已进入 step 2，请控制 agent 读取 prompt 与 step 1 CSV，补全 step 2 YAML 后再继续。",
                )
                print(f"C114 分析完成 {target_date.isoformat()}")
                print(f"文章数: {len(analyses)}")
                print(f"主题数: {len(briefs)}")
                print(f"\n逐篇分析 CSV: {output_paths.analysis_output}")
                print(f"Step 2 模板 YAML: {output_paths.checklist_output}")
                print(f"执行 manifest: {manifest_path}\n")
                continue
            try:
                checklist_items = write_analysis_outputs(
                    output_paths,
                    target_date.isoformat(),
                    analyses,
                    llm_client=llm_client,
                )
            except RuntimeError as error:
                print(f"C114 分析完成 {target_date.isoformat()}")
                print(f"文章数: {len(analyses)}")
                print(f"主题数: {len(briefs)}")
                print(f"\n逐篇分析 CSV: {output_paths.analysis_output}")
                print(f"Step 2 未执行: {error}\n")
                continue
            print(f"C114 分析完成 {target_date.isoformat()}")
            print(f"文章数: {len(analyses)}")
            print(f"主题数: {len(briefs)}")
            print(f"\n逐篇分析 CSV: {output_paths.analysis_output}")
            if checklist_items:
                print(f"搜索清单 YAML: {output_paths.checklist_output}\n")
            else:
                print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
        return

    if args.command == "c114-search":
        execution_mode = resolve_requested_execution_mode(args)
        target_dates = resolve_c114_date_range(args)
        runtime_config = load_c114_runtime_config()
        llm_client = require_llm_client() if execution_mode == "builtin" else None
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
            checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=output_paths.output_path,
                    step_name="step_3",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_3",
                report_date=target_date.isoformat(),
                input_path=output_paths.input_path,
                output_path=output_paths.output_path,
            )
            trace_run_dir = infer_trace_run_dir(output_paths.input_path, output_paths.output_path)
            bind_llm_trace_log(
                llm_client,
                run_dir=trace_run_dir,
                target_date=target_date,
                reset_file=not (trace_run_dir / LLM_TRACE_LOG_DIR_NAME).exists(),
            )
            if execution_mode == "controller-agent":
                if output_paths.output_path.exists():
                    if step3_review_completed(output_paths.output_path):
                        print(f"C114 搜索完成 {target_date.isoformat()}")
                        print(f"Step 3 已完成: {output_paths.output_path}\n")
                        continue
                    manifest_path = write_controller_agent_manifest(
                        day_dir=output_paths.output_path.parent,
                        target_date=target_date,
                        current_step="step_3",
                        output_paths=build_controller_output_paths(output_paths.output_path.parent, target_date),
                        prompt_paths={"step_3": SEARCH_REVIEW_PROMPT_PATH},
                        input_paths={"step_3": (output_paths.input_path, output_paths.output_path)},
                        required_fields={"step_3": ("keep_level", "reason", "relevance_note", "value_type")},
                        instruction=StepInstruction(
                            step_name="step_3",
                            prompt_path=SEARCH_REVIEW_PROMPT_PATH,
                            input_paths=(output_paths.input_path, output_paths.output_path),
                            output_path=output_paths.output_path,
                            required_fields=("ai_review.status=reviewed", "ai_review.keep_level", "ai_review.reason", "ai_review.relevance_note", "ai_review.value_type"),
                            notes=("step 3 文件已存在；请继续在现有文件上审查。",),
                        ),
                        next_action="step 3 文件已存在，请继续在现有 YAML 上补全 ai_review。",
                    )
                    print(f"C114 搜索结果文件已存在 {target_date.isoformat()}")
                    print(f"搜索结果 YAML: {output_paths.output_path}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                _report_date, checklist_items = load_search_checklist_yaml(output_paths.input_path)
                validate_search_checklist_items(checklist_items)
            payload = run_search_workflow(
                input_path=output_paths.input_path,
                report_date=target_date.isoformat(),
                per_query_limit=int(args.per_query_limit),
                per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
                extract_limit=int(args.extract_limit),
                provider_name=output_paths.provider,
                llm_client=llm_client,
                trace_logger=build_search_trace_logger(
                    trace_run_dir,
                    target_date,
                    reset_file=not (
                        trace_run_dir / LLM_TRACE_LOG_DIR_NAME / search_trace_log_name_for_step("step_3", target_date)
                    ).exists(),
                ),
                checkpoint_store=checkpoint_store,
            )
            save_search_results(output_paths.output_path, payload)
            if execution_mode == "controller-agent":
                run_dir = output_paths.output_path.parent
                output_map = build_controller_output_paths(run_dir, target_date)
                manifest_path = write_controller_agent_manifest(
                    day_dir=run_dir,
                    target_date=target_date,
                    current_step="step_3",
                    output_paths=output_map,
                    prompt_paths={"step_3": SEARCH_REVIEW_PROMPT_PATH},
                    input_paths={"step_3": (output_paths.input_path,)},
                    required_fields={
                        "step_3": (
                            "categories[*].items[*].selected_results[*].ai_review.status=reviewed",
                            "categories[*].items[*].selected_results[*].ai_review.keep_level",
                            "categories[*].items[*].selected_results[*].ai_review.reason",
                            "categories[*].items[*].selected_results[*].ai_review.relevance_note",
                            "categories[*].items[*].selected_results[*].ai_review.value_type",
                        )
                    },
                    instruction=StepInstruction(
                        step_name="step_3",
                        prompt_path=SEARCH_REVIEW_PROMPT_PATH,
                        input_paths=(output_paths.input_path, output_paths.output_path),
                        output_path=output_paths.output_path,
                        required_fields=(
                            "selected_results[*].ai_review.status=reviewed",
                            "selected_results[*].ai_review.keep_level",
                            "selected_results[*].ai_review.reason",
                            "selected_results[*].ai_review.relevance_note",
                            "selected_results[*].ai_review.value_type",
                        ),
                        notes=(
                            "控制 agent 只填写 selected_results 下的 ai_review 字段，不改搜索元数据。",
                            "所有 selected_results 都必须审查完成后，step 4 才会继续。",
                        ),
                    ),
                    next_action="当前已进入 step 3，请控制 agent 逐条审查 selected_results 并补全 ai_review 后再继续。",
                )
                print(f"C114 搜索完成 {target_date.isoformat()}")
                print(f"\n输入清单 YAML: {output_paths.input_path}")
                print(f"搜索结果 YAML: {output_paths.output_path}")
                print(f"Provider 用量统计: {output_paths.output_path.parent / provider_stats_name(target_date)}")
                print(f"执行 manifest: {manifest_path}\n")
                continue
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
            checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=output_paths.output_path,
                    step_name="step_4",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_4",
                report_date=target_date.isoformat(),
                input_path=output_paths.input_path,
                output_path=output_paths.output_path,
            )
            payload = run_content_fetch_workflow(
                input_path=output_paths.input_path,
                report_date=target_date.isoformat(),
                checkpoint_store=checkpoint_store,
            )
            save_content_results(output_paths.output_path, payload)
            print(f"C114 正文抓取完成 {target_date.isoformat()}")
            print(f"正文内容 YAML: {output_paths.output_path}\n")
        return

    if args.command == "c114-analyze-content":
        execution_mode = resolve_requested_execution_mode(args)
        target_dates = resolve_c114_date_range(args)
        llm_client = require_llm_client() if execution_mode == "builtin" else None
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
            trace_run_dir = infer_trace_run_dir(
                output_paths.input_path,
                output_paths.analysis_output,
                output_paths.issues_output,
                output_paths.brief_output,
            )
            bind_llm_trace_log(
                llm_client,
                run_dir=trace_run_dir,
                target_date=target_date,
                reset_file=not (trace_run_dir / LLM_TRACE_LOG_DIR_NAME).exists(),
            )
            if execution_mode == "controller-agent" and output_paths.analysis_output.exists():
                payload = load_content_analysis_inputs(output_paths.analysis_output)
            else:
                payload = load_content_analysis_inputs(output_paths.input_path)
            if execution_mode == "builtin":
                runtime_config = load_c114_runtime_config()
                analysis_mode, analysis_retry_attempts = resolve_content_analysis_runtime(runtime_config)
                step5_checkpoint_store = StepCheckpointStore.load_or_create(
                    checkpoint_path=checkpoint_path_for_step(
                        output_path=output_paths.analysis_output,
                        step_name="step_5",
                        report_date=target_date.isoformat(),
                    ),
                    step_name="step_5",
                    report_date=target_date.isoformat(),
                    input_path=output_paths.input_path,
                    output_path=output_paths.analysis_output,
                )
                payload = auto_complete_content_analysis(
                    payload,
                    llm_client,
                    mode=analysis_mode,
                    batch_retry_attempts=analysis_retry_attempts,
                    checkpoint_store=step5_checkpoint_store,
                )
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
            if execution_mode == "controller-agent":
                output_map = build_controller_output_paths(run_dir, target_date)
                if missing_analysis:
                    manifest_path = write_controller_agent_manifest(
                        day_dir=run_dir,
                        target_date=target_date,
                        current_step="step_5",
                        output_paths=output_map,
                        prompt_paths={"step_5": CONTENT_ANALYSIS_PROMPT_PATH},
                        input_paths={"step_5": (output_paths.input_path, output_paths.issues_output)},
                        required_fields={
                            "step_5": (
                                "analysis.summary",
                                "analysis.core_points",
                                "analysis.new_facts",
                                "analysis.entities",
                                "analysis.signals",
                                "analysis.risk_or_uncertainty",
                                "analysis.why_it_matters",
                                "analysis.layer_notes",
                            )
                        },
                        instruction=StepInstruction(
                            step_name="step_5",
                            prompt_path=CONTENT_ANALYSIS_PROMPT_PATH,
                            input_paths=(output_paths.input_path, output_paths.issues_output),
                            output_path=output_paths.analysis_output,
                            required_fields=(
                                "summary",
                                "core_points",
                                "new_facts",
                                "entities",
                                "signals",
                                "risk_or_uncertainty",
                                "why_it_matters",
                                "layer_notes",
                            ),
                            notes=(
                                "控制 agent 只补 analysis 字段，不改 original_content 和 selected_contents。",
                                "每篇文章的八个分析字段都必须完整。",
                            ),
                        ),
                        next_action="当前已进入 step 5，请控制 agent 基于正文内容与补充链接补全 analysis 字段后再继续。",
                    )
                    print(f"C114 正文分析模板生成完成 {target_date.isoformat()}")
                    print(f"正文分析 YAML: {output_paths.analysis_output}")
                    print(f"层问题 YAML: {output_paths.issues_output}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if output_paths.brief_output.exists():
                    try:
                        validate_brief_markdown_for_agent(output_paths.brief_output, output_paths.analysis_output)
                        print(f"C114 主题简报已完成 {target_date.isoformat()}")
                        print(f"主题简报 MD: {output_paths.brief_output}\n")
                        continue
                    except Exception:
                        pass
                if not output_paths.brief_output.exists():
                    output_paths.brief_output.parent.mkdir(parents=True, exist_ok=True)
                    output_paths.brief_output.write_text(render_brief_markdown(payload), encoding="utf-8")
                manifest_path = write_controller_agent_manifest(
                    day_dir=run_dir,
                    target_date=target_date,
                    current_step="step_6",
                    output_paths=output_map,
                    prompt_paths={"step_6": BRIEF_PROMPT_PATH},
                    input_paths={"step_6": (output_paths.analysis_output, output_paths.issues_output)},
                    required_fields={
                        "step_6": (
                            "核心判断",
                            "增量信息",
                            "产业/公司影响",
                            "需要继续跟踪的点",
                            "源地址",
                            "补充地址",
                        )
                    },
                    instruction=StepInstruction(
                        step_name="step_6",
                        prompt_path=BRIEF_PROMPT_PATH,
                        input_paths=(output_paths.analysis_output, output_paths.issues_output),
                        output_path=output_paths.brief_output,
                        required_fields=(
                            "核心判断",
                            "增量信息",
                            "产业/公司影响",
                            "需要继续跟踪的点",
                            "源地址",
                            "补充地址",
                        ),
                        notes=(
                            "控制 agent 只补 step 6 正式简报内容，不改运行摘要统计。",
                            "如果仍有模板占位语句，下一步不会继续。",
                        ),
                    ),
                    next_action="当前已进入 step 6，请控制 agent 基于 step 5 分析结果补完正式简报后再继续。",
                )
                print(f"C114 正文分析模板生成完成 {target_date.isoformat()}")
                print(f"正文分析 YAML: {output_paths.analysis_output}")
                print(f"层问题 YAML: {output_paths.issues_output}")
                print(f"主题简报 MD: {output_paths.brief_output}")
                print(f"执行 manifest: {manifest_path}\n")
                continue
            print(f"C114 正文分析模板生成完成 {target_date.isoformat()}")
            print(f"正文分析 YAML: {output_paths.analysis_output}")
            print(f"层问题 YAML: {output_paths.issues_output}")
            if missing_analysis:
                print("step 5 模型补全后仍不完整，step 6 不继续生成。")
                print(f"当前使用的内置提示词: {CONTENT_ANALYSIS_PROMPT_PATH}")
                print("请检查 step 4 正文质量或 LLM 输出，并在修复后重新运行 c114-analyze-content：")
                for issue in missing_analysis[:10]:
                    fields = "、".join(issue["missing_fields"])
                    print(f"- [{issue['topic']}] {issue['original_title']}: {fields}")
                if len(missing_analysis) > 10:
                    print(f"- 其余 {len(missing_analysis) - 10} 篇请查看 step 5 YAML")
                print("")
                continue
            output_paths.brief_output.parent.mkdir(parents=True, exist_ok=True)
            step6_checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=output_paths.brief_output,
                    step_name="step_6",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_6",
                report_date=target_date.isoformat(),
                input_path=output_paths.analysis_output,
                output_path=output_paths.brief_output,
            )
            output_paths.brief_output.write_text(
                generate_brief_markdown(payload, llm_client, checkpoint_store=step6_checkpoint_store),
                encoding="utf-8",
            )
            print(f"主题简报 MD: {output_paths.brief_output}\n")
        return

    if args.command == "c114-review-brief":
        execution_mode = resolve_requested_execution_mode(args)
        target_dates = resolve_c114_date_range(args)
        llm_client = require_llm_client() if execution_mode == "builtin" else None
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
            trace_run_dir = infer_trace_run_dir(
                output_paths.brief_input_path,
                output_paths.analysis_input_path,
                output_paths.content_input_path,
                output_paths.review_output_path,
            )
            bind_llm_trace_log(
                llm_client,
                run_dir=trace_run_dir,
                target_date=target_date,
                reset_file=not (trace_run_dir / LLM_TRACE_LOG_DIR_NAME).exists(),
            )
            if execution_mode == "controller-agent":
                if output_paths.review_output_path.exists():
                    try:
                        validate_brief_review_yaml_for_agent(output_paths.review_output_path)
                        print(f"C114 简报审查已完成 {target_date.isoformat()}")
                        print(f"审查报告 YAML: {output_paths.review_output_path}\n")
                        continue
                    except Exception:
                        pass
                else:
                    output_paths.review_output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_paths.review_output_path.write_text(
                        render_brief_review_template_yaml(
                            target_date.isoformat(),
                            output_paths.brief_input_path,
                            output_paths.analysis_input_path,
                        ),
                        encoding="utf-8",
                    )
                run_dir = output_paths.review_output_path.parent
                output_map = build_controller_output_paths(run_dir, target_date)
                manifest_path = write_controller_agent_manifest(
                    day_dir=run_dir,
                    target_date=target_date,
                    current_step="step_7",
                    output_paths=output_map,
                    prompt_paths={"step_7": BRIEF_REVIEW_PROMPT_PATH},
                    input_paths={
                        "step_7": (
                            output_paths.brief_input_path,
                            output_paths.analysis_input_path,
                            output_paths.content_input_path,
                        )
                    },
                    required_fields={
                        "step_7": (
                            "overall_decision",
                            "summary",
                            "findings",
                            "strengths",
                        )
                    },
                    instruction=StepInstruction(
                        step_name="step_7",
                        prompt_path=BRIEF_REVIEW_PROMPT_PATH,
                        input_paths=(
                            output_paths.brief_input_path,
                            output_paths.analysis_input_path,
                            output_paths.content_input_path,
                        ),
                        output_path=output_paths.review_output_path,
                        required_fields=("overall_decision", "summary", "findings", "strengths"),
                        notes=(
                            "控制 agent 只填写审查结论，不改 step 6 简报或 step 5 分析文件。",
                            "overall_decision 只能是 pass 或 revise。",
                        ),
                    ),
                    next_action="当前已进入 step 7，请控制 agent 基于简报与分析证据完成审查 YAML。",
                )
                print(f"C114 简报审查模板生成完成 {target_date.isoformat()}")
                print(f"审查报告 YAML: {output_paths.review_output_path}")
                print(f"执行 manifest: {manifest_path}\n")
                continue
            checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=output_paths.review_output_path,
                    step_name="step_7",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_7",
                report_date=target_date.isoformat(),
                input_path=output_paths.brief_input_path,
                output_path=output_paths.review_output_path,
            )
            report = build_brief_review_report_with_llm(
                report_date=target_date.isoformat(),
                brief_path=output_paths.brief_input_path,
                analysis_path=output_paths.analysis_input_path,
                content_path=output_paths.content_input_path,
                llm_client=llm_client,
                checkpoint_store=checkpoint_store,
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
            runtime_config = load_c114_runtime_config()
            if runtime_config.execution_mode == "controller-agent":
                print("说明：当前是 controller-agent 模式，不强制要求内置 LLM key；请至少配置一个搜索 provider key，并补齐 search/content/brief 基础配置。")
            else:
                print("说明：step 1 可先不配 API key；若要继续跑 step 2-7，需要先配置 llm.providers[0].api_key，再至少配置一个搜索 provider key（Tavily / Metaso / Baidu 三选一），并补齐其余 search/content/brief 基础配置。若启用 provider 链切换，还要补齐链路中其它 provider 的 api_key。")
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
            print("请编辑 runtime.local.json，补齐 keys/search/content/brief/llm 配置后再运行。")
        return

    if args.command == "run":
        execution_mode = resolve_requested_execution_mode(args)
        target_dates = resolve_c114_date_range(args, default_to_today=True)
        runtime_config = load_c114_runtime_config()
        llm_client = require_llm_client() if execution_mode == "builtin" else None
        if execution_mode == "builtin":
            if len(target_dates) > 1:
                day_dirs = create_c114_range_day_directories(resolved_paths, target_dates)
            else:
                run_dir = create_search_run_directory(resolved_paths.reports_dir)
                day_dirs = {target_dates[0]: run_dir}
        else:
            day_dirs = {target_date: controller_run_directory(resolved_paths, target_date) for target_date in target_dates}

        for target_date in target_dates:
            day_dir = day_dirs[target_date]
            bind_llm_trace_log(
                llm_client,
                run_dir=day_dir,
                target_date=target_date,
                reset_file=execution_mode == "builtin",
            )
            if execution_mode == "controller-agent":
                output_map = build_controller_output_paths(day_dir, target_date)
                raw_output = resolve_hot_topics_output_path(
                    project_root=resolved_paths.project_root,
                    raw_dir=resolved_paths.raw_dir,
                    report_date=target_date,
                    output_override=None,
                )
                if not output_map["step_1"].exists():
                    report = collect_daily_report(
                        report_date=target_date,
                        channel_keys=args.channels,
                        timeout=float(args.timeout),
                        candidate_limit=int(args.candidate_limit),
                    )
                    save_daily_report(raw_output, target_date, report)
                    analysis_paths = resolve_analysis_output_paths(
                        paths=resolved_paths,
                        report_date=target_date,
                        analysis_output_override=str(output_map["step_1"]),
                        checklist_output_override=str(output_map["step_2"]),
                    )
                    analyses, briefs = analyze_daily_articles(analysis_paths.input_path, target_date.isoformat())
                    checklist_items = write_analysis_outputs(
                        analysis_paths,
                        target_date.isoformat(),
                        analyses,
                        llm_client=None,
                        auto_fill_keywords=False,
                    )
                    if not checklist_items:
                        print(f"C114 全流程 step 1 完成 {target_date.isoformat()}")
                        print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
                        continue
                    manifest_path = write_controller_agent_manifest(
                        day_dir=day_dir,
                        target_date=target_date,
                        current_step="step_2",
                        output_paths=output_map,
                        prompt_paths={"step_2": SEARCH_KEYWORD_PROMPT_PATH},
                        input_paths={"step_2": (output_map["step_1"],)},
                        required_fields={"step_2": ("keywords[0]", "keywords[1]")},
                        instruction=StepInstruction(
                            step_name="step_2",
                            prompt_path=SEARCH_KEYWORD_PROMPT_PATH,
                            input_paths=(output_map["step_1"],),
                            output_path=output_map["step_2"],
                            required_fields=("keywords[0]", "keywords[1]"),
                            notes=("请控制 agent 为每篇文章补足两组关键词。",),
                        ),
                        next_action="当前已进入 step 2，请补全关键词后重新运行 run。",
                    )
                    print(f"C114 全流程 step 1-2 准备完成 {target_date.isoformat()}")
                    print(f"Step 1 CSV: {output_map['step_1']}")
                    print(f"Step 2 模板 YAML: {output_map['step_2']}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if not step2_keywords_completed(output_map["step_2"]):
                    manifest_path = write_controller_agent_manifest(
                        day_dir=day_dir,
                        target_date=target_date,
                        current_step="step_2",
                        output_paths=output_map,
                        prompt_paths={"step_2": SEARCH_KEYWORD_PROMPT_PATH},
                        input_paths={"step_2": (output_map["step_1"],)},
                        required_fields={"step_2": ("keywords[0]", "keywords[1]")},
                        instruction=StepInstruction(
                            step_name="step_2",
                            prompt_path=SEARCH_KEYWORD_PROMPT_PATH,
                            input_paths=(output_map["step_1"],),
                            output_path=output_map["step_2"],
                            required_fields=("keywords[0]", "keywords[1]"),
                            notes=("step 2 还未补完，请继续填写 step 2 YAML。",),
                        ),
                        next_action="step 2 尚未完成，请补全关键词后重新运行 run。",
                    )
                    print(f"Step 2 仍待控制 agent 完成：{output_map['step_2']}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if not output_map["step_3"].exists():
                    search_paths = resolve_search_output_paths(
                        paths=resolved_paths,
                        report_date=target_date,
                        input_override=str(output_map["step_2"]),
                        output_override=str(output_map["step_3"]),
                        provider=args.provider,
                    )
                    search_checkpoint_store = StepCheckpointStore.load_or_create(
                        checkpoint_path=checkpoint_path_for_step(
                            output_path=search_paths.output_path,
                            step_name="step_3",
                            report_date=target_date.isoformat(),
                        ),
                        step_name="step_3",
                        report_date=target_date.isoformat(),
                        input_path=search_paths.input_path,
                        output_path=search_paths.output_path,
                    )
                    search_payload = run_search_workflow(
                        input_path=search_paths.input_path,
                        report_date=target_date.isoformat(),
                        per_query_limit=int(args.per_query_limit),
                        per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
                        extract_limit=int(args.extract_limit),
                        provider_name=search_paths.provider,
                        llm_client=None,
                        trace_logger=build_search_trace_logger(
                            day_dir,
                            target_date,
                            reset_file=not (
                                day_dir / LLM_TRACE_LOG_DIR_NAME / search_trace_log_name_for_step("step_3", target_date)
                            ).exists(),
                        ),
                        checkpoint_store=search_checkpoint_store,
                    )
                    save_search_results(search_paths.output_path, search_payload)
                    manifest_path = write_controller_agent_manifest(
                        day_dir=day_dir,
                        target_date=target_date,
                        current_step="step_3",
                        output_paths=output_map,
                        prompt_paths={"step_3": SEARCH_REVIEW_PROMPT_PATH},
                        input_paths={"step_3": (output_map["step_2"], output_map["step_3"])},
                        required_fields={"step_3": ("keep_level", "reason", "relevance_note", "value_type")},
                        instruction=StepInstruction(
                            step_name="step_3",
                            prompt_path=SEARCH_REVIEW_PROMPT_PATH,
                            input_paths=(output_map["step_2"], output_map["step_3"]),
                            output_path=output_map["step_3"],
                            required_fields=("ai_review.status=reviewed", "ai_review.keep_level", "ai_review.reason", "ai_review.relevance_note", "ai_review.value_type"),
                            notes=("只补 selected_results 下的 ai_review。",),
                        ),
                        next_action="当前已进入 step 3，请完成外链精筛后重新运行 run。",
                    )
                    print(f"Step 3 YAML: {output_map['step_3']}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if not step3_review_completed(output_map["step_3"]):
                    manifest_path = write_controller_agent_manifest(
                        day_dir=day_dir,
                        target_date=target_date,
                        current_step="step_3",
                        output_paths=output_map,
                        prompt_paths={"step_3": SEARCH_REVIEW_PROMPT_PATH},
                        input_paths={"step_3": (output_map["step_2"], output_map["step_3"])},
                        required_fields={"step_3": ("keep_level", "reason", "relevance_note", "value_type")},
                        instruction=StepInstruction(
                            step_name="step_3",
                            prompt_path=SEARCH_REVIEW_PROMPT_PATH,
                            input_paths=(output_map["step_2"], output_map["step_3"]),
                            output_path=output_map["step_3"],
                            required_fields=("ai_review.status=reviewed", "ai_review.keep_level", "ai_review.reason", "ai_review.relevance_note", "ai_review.value_type"),
                            notes=("step 3 尚未审查完成。",),
                        ),
                        next_action="step 3 尚未完成，请补全 ai_review 后重新运行 run。",
                    )
                    print(f"Step 3 仍待控制 agent 完成：{output_map['step_3']}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if not output_map["step_4"].exists():
                    content_paths = resolve_content_output_paths(
                        paths=resolved_paths,
                        report_date=target_date,
                        input_override=str(output_map["step_3"]),
                        output_override=str(output_map["step_4"]),
                    )
                    content_payload = run_content_fetch_workflow(
                        input_path=content_paths.input_path,
                        report_date=target_date.isoformat(),
                    )
                    save_content_results(content_paths.output_path, content_payload)
                if not output_map["step_5"].exists():
                    content_analysis_paths = resolve_content_analysis_output_paths(
                        paths=resolved_paths,
                        report_date=target_date,
                        input_override=str(output_map["step_4"]),
                        output_override=str(output_map["step_5"]),
                        issues_output_override=str(day_dir / layer_issues_name(target_date)),
                    )
                    analysis_payload = load_content_analysis_inputs(content_analysis_paths.input_path)
                    save_content_analysis_yaml(content_analysis_paths.analysis_output, analysis_payload)
                    issues = generate_layer_issues(
                        report_date=target_date.isoformat(),
                        raw_csv_path=resolved_paths.raw_dir / "c114_hot_topics.csv",
                        checklist_path=day_dir / step_2_checklist_name(target_date),
                        search_results_path=day_dir / step_3_results_name(target_date),
                        content_path=content_analysis_paths.input_path,
                    )
                    save_layer_issues_yaml(content_analysis_paths.issues_output, target_date.isoformat(), issues)
                if not step5_analysis_completed(output_map["step_5"]):
                    manifest_path = write_controller_agent_manifest(
                        day_dir=day_dir,
                        target_date=target_date,
                        current_step="step_5",
                        output_paths=output_map,
                        prompt_paths={"step_5": CONTENT_ANALYSIS_PROMPT_PATH},
                        input_paths={"step_5": (output_map["step_4"],)},
                        required_fields={"step_5": ("summary", "core_points", "new_facts", "entities", "signals", "risk_or_uncertainty", "why_it_matters", "layer_notes")},
                        instruction=StepInstruction(
                            step_name="step_5",
                            prompt_path=CONTENT_ANALYSIS_PROMPT_PATH,
                            input_paths=(output_map["step_4"],),
                            output_path=output_map["step_5"],
                            required_fields=("summary", "core_points", "new_facts", "entities", "signals", "risk_or_uncertainty", "why_it_matters", "layer_notes"),
                            notes=("请控制 agent 只补 analysis 字段。",),
                        ),
                        next_action="当前已进入 step 5，请补完正文分析后重新运行 run。",
                    )
                    print(f"Step 4 YAML: {output_map['step_4']}")
                    print(f"Step 5 YAML: {output_map['step_5']}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if not output_map["step_6"].exists():
                    analysis_payload = load_content_analysis_inputs(output_map["step_5"])
                    output_map["step_6"].parent.mkdir(parents=True, exist_ok=True)
                    output_map["step_6"].write_text(render_brief_markdown(analysis_payload), encoding="utf-8")
                if not step6_brief_completed(output_map["step_6"], output_map["step_5"]):
                    manifest_path = write_controller_agent_manifest(
                        day_dir=day_dir,
                        target_date=target_date,
                        current_step="step_6",
                        output_paths=output_map,
                        prompt_paths={"step_6": BRIEF_PROMPT_PATH},
                        input_paths={"step_6": (output_map["step_5"],)},
                        required_fields={"step_6": ("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点", "源地址", "补充地址")},
                        instruction=StepInstruction(
                            step_name="step_6",
                            prompt_path=BRIEF_PROMPT_PATH,
                            input_paths=(output_map["step_5"],),
                            output_path=output_map["step_6"],
                            required_fields=("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点", "源地址", "补充地址"),
                            notes=("请控制 agent 正式撰写 step 6 简报。",),
                        ),
                        next_action="当前已进入 step 6，请补完正式简报后重新运行 run。",
                    )
                    print(f"Step 6 MD: {output_map['step_6']}")
                    print(f"执行 manifest: {manifest_path}\n")
                    continue
                if runtime_config.review_enable_step7:
                    if not output_map["step_7"].exists():
                        output_map["step_7"].parent.mkdir(parents=True, exist_ok=True)
                        output_map["step_7"].write_text(
                            render_brief_review_template_yaml(target_date.isoformat(), output_map["step_6"], output_map["step_5"]),
                            encoding="utf-8",
                        )
                    if not step7_review_completed(output_map["step_7"]):
                        manifest_path = write_controller_agent_manifest(
                            day_dir=day_dir,
                            target_date=target_date,
                            current_step="step_7",
                            output_paths=output_map,
                            prompt_paths={"step_7": BRIEF_REVIEW_PROMPT_PATH},
                            input_paths={"step_7": (output_map["step_6"], output_map["step_5"], output_map["step_4"])},
                            required_fields={"step_7": ("overall_decision", "summary", "findings", "strengths")},
                            instruction=StepInstruction(
                                step_name="step_7",
                                prompt_path=BRIEF_REVIEW_PROMPT_PATH,
                                input_paths=(output_map["step_6"], output_map["step_5"], output_map["step_4"]),
                                output_path=output_map["step_7"],
                                required_fields=("overall_decision", "summary", "findings", "strengths"),
                                notes=("请控制 agent 完成最终审查 YAML。",),
                            ),
                            next_action="当前已进入 step 7，请补完审查 YAML 后重新运行 run。",
                        )
                        print(f"Step 7 YAML: {output_map['step_7']}")
                        print(f"执行 manifest: {manifest_path}\n")
                        continue
                manifest_path = write_controller_agent_manifest(
                    day_dir=day_dir,
                    target_date=target_date,
                    current_step="completed",
                    output_paths=output_map,
                    prompt_paths={},
                    input_paths={},
                    required_fields={},
                    instruction=StepInstruction(
                        step_name="completed",
                        prompt_path=None,
                        input_paths=(),
                        output_path=output_map["step_7"] if runtime_config.review_enable_step7 else output_map["step_6"],
                        required_fields=(),
                        notes=("当前日期的 controller-agent 流程已完成。",),
                    ),
                    next_action="当前日期流程已完成。",
                )
                print(f"C114 controller-agent 流程完成 {target_date.isoformat()}")
                print(f"Step 6 MD: {output_map['step_6']}")
                if runtime_config.review_enable_step7:
                    print(f"Step 7 YAML: {output_map['step_7']}")
                print(f"执行 manifest: {manifest_path}\n")
                continue
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
            if execution_mode == "builtin" and analyses and all(isinstance(item, ArticleAnalysis) for item in analyses):
                topic_grouping_checkpoint_store = StepCheckpointStore.load_or_create(
                    checkpoint_path=checkpoint_path_for_step(
                        output_path=analysis_paths.analysis_output,
                        step_name="step_1_5",
                        report_date=target_date.isoformat(),
                    ),
                    step_name="step_1_5",
                    report_date=target_date.isoformat(),
                    input_path=analysis_paths.input_path,
                    output_path=analysis_paths.analysis_output,
                )
                analyses, briefs = auto_group_analysis_topics(
                    analyses,
                    llm_client,
                    report_date=target_date.isoformat(),
                    checkpoint_store=topic_grouping_checkpoint_store,
                )
            checklist_items = write_analysis_outputs(analysis_paths, target_date.isoformat(), analyses, llm_client=llm_client)
            print(f"C114 全流程 step 1-2 完成 {target_date.isoformat()}")
            print(f"文章数: {len(analyses)}")
            print(f"主题数: {len(briefs)}")
            print(f"Step 1 CSV: {analysis_paths.analysis_output}")
            if not checklist_items:
                print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
                continue
            print(f"Step 2 YAML: {analysis_paths.checklist_output}")
            search_output = (day_dir / step_3_results_name(target_date)).resolve()
            search_paths = resolve_search_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=str(analysis_paths.checklist_output),
                output_override=str(search_output),
                provider=args.provider,
            )
            search_checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=search_paths.output_path,
                    step_name="step_3",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_3",
                report_date=target_date.isoformat(),
                input_path=search_paths.input_path,
                output_path=search_paths.output_path,
            )
            search_payload = run_search_workflow(
                input_path=search_paths.input_path,
                report_date=target_date.isoformat(),
                per_query_limit=int(args.per_query_limit),
                per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
                extract_limit=int(args.extract_limit),
                provider_name=search_paths.provider,
                llm_client=llm_client,
                trace_logger=build_search_trace_logger(
                    day_dir,
                    target_date,
                    reset_file=not (
                        day_dir / LLM_TRACE_LOG_DIR_NAME / search_trace_log_name_for_step("step_3", target_date)
                    ).exists(),
                ),
                checkpoint_store=search_checkpoint_store,
            )
            save_search_results(search_paths.output_path, search_payload)
            content_output = (day_dir / step_4_content_name(target_date)).resolve()
            content_paths = resolve_content_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=str(search_paths.output_path),
                output_override=str(content_output),
            )
            content_checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=content_paths.output_path,
                    step_name="step_4",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_4",
                report_date=target_date.isoformat(),
                input_path=content_paths.input_path,
                output_path=content_paths.output_path,
            )
            content_payload = run_content_fetch_workflow(
                input_path=content_paths.input_path,
                report_date=target_date.isoformat(),
                checkpoint_store=content_checkpoint_store,
            )
            save_content_results(content_paths.output_path, content_payload)
            analysis_output = (day_dir / step_5_content_analysis_name(target_date)).resolve()
            issues_output = (day_dir / layer_issues_name(target_date)).resolve()
            content_analysis_paths = resolve_content_analysis_output_paths(
                paths=resolved_paths,
                report_date=target_date,
                input_override=str(content_paths.output_path),
                output_override=str(analysis_output),
                issues_output_override=str(issues_output),
            )
            analysis_mode, analysis_retry_attempts = resolve_content_analysis_runtime(runtime_config)
            step5_checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=content_analysis_paths.analysis_output,
                    step_name="step_5",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_5",
                report_date=target_date.isoformat(),
                input_path=content_analysis_paths.input_path,
                output_path=content_analysis_paths.analysis_output,
            )
            analysis_payload = auto_complete_content_analysis(
                load_content_analysis_inputs(content_analysis_paths.input_path),
                llm_client,
                mode=analysis_mode,
                batch_retry_attempts=analysis_retry_attempts,
                checkpoint_store=step5_checkpoint_store,
            )
            save_content_analysis_yaml(content_analysis_paths.analysis_output, analysis_payload)
            issues = generate_layer_issues(
                report_date=target_date.isoformat(),
                raw_csv_path=resolved_paths.raw_dir / "c114_hot_topics.csv",
                checklist_path=day_dir / step_2_checklist_name(target_date),
                search_results_path=day_dir / step_3_results_name(target_date),
                content_path=content_analysis_paths.input_path,
            )
            save_layer_issues_yaml(content_analysis_paths.issues_output, target_date.isoformat(), issues)
            if collect_missing_analysis_fields(analysis_payload):
                raise RuntimeError(f"{target_date.isoformat()} 的 step 5 模型补全后仍不完整，流程已中止。")
            brief_output = content_analysis_paths.brief_output
            brief_output.parent.mkdir(parents=True, exist_ok=True)
            step6_checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=brief_output,
                    step_name="step_6",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_6",
                report_date=target_date.isoformat(),
                input_path=content_analysis_paths.analysis_output,
                output_path=brief_output,
            )
            brief_output.write_text(
                generate_brief_markdown(
                    analysis_payload,
                    llm_client,
                    checkpoint_store=step6_checkpoint_store,
                ),
                encoding="utf-8",
            )
            print(f"Step 3 YAML: {search_paths.output_path}")
            print(f"Step 4 YAML: {content_paths.output_path}")
            print(f"Step 5 YAML: {content_analysis_paths.analysis_output}")
            print(f"Step 6 MD: {brief_output}")
            if runtime_config.review_enable_step7:
                review_paths = resolve_brief_review_output_paths(
                    paths=resolved_paths,
                    report_date=target_date,
                    brief_input_override=str(brief_output),
                    analysis_input_override=str(content_analysis_paths.analysis_output),
                    content_input_override=str(content_paths.output_path),
                    output_override=str((day_dir / step_7_brief_review_name(target_date)).resolve()),
                )
                step7_checkpoint_store = StepCheckpointStore.load_or_create(
                    checkpoint_path=checkpoint_path_for_step(
                        output_path=review_paths.review_output_path,
                        step_name="step_7",
                        report_date=target_date.isoformat(),
                    ),
                    step_name="step_7",
                    report_date=target_date.isoformat(),
                    input_path=review_paths.brief_input_path,
                    output_path=review_paths.review_output_path,
                )
                review_report = build_brief_review_report_with_llm(
                    report_date=target_date.isoformat(),
                    brief_path=review_paths.brief_input_path,
                    analysis_path=review_paths.analysis_input_path,
                    content_path=review_paths.content_input_path,
                    llm_client=llm_client,
                    checkpoint_store=step7_checkpoint_store,
                )
                save_brief_review_yaml(review_paths.review_output_path, review_report)
                print(f"Step 7 YAML: {review_paths.review_output_path}\n")
            else:
                print("Step 7 已在配置中关闭，流程停留在 step 6。\n")
        return

    raise ValueError(f"未知命令: {args.command}")


def main(argv: list[str] | None = None) -> None:
    """作为脚本入口解析参数并执行对应命令。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    run_with_args(args)


if __name__ == "__main__":
    main()
