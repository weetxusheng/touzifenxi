from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from utils.tools.facades.content import (
    ArticleContentPayload,
    ContentCategoryPayload,
    ContentWorkflowPayload,
    FetchResult,
    run_content_fetch_workflow,
    save_content_results,
)
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
    write_analysis_outputs,
)
from utils.tools.llm import StructuredChatClient
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step
from c114.runtime.config import load_c114_runtime_config
from utils.tools.search.workflow import SearchTraceLogger, run_search_workflow, save_search_results
from utils.tools.output.briefing import write_step1_csv
from utils.tools.output.chip_email import render_chip_brief_email
from chip.source_adapter import ChipSourceAdapter
from chip.topic_grouping import group_analyses_by_chip_themes
from utils.tools.content_models import StandardArticle

from chip.settings import AppPaths, ensure_directories, resolve_paths

RUN_DIR_PREFIX = "chip_search_"
RAW_JSON_PREFIX = "chip_hot_topics"
RAW_CSV_NAME = "chip_hot_topics.csv"
STEP_1_ANALYSIS_PREFIX = "chip_step_1_analysis"
STEP_2_CHECKLIST_PREFIX = "chip_step_2_search_checklist"
STEP_3_RESULTS_PREFIX = "chip_step_3_search_results"
STEP_4_CONTENT_PREFIX = "chip_step_4_content"
STEP_5_CONTENT_ANALYSIS_PREFIX = "chip_step_5_content_analysis"
STEP_6_BRIEF_PREFIX = "chip_step_6_brief"
LAYER_ISSUES_PREFIX = "chip_layer_issues"
SEARCH_TRACE_PREFIX = "chip_search_trace_step_3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Chip daily hot-topics skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hot_topics = subparsers.add_parser("chip-hot-topics", help="Fetch daily hot topics from Chip.")
    hot_topics.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")

    run_parser = subparsers.add_parser("run", help="Run the full Chip pipeline from step 1 to step 6.")
    run_parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_with_args(args)


def run_with_args(args: argparse.Namespace, *, paths: AppPaths | None = None) -> None:
    resolved_paths = paths or resolve_paths()
    ensure_directories(resolved_paths)
    target_date = date.fromisoformat(args.date)

    if args.command == "chip-hot-topics":
        payload = fetch_and_materialize_chip_articles(target_date, resolved_paths)
        print(f"Chip 当日文章抓取完成 {target_date.isoformat()}")
        print(f"文章数: {len(payload['articles'])}")
        print(f"原始 JSON: {payload['raw_json_path']}")
        print(f"原始 CSV: {payload['raw_csv_path']}")
        return

    if args.command == "run":
        runtime_config = load_c114_runtime_config()
        llm_client = require_llm_client()
        run_dir = create_run_directory(resolved_paths.reports_dir)
        bind_llm_trace_log(llm_client, run_dir=run_dir, target_date=target_date, reset_file=True)

        raw_payload = fetch_and_materialize_chip_articles(target_date, resolved_paths, run_dir=run_dir)
        raw_csv_path = raw_payload["raw_csv_path"]
        report_date_text = target_date.isoformat()

        analysis_output = run_dir / step_1_analysis_name(target_date)
        checklist_output = run_dir / step_2_checklist_name(target_date)
        analysis_paths = AnalysisOutputPaths(
            input_path=raw_csv_path,
            analysis_output=analysis_output,
            checklist_output=checklist_output,
        )
        analyses, briefs = analyze_daily_articles(raw_csv_path, report_date_text)
        if analyses and all(isinstance(item, ArticleAnalysis) for item in analyses):
            # chip 走 deterministic CHIP_THEMES 桶分组，跳过 LLM 主题聚类
            analyses, briefs = group_analyses_by_chip_themes(analyses)
        checklist_items = write_analysis_outputs(
            analysis_paths,
            report_date_text,
            analyses,
            llm_client=llm_client,
            checkpoint_prefix="chip",
            keyword_count=runtime_config.search_keyword_count,
        )
        print(f"Chip step 1-2 完成 {target_date.isoformat()}")
        print(f"文章数: {len(analyses)}")
        print(f"主题数: {len(briefs)}")
        print(f"Step 1 CSV: {analysis_output}")
        print(f"Step 2 YAML: {checklist_output}")
        if not checklist_items:
            print("当日无文章，流程停留在 step 1-2。")
            return

        step3_output = run_dir / step_3_results_name(target_date)
        search_trace_logger = SearchTraceLogger(run_dir / LLM_TRACE_LOG_DIR_NAME / search_trace_log_name(target_date))
        step3_checkpoint = StepCheckpointStore.load_or_create(
            checkpoint_path=checkpoint_path_for_step(
                output_path=step3_output,
                step_name="step_3",
                report_date=report_date_text,
                prefix="chip",
            ),
            step_name="step_3",
            report_date=report_date_text,
            input_path=checklist_output,
            output_path=step3_output,
        )
        search_payload = run_search_workflow(
            checklist_output,
            report_date_text,
            per_query_limit=5,
            per_article_limit=5,
            extract_limit=5,
            provider_name="auto",
            llm_client=llm_client,
            trace_logger=search_trace_logger,
            checkpoint_store=step3_checkpoint,
        )
        save_search_results(step3_output, search_payload)
        print(f"Step 3 YAML: {step3_output}")

        step4_output = run_dir / step_4_content_name(target_date)
        step4_checkpoint = StepCheckpointStore.load_or_create(
            checkpoint_path=checkpoint_path_for_step(
                output_path=step4_output,
                step_name="step_4",
                report_date=report_date_text,
                prefix="chip",
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
            source_label="半导体",
        )
        content_payload = rehydrate_original_contents(content_payload, raw_payload["articles"])
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
                prefix="chip",
            ),
            step_name="step_5",
            report_date=report_date_text,
            input_path=step4_output,
            output_path=step5_output,
        )
        analysis_payload = auto_complete_content_analysis(
            content_input,
            llm_client,
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
                prefix="chip",
            ),
            step_name="step_6",
            report_date=report_date_text,
            input_path=step5_output,
            output_path=step6_output,
        )
        # chip step6 采用「文章卡片」格式：按 chip_themes 桶（step5 已分好）逐篇列出，
        # 每篇 = ### 【来源】标题 + 概要 + 核心要点 + 涉及实体 + 原文链接。
        # 直接复用 step5 逐篇分析（不再 LLM 综合成「桶四要素」），保留单篇可读性。
        _ = step6_checkpoint  # 卡片格式不调 LLM，checkpoint 仅占位保持目录结构一致
        brief_markdown = _build_chip_article_cards_markdown(step5_output, report_date_text)

        content_analysis_paths.brief_output.write_text(brief_markdown, encoding="utf-8")
        print(f"Step 6 MD: {step6_output}")

        # 同时落 .html 邮件正文版。文章卡片格式用 chip_email 渲染（带来源标签 + 卡片样式）。
        html_output = step6_output.with_suffix(".html")
        chip_footer = (
            "本简报由 chip（SEMI 中国 + 爱集微）产业新闻流水线生成，仅供内部研究参考。"
        )
        html_text = render_chip_brief_email(brief_markdown, footer_disclaimer=chip_footer).html
        html_output.write_text(html_text, encoding="utf-8")
        print(f"Step 6 HTML: {html_output}")
        return

    raise ValueError(f"Unsupported command: {args.command}")


