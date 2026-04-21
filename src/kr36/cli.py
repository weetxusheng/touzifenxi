from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from c114.runtime.config import load_c114_runtime_config
from utils.tools.facades.content import run_content_fetch_workflow, save_content_results
from utils.tools.facades.content_analysis import (
    ContentAnalysisOutputPaths,
    auto_complete_content_analysis,
    generate_brief_markdown,
    generate_layer_issues,
    load_content_analysis_inputs,
    save_content_analysis_yaml,
    save_layer_issues_yaml,
)
from utils.tools.facades.intelligence import (
    LLM_TRACE_LOG_DIR_NAME,
    AnalysisOutputPaths,
    ArticleAnalysis,
    analyze_daily_articles,
    auto_group_analysis_topics,
    write_analysis_outputs,
)
from utils.tools.llm import StructuredChatClient
from utils.tools.output.briefing import write_step1_csv
from .email import render_kr36_brief_email
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step
from utils.tools.search.types import ArticleSearchPayload, SearchCategoryPayload, SearchWorkflowPayload
from utils.tools.search.workflow import (
    SearchTraceLogger,
    load_search_checklist_yaml,
    run_search_workflow,
    save_search_results,
)

from .settings import AppPaths, ensure_directories, resolve_paths
from .source_adapter import Kr36SourceAdapter

