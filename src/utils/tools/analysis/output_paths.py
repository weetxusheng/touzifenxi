"""Step 1/2 输出路径与落盘编排。

本模块负责 step 1 CSV 与 step 2 YAML 的最终落盘，以及相关 checkpoint 接线。
它依赖已有的 step2 关键词函数，但不承载关键词生成和文本规则本身。
"""

from __future__ import annotations

from datetime import date, datetime

from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step
from c114.runtime.settings import AppPaths, resolve_override_path
from .csv_io import save_article_analysis_csv
from .models import AnalysisOutputPaths, ArticleAnalysis, SearchChecklistItem


def resolve_analysis_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    analysis_output_override: str | None = None,
    checklist_output_override: str | None = None,
    run_started_at: datetime | None = None,
) -> AnalysisOutputPaths:
    """Resolve canonical input and output paths for the C114 analysis workflow."""

    from ..facades.intelligence import create_search_run_directory, step_1_analysis_name, step_2_checklist_name

    input_path = (
        resolve_override_path(paths.project_root, input_override)
        if input_override
        else (paths.raw_dir / "c114_hot_topics.csv").resolve()
    )
    run_dir = None
    if not analysis_output_override and not checklist_output_override:
        run_dir = create_search_run_directory(paths.reports_dir, run_started_at=run_started_at)
    analysis_output = (
        resolve_override_path(paths.project_root, analysis_output_override)
        if analysis_output_override
        else ((run_dir or paths.processed_dir) / step_1_analysis_name(report_date)).resolve()
    )
    checklist_output = (
        resolve_override_path(paths.project_root, checklist_output_override)
        if checklist_output_override
        else ((run_dir or paths.reports_dir) / step_2_checklist_name(report_date)).resolve()
    )
    return AnalysisOutputPaths(
        input_path=input_path,
        analysis_output=analysis_output,
        checklist_output=checklist_output,
    )


def write_analysis_outputs(
    output_paths: AnalysisOutputPaths,
    report_date: str,
    analyses: list[ArticleAnalysis],
    llm_client: object | None = None,
    *,
    auto_fill_keywords: bool = True,
    checkpoint_prefix: str = "c114",
    keyword_count: int = 1,
) -> list[SearchChecklistItem]:
    """Persist the analysis CSV and, when available, the step 2 checklist YAML."""

    from ..steps.step2_keywords import (
        apply_step2_checkpoint_results,
        autofill_search_checklist_items,
        build_search_checklist_items,
        build_step2_checkpoint_entry_id,
        build_step2_checkpoint_request_context,
        render_search_checklist_yaml,
    )

    save_article_analysis_csv(output_paths.analysis_output, analyses)
    if not analyses:
        if output_paths.checklist_output.exists():
            output_paths.checklist_output.unlink()
        return []

    checklist_items = build_search_checklist_items(analyses)
    checkpoint_store = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=output_paths.checklist_output,
            step_name="step_2",
            report_date=report_date,
            prefix=checkpoint_prefix,
        ),
        step_name="step_2",
        report_date=report_date,
        input_path=output_paths.analysis_output,
        output_path=output_paths.checklist_output,
    )
    if auto_fill_keywords:
        if llm_client is None:
            raise RuntimeError("未配置 llm.api_key，无法自动生成 step 2 搜索关键词。")
        checklist_items = autofill_search_checklist_items(
            checklist_items,
            analyses,
            llm_client,
            keyword_count=keyword_count,
            checkpoint_store=checkpoint_store,
        )
    else:
        for item in checklist_items:
            if checkpoint_store.get_entry(build_step2_checkpoint_entry_id(item)) is None:
                checkpoint_store.record_entry(
                    entry_id=build_step2_checkpoint_entry_id(item),
                    status="pending",
                    request_context=build_step2_checkpoint_request_context(item),
                    result={"keywords": list(item.search_queries)},
                )
        checklist_items = apply_step2_checkpoint_results(
            checklist_items,
            checkpoint_store,
            keyword_count=keyword_count,
        )
    output_paths.checklist_output.parent.mkdir(parents=True, exist_ok=True)
    output_paths.checklist_output.write_text(
        render_search_checklist_yaml(report_date, checklist_items, keyword_count=keyword_count),
        encoding="utf-8",
    )
    return checklist_items