_CHIP_CHANNEL_LABEL = {"semi": "SEMI", "laoyaoba": "爱集微"}


def _build_chip_article_cards_markdown(step5_yaml_path: Path, report_date_text: str) -> str:
    """从 step5 逐篇分析生成「文章卡片」markdown，供 chip_email 渲染。

    格式：## 桶（chip_themes）/ ### 【来源】标题 / **概要** / **核心要点**（列表）/
    **涉及实体** / [原文链接]。step5 的 categories 已按 chip_themes 桶分组。
    """
    import yaml

    from chip.topic_grouping import NON_SEMI_TOPIC, OTHER_TOPIC
    from utils.tools.content_models import StandardArticle
    from utils.tools.research.chip_themes import CHIP_THEMES, classify_chip_themes_with_filter

    def _reclassify(item: dict) -> str:
        # 不信任 step5 的 topic（早期数据可能整桶错标），用当前 chip_themes 规则按
        # 标题 + 概要重新分类，保证新老数据分桶口径一致。
        analysis = item.get("analysis") or {}
        sa = StandardArticle(
            source_site="chip",
            source_bucket="",
            channel=item.get("channel", "") or "",
            article_id="",
            title=item.get("original_title", "") or "",
            url=item.get("original_url", "") or "",
            published_at="",
            author="",
            tags=[],
            keywords=[],
            summary=analysis.get("summary", "") or "",
            content_text="",
            metadata={},
        )
        themes, is_semi = classify_chip_themes_with_filter(sa)
        if not is_semi:
            return NON_SEMI_TOPIC
        return themes[0] if themes else OTHER_TOPIC

    data = yaml.safe_load(step5_yaml_path.read_text(encoding="utf-8")) or {}
    by_topic: dict[str, list] = {}
    for cat in data.get("categories", []) or []:
        for item in cat.get("items", []) or []:
            by_topic.setdefault(_reclassify(item), []).append(item)

    # 固定输出全部分类桶（含 0 篇），保证主题导航完整：
    # 新品发布 / 价格变动 / 投融资·并购 / 产业动态 / 其他动态 / 非半导体内容
    full_order = list(CHIP_THEMES.keys()) + [OTHER_TOPIC, NON_SEMI_TOPIC]
    out: list[str] = [f"# 半导体简报（{report_date_text}）", ""]
    for topic in full_order:
        items = by_topic.get(topic, [])
        out.append(f"## {topic}")
        out.append("")
        for item in items:
            channel = item.get("channel", "") or ""
            label = _CHIP_CHANNEL_LABEL.get(channel, channel or "来源")
            title = (item.get("original_title") or "").strip()
            out.append(f"### 【{label}】{title}")
            out.append("")
            analysis = item.get("analysis") or {}
            summary = (analysis.get("summary") or "").strip()
            if summary:
                out.append(f"**概要**：{summary}")
                out.append("")
            core_points = [str(p).strip() for p in (analysis.get("core_points") or []) if str(p).strip()]
            if core_points:
                out.append("**核心要点**：")
                out.extend(f"- {p}" for p in core_points)
                out.append("")
            entities = [str(e).strip() for e in (analysis.get("entities") or []) if str(e).strip()]
            if entities:
                out.append(f"**涉及实体**：{'、'.join(entities)}")
                out.append("")
            url = (item.get("original_url") or "").strip()
            if url:
                out.append(f"[原文链接]({url})")
                out.append("")
            out.append("---")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def rehydrate_original_contents(
    payload: ContentWorkflowPayload,
    articles: list[StandardArticle],
) -> ContentWorkflowPayload:
    """用站点适配器已抓到的正文回填 original_content，避免再次退化成占位 HTML。"""

    by_url = {article.url: article for article in articles if article.url}
    categories: list[ContentCategoryPayload] = []
    for category in payload.categories:
        items: list[ArticleContentPayload] = []
        for item in category.items:
            article = by_url.get(item.original_url)
            if article is None or not article.content_text.strip():
                items.append(item)
                continue
            if item.original_content.content_source != "html_fallback":
                items.append(item)
                continue
            items.append(
                ArticleContentPayload(
                    original_title=item.original_title,
                    topic=item.topic,
                    channel=item.channel,
                    original_url=item.original_url,
                    original_published_at=item.original_published_at,
                    original_content=FetchResult(
                        url=item.original_url,
                        domain=urlsplit(item.original_url).netloc,
                        content_title=article.title,
                        content_summary=article.summary,
                        content_text=article.content_text,
                        fetch_status="success",
                        fetch_error="",
                        content_source="source_adapter",
                    ),
                    selected_contents=item.selected_contents,
                )
            )
        categories.append(ContentCategoryPayload(topic=category.topic, items=items))
    return ContentWorkflowPayload(
        report_date=payload.report_date,
        input_path=payload.input_path,
        generated_at=payload.generated_at,
        categories=categories,
    )


