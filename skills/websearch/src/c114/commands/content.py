"""step 4-7 内容链路命令。"""

from __future__ import annotations

import argparse
from typing import Any

from touzifenxi.settings import AppPaths


def handle_fetch_content_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 step 4 正文抓取。"""

    target_dates = facade.resolve_c114_date_range(args)
    range_day_dirs = facade.create_c114_range_day_directories(paths, target_dates) if len(target_dates) > 1 else {}
    for target_date in target_dates:
        input_override = args.input
        output_override = args.output
        if range_day_dirs and not input_override:
            input_override = str(
                facade.find_required_step_input(paths, target_date, facade.step_3_results_name(target_date), "搜索结果 YAML")
            )
        if range_day_dirs and not output_override:
            output_override = str((range_day_dirs[target_date] / facade.step_4_content_name(target_date)).resolve())
        output_paths = facade.resolve_content_output_paths(
            paths=paths,
            report_date=target_date,
            input_override=input_override,
            output_override=output_override,
        )
        checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
                output_path=output_paths.output_path,
                step_name="step_4",
                report_date=target_date.isoformat(),
            ),
            step_name="step_4",
            report_date=target_date.isoformat(),
            input_path=output_paths.input_path,
            output_path=output_paths.output_path,
        )
        payload = facade.run_content_fetch_workflow(
            input_path=output_paths.input_path,
            report_date=target_date.isoformat(),
            checkpoint_store=checkpoint_store,
        )
        facade.save_content_results(output_paths.output_path, payload)
        print(f"C114 正文抓取完成 {target_date.isoformat()}")
        print(f"正文内容 YAML: {output_paths.output_path}\n")


def handle_analyze_content_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 step 5 正文分析和 step 6 简报生成。"""

    execution_mode = facade.resolve_requested_execution_mode(args)
    target_dates = facade.resolve_c114_date_range(args)
    llm_client = facade.require_llm_client() if execution_mode == "builtin" else None
    range_day_dirs = facade.create_c114_range_day_directories(paths, target_dates) if len(target_dates) > 1 else {}
    for target_date in target_dates:
        input_override = args.input
        output_override = args.output
        issues_output = args.issues_output
        if range_day_dirs and not input_override:
            input_override = str(
                facade.find_required_step_input(paths, target_date, facade.step_4_content_name(target_date), "正文 YAML")
            )
        if range_day_dirs and not output_override:
            output_override = str((range_day_dirs[target_date] / facade.step_5_content_analysis_name(target_date)).resolve())
        if range_day_dirs and not issues_output:
            issues_output = str((range_day_dirs[target_date] / facade.layer_issues_name(target_date)).resolve())
        output_paths = facade.resolve_content_analysis_output_paths(
            paths=paths,
            report_date=target_date,
            input_override=input_override,
            output_override=output_override,
            issues_output_override=issues_output,
        )
        trace_run_dir = facade.infer_trace_run_dir(
            output_paths.input_path,
            output_paths.analysis_output,
            output_paths.issues_output,
            output_paths.brief_output,
        )
        facade.bind_llm_trace_log(
            llm_client,
            run_dir=trace_run_dir,
            target_date=target_date,
            reset_file=not (trace_run_dir / facade.LLM_TRACE_LOG_DIR_NAME).exists(),
        )
        if execution_mode == "controller-agent" and output_paths.analysis_output.exists():
            payload = facade.load_content_analysis_inputs(output_paths.analysis_output)
        else:
            payload = facade.load_content_analysis_inputs(output_paths.input_path)
        if execution_mode == "builtin":
            runtime_config = facade.load_c114_runtime_config()
            analysis_mode, analysis_retry_attempts = facade.resolve_content_analysis_runtime(runtime_config)
            step5_checkpoint_store = facade.StepCheckpointStore.load_or_create(
                checkpoint_path=facade.checkpoint_path_for_step(
                    output_path=output_paths.analysis_output,
                    step_name="step_5",
                    report_date=target_date.isoformat(),
                ),
                step_name="step_5",
                report_date=target_date.isoformat(),
                input_path=output_paths.input_path,
                output_path=output_paths.analysis_output,
            )
            payload = facade.auto_complete_content_analysis(
                payload,
                llm_client,
                mode=analysis_mode,
                batch_retry_attempts=analysis_retry_attempts,
                checkpoint_store=step5_checkpoint_store,
            )
        facade.save_content_analysis_yaml(output_paths.analysis_output, payload)
        run_dir = output_paths.input_path.parent
        issues = facade.generate_layer_issues(
            report_date=target_date.isoformat(),
            raw_csv_path=paths.raw_dir / "c114_hot_topics.csv",
            checklist_path=run_dir / facade.step_2_checklist_name(target_date),
            search_results_path=run_dir / facade.step_3_results_name(target_date),
            content_path=output_paths.input_path,
        )
        facade.save_layer_issues_yaml(output_paths.issues_output, target_date.isoformat(), issues)
        missing_analysis = facade.collect_missing_analysis_fields(payload)
        if execution_mode == "controller-agent":
            _handle_controller_analyze_content_command(
                facade=facade,
                target_date=target_date,
                output_paths=output_paths,
                run_dir=run_dir,
                payload=payload,
                missing_analysis=missing_analysis,
            )
            continue
        print(f"C114 正文分析模板生成完成 {target_date.isoformat()}")
        print(f"正文分析 YAML: {output_paths.analysis_output}")
        print(f"层问题 YAML: {output_paths.issues_output}")
        if missing_analysis:
            print("step 5 模型补全后仍不完整，step 6 不继续生成。")
            print(f"当前使用的内置提示词: {facade.CONTENT_ANALYSIS_PROMPT_PATH}")
            print("请检查 step 4 正文质量或 LLM 输出，并在修复后重新运行 c114-analyze-content：")
            for issue in missing_analysis[:10]:
                fields = "、".join(issue["missing_fields"])
                print(f"- [{issue['topic']}] {issue['original_title']}: {fields}")
            if len(missing_analysis) > 10:
                print(f"- 其余 {len(missing_analysis) - 10} 篇请查看 step 5 YAML")
            print("")
            continue
        output_paths.brief_output.parent.mkdir(parents=True, exist_ok=True)
        step6_checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
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
            facade.generate_brief_markdown(payload, llm_client, checkpoint_store=step6_checkpoint_store),
            encoding="utf-8",
        )
        print(f"主题简报 MD: {output_paths.brief_output}\n")


