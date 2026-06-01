"""step 3 搜索命令。"""

from __future__ import annotations

import argparse
from typing import Any

from utils.tools.settings import AppPaths


def handle_search_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 step 3 搜索与 ai_review 编排。"""

    execution_mode = facade.resolve_requested_execution_mode(args)
    target_dates = facade.resolve_c114_date_range(args)
    runtime_config = facade.load_c114_runtime_config()
    llm_client = facade.require_llm_client() if execution_mode == "builtin" else None
    range_day_dirs = facade.create_c114_range_day_directories(paths, target_dates) if len(target_dates) > 1 else {}
    for target_date in target_dates:
        input_override = args.input
        output_override = args.output
        if range_day_dirs and not input_override:
            input_override = str(
                facade.find_required_step_input(paths, target_date, facade.step_2_checklist_name(target_date), "搜索清单 YAML")
            )
        if range_day_dirs and not output_override:
            output_override = str((range_day_dirs[target_date] / facade.step_3_results_name(target_date)).resolve())
        output_paths = facade.resolve_search_output_paths(
            paths=paths,
            report_date=target_date,
            input_override=input_override,
            output_override=output_override,
            provider=args.provider,
        )
        checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
                output_path=output_paths.output_path,
                step_name="step_3",
                report_date=target_date.isoformat(),
            ),
            step_name="step_3",
            report_date=target_date.isoformat(),
            input_path=output_paths.input_path,
            output_path=output_paths.output_path,
        )
        trace_run_dir = facade.infer_trace_run_dir(output_paths.input_path, output_paths.output_path)
        facade.bind_llm_trace_log(
            llm_client,
            run_dir=trace_run_dir,
            target_date=target_date,
            reset_file=not (trace_run_dir / facade.LLM_TRACE_LOG_DIR_NAME).exists(),
        )
        if execution_mode == "controller-agent":
            if output_paths.output_path.exists():
                if facade.step3_review_completed(output_paths.output_path):
                    print(f"C114 搜索完成 {target_date.isoformat()}")
                    print(f"Step 3 已完成: {output_paths.output_path}\n")
                    continue
                manifest_path = facade.write_controller_agent_manifest(
                    day_dir=output_paths.output_path.parent,
                    target_date=target_date,
                    current_step="step_3",
                    output_paths=facade.build_controller_output_paths(output_paths.output_path.parent, target_date),
                    prompt_paths={"step_3": facade.SEARCH_REVIEW_PROMPT_PATH},
                    input_paths={"step_3": (output_paths.input_path, output_paths.output_path)},
                    required_fields={"step_3": ("keep_level", "reason", "relevance_note", "value_type")},
                    instruction=facade.StepInstruction(
                        step_name="step_3",
                        prompt_path=facade.SEARCH_REVIEW_PROMPT_PATH,
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
            _report_date, checklist_items = facade.load_search_checklist_yaml(output_paths.input_path)
            facade.validate_search_checklist_items(
                checklist_items,
                keyword_count=runtime_config.search_keyword_count,
            )
        payload = facade.run_search_workflow(
            input_path=output_paths.input_path,
            report_date=target_date.isoformat(),
            per_query_limit=int(args.per_query_limit),
            per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
            extract_limit=int(args.extract_limit),
            provider_name=output_paths.provider,
            llm_client=llm_client,
            trace_logger=facade.build_search_trace_logger(
                trace_run_dir,
                target_date,
                reset_file=not (
                    trace_run_dir / facade.LLM_TRACE_LOG_DIR_NAME / facade.search_trace_log_name_for_step("step_3", target_date)
                ).exists(),
            ),
            checkpoint_store=checkpoint_store,
        )
        facade.save_search_results(output_paths.output_path, payload)
        if execution_mode == "controller-agent":
            run_dir = output_paths.output_path.parent
            output_map = facade.build_controller_output_paths(run_dir, target_date)
            manifest_path = facade.write_controller_agent_manifest(
                day_dir=run_dir,
                target_date=target_date,
                current_step="step_3",
                output_paths=output_map,
                prompt_paths={"step_3": facade.SEARCH_REVIEW_PROMPT_PATH},
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
                instruction=facade.StepInstruction(
                    step_name="step_3",
                    prompt_path=facade.SEARCH_REVIEW_PROMPT_PATH,
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
            print(f"Provider 用量统计: {output_paths.output_path.parent / facade.provider_stats_name(target_date)}")
            print(f"执行 manifest: {manifest_path}\n")
            continue
        print(f"C114 搜索完成 {target_date.isoformat()}")
        print(f"\n输入清单 YAML: {output_paths.input_path}")
        print(f"搜索结果 YAML: {output_paths.output_path}")
        print(f"Provider 用量统计: {output_paths.output_path.parent / facade.provider_stats_name(target_date)}\n")