RUN_DIR_PREFIX = "kr36_search_"
RAW_JSON_PREFIX = "kr36_hot_topics"
RAW_CSV_NAME = "kr36_hot_topics.csv"
STEP_1_ANALYSIS_PREFIX = "kr36_step_1_analysis"
STEP_2_CHECKLIST_PREFIX = "kr36_step_2_search_checklist"
STEP_3_RESULTS_PREFIX = "kr36_step_3_search_results"
STEP_4_CONTENT_PREFIX = "kr36_step_4_content"
STEP_5_CONTENT_ANALYSIS_PREFIX = "kr36_step_5_content_analysis"
STEP_6_BRIEF_PREFIX = "kr36_step_6_brief"
LAYER_ISSUES_PREFIX = "kr36_layer_issues"
SEARCH_TRACE_PREFIX = "kr36_search_trace_step_3"
SYNTHETIC_STEP3_PROVIDER = "kr36_no_external_search"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TOPIC_GROUPING_PROMPT_PATH = PROMPTS_DIR / "topic-grouping-agent.md"
SEARCH_KEYWORD_PROMPT_PATH = PROMPTS_DIR / "search-keyword-agent.md"
CONTENT_ANALYSIS_PROMPT_PATH = PROMPTS_DIR / "content-analysis-agent.md"
BRIEF_PROMPT_PATH = PROMPTS_DIR / "brief-agent.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the 36Kr daily hot-topics skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hot_topics = subparsers.add_parser("kr36-hot-topics", help="Fetch daily hot topics from 36Kr.")
    hot_topics.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")

    run_parser = subparsers.add_parser("run", help="Run the full 36Kr pipeline from step 1 to step 6.")
    run_parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    run_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google"],
        help="Search provider when --external-search is enabled.",
    )
    run_parser.add_argument("--per-query-limit", type=int, default=5)
    run_parser.add_argument("--per-article-limit", type=int, default=None)
    run_parser.add_argument("--extract-limit", type=int, default=5)
    run_parser.add_argument(
        "--external-search",
        action="store_true",
        help="Enable step3 external search. By default 36Kr skips step3 external search.",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_with_args(args)


def build_kr36_synthetic_search_payload(checklist_path: Path, report_date: str) -> SearchWorkflowPayload:
    yaml_report_date, items = load_search_checklist_yaml(checklist_path)
    if yaml_report_date and yaml_report_date != report_date:
        raise ValueError(f"输入搜索清单日期为 {yaml_report_date}，与命令日期 {report_date} 不一致。")
    topic_order: list[str] = []
    by_topic: dict[str, list[ArticleSearchPayload]] = {}
    for row in items:
        if row.topic not in by_topic:
            topic_order.append(row.topic)
            by_topic[row.topic] = []
        by_topic[row.topic].append(
            ArticleSearchPayload(
                topic=row.topic,
                channel=row.channel,
                original_title=row.original_title,
                original_url=row.original_url,
                original_published_at=row.original_published_at,
                queries=[],
                search_results=[],
                selected_results=[],
            )
        )
    categories = [SearchCategoryPayload(topic=topic, items=by_topic[topic]) for topic in topic_order]
    return SearchWorkflowPayload(
        report_date=report_date,
        provider=SYNTHETIC_STEP3_PROVIDER,
        input_path=checklist_path,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )


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

    target_date = date.fromisoformat(args.date)
    runtime_config = load_c114_runtime_config()
    llm_client = require_llm_client()
    run_dir = create_run_directory(resolved_paths.reports_dir)
    bind_llm_trace_log(llm_client, run_dir=run_dir, target_date=target_date, reset_file=True)

    raw_payload = fetch_and_materialize_kr36_articles(target_date, resolved_paths, run_dir=run_dir)
    raw_csv_path = raw_payload["raw_csv_path"]
    report_date_text = target_date.isoformat()

    analysis_output = run_dir / step_1_analysis_name(target_date)
    checklist_output = run_dir / step_2_checklist_name(target_date)
    analysis_paths = AnalysisOutputPaths(
        input_path=raw_csv_path,
        analysis_output=analysis_output,
        checklist_output=checklist_output,
    )
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
    checklist_items = write_analysis_outputs(
        analysis_paths,
        report_date_text,
        analyses,
        llm_client=llm_client,
        checkpoint_prefix="kr36",
        keyword_count=runtime_config.search_keyword_count,
        keyword_prompt_path=SEARCH_KEYWORD_PROMPT_PATH,
    )
    print(f"36Kr step 1-2 完成 {target_date.isoformat()}")
    print(f"文章数: {len(analyses)}")
    print(f"Step 1 CSV: {analysis_output}")
    print(f"Step 2 YAML: {checklist_output}")
    if not checklist_items:
        print("当日无文章，流程停留在 step 1-2。")
        return

    step3_output = run_dir / step_3_results_name(target_date)
    external_search = bool(getattr(args, "external_search", False))
    if external_search:
        search_trace_logger = SearchTraceLogger(run_dir / LLM_TRACE_LOG_DIR_NAME / search_trace_log_name(target_date))
        step3_checkpoint = StepCheckpointStore.load_or_create(
            checkpoint_path=checkpoint_path_for_step(
                output_path=step3_output,
                step_name="step_3",
                report_date=report_date_text,
                prefix="kr36",
            ),
            step_name="step_3",
            report_date=report_date_text,
            input_path=checklist_output,
            output_path=step3_output,
        )
        search_payload = run_search_workflow(
            checklist_output,
            report_date_text,
            per_query_limit=int(getattr(args, "per_query_limit", 5) or 5),
            per_article_limit=int(
                (getattr(args, "per_article_limit", None) or runtime_config.search_max_external_results)
            ),
            extract_limit=int(getattr(args, "extract_limit", 5) or 5),
            provider_name=str(getattr(args, "provider", "auto") or "auto"),
            llm_client=llm_client,
            trace_logger=search_trace_logger,
            checkpoint_store=step3_checkpoint,
        )
        save_search_results(step3_output, search_payload)
        print(f"Step 3 YAML（联网搜索）: {step3_output}")
    else:
        synthetic_payload = build_kr36_synthetic_search_payload(checklist_output, report_date_text)
        save_search_results(step3_output, synthetic_payload)
        print(
            "Step 3 已跳过外部搜索：已根据第 2 步清单写入合成搜索结果 YAML（无 queries / 无补充链接），"
            f"provider={SYNTHETIC_STEP3_PROVIDER}"
        )
        print(f"Step 3 YAML: {step3_output}")

    step4_output = run_dir / step_4_content_name(target_date)
    step4_checkpoint = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step4_output,
            step_name="step_4",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_4",
        report_date=report_date_text,
        input_path=step3_output,
        output_path=step4_output,
    )
    content_payload = run_content_fetch_workflow(
        step3_output,
        report_date_text,
        checkpoint_store=step4_checkpoint,
    )
    save_content_results(step4_output, content_payload)
    print(f"Step 4 YAML: {step4_output}")

    step5_output = run_dir / step_5_content_analysis_name(target_date)
    step6_output = run_dir / step_6_brief_name(target_date)
    layer_issues_output = run_dir / layer_issues_name(target_date)
    content_analysis_paths = ContentAnalysisOutputPaths(
        input_path=step4_output,
        analysis_output=step5_output,
        brief_output=step6_output,
        issues_output=layer_issues_output,
    )
    content_input = load_content_analysis_inputs(step4_output)
    step5_checkpoint = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step5_output,
            step_name="step_5",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_5",
        report_date=report_date_text,
        input_path=step4_output,
        output_path=step5_output,
    )
    analysis_payload = auto_complete_content_analysis(
        content_input,
        llm_client,
        prompt_path=CONTENT_ANALYSIS_PROMPT_PATH,
        checkpoint_store=step5_checkpoint,
    )
    save_content_analysis_yaml(step5_output, analysis_payload)
    save_layer_issues_yaml(
        layer_issues_output,
        report_date_text,
        generate_layer_issues(analysis_payload, keyword_count=runtime_config.search_keyword_count),
    )
    print(f"Step 5 YAML: {step5_output}")

    step6_checkpoint = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step6_output,
            step_name="step_6",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_6",
        report_date=report_date_text,
        input_path=step5_output,
        output_path=step6_output,
    )
    brief_markdown = generate_brief_markdown(
        analysis_payload,
        llm_client,
        prompt_path=BRIEF_PROMPT_PATH,
        checkpoint_store=step6_checkpoint,
    )
    content_analysis_paths.brief_output.write_text(brief_markdown, encoding="utf-8")
    print(f"Step 6 MD: {step6_output}")
    html_output, text_output = save_brief_preview_assets(content_analysis_paths.brief_output, brief_markdown)
    print(f"Step 6 HTML: {html_output}")
    print(f"Step 6 TXT: {text_output}")


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