def _handle_controller_analyze_content_command(
    *,
    facade: Any,
    target_date: object,
    output_paths: object,
    run_dir: object,
    payload: object,
    missing_analysis: list[object],
) -> None:
    """controller-agent 模式下输出 step 5/6 的模板与 manifest。"""

    output_map = facade.build_controller_output_paths(run_dir, target_date)
    if missing_analysis:
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=run_dir,
            target_date=target_date,
            current_step="step_5",
            output_paths=output_map,
            prompt_paths={"step_5": facade.CONTENT_ANALYSIS_PROMPT_PATH},
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
            instruction=facade.StepInstruction(
                step_name="step_5",
                prompt_path=facade.CONTENT_ANALYSIS_PROMPT_PATH,
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
        return
    if output_paths.brief_output.exists():
        try:
            facade.validate_brief_markdown_for_agent(output_paths.brief_output, output_paths.analysis_output)
            print(f"C114 主题简报已完成 {target_date.isoformat()}")
            print(f"主题简报 MD: {output_paths.brief_output}\n")
            return
        except Exception:
            pass
    if not output_paths.brief_output.exists():
        output_paths.brief_output.parent.mkdir(parents=True, exist_ok=True)
        output_paths.brief_output.write_text(facade.render_brief_markdown(payload), encoding="utf-8")
    manifest_path = facade.write_controller_agent_manifest(
        day_dir=run_dir,
        target_date=target_date,
        current_step="step_6",
        output_paths=output_map,
        prompt_paths={"step_6": facade.BRIEF_PROMPT_PATH},
        input_paths={"step_6": (output_paths.analysis_output, output_paths.issues_output)},
        required_fields={"step_6": ("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点", "源地址", "补充地址")},
        instruction=facade.StepInstruction(
            step_name="step_6",
            prompt_path=facade.BRIEF_PROMPT_PATH,
            input_paths=(output_paths.analysis_output, output_paths.issues_output),
            output_path=output_paths.brief_output,
            required_fields=("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点", "源地址", "补充地址"),
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


def handle_review_brief_command(args: argparse.Namespace, *, paths: AppPaths, facade: Any) -> None:
    """执行 step 7 简报审查。"""

    execution_mode = facade.resolve_requested_execution_mode(args)
    target_dates = facade.resolve_c114_date_range(args)
    llm_client = facade.require_llm_client() if execution_mode == "builtin" else None
    range_day_dirs = facade.create_c114_range_day_directories(paths, target_dates) if len(target_dates) > 1 else {}
    for target_date in target_dates:
        brief_input = args.input
        analysis_input = args.analysis_input
        content_input = args.content_input
        output_override = args.output
        if range_day_dirs and not brief_input:
            brief_input = str(
                facade.find_required_step_input(paths, target_date, facade.step_6_brief_name(target_date), "主题简报 MD")
            )
        if range_day_dirs and not analysis_input:
            analysis_input = str(
                facade.find_required_step_input(paths, target_date, facade.step_5_content_analysis_name(target_date), "正文分析 YAML")
            )
        if range_day_dirs and not content_input:
            content_input = str(
                facade.find_required_step_input(paths, target_date, facade.step_4_content_name(target_date), "正文内容 YAML")
            )
        if range_day_dirs and not output_override:
            output_override = str((range_day_dirs[target_date] / facade.step_7_brief_review_name(target_date)).resolve())
        output_paths = facade.resolve_brief_review_output_paths(
            paths=paths,
            report_date=target_date,
            brief_input_override=brief_input,
            analysis_input_override=analysis_input,
            content_input_override=content_input,
            output_override=output_override,
        )
        trace_run_dir = facade.infer_trace_run_dir(
            output_paths.brief_input_path,
            output_paths.analysis_input_path,
            output_paths.content_input_path,
            output_paths.review_output_path,
        )
        facade.bind_llm_trace_log(
            llm_client,
            run_dir=trace_run_dir,
            target_date=target_date,
            reset_file=not (trace_run_dir / facade.LLM_TRACE_LOG_DIR_NAME).exists(),
        )
        if execution_mode == "controller-agent":
            if output_paths.review_output_path.exists():
                try:
                    facade.validate_brief_review_yaml_for_agent(output_paths.review_output_path)
                    print(f"C114 简报审查已完成 {target_date.isoformat()}")
                    print(f"审查报告 YAML: {output_paths.review_output_path}\n")
                    continue
                except Exception:
                    pass
            else:
                output_paths.review_output_path.parent.mkdir(parents=True, exist_ok=True)
                output_paths.review_output_path.write_text(
                    facade.render_brief_review_template_yaml(
                        target_date.isoformat(),
                        output_paths.brief_input_path,
                        output_paths.analysis_input_path,
                    ),
                    encoding="utf-8",
                )
            run_dir = output_paths.review_output_path.parent
            output_map = facade.build_controller_output_paths(run_dir, target_date)
            manifest_path = facade.write_controller_agent_manifest(
                day_dir=run_dir,
                target_date=target_date,
                current_step="step_7",
                output_paths=output_map,
                prompt_paths={"step_7": facade.BRIEF_REVIEW_PROMPT_PATH},
                input_paths={"step_7": (output_paths.brief_input_path, output_paths.analysis_input_path, output_paths.content_input_path)},
                required_fields={"step_7": ("overall_decision", "summary", "findings", "strengths")},
                instruction=facade.StepInstruction(
                    step_name="step_7",
                    prompt_path=facade.BRIEF_REVIEW_PROMPT_PATH,
                    input_paths=(output_paths.brief_input_path, output_paths.analysis_input_path, output_paths.content_input_path),
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
        checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
                output_path=output_paths.review_output_path,
                step_name="step_7",
                report_date=target_date.isoformat(),
            ),
            step_name="step_7",
            report_date=target_date.isoformat(),
            input_path=output_paths.brief_input_path,
            output_path=output_paths.review_output_path,
        )
        report = facade.build_brief_review_report_with_llm(
            report_date=target_date.isoformat(),
            brief_path=output_paths.brief_input_path,
            analysis_path=output_paths.analysis_input_path,
            content_path=output_paths.content_input_path,
            llm_client=llm_client,
            checkpoint_store=checkpoint_store,
        )
        facade.save_brief_review_yaml(output_paths.review_output_path, report)
        print(f"C114 简报审查完成 {target_date.isoformat()}")
        print(f"总评: {report.overall_decision}")
        print(f"问题数: {len(report.findings)}")
        print(f"审查报告 YAML: {output_paths.review_output_path}\n")
