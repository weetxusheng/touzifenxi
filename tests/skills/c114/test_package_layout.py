from __future__ import annotations


def test_first_batch_package_layout_exposes_runtime_llm_and_search_entrypoints() -> None:
    """第一批目录收口后，横向能力应从职责子包暴露。"""

    from c114.llm import StructuredChatClient
    from c114.llm.client import StructuredChatClient as StructuredChatClientImpl
    from c114.runtime.checkpoint import StepCheckpointStore
    from c114.runtime.config import load_c114_runtime_config
    from c114.search.workflow import run_search_workflow
    from c114.search.yaml_io import save_search_results

    assert StructuredChatClient is StructuredChatClientImpl
    assert StepCheckpointStore.__name__ == "StepCheckpointStore"
    assert callable(load_c114_runtime_config)
    assert callable(run_search_workflow)
    assert callable(save_search_results)


def test_step5_and_step6_modules_are_split_from_content_analysis_facade() -> None:
    """Step 5/6 主体逻辑应由 steps 和 brief/analysis 横向模块承载。"""

    from c114.brief.markdown import render_brief_markdown
    from c114.steps.step5_content_analysis import auto_complete_content_analysis
    from c114.steps.step6_brief import generate_brief_markdown

    assert callable(auto_complete_content_analysis)
    assert callable(generate_brief_markdown)
    assert callable(render_brief_markdown)


def test_step1_step1_5_and_step2_modules_are_split_from_intelligence_facade() -> None:
    """Step 1/1.5/2 主体逻辑应由 steps 与 analysis 模块承载。"""

    from c114.analysis.models import ArticleAnalysis
    from c114.steps.step1_5_topic_grouping import auto_group_analysis_topics
    from c114.steps.step1_analysis import analyze_daily_articles
    from c114.steps.step2_keywords import build_search_checklist_items

    assert ArticleAnalysis.__name__ == "ArticleAnalysis"
    assert callable(analyze_daily_articles)
    assert callable(auto_group_analysis_topics)
    assert callable(build_search_checklist_items)


def test_step4_content_fetch_is_split_from_content_facade() -> None:
    """Step 4 正文抓取应由 content 与 steps 模块承载。"""

    from c114.content.fetch import fetch_article_contents
    from c114.content.html import extract_visible_text
    from c114.steps.step4_content_fetch import run_content_fetch_workflow

    assert callable(fetch_article_contents)
    assert callable(extract_visible_text)
    assert callable(run_content_fetch_workflow)


def test_cli_is_split_into_pipeline_and_commands() -> None:
    """CLI 只能做 parser 和分发，完整流程进入 pipeline/commands。"""

    from c114.commands.run import handle_run_command
    from c114.pipeline import run_daily_pipeline

    assert callable(handle_run_command)
    assert callable(run_daily_pipeline)
