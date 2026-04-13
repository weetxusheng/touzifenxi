"""step 1、1.5、2 命令。"""

from __future__ import annotations

import argparse
from typing import Any

from touzifenxi.settings import AppPaths


def handle_analyze_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 step 1、1.5、2。"""

    execution_mode = facade.resolve_requested_execution_mode(args)
    target_dates = facade.resolve_c114_date_range(args)
    range_day_dirs = facade.create_c114_range_day_directories(paths, target_dates) if len(target_dates) > 1 else {}
    llm_client = None
    if execution_mode == "builtin":
        try:
            llm_client = facade.require_llm_client()
        except RuntimeError:
            llm_client = None
    for target_date in target_dates:
        output_paths = facade.resolve_analysis_output_paths(
            paths=paths,
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
        trace_run_dir = facade.infer_trace_run_dir(output_paths.analysis_output, output_paths.checklist_output)
        facade.bind_llm_trace_log(
            llm_client,
            run_dir=trace_run_dir,
            target_date=target_date,
            reset_file=not (trace_run_dir / facade.LLM_TRACE_LOG_DIR_NAME).exists(),
        )
        analyses, briefs = facade.analyze_daily_articles(output_paths.input_path, target_date.isoformat())
        if execution_mode == "builtin" and analyses and all(isinstance(item, facade.ArticleAnalysis) for item in analyses):
            topic_grouping_checkpoint_store = facade.StepCheckpointStore.load_or_create(
                checkpoint_path=facade.checkpoint_path_for_step(
                    output_path=output_paths.analysis_output,
                    step_name="step_1_5",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_1_5",
                report_date=target_date.isoformat(),
                input_path=output_paths.input_path,
                output_path=output_paths.analysis_output,
            )
            analyses, briefs = facade.auto_group_analysis_topics(
                analyses,
                llm_client,
                report_date=target_date.isoformat(),
                source_site="c114",
                checkpoint_store=topic_grouping_checkpoint_store,
            )
        if execution_mode == "controller-agent":
            _handle_controller_analyze_command(
                facade=facade,
                target_date=target_date,
                output_paths=output_paths,
                analyses=analyses,
                briefs=briefs,
            )
            continue
        try:
            checklist_items = facade.write_analysis_outputs(
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
            continue
        print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")


def _handle_controller_analyze_command(
    *, facade: Any, target_date: object, output_paths: object, analyses: list[object], briefs: list[object]
) -> None:
    """controller-agent 模式下只生成 step 2 模板与 manifest。"""

    if output_paths.checklist_output.exists():
        if facade.step2_keywords_completed(output_paths.checklist_output):
            print(f"C114 分析完成 {target_date.isoformat()}")
            print(f"Step 2 已完成: {output_paths.checklist_output}\n")
            return
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=output_paths.checklist_output.parent,
            target_date=target_date,
            current_step="step_2",
            output_paths=facade.build_controller_output_paths(output_paths.checklist_output.parent, target_date),
            prompt_paths={"step_2": facade.SEARCH_KEYWORD_PROMPT_PATH},
            input_paths={"step_2": (output_paths.analysis_output,)},
            required_fields={"step_2": ("keywords[0]", "keywords[1]")},
            instruction=facade.StepInstruction(
                step_name="step_2",
                prompt_path=facade.SEARCH_KEYWORD_PROMPT_PATH,
                input_paths=(output_paths.analysis_output,),
                output_path=output_paths.checklist_output,
                required_fields=("keywords[0]", "keywords[1]"),
                notes=("step 2 文件已存在；请继续在现有文件上补写或检查。",),
            ),
            next_action="step 2 文件已存在，请继续在现有 YAML 上补全后再继续。",
        )
        print(f"C114 分析完成 {target_date.isoformat()}")
        print(f"Step 2 模板 YAML: {output_paths.checklist_output}")
        print(f"执行 manifest: {manifest_path}\n")
        return
    checklist_items = facade.write_analysis_outputs(
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
        return
    run_dir = output_paths.checklist_output.parent
    output_map = facade.build_controller_output_paths(run_dir, target_date)
    manifest_path = facade.write_controller_agent_manifest(
        day_dir=run_dir,
        target_date=target_date,
        current_step="step_2",
        output_paths=output_map,
        prompt_paths={"step_2": facade.SEARCH_KEYWORD_PROMPT_PATH},
        input_paths={"step_2": (output_paths.analysis_output,)},
        required_fields={"step_2": ("categories[*].items[*].keywords[0]", "categories[*].items[*].keywords[1]")},
        instruction=facade.StepInstruction(
            step_name="step_2",
            prompt_path=facade.SEARCH_KEYWORD_PROMPT_PATH,
            input_paths=(output_paths.analysis_output,),
            output_path=output_paths.checklist_output,
            required_fields=("categories[*].items[*].keywords[0]", "categories[*].items[*].keywords[1]"),
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