def step_1_analysis_name(report_date: date) -> str:
    return f"{STEP_1_ANALYSIS_PREFIX}_{report_date.strftime('%Y%m%d')}.csv"


def step_2_checklist_name(report_date: date) -> str:
    return f"{STEP_2_CHECKLIST_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step_3_results_name(report_date: date) -> str:
    return f"{STEP_3_RESULTS_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step_4_content_name(report_date: date) -> str:
    return f"{STEP_4_CONTENT_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step_5_content_analysis_name(report_date: date) -> str:
    return f"{STEP_5_CONTENT_ANALYSIS_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step_6_brief_name(report_date: date) -> str:
    return f"{STEP_6_BRIEF_PREFIX}_{report_date.strftime('%Y%m%d')}.md"


def layer_issues_name(report_date: date) -> str:
    return f"{LAYER_ISSUES_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def search_trace_log_name(report_date: date) -> str:
    return f"{SEARCH_TRACE_PREFIX}_{report_date.strftime('%Y%m%d')}.jsonl"


def save_brief_preview_assets(step6_markdown_path: Path, markdown_text: str) -> tuple[Path, Path]:
    rendered = render_kr36_brief_email(markdown_text, step6_markdown_path)
    html_output = step6_markdown_path.with_suffix(".html")
    legacy_email_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.html")
    text_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.txt")
    html_output.write_text(rendered.html, encoding="utf-8")
    legacy_email_output.write_text(rendered.html, encoding="utf-8")
    text_output.write_text(rendered.text, encoding="utf-8")
    return html_output, text_output

