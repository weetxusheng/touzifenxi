"""`run` 主流程的具体执行实现。"""

from __future__ import annotations

import argparse
from typing import Any

from touzifenxi.settings import AppPaths


def run_controller_daily_pipeline(
    *,
    args: argparse.Namespace,
    paths: AppPaths,
    facade: Any,
    runtime_config: object,
    target_date: object,
    day_dir: object,
) -> None:
    """在 controller-agent 模式下逐步检查并停在当前待补齐的步骤。"""

    output_map = facade.build_controller_output_paths(day_dir, target_date)
    raw_output = facade.resolve_hot_topics_output_path(
        project_root=paths.project_root,
        raw_dir=paths.raw_dir,
        report_date=target_date,
        output_override=None,
    )
    if not output_map["step_1"].exists():
        report = facade.collect_daily_report(
            report_date=target_date,
            channel_keys=args.channels,
            timeout=float(args.timeout),
            candidate_limit=int(args.candidate_limit),
        )
        facade.save_daily_report(raw_output, target_date, report)
        analysis_paths = facade.resolve_analysis_output_paths(
            paths=paths,
            report_date=target_date,
            analysis_output_override=str(output_map["step_1"]),
            checklist_output_override=str(output_map["step_2"]),
        )
        analyses, briefs = facade.analyze_daily_articles(analysis_paths.input_path, target_date.isoformat())
        checklist_items = facade.write_analysis_outputs(
            analysis_paths,
            target_date.isoformat(),
            analyses,
            llm_client=None,
            auto_fill_keywords=False,
        )
        if not checklist_items:
            print(f"C114 全流程 step 1 完成 {target_date.isoformat()}")
            print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
            return
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=day_dir,
            target_date=target_date,
            current_step="step_2",
            output_paths=output_map,
            prompt_paths={"step_2": facade.SEARCH_KEYWORD_PROMPT_PATH},
            input_paths={"step_2": (output_map["step_1"],)},
            required_fields={"step_2": ("keywords[0]", "keywords[1]")},
            instruction=facade.StepInstruction(
                step_name="step_2",
                prompt_path=facade.SEARCH_KEYWORD_PROMPT_PATH,
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
        return
    if not facade.step2_keywords_completed(output_map["step_2"]):
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=day_dir,
            target_date=target_date,
            current_step="step_2",
            output_paths=output_map,
            prompt_paths={"step_2": facade.SEARCH_KEYWORD_PROMPT_PATH},
            input_paths={"step_2": (output_map["step_1"],)},
            required_fields={"step_2": ("keywords[0]", "keywords[1]")},
            instruction=facade.StepInstruction(
                step_name="step_2",
                prompt_path=facade.SEARCH_KEYWORD_PROMPT_PATH,
                input_paths=(output_map["step_1"],),
                output_path=output_map["step_2"],
                required_fields=("keywords[0]", "keywords[1]"),
                notes=("step 2 还未补完，请继续填写 step 2 YAML。",),
            ),
            next_action="step 2 尚未完成，请补全关键词后重新运行 run。",
        )
        print(f"Step 2 仍待控制 agent 完成：{output_map['step_2']}")
        print(f"执行 manifest: {manifest_path}\n")
        return
    if not output_map["step_3"].exists():
        search_paths = facade.resolve_search_output_paths(
            paths=paths,
            report_date=target_date,
            input_override=str(output_map["step_2"]),
            output_override=str(output_map["step_3"]),
            provider=args.provider,
        )
        search_checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
                output_path=search_paths.output_path,
                step_name="step_3",
                report_date=target_date.isoformat(),
            ),
            step_name="step_3",
            report_date=target_date.isoformat(),
            input_path=search_paths.input_path,
            output_path=search_paths.output_path,
        )
        search_payload = facade.run_search_workflow(
            input_path=search_paths.input_path,
            report_date=target_date.isoformat(),
            per_query_limit=int(args.per_query_limit),
            per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
            extract_limit=int(args.extract_limit),
            provider_name=search_paths.provider,
            llm_client=None,
            trace_logger=facade.build_search_trace_logger(
                day_dir,
                target_date,
                reset_file=not (
                    day_dir
                    / facade.LLM_TRACE_LOG_DIR_NAME
                    / facade.search_trace_log_name_for_step("step_3", target_date)
                ).exists(),
            ),
            checkpoint_store=search_checkpoint_store,
        )
        facade.save_search_results(search_paths.output_path, search_payload)
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=day_dir,
            target_date=target_date,
            current_step="step_3",
            output_paths=output_map,
            prompt_paths={"step_3": facade.SEARCH_REVIEW_PROMPT_PATH},
            input_paths={"step_3": (output_map["step_2"], output_map["step_3"])},
            required_fields={"step_3": ("keep_level", "reason", "relevance_note", "value_type")},
            instruction=facade.StepInstruction(
                step_name="step_3",
                prompt_path=facade.SEARCH_REVIEW_PROMPT_PATH,
                input_paths=(output_map["step_2"], output_map["step_3"]),
                output_path=output_map["step_3"],
                required_fields=(
                    "ai_review.status=reviewed",
                    "ai_review.keep_level",
                    "ai_review.reason",
                    "ai_review.relevance_note",
                    "ai_review.value_type",
                ),
                notes=("只补 selected_results 下的 ai_review。",),
            ),
            next_action="当前已进入 step 3，请完成外链精筛后重新运行 run。",
        )
        print(f"Step 3 YAML: {output_map['step_3']}")
        print(f"执行 manifest: {manifest_path}\n")
        return
    if not facade.step3_review_completed(output_map["step_3"]):
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=day_dir,
            target_date=target_date,
            current_step="step_3",
            output_paths=output_map,
            prompt_paths={"step_3": facade.SEARCH_REVIEW_PROMPT_PATH},
            input_paths={"step_3": (output_map["step_2"], output_map["step_3"])},
            required_fields={"step_3": ("keep_level", "reason", "relevance_note", "value_type")},
            instruction=facade.StepInstruction(
                step_name="step_3",
                prompt_path=facade.SEARCH_REVIEW_PROMPT_PATH,
                input_paths=(output_map["step_2"], output_map["step_3"]),
                output_path=output_map["step_3"],
                required_fields=(
                    "ai_review.status=reviewed",
                    "ai_review.keep_level",
                    "ai_review.reason",
                    "ai_review.relevance_note",
                    "ai_review.value_type",
                ),
                notes=("step 3 尚未审查完成。",),
            ),
            next_action="step 3 尚未完成，请补全 ai_review 后重新运行 run。",
        )
        print(f"Step 3 仍待控制 agent 完成：{output_map['step_3']}")
        print(f"执行 manifest: {manifest_path}\n")
        return
    if not output_map["step_4"].exists():
        content_paths = facade.resolve_content_output_paths(
            paths=paths,
            report_date=target_date,
            input_override=str(output_map["step_3"]),
            output_override=str(output_map["step_4"]),
        )
        content_payload = facade.run_content_fetch_workflow(
            input_path=content_paths.input_path,
            report_date=target_date.isoformat(),
        )
        facade.save_content_results(content_paths.output_path, content_payload)
    if not output_map["step_5"].exists():
        content_analysis_paths = facade.resolve_content_analysis_output_paths(
            paths=paths,
            report_date=target_date,
            input_override=str(output_map["step_4"]),
            output_override=str(output_map["step_5"]),
            issues_output_override=str(day_dir / facade.layer_issues_name(target_date)),
        )
        analysis_payload = facade.load_content_analysis_inputs(content_analysis_paths.input_path)
        facade.save_content_analysis_yaml(content_analysis_paths.analysis_output, analysis_payload)
        issues = facade.generate_layer_issues(
            report_date=target_date.isoformat(),
            raw_csv_path=paths.raw_dir / "c114_hot_topics.csv",
            checklist_path=day_dir / facade.step_2_checklist_name(target_date),
            search_results_path=day_dir / facade.step_3_results_name(target_date),
            content_path=content_analysis_paths.input_path,
        )
        facade.save_layer_issues_yaml(content_analysis_paths.issues_output, target_date.isoformat(), issues)
    if not facade.step5_analysis_completed(output_map["step_5"]):
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=day_dir,
            target_date=target_date,
            current_step="step_5",
            output_paths=output_map,
            prompt_paths={"step_5": facade.CONTENT_ANALYSIS_PROMPT_PATH},
            input_paths={"step_5": (output_map["step_4"],)},
            required_fields={
                "step_5": (
                    "summary",
                    "core_points",
                    "new_facts",
                    "entities",
                    "signals",
                    "risk_or_uncertainty",
                    "why_it_matters",
                    "layer_notes",
                )
            },
            instruction=facade.StepInstruction(
                step_name="step_5",
                prompt_path=facade.CONTENT_ANALYSIS_PROMPT_PATH,
                input_paths=(output_map["step_4"],),
                output_path=output_map["step_5"],
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
                notes=("请控制 agent 只补 analysis 字段。",),
            ),
            next_action="当前已进入 step 5，请补完正文分析后重新运行 run。",
        )
        print(f"Step 4 YAML: {output_map['step_4']}")
        print(f"Step 5 YAML: {output_map['step_5']}")
        print(f"执行 manifest: {manifest_path}\n")
        return
    if not output_map["step_6"].exists():
        analysis_payload = facade.load_content_analysis_inputs(output_map["step_5"])
        output_map["step_6"].parent.mkdir(parents=True, exist_ok=True)
        output_map["step_6"].write_text(facade.render_brief_markdown(analysis_payload), encoding="utf-8")
    if not facade.step6_brief_completed(output_map["step_6"], output_map["step_5"]):
        manifest_path = facade.write_controller_agent_manifest(
            day_dir=day_dir,
            target_date=target_date,
            current_step="step_6",
            output_paths=output_map,
            prompt_paths={"step_6": facade.BRIEF_PROMPT_PATH},
            input_paths={"step_6": (output_map["step_5"],)},
            required_fields={"step_6": ("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点", "源地址", "补充地址")},
            instruction=facade.StepInstruction(
                step_name="step_6",
                prompt_path=facade.BRIEF_PROMPT_PATH,
                input_paths=(output_map["step_5"],),
                output_path=output_map["step_6"],
                required_fields=("核心判断", "增量信息", "产业/公司影响", "需要继续跟踪的点", "源地址", "补充地址"),
                notes=("请控制 agent 正式撰写 step 6 简报。",),
            ),
            next_action="当前已进入 step 6，请补完正式简报后重新运行 run。",
        )
        print(f"Step 6 MD: {output_map['step_6']}")
        print(f"执行 manifest: {manifest_path}\n")
        return
    if runtime_config.review_enable_step7:
        if not output_map["step_7"].exists():
            output_map["step_7"].parent.mkdir(parents=True, exist_ok=True)
            output_map["step_7"].write_text(
                facade.render_brief_review_template_yaml(target_date.isoformat(), output_map["step_6"], output_map["step_5"]),
                encoding="utf-8",
            )
        if not facade.step7_review_completed(output_map["step_7"]):
            manifest_path = facade.write_controller_agent_manifest(
                day_dir=day_dir,
                target_date=target_date,
                current_step="step_7",
                output_paths=output_map,
                prompt_paths={"step_7": facade.BRIEF_REVIEW_PROMPT_PATH},
                input_paths={"step_7": (output_map["step_6"], output_map["step_5"], output_map["step_4"])},
                required_fields={"step_7": ("overall_decision", "summary", "findings", "strengths")},
                instruction=facade.StepInstruction(
                    step_name="step_7",
                    prompt_path=facade.BRIEF_REVIEW_PROMPT_PATH,
                    input_paths=(output_map["step_6"], output_map["step_5"], output_map["step_4"]),
                    output_path=output_map["step_7"],
                    required_fields=("overall_decision", "summary", "findings", "strengths"),
                    notes=("请控制 agent 完成最终审查 YAML。",),
                ),
                next_action="当前已进入 step 7，请补完审查 YAML 后重新运行 run。",
            )
            print(f"Step 7 YAML: {output_map['step_7']}")
            print(f"执行 manifest: {manifest_path}\n")
            return
    manifest_path = facade.write_controller_agent_manifest(
        day_dir=day_dir,
        target_date=target_date,
        current_step="completed",
        output_paths=output_map,
        prompt_paths={},
        input_paths={},
        required_fields={},
        instruction=facade.StepInstruction(
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


def run_builtin_daily_pipeline(
    *,
    args: argparse.Namespace,
    paths: AppPaths,
    facade: Any,
    runtime_config: object,
    llm_client: object,
    target_date: object,
    day_dir: object,
) -> None:
    """在 builtin 模式下完整跑通 step 1-7。"""

    report = facade.collect_daily_report(
        report_date=target_date,
        channel_keys=args.channels,
        timeout=float(args.timeout),
        candidate_limit=int(args.candidate_limit),
    )
    raw_output = facade.resolve_hot_topics_output_path(
        project_root=paths.project_root,
        raw_dir=paths.raw_dir,
        report_date=target_date,
        output_override=None,
    )
    facade.save_daily_report(raw_output, target_date, report)
    analysis_output = (day_dir / facade.step_1_analysis_name(target_date)).resolve()
    checklist_output = (day_dir / facade.step_2_checklist_name(target_date)).resolve()
    analysis_paths = facade.resolve_analysis_output_paths(
        paths=paths,
        report_date=target_date,
        analysis_output_override=str(analysis_output),
        checklist_output_override=str(checklist_output),
    )
    analyses, briefs = facade.analyze_daily_articles(analysis_paths.input_path, target_date.isoformat())
    if analyses and all(isinstance(item, facade.ArticleAnalysis) for item in analyses):
        topic_grouping_checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
                output_path=analysis_paths.analysis_output,
                step_name="step_1_5",
                report_date=target_date.isoformat(),
            ),
            step_name="step_1_5",
            report_date=target_date.isoformat(),
            input_path=analysis_paths.input_path,
            output_path=analysis_paths.analysis_output,
        )
        analyses, briefs = facade.auto_group_analysis_topics(
            analyses,
            llm_client,
            report_date=target_date.isoformat(),
            source_site="c114",
            checkpoint_store=topic_grouping_checkpoint_store,
        )
    checklist_items = facade.write_analysis_outputs(
        analysis_paths,
        target_date.isoformat(),
        analyses,
        llm_client=llm_client,
    )
    print(f"C114 全流程 step 1-2 完成 {target_date.isoformat()}")
    print(f"文章数: {len(analyses)}")
    print(f"主题数: {len(briefs)}")
    print(f"Step 1 CSV: {analysis_paths.analysis_output}")
    if not checklist_items:
        print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
        return
    print(f"Step 2 YAML: {analysis_paths.checklist_output}")
    search_output = (day_dir / facade.step_3_results_name(target_date)).resolve()
    search_paths = facade.resolve_search_output_paths(
        paths=paths,
        report_date=target_date,
        input_override=str(analysis_paths.checklist_output),
        output_override=str(search_output),
        provider=args.provider,
    )
    search_checkpoint_store = facade.StepCheckpointStore.load_or_create(
        checkpoint_path=facade.checkpoint_path_for_step(
            output_path=search_paths.output_path,
            step_name="step_3",
            report_date=target_date.isoformat(),
        ),
        step_name="step_3",
        report_date=target_date.isoformat(),
        input_path=search_paths.input_path,
        output_path=search_paths.output_path,
    )
    search_payload = facade.run_search_workflow(
        input_path=search_paths.input_path,
        report_date=target_date.isoformat(),
        per_query_limit=int(args.per_query_limit),
        per_article_limit=int(args.per_article_limit or runtime_config.search_max_external_results),
        extract_limit=int(args.extract_limit),
        provider_name=search_paths.provider,
        llm_client=llm_client,
        trace_logger=facade.build_search_trace_logger(
            day_dir,
            target_date,
            reset_file=not (
                day_dir / facade.LLM_TRACE_LOG_DIR_NAME / facade.search_trace_log_name_for_step("step_3", target_date)
            ).exists(),
        ),
        checkpoint_store=search_checkpoint_store,
    )
    facade.save_search_results(search_paths.output_path, search_payload)
    content_output = (day_dir / facade.step_4_content_name(target_date)).resolve()
    content_paths = facade.resolve_content_output_paths(
        paths=paths,
        report_date=target_date,
        input_override=str(search_paths.output_path),
        output_override=str(content_output),
    )
    content_checkpoint_store = facade.StepCheckpointStore.load_or_create(
        checkpoint_path=facade.checkpoint_path_for_step(
            output_path=content_paths.output_path,
            step_name="step_4",
            report_date=target_date.isoformat(),
        ),
        step_name="step_4",
        report_date=target_date.isoformat(),
        input_path=content_paths.input_path,
        output_path=content_paths.output_path,
    )
    content_payload = facade.run_content_fetch_workflow(
        input_path=content_paths.input_path,
        report_date=target_date.isoformat(),
        checkpoint_store=content_checkpoint_store,
    )
    facade.save_content_results(content_paths.output_path, content_payload)
    analysis_output = (day_dir / facade.step_5_content_analysis_name(target_date)).resolve()
    issues_output = (day_dir / facade.layer_issues_name(target_date)).resolve()
    content_analysis_paths = facade.resolve_content_analysis_output_paths(
        paths=paths,
        report_date=target_date,
        input_override=str(content_paths.output_path),
        output_override=str(analysis_output),
        issues_output_override=str(issues_output),
    )
    analysis_mode, analysis_retry_attempts = facade.resolve_content_analysis_runtime(runtime_config)
    step5_checkpoint_store = facade.StepCheckpointStore.load_or_create(
        checkpoint_path=facade.checkpoint_path_for_step(
            output_path=content_analysis_paths.analysis_output,
            step_name="step_5",
            report_date=target_date.isoformat(),
        ),
        step_name="step_5",
        report_date=target_date.isoformat(),
        input_path=content_analysis_paths.input_path,
        output_path=content_analysis_paths.analysis_output,
    )
    analysis_payload = facade.auto_complete_content_analysis(
        facade.load_content_analysis_inputs(content_analysis_paths.input_path),
        llm_client,
        mode=analysis_mode,
        batch_retry_attempts=analysis_retry_attempts,
        checkpoint_store=step5_checkpoint_store,
    )
    facade.save_content_analysis_yaml(content_analysis_paths.analysis_output, analysis_payload)
    issues = facade.generate_layer_issues(
        report_date=target_date.isoformat(),
        raw_csv_path=paths.raw_dir / "c114_hot_topics.csv",
        checklist_path=day_dir / facade.step_2_checklist_name(target_date),
        search_results_path=day_dir / facade.step_3_results_name(target_date),
        content_path=content_analysis_paths.input_path,
    )
    facade.save_layer_issues_yaml(content_analysis_paths.issues_output, target_date.isoformat(), issues)
    if facade.collect_missing_analysis_fields(analysis_payload):
        raise RuntimeError(f"{target_date.isoformat()} 的 step 5 模型补全后仍不完整，流程已中止。")
    brief_output = content_analysis_paths.brief_output
    brief_output.parent.mkdir(parents=True, exist_ok=True)
    step6_checkpoint_store = facade.StepCheckpointStore.load_or_create(
        checkpoint_path=facade.checkpoint_path_for_step(
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
        facade.generate_brief_markdown(
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
        review_paths = facade.resolve_brief_review_output_paths(
            paths=paths,
            report_date=target_date,
            brief_input_override=str(brief_output),
            analysis_input_override=str(content_analysis_paths.analysis_output),
            content_input_override=str(content_paths.output_path),
            output_override=str((day_dir / facade.step_7_brief_review_name(target_date)).resolve()),
        )
        step7_checkpoint_store = facade.StepCheckpointStore.load_or_create(
            checkpoint_path=facade.checkpoint_path_for_step(
                output_path=review_paths.review_output_path,
                step_name="step_7",
                report_date=target_date.isoformat(),
            ),
            step_name="step_7",
            report_date=target_date.isoformat(),
            input_path=review_paths.brief_input_path,
            output_path=review_paths.review_output_path,
        )
        review_report = facade.build_brief_review_report_with_llm(
            report_date=target_date.isoformat(),
            brief_path=review_paths.brief_input_path,
            analysis_path=review_paths.analysis_input_path,
            content_path=review_paths.content_input_path,
            llm_client=llm_client,
            checkpoint_store=step7_checkpoint_store,
        )
        facade.save_brief_review_yaml(review_paths.review_output_path, review_report)
        print(f"Step 7 YAML: {review_paths.review_output_path}\n")
        return
    print("Step 7 已在配置中关闭，流程停留在 step 6。\n")
