"""Websearch skill 的统一命令行入口。

这个文件只保留 parser 构建、测试可 patch 的门面，以及命令分发。
复杂工作流已下沉到 `commands/` 与 `pipeline.py`。
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.tools.orchestration.c114.brief_review import (
    BRIEF_REVIEW_PROMPT_PATH,
    build_brief_review_report_with_llm,
    render_brief_review_template_yaml,
    resolve_brief_review_output_paths,
    save_brief_review_yaml,
    validate_brief_markdown_for_agent,
    validate_brief_review_yaml_for_agent,
)
from utils.tools.orchestration.c114.hot_topics_data import (
    collect_daily_report,
    render_daily_report,
    resolve_hot_topics_output_path,
    save_daily_report,
)
from utils.tools.orchestration.c114.config import (
    handle_config_apply_command,
    handle_config_init_command,
    handle_config_status_command,
)
from utils.tools.orchestration.c114.run import handle_run_command
from utils.tools.orchestration.c114.step_commands import (
    handle_analyze_command,
    handle_analyze_content_command,
    handle_fetch_content_command,
    handle_hot_topics_command,
    handle_review_brief_command,
    handle_search_command,
)
from utils.tools.orchestration.state_engine import (
    build_controller_output_paths,
    controller_run_directory,
    step2_keywords_completed,
    step3_review_completed,
    step5_analysis_completed,
    step6_brief_completed,
    step7_review_completed,
    step_manifest_path,
    write_controller_agent_manifest,
)
from utils.tools.facades.content import (
    load_search_results_yaml,
    resolve_content_output_paths,
    run_content_fetch_workflow,
    save_content_results,
    validate_content_fetch_inputs,
)
from utils.tools.facades.content_analysis import (
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
from utils.tools.facades.intelligence import (
    LLM_TRACE_LOG_DIR_NAME,
    ArticleAnalysis,
    analyze_daily_articles,
    auto_group_analysis_topics,
    create_search_range_directory,
    create_search_run_directory,
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
from utils.tools.facades.intelligence import PROMPT_PATH as SEARCH_KEYWORD_PROMPT_PATH
from utils.tools.llm import StructuredChatClient
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step
from .runtime.config import (
    collect_missing_c114_config,
    initialize_c114_local_config,
    load_c114_runtime_config,
    read_c114_local_config,
    runtime_local_path,
    write_c114_local_config,
)
from .runtime.execution import StepInstruction, normalize_execution_mode
from .runtime.settings import AppPaths, ensure_directories, resolve_paths
from utils.tools.search.workflow import (
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


def resolve_c114_date_range(
    args: argparse.Namespace,
    *,
    default_to_today: bool = False,
    default_days_ago: int = 0,
) -> list[date]:
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
        return [_parse_single_date_or_time(single_date)]
    if default_to_today:
        offset = max(0, int(default_days_ago))
        return [datetime.now().date() - timedelta(days=offset)]
    raise ValueError("必须提供 --date，或者同时提供 --start-date 和 --end-date。")


def _parse_single_date_or_time(value: str) -> date:
    """解析单日参数：支持 YYYY-MM-DD；若仅传 HH:MM 则按当天处理。"""

    normalized = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", normalized):
        return datetime.now().date()
    return date.fromisoformat(normalized)


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

    from utils.tools.facades.intelligence import find_latest_c114_step_file

    resolved = find_latest_c114_step_file(paths.reports_dir, target_date, file_name)
    if resolved is None:
        raise FileNotFoundError(f"未找到 {target_date.isoformat()} 的{label}：{file_name}")
    return resolved


def require_llm_client() -> StructuredChatClient:
    """构造统一的主备大模型客户端，缺配置时抛出可读错误。"""

    return StructuredChatClient.from_runtime_config()


def bind_llm_trace_log(
    llm_client: StructuredChatClient | None,
    *,
    run_dir: Path,
    target_date: date,
    source_prefix: str = "c114",
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
            source_prefix=source_prefix,
            reset_files=reset_file,
        )
        return
    set_trace_log_path = getattr(llm_client, "set_trace_log_path", None)
    if not callable(set_trace_log_path):
        return
    set_trace_log_path(
        (run_dir / LLM_TRACE_LOG_DIR_NAME / f"legacy_{target_date.isoformat()}.jsonl").resolve(),
        reset_file=reset_file,
    )


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
    """构造单一 websearch skill 的命令行解析器。"""

    parser = argparse.ArgumentParser(description="Run the websearch skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    c114_parser = subparsers.add_parser("c114-hot-topics", help="Fetch daily hot topics from C114.")
    add_c114_date_arguments(c114_parser, default_to_today=True)
    c114_parser.add_argument(
        "--channels",
        nargs="+",
        default=["home", "wireless", "quantum", "satellite", "la", "ai"],
        choices=["home", "wireless", "quantum", "satellite", "la", "ai"],
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
        choices=["auto", "tavily", "metaso", "baidu", "google", "aliyun_iqs"],
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

    c114_run_parser = subparsers.add_parser("run", help="Run the full pipeline for a selected source.")
    add_c114_date_arguments(c114_run_parser, default_to_today=True)
    add_execution_mode_argument(c114_run_parser)
    c114_run_parser.add_argument(
        "--source",
        default="c114",
        choices=["c114", "infoq", "36kr"],
        help="站点来源：默认 c114，也支持 infoq、36kr。",
    )
    c114_run_parser.add_argument(
        "--channels",
        nargs="+",
        default=["home", "wireless", "quantum", "satellite", "la", "ai"],
        choices=["home", "wireless", "quantum", "satellite", "la", "ai"],
        help="Channels to fetch.",
    )
    c114_run_parser.add_argument("--candidate-limit", type=int, default=30, help="Maximum candidates per channel.")
    c114_run_parser.add_argument("--timeout", type=float, default=45.0, help="Per-request timeout in seconds.")
    c114_run_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google", "aliyun_iqs"],
        help="Search provider.",
    )
    c114_run_parser.add_argument("--per-query-limit", type=int, default=5)
    c114_run_parser.add_argument("--per-article-limit", type=int, default=None)
    c114_run_parser.add_argument("--extract-limit", type=int, default=5)
    c114_run_parser.add_argument(
        "--external-search",
        action="store_true",
        help="仅 36kr 生效：启用 step3 外部搜索；默认关闭并跳过 step3 外搜。",
    )

    c114_direct_run_parser = subparsers.add_parser("c114-run", help="Run the full c114 pipeline directly.")
    add_c114_date_arguments(c114_direct_run_parser, default_to_today=True)
    add_execution_mode_argument(c114_direct_run_parser)
    c114_direct_run_parser.add_argument(
        "--channels",
        nargs="+",
        default=["home", "wireless", "quantum", "satellite", "la", "ai"],
        choices=["home", "wireless", "quantum", "satellite", "la", "ai"],
        help="Channels to fetch.",
    )
    c114_direct_run_parser.add_argument("--candidate-limit", type=int, default=30, help="Maximum candidates per channel.")
    c114_direct_run_parser.add_argument("--timeout", type=float, default=45.0, help="Per-request timeout in seconds.")
    c114_direct_run_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google", "aliyun_iqs"],
        help="Search provider.",
    )
    c114_direct_run_parser.add_argument("--per-query-limit", type=int, default=5)
    c114_direct_run_parser.add_argument("--per-article-limit", type=int, default=None)
    c114_direct_run_parser.add_argument("--extract-limit", type=int, default=5)
    return parser


def run_with_args(args: argparse.Namespace, paths: AppPaths | None = None) -> None:
    """根据命令行参数执行一次 C114 工作流命令。"""

    resolved_paths = paths or resolve_paths()
    ensure_directories(resolved_paths)
    if args.command == "c114-run":
        setattr(args, "source", "c114")
    facade = sys.modules[__name__]
    handlers = {
        "c114-hot-topics": lambda: handle_hot_topics_command(args, paths=resolved_paths, facade=facade),
        "c114-analyze": lambda: handle_analyze_command(args, paths=resolved_paths, facade=facade),
        "c114-search": lambda: handle_search_command(args, paths=resolved_paths, facade=facade),
        "c114-fetch-content": lambda: handle_fetch_content_command(args, paths=resolved_paths, facade=facade),
        "c114-analyze-content": lambda: handle_analyze_content_command(args, paths=resolved_paths, facade=facade),
        "c114-review-brief": lambda: handle_review_brief_command(args, paths=resolved_paths, facade=facade),
        "c114-config-status": lambda: handle_config_status_command(args, facade=facade),
        "c114-config-apply": lambda: handle_config_apply_command(args, facade=facade),
        "c114-config-init": lambda: handle_config_init_command(args, facade=facade),
        "run": lambda: handle_run_command(args, paths=resolved_paths, facade=facade),
        "c114-run": lambda: handle_run_command(args, paths=resolved_paths, facade=facade),
    }
    handler = handlers.get(args.command)
    if handler is None:
        raise ValueError(f"未知命令: {args.command}")
    handler()


def main(argv: list[str] | None = None) -> None:
    """作为脚本入口解析参数并执行对应命令。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    run_with_args(args)


