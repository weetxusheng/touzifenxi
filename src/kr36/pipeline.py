"""36Kr 步骤 2–4：仅依赖步骤 1 的 CSV/ArticleAnalysis，不经过 C114 搜索清单与外搜。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from c114.runtime.config import load_c114_runtime_config
from utils.tools.analysis.models import ArticleAnalysis
from utils.tools.facades.content import (
    run_content_fetch_workflow,
    save_content_results,
    search_workflow_to_search_results_input,
)
from utils.tools.facades.content_analysis import (
    auto_complete_content_analysis,
    generate_brief_markdown,
    generate_layer_issues,
    load_content_analysis_inputs,
    save_content_analysis_yaml,
    save_layer_issues_yaml,
)
from utils.tools.llm import StructuredChatClient
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step

from . import names as n
from .brief_assets import save_brief_preview_assets
from .synthetic_workflow_from_step1 import build_synthetic_workflow_from_analyses

PROMPTS = Path(__file__).resolve().parent / "prompts"
CONTENT_ANALYSIS_PROMPT_PATH = PROMPTS / "content-analysis-agent.md"
BRIEF_PROMPT_PATH = PROMPTS / "brief-agent.md"


def run_stages_2_3_4(
    *,
    run_dir: Path,
    target_date: date,
    report_date_text: str,
    analyses: list[ArticleAnalysis],
    step1_csv_path: Path,
    llm_client: StructuredChatClient,
) -> None:
    if not analyses:
        return
    rc = load_c114_runtime_config()
    # --- 步骤 2：按步骤 1 的链接拉取原文 ---
    n1 = len(analyses)
    swf = build_synthetic_workflow_from_analyses(
        analyses, report_date_text, input_path=step1_csv_path.resolve()
    )
    fetch_in = search_workflow_to_search_results_input(swf)
    n_in = sum(len(c.items) for c in fetch_in.categories)
    if n_in != n1:
        raise ValueError(f"36kr: 转 SearchResults 后条数 {n_in} 与 步骤1 条数 {n1} 不一致。")
    step2_path = run_dir / n.step2_content_name(target_date)
    step2_cp = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step2_path,
            step_name="step_2",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_2",
        report_date=report_date_text,
        input_path=step1_csv_path,
        output_path=step2_path,
    )
    content_payload = run_content_fetch_workflow(
        step1_csv_path,
        report_date_text,
        checkpoint_store=step2_cp,
        search_input=fetch_in,
        expected_article_count=n1,
    )
    save_content_results(step2_path, content_payload)
    n2 = sum(len(c.items) for c in content_payload.categories)
    if n2 != n1:
        raise RuntimeError(f"36kr: 步骤2 落盘条数 {n2} 与 步骤1 条数 {n1} 不一致。")
    print(f"36Kr 步骤2（每链接原文，与步骤1 同 {n1} 条）: {step2_path}")
    # --- 步骤 3：LLM 整合/结构化分析 + 分层问题 ---
    step3_path = run_dir / n.step3_analysis_name(target_date)
    step4_brief = run_dir / n.step4_brief_name(target_date)
    layer_path = run_dir / n.layer_issues_name(target_date)
    content_for_analysis = load_content_analysis_inputs(step2_path)
    step3_cp = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step3_path,
            step_name="step_3",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_3",
        report_date=report_date_text,
        input_path=step2_path,
        output_path=step3_path,
    )
    analysis_payload = auto_complete_content_analysis(
        content_for_analysis,
        llm_client,
        prompt_path=CONTENT_ANALYSIS_PROMPT_PATH,
        checkpoint_store=step3_cp,
    )
    save_content_analysis_yaml(step3_path, analysis_payload)
    save_layer_issues_yaml(
        layer_path,
        report_date_text,
        generate_layer_issues(analysis_payload, keyword_count=rc.search_keyword_count),
    )
    print(f"36Kr 步骤3（数据整合/分析）: {step3_path}")
    # --- 步骤 4：导出简报与 HTML/邮件用文本 ---
    step4_cp = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step4_brief,
            step_name="step_4",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_4",
        report_date=report_date_text,
        input_path=step3_path,
        output_path=step4_brief,
    )
    brief_markdown = generate_brief_markdown(
        analysis_payload,
        llm_client,
        prompt_path=BRIEF_PROMPT_PATH,
        checkpoint_store=step4_cp,
    )
    step4_brief.write_text(brief_markdown, encoding="utf-8")
    html_p, text_p, doc_p = save_brief_preview_assets(step4_brief, brief_markdown)
    print(f"36Kr 步骤4（导出）: {step4_brief}")
    print(f"  预览: {html_p} / {text_p} / {doc_p}")
