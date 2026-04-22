from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from c114.runtime.config import load_c114_runtime_config
from utils.tools.analysis.csv_io import save_article_analysis_csv
from utils.tools.facades.intelligence import (
    LLM_TRACE_LOG_DIR_NAME,
    ArticleAnalysis,
    analyze_daily_articles,
    auto_group_analysis_topics,
)
from utils.tools.llm import StructuredChatClient
from utils.tools.output.briefing import write_step1_csv
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step

from . import names as kr36_names
from .brief_assets import save_brief_preview_assets
from .pipeline import run_stages_2_3_4
from .settings import AppPaths, ensure_directories, resolve_paths
from .source_adapter import Kr36SourceAdapter

RUN_DIR_PREFIX = "kr36_search_"
RAW_JSON_PREFIX = "kr36_hot_topics"
RAW_CSV_NAME = "kr36_hot_topics.csv"
# 与历史脚本兼容：曾用下划线区分的 step2–6 文件前缀（见各 *_name 函数）
LEGACY_PREFIX_STEP2_CHECKLIST = "kr36_step_2_search_checklist"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TOPIC_GROUPING_PROMPT_PATH = PROMPTS_DIR / "topic-grouping-agent.md"
SEARCH_KEYWORD_PROMPT_PATH = (
    PROMPTS_DIR / "search-keyword-agent.md"
)  # 保留；新流程不调用 write_analysis_outputs
SEARCH_TRACE_PREFIX = "kr36_search_trace_step_3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the 36Kr daily hot-topics skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hot_topics = subparsers.add_parser("kr36-hot-topics", help="Fetch daily hot topics from 36Kr.")
    hot_topics.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")

    run_parser = subparsers.add_parser("run", help="Run 36kr：四步（步骤1 分析 CSV → 步骤2 正文 → 步骤3 整合分析 → 步骤4 导出）。")
    run_parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    run_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google"],
        help="c114 入口兼容保留；新四步 36kr 不执行外搜，此参数无效果。",
    )
    run_parser.add_argument("--per-query-limit", type=int, default=5, help="兼容 c114 入口，无效果。")
    run_parser.add_argument("--per-article-limit", type=int, default=None, help="兼容 c114 入口，无效果。")
    run_parser.add_argument("--extract-limit", type=int, default=5, help="兼容 c114 入口，无效果。")
    run_parser.add_argument(
        "--external-search",
        action="store_true",
        help="兼容 c114 入口；新 36kr 四步不启用外搜，将忽略。",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_with_args(args)


def run_with_args(args: argparse.Namespace, *, paths: AppPaths | None = None) -> None:
    resolved_paths = paths or resolve_paths()
    ensure_directories(resolved_paths)

    if args.command == "kr36-hot-topics":
        target_date = date.fromisoformat(args.date)
        payload = fetch_and_materialize_kr36_articles(target_date, resolved_paths)
        print(f"36Kr 当日文章抓取完成 {target_date.isoformat()}")
        print(f"文章数: {len(payload['articles'])}")
        print(f"原始 JSON: {payload['raw_json_path']}")
        print(f"原始 CSV: {payload['raw_csv_path']}")
        return

    if args.command != "run":
        raise ValueError(f"Unsupported command: {args.command}")

    if getattr(args, "external_search", False):
        print("提示：--external-search 在 36kr 新四步流程中已忽略（不再走 C114 外搜/搜索清单）。")

    target_date = date.fromisoformat(args.date)
    _ = load_c114_runtime_config()
    llm_client = require_llm_client()
    run_dir = create_run_directory(resolved_paths.reports_dir)
    bind_llm_trace_log(llm_client, run_dir=run_dir, target_date=target_date, reset_file=True)

    raw_payload = fetch_and_materialize_kr36_articles(target_date, resolved_paths, run_dir=run_dir)
    raw_csv_path = raw_payload["raw_csv_path"]
    report_date_text = target_date.isoformat()

    analysis_output = run_dir / step_1_analysis_name(target_date)
    analyses, _briefs = analyze_daily_articles(raw_csv_path, report_date_text)
    if analyses and all(isinstance(item, ArticleAnalysis) for item in analyses):
        topic_grouping_checkpoint_store = StepCheckpointStore.load_or_create(
            checkpoint_path=checkpoint_path_for_step(
                output_path=analysis_output,
                step_name="step_1_5",
                report_date=report_date_text,
                prefix="kr36",
            ),
            step_name="step_1_5",
            report_date=report_date_text,
            input_path=raw_csv_path,
            output_path=analysis_output,
        )
        analyses, _briefs = auto_group_analysis_topics(
            analyses,
            llm_client,
            report_date=report_date_text,
            source_site="36kr",
            checkpoint_store=topic_grouping_checkpoint_store,
            prompt_path=TOPIC_GROUPING_PROMPT_PATH,
        )
    save_article_analysis_csv(analysis_output, analyses)
    count = len(analyses)
    print(f"36Kr 步骤1 {target_date.isoformat()} 完成 | 条数: {count}")
    print(f"  Step1 CSV: {analysis_output}")
    if not analyses:
        print("当日无分析条目，结束。")
        return

    run_stages_2_3_4(
        run_dir=run_dir,
        target_date=target_date,
        report_date_text=report_date_text,
        analyses=analyses,
        step1_csv_path=analysis_output,
        llm_client=llm_client,
    )


def fetch_and_materialize_kr36_articles(
    target_date: date,
    paths: AppPaths,
    *,
    run_dir: Path | None = None,
) -> dict[str, object]:
    runtime_config = load_c114_runtime_config()
    adapter = Kr36SourceAdapter(runtime_config.source_configs.get("kr36", {}))
    articles = adapter.fetch_standard_articles(target_date)
    raw_json_path = (run_dir or paths.raw_dir) / raw_json_name(target_date)
    raw_csv_path = (run_dir or paths.raw_dir) / RAW_CSV_NAME
    raw_json_path.parent.mkdir(parents=True, exist_ok=True)
    raw_json_path.write_text(
        json.dumps(
            {
                "report_date": target_date.isoformat(),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "articles": [asdict(article) for article in articles],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_step1_csv(raw_csv_path, target_date.isoformat(), articles)
    return {
        "articles": articles,
        "raw_json_path": raw_json_path,
        "raw_csv_path": raw_csv_path,
    }


def require_llm_client() -> StructuredChatClient:
    return StructuredChatClient.from_runtime_config()


def bind_llm_trace_log(
    llm_client: StructuredChatClient,
    *,
    run_dir: Path,
    target_date: date,
    reset_file: bool = False,
) -> None:
    llm_client.set_trace_log_directory(
        (run_dir / LLM_TRACE_LOG_DIR_NAME).resolve(),
        report_date=target_date.isoformat(),
        source_prefix="kr36",
        reset_files=reset_file,
    )


def create_run_directory(reports_dir: Path, run_started_at: datetime | None = None) -> Path:
    timestamp = run_started_at or datetime.now()
    base_name = f"{RUN_DIR_PREFIX}{timestamp.strftime('%Y%m%d%H%M')}"
    base_dir = reports_dir / "kr36_report"
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def raw_json_name(report_date: date) -> str:
    return f"{RAW_JSON_PREFIX}_{report_date.strftime('%Y%m%d')}.json"


# --- 新四步：文件名以 kr36_names 为准 ---


def step_1_analysis_name(report_date: date) -> str:
    return kr36_names.step1_analysis_name(report_date)


def step2_content_name(report_date: date) -> str:
    return kr36_names.step2_content_name(report_date)


def step3_analysis_name(report_date: date) -> str:
    return kr36_names.step3_analysis_name(report_date)


def step4_brief_name(report_date: date) -> str:
    return kr36_names.step4_brief_name(report_date)


def layer_issues_name(report_date: date) -> str:
    return kr36_names.layer_issues_name(report_date)


# --- 与旧 6 步脚本的对应（同一角色，新文件名见 kr36/docs/pipeline.md） ---


def step_2_checklist_name(report_date: date) -> str:
    """已废弃；新流程不生成。保留仅以免旧 import 在导入期崩溃。"""
    return f"{LEGACY_PREFIX_STEP2_CHECKLIST}_{report_date.strftime('%Y%m%d')}.yaml"


def step_3_results_name(report_date: date) -> str:
    """已废弃。保留供极少数字符串检查。"""
    return f"kr36_step_3_search_results_{report_date.strftime('%Y%m%d')}.yaml"


def step_4_content_name(report_date: date) -> str:
    return kr36_names.step2_content_name(report_date)


def step_5_content_analysis_name(report_date: date) -> str:
    return kr36_names.step3_analysis_name(report_date)


def step_6_brief_name(report_date: date) -> str:
    return kr36_names.step4_brief_name(report_date)


def search_trace_log_name(report_date: date) -> str:
    return f"{SEARCH_TRACE_PREFIX}_{report_date.strftime('%Y%m%d')}.jsonl"