__all__ = [
    "AppPaths",
    "ArticleAnalysis",
    "BRIEF_PROMPT_PATH",
    "BRIEF_REVIEW_PROMPT_PATH",
    "CONTENT_ANALYSIS_PROMPT_PATH",
    "LLM_TRACE_LOG_DIR_NAME",
    "SEARCH_KEYWORD_PROMPT_PATH",
    "SEARCH_REVIEW_PROMPT_PATH",
    "SearchTraceLogger",
    "StepCheckpointStore",
    "StepInstruction",
    "add_c114_date_arguments",
    "add_execution_mode_argument",
    "analyze_daily_articles",
    "auto_complete_content_analysis",
    "auto_group_analysis_topics",
    "bind_llm_trace_log",
    "build_brief_review_report_with_llm",
    "build_controller_output_paths",
    "build_parser",
    "build_search_trace_logger",
    "checkpoint_path_for_step",
    "collect_daily_report",
    "collect_missing_analysis_fields",
    "collect_missing_c114_config",
    "controller_run_directory",
    "create_c114_range_day_directories",
    "create_search_run_directory",
    "ensure_directories",
    "find_required_step_input",
    "generate_brief_markdown",
    "generate_layer_issues",
    "handle_analyze_command",
    "handle_analyze_content_command",
    "handle_config_apply_command",
    "handle_config_init_command",
    "handle_config_status_command",
    "handle_fetch_content_command",
    "handle_hot_topics_command",
    "handle_review_brief_command",
    "handle_run_command",
    "handle_search_command",
    "infer_trace_run_dir",
    "initialize_c114_local_config",
    "layer_issues_name",
    "load_c114_runtime_config",
    "load_content_analysis_inputs",
    "load_search_checklist_yaml",
    "load_search_results_yaml",
    "normalize_execution_mode",
    "provider_stats_name",
    "read_c114_local_config",
    "render_brief_markdown",
    "render_brief_review_template_yaml",
    "render_daily_report",
    "require_llm_client",
    "resolve_analysis_output_paths",
    "resolve_brief_review_output_paths",
    "resolve_c114_date_range",
    "resolve_content_analysis_output_paths",
    "resolve_content_analysis_runtime",
    "resolve_content_output_paths",
    "resolve_hot_topics_output_path",
    "resolve_paths",
    "resolve_requested_execution_mode",
    "resolve_search_output_paths",
    "run_content_fetch_workflow",
    "run_search_workflow",
    "run_with_args",
    "runtime_local_path",
    "save_brief_review_yaml",
    "save_content_analysis_yaml",
    "save_content_results",
    "save_daily_report",
    "save_layer_issues_yaml",
    "save_search_results",
    "search_trace_log_name_for_step",
    "step2_keywords_completed",
    "step3_review_completed",
    "step5_analysis_completed",
    "step6_brief_completed",
    "step7_review_completed",
    "step_1_analysis_name",
    "step_2_checklist_name",
    "step_3_results_name",
    "step_4_content_name",
    "step_5_content_analysis_name",
    "step_6_brief_name",
    "step_7_brief_review_name",
    "step_manifest_path",
    "validate_brief_markdown_for_agent",
    "validate_brief_review_yaml_for_agent",
    "validate_content_fetch_inputs",
    "validate_search_checklist_items",
    "write_analysis_outputs",
    "write_controller_agent_manifest",
    "write_c114_local_config",
]


if __name__ == "__main__":
    main()