def fetch_and_materialize_chip_articles(
    target_date: date,
    paths: AppPaths,
    *,
    run_dir: Path | None = None,
) -> dict[str, object]:
    """Step 0: fetch listing + per-article details via ChipSourceAdapter, persist raw JSON/CSV."""

    adapter = ChipSourceAdapter()
    refs = adapter.fetch_listing(target_date)
    standards: list[StandardArticle] = []
    for ref in refs:
        try:
            detail = adapter.fetch_article(ref)
        except Exception:
            continue
        standards.append(adapter.normalize_article(detail))

    raw_json_path = (run_dir or paths.raw_dir) / raw_json_name(target_date)
    raw_json_path.parent.mkdir(parents=True, exist_ok=True)
    payload_articles = [asdict(s) for s in standards]
    raw_json_path.write_text(
        json.dumps(
            {
                "report_date": target_date.isoformat(),
                "by_channel": {
                    "semi": [a for a in payload_articles if a["channel"] == "semi"],
                    "laoyaoba": [a for a in payload_articles if a["channel"] == "laoyaoba"],
                },
                "articles": payload_articles,
                "failed_channels": sorted(adapter.failed_channels),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    raw_csv_path = (run_dir or paths.raw_dir) / RAW_CSV_NAME
    write_step1_csv(raw_csv_path, target_date.isoformat(), standards)
    return {
        "articles": standards,
        "raw_json_path": raw_json_path,
        "raw_csv_path": raw_csv_path,
        "failed_channels": sorted(adapter.failed_channels),
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
        source_prefix="chip",
        reset_files=reset_file,
    )


def create_run_directory(reports_dir: Path, run_started_at: datetime | None = None) -> Path:
    timestamp = run_started_at or datetime.now()
    base_name = f"{RUN_DIR_PREFIX}{timestamp.strftime('%Y%m%d%H%M')}"
    base_dir = reports_dir / "chip_report"
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
