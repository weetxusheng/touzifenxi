"""`run` 主流程的具体执行实现。"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from utils.tools.settings import AppPaths
from .step6_outputs import write_step6_outputs


def _step2_required_fields(keyword_count: int) -> tuple[str, ...]:
    return tuple(f"keywords[{index}]" for index in range(keyword_count))


def _search_keyword_count(runtime_config: object) -> int:
    return max(1, int(getattr(runtime_config, "search_keyword_count", 1)))


def _looks_garbled_text(text: str) -> bool:
    """Heuristic check for common mojibake markers."""

    if not text:
        return False
    markers = ("�", "锟斤拷", "Ã", "Â", "â€”", "â€œ", "â€", "Ð", "Ñ")
    return any(marker in text for marker in markers)


def _coerce_items(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _take_llm_http_rounds(llm_client: object) -> int:
    take = getattr(llm_client, "take_http_round_count", None)
    return int(take()) if callable(take) else 0


def _print_step1_hot_topics_log(report: list[object], *, analyses_count: int) -> None:
    step1_urls: list[str] = []
    step1_titles: list[str] = []
    step1_total_articles = 0
    for channel in _coerce_items(report):
        channel_articles = list(getattr(channel, "articles", []) or [])
        step1_total_articles += len(channel_articles)
        for article in channel_articles:
            url = str(getattr(article, "url", "") or "").strip()
            title = str(getattr(article, "title", "") or "").strip()
            if url:
                step1_urls.append(url)
            if title:
                step1_titles.append(title)
    dup_url = len(step1_urls) - len(set(step1_urls))
    dup_title = len(step1_titles) - len(set(step1_titles))
    print(
        "[C114][Step1 抓取汇总] "
        f"抓取文章数={step1_total_articles} | "
        f"进入分析文章数={analyses_count} | "
        f"重复URL={dup_url} | "
        f"重复标题={dup_title}"
    )


def _print_step15_topic_group_log(*, llm_rounds: int) -> None:
    print(f"[C114][Step1.5 主题聚合] LLM HTTP轮次={llm_rounds}")


def _print_step2_checklist_log(checklist_items: list[object], *, keyword_count: int, llm_rounds: int) -> None:
    total_kw = 0
    missing = 0
    for item in _coerce_items(checklist_items):
        keywords = [str(k).strip() for k in list(getattr(item, "keywords", []) or []) if str(k).strip()]
        total_kw += len(keywords)
        if not keywords:
            missing += 1
    print(
        "[C114][Step2 检索清单] "
        f"条目数={len(checklist_items)} | "
        f"关键词条数={total_kw} | "
        f"缺关键词条目={missing} | "
        f"每组槽位数={keyword_count} | "
        f"LLM HTTP轮次={llm_rounds}"
    )


def _print_step3_search_log(search_payload: object, *, llm_rounds: int) -> None:
    search_categories = _coerce_items(getattr(search_payload, "categories", []))
    search_articles = [article for category in search_categories for article in list(getattr(category, "items", []) or [])]
    step3_query_buckets = [bucket for article in search_articles for bucket in list(getattr(article, "queries", []) or [])]
    step3_raw_results = [result for bucket in step3_query_buckets for result in list(getattr(bucket, "results", []) or [])]
    step3_raw_urls = [str(getattr(result, "url", "") or "").strip() for result in step3_raw_results if str(getattr(result, "url", "") or "").strip()]
    step3_dup = len(step3_raw_urls) - len(set(step3_raw_urls))
    step3_selected = [result for article in search_articles for result in list(getattr(article, "selected_results", []) or [])]
    extract_err = sum(
        1
        for result in step3_selected
        if str(getattr(result, "extract_status", "") or "").strip().lower() not in {"", "success"}
    )
    review_pending = sum(
        1 for result in step3_selected if str(getattr(result, "review_status", "") or "").strip().lower() != "reviewed"
    )
    print(
        "[C114][Step3 外搜] "
        f"文章任务数={len(search_articles)} | 查询桶数={len(step3_query_buckets)} | "
        f"原始结果条数={len(step3_raw_results)} | 重复URL(原始)={step3_dup} | "
        f"入选外链数={len(step3_selected)} | 抽取失败={extract_err} | "
        f"待审查={review_pending} | LLM HTTP轮次={llm_rounds}"
    )


def _print_step5_analysis_log(
    analysis_payload: object,
    *,
    llm_rounds: int,
    batch_retry_cfg: int,
    mode: str,
) -> None:
    categories = _coerce_items(getattr(analysis_payload, "categories", []))
    topic_count = len(categories)
    item_count = sum(len(list(getattr(section, "items", []) or [])) for section in categories)
    print(
        "[C114][Step5 正文分析] "
        f"主题数={topic_count} | 文章条目={item_count} | 模式={mode} | "
        f"单批最大重试(配置)={batch_retry_cfg} | LLM HTTP轮次={llm_rounds}"
    )


def _print_step6_brief_log(*, llm_rounds: int) -> None:
    print(f"[C114][Step6 主题简报] LLM HTTP轮次={llm_rounds}")


def _print_step7_review_log(*, llm_rounds: int) -> None:
    print(f"[C114][Step7 简报审查] LLM HTTP轮次={llm_rounds}")


def _build_builtin_run_log_markdown(
    *,
    report_date: object,
    report: list[object],
    analyses: list[object],
    checklist_items: list[object],
    search_payload: object,
    content_payload: object,
) -> str:
    """Render per-step quality metrics for one builtin run."""

    step1_urls: list[str] = []
    step1_titles: list[str] = []
    step1_channel_breakdown: list[str] = []
    step1_total_articles = 0
    for channel in _coerce_items(report):
        channel_articles = list(getattr(channel, "articles", []) or [])
        channel_name = str(getattr(channel, "channel_name", "unknown"))
        step1_channel_breakdown.append(f"- {channel_name}: {len(channel_articles)}")
        step1_total_articles += len(channel_articles)
        for article in channel_articles:
            url = str(getattr(article, "url", "") or "").strip()
            title = str(getattr(article, "title", "") or "").strip()
            if url:
                step1_urls.append(url)
            if title:
                step1_titles.append(title)
    step1_duplicate_url = len(step1_urls) - len(set(step1_urls))
    step1_duplicate_title = len(step1_titles) - len(set(step1_titles))

    step2_keyword_total = 0
    step2_missing_keywords = 0
    for item in _coerce_items(checklist_items):
        keywords = [str(keyword).strip() for keyword in list(getattr(item, "keywords", []) or []) if str(keyword).strip()]
        step2_keyword_total += len(keywords)
        if not keywords:
            step2_missing_keywords += 1

    search_categories = _coerce_items(getattr(search_payload, "categories", []))
    search_articles = [article for category in search_categories for article in list(getattr(category, "items", []) or [])]
    step3_query_buckets = [bucket for article in search_articles for bucket in list(getattr(article, "queries", []) or [])]
    step3_raw_results = [result for bucket in step3_query_buckets for result in list(getattr(bucket, "results", []) or [])]
    step3_raw_urls = [str(getattr(result, "url", "") or "").strip() for result in step3_raw_results if str(getattr(result, "url", "") or "").strip()]
    step3_duplicate_raw = len(step3_raw_urls) - len(set(step3_raw_urls))
    step3_selected_results = [result for article in search_articles for result in list(getattr(article, "selected_results", []) or [])]
    step3_extract_error = sum(
        1
        for result in step3_selected_results
        if str(getattr(result, "extract_status", "") or "").strip().lower() not in {"", "success"}
    )
    step3_review_pending = sum(
        1 for result in step3_selected_results if str(getattr(result, "review_status", "") or "").strip().lower() != "reviewed"
    )

    content_categories = _coerce_items(getattr(content_payload, "categories", []))
    content_articles = [article for category in content_categories for article in list(getattr(category, "items", []) or [])]
    original_contents = [getattr(article, "original_content", None) for article in content_articles]
    original_contents = [item for item in original_contents if item is not None]
    selected_contents = [item for article in content_articles for item in list(getattr(article, "selected_contents", []) or [])]
    all_contents = [*original_contents, *selected_contents]
    step4_fetch_error = sum(
        1
        for item in all_contents
        if str(getattr(item, "fetch_status", "") or "").strip().lower() not in {"", "success"}
        or str(getattr(item, "fetch_error", "") or "").strip() != ""
    )
    step4_garbled = sum(1 for item in all_contents if _looks_garbled_text(str(getattr(item, "content_text", "") or "")))
    step4_urls = [str(getattr(item, "url", "") or "").strip() for item in all_contents if str(getattr(item, "url", "") or "").strip()]
    step4_duplicate_url = len(step4_urls) - len(set(step4_urls))
    step4_source_counter = Counter(str(getattr(item, "content_source", "") or "unknown").strip() for item in all_contents)
    step4_source_lines = [f"- {source}: {count}" for source, count in step4_source_counter.most_common()]

    date_label = str(getattr(report_date, "isoformat", lambda: report_date)())
    lines = [
        f"# C114 流水线执行日志（{date_label}）",
        "",
        "## Step 1 抓取汇总",
        f"- 抓取文章总数: {step1_total_articles}",
        f"- Step 1 分析文章数: {len(analyses)}",
        f"- 重复文章链接数: {step1_duplicate_url}",
        f"- 重复文章标题数: {step1_duplicate_title}",
        "- 栏目分布:",
        *(step1_channel_breakdown or ["- 无"]),
        "",
        "## Step 2 检索清单",
        f"- 清单条目数: {len(checklist_items)}",
        f"- 关键词总数: {step2_keyword_total}",
        f"- 缺关键词条目数: {step2_missing_keywords}",
        "",
        "## Step 3 外部搜索",
        f"- 文章搜索任务数: {len(search_articles)}",
        f"- 查询桶数: {len(step3_query_buckets)}",
        f"- 原始结果总数(去重前): {len(step3_raw_results)}",
        f"- 原始结果重复数(按 URL): {step3_duplicate_raw}",
        f"- 入选外链数(selected_results): {len(step3_selected_results)}",
        f"- 入选外链抽取错误数: {step3_extract_error}",
        f"- 入选外链待审查数: {step3_review_pending}",
        "",
        "## Step 4 正文抓取",
        f"- 原文抓取数: {len(original_contents)}",
        f"- 外链抓取数: {len(selected_contents)}",
        f"- 抓取总数: {len(all_contents)}",
        f"- 抓取错误数: {step4_fetch_error}",
        f"- 重复链接数(按 URL): {step4_duplicate_url}",
        f"- 乱码疑似条数: {step4_garbled}",
        "- 内容来源分布:",
        *(step4_source_lines or ["- 无"]),
        "",
    ]
    return "\n".join(lines)


def _write_builtin_run_log(
    *,
    day_dir: Path,
    report_date: object,
    report: list[object],
    analyses: list[object],
    checklist_items: list[object],
    search_payload: object,
    content_payload: object,
) -> Path:
    date_token = str(getattr(report_date, "strftime", lambda _fmt: report_date)("%Y%m%d"))
    output_path = (day_dir / f"c114_pipeline_log_{date_token}.md").resolve()
    output_path.write_text(
        _build_builtin_run_log_markdown(
            report_date=report_date,
            report=report,
            analyses=analyses,
            checklist_items=checklist_items,
            search_payload=search_payload,
            content_payload=content_payload,
        ),
        encoding="utf-8",
    )
    return output_path


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

    search_keyword_count = _search_keyword_count(runtime_config)
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
            keyword_count=search_keyword_count,
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
            required_fields={"step_2": _step2_required_fields(search_keyword_count)},
            instruction=facade.StepInstruction(
                step_name="step_2",
                prompt_path=facade.SEARCH_KEYWORD_PROMPT_PATH,
                input_paths=(output_map["step_1"],),
                output_path=output_map["step_2"],
                required_fields=_step2_required_fields(search_keyword_count),
                notes=(f"请控制 agent 为每篇文章补足 {search_keyword_count} 组关键词。",),
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
            required_fields={"step_2": _step2_required_fields(search_keyword_count)},
            instruction=facade.StepInstruction(
                step_name="step_2",
                prompt_path=facade.SEARCH_KEYWORD_PROMPT_PATH,
                input_paths=(output_map["step_1"],),
                output_path=output_map["step_2"],
                required_fields=_step2_required_fields(search_keyword_count),
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
            keyword_count=search_keyword_count,
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

    search_keyword_count = _search_keyword_count(runtime_config)
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
    _print_step1_hot_topics_log(report, analyses_count=len(analyses))
    rounds_step_1_5 = 0
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
        rounds_step_1_5 = _take_llm_http_rounds(llm_client)
    _print_step15_topic_group_log(llm_rounds=rounds_step_1_5)
    checklist_items = facade.write_analysis_outputs(
        analysis_paths,
        target_date.isoformat(),
        analyses,
        llm_client=llm_client,
        keyword_count=search_keyword_count,
    )
    rounds_step_2 = _take_llm_http_rounds(llm_client)
    print(f"C114 全流程 step 1-2 完成 {target_date.isoformat()} | 文章数={len(analyses)} | 主题数={len(briefs)}")
    print(f"Step 1 CSV: {analysis_paths.analysis_output}")
    if not checklist_items:
        print("当日无文章，流程停留在 step 1；step 2 及后续文件不生成。\n")
        return
    _print_step2_checklist_log(checklist_items, keyword_count=search_keyword_count, llm_rounds=rounds_step_2)
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
    rounds_step_3 = _take_llm_http_rounds(llm_client)
    _print_step3_search_log(search_payload, llm_rounds=rounds_step_3)
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
    rounds_step_5 = _take_llm_http_rounds(llm_client)
    _print_step5_analysis_log(
        analysis_payload,
        llm_rounds=rounds_step_5,
        batch_retry_cfg=analysis_retry_attempts,
        mode=analysis_mode,
    )
    issues = facade.generate_layer_issues(
        report_date=target_date.isoformat(),
        raw_csv_path=paths.raw_dir / "c114_hot_topics.csv",
        checklist_path=day_dir / facade.step_2_checklist_name(target_date),
        search_results_path=day_dir / facade.step_3_results_name(target_date),
        content_path=content_analysis_paths.input_path,
        keyword_count=search_keyword_count,
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
    brief_markdown = facade.generate_brief_markdown(
        analysis_payload,
        llm_client,
        checkpoint_store=step6_checkpoint_store,
    )
    html_output, step6_log = write_step6_outputs(
        brief_output=brief_output,
        markdown_text=brief_markdown,
        run_dir=day_dir,
        target_date=target_date,
        context="c114-run builtin",
    )
    rounds_step_6 = _take_llm_http_rounds(llm_client)
    _print_step6_brief_log(llm_rounds=rounds_step_6)
    run_log_path = _write_builtin_run_log(
        day_dir=day_dir,
        report_date=target_date,
        report=report,
        analyses=analyses,
        checklist_items=checklist_items,
        search_payload=search_payload,
        content_payload=content_payload,
    )
    print(f"Step 3 YAML: {search_paths.output_path}")
    print(f"Step 4 YAML: {content_paths.output_path}")
    print(f"Step 5 YAML: {content_analysis_paths.analysis_output}")
    print(f"Step 6 MD: {brief_output}")
    print(f"Step 6 HTML: {html_output}")
    print(f"Step 6 执行日志: {step6_log}")
    print(f"Pipeline 日志: {run_log_path}")
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
        rounds_step_7 = _take_llm_http_rounds(llm_client)
        _print_step7_review_log(llm_rounds=rounds_step_7)
        print(f"Step 7 YAML: {review_paths.review_output_path}\n")
        return
    print("Step 7 已在配置中关闭，流程停留在 step 6。\n")
