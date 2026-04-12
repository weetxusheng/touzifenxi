from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from touzifenxi.content_sources.models import StandardArticle

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "skills" / "infoq-daily-hot-topics" / "scripts" / "infoq.py"


def load_infoq_cli_module():
    spec = importlib.util.spec_from_file_location("infoq_script", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.bootstrap()

    from infoq import cli as infoq_cli

    return infoq_cli


class InfoQRunCliTests(unittest.TestCase):
    def test_rehydrate_original_contents_prefers_adapter_content_for_original_article(self) -> None:
        infoq_cli = load_infoq_cli_module()
        article = StandardArticle(
            source_site="infoq",
            source_bucket="首页",
            channel="首页",
            article_id="infoq:test-1",
            title="测试文章",
            url="https://www.infoq.cn/article/test-1",
            published_at="2026-04-12",
            author="作者",
            tags=["AI"],
            keywords=["AI"],
            summary="适配器摘要",
            content_text="适配器正文",
            metadata={},
        )
        payload = infoq_cli.ContentWorkflowPayload(
            report_date="2026-04-12",
            input_path=Path("/tmp/infoq_step_3_search_results_20260412.yaml"),
            generated_at="2026-04-12T18:00:00+08:00",
            categories=[
                infoq_cli.ContentCategoryPayload(
                    topic="测试主题",
                    items=[
                        infoq_cli.ArticleContentPayload(
                            original_title="测试文章",
                            topic="测试主题",
                            channel="首页",
                            original_url="https://www.infoq.cn/article/test-1",
                            original_published_at="2026-04-12",
                            original_content=infoq_cli.FetchResult(
                                url="https://www.infoq.cn/article/test-1",
                                domain="www.infoq.cn",
                                content_title="InfoQ - 促进软件开发及相关领域知识与创新的传播-极客邦",
                                content_summary="占位摘要",
                                content_text="占位正文",
                                fetch_status="success",
                                fetch_error="",
                                content_source="html_fallback",
                            ),
                            selected_contents=[],
                        )
                    ],
                )
            ],
        )

        hydrated = infoq_cli.rehydrate_original_contents(payload, [article])

        original_content = hydrated.categories[0].items[0].original_content
        self.assertEqual(original_content.content_source, "source_adapter")
        self.assertEqual(original_content.content_title, "测试文章")
        self.assertEqual(original_content.content_summary, "适配器摘要")
        self.assertEqual(original_content.content_text, "适配器正文")

    def test_run_command_executes_full_pipeline_with_shared_steps(self) -> None:
        infoq_cli = load_infoq_cli_module()
        sample_article = StandardArticle(
            source_site="infoq",
            source_bucket="首页",
            channel="首页",
            article_id="infoq:test-1",
            title="测试文章",
            url="https://www.infoq.cn/article/test-1",
            published_at="2026-04-12",
            author="作者",
            tags=["AI"],
            keywords=["AI"],
            summary="测试摘要",
            content_text="测试正文",
            metadata={"channel_key": "home", "channel_name": "首页", "channel_url": "https://www.infoq.cn/"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir)
            paths = infoq_cli.AppPaths(
                project_root=output_root,
                data_dir=output_root / "data",
                raw_dir=output_root / "data" / "raw",
                processed_dir=output_root / "data" / "processed",
                reports_dir=output_root / "reports",
                state_dir=output_root / "state",
            )
            infoq_cli.ensure_directories(paths)
            args = argparse.Namespace(command="run", date="2026-04-12")
            analysis_item = SimpleNamespace(topic="测试主题")
            search_payload = {"report_date": "2026-04-12", "items": []}
            content_payload = infoq_cli.ContentWorkflowPayload(
                report_date="2026-04-12",
                input_path=output_root / "reports" / "infoq_report" / "dummy_step_3.yaml",
                generated_at="2026-04-12T18:00:00+08:00",
                categories=[],
            )
            analysis_payload = {"report_date": "2026-04-12", "categories": []}

            with (
                patch.object(
                    infoq_cli,
                    "fetch_and_materialize_infoq_articles",
                    return_value={
                        "articles": [sample_article],
                        "raw_json_path": output_root / "data" / "raw" / "infoq_hot_topics_20260412.json",
                        "raw_csv_path": output_root / "data" / "raw" / "infoq_hot_topics.csv",
                    },
                ),
                patch.object(infoq_cli, "require_llm_client", return_value=object()),
                patch.object(infoq_cli, "bind_llm_trace_log"),
                patch.object(infoq_cli, "analyze_daily_articles", return_value=([analysis_item], {"测试主题": []})),
                patch.object(infoq_cli, "auto_group_analysis_topics", return_value=([analysis_item], {"测试主题": []})),
                patch.object(infoq_cli, "write_analysis_outputs", return_value=[{"original_title": "测试文章"}]),
                patch.object(infoq_cli, "run_search_workflow", return_value=search_payload),
                patch.object(infoq_cli, "save_search_results") as save_search_results,
                patch.object(infoq_cli, "run_content_fetch_workflow", return_value=content_payload),
                patch.object(infoq_cli, "save_content_results") as save_content_results,
                patch.object(infoq_cli, "load_content_analysis_inputs", return_value=content_payload),
                patch.object(infoq_cli, "auto_complete_content_analysis", return_value=analysis_payload),
                patch.object(infoq_cli, "save_content_analysis_yaml") as save_content_analysis_yaml,
                patch.object(infoq_cli, "generate_layer_issues", return_value={"report_date": "2026-04-12", "issues": []}),
                patch.object(infoq_cli, "save_layer_issues_yaml") as save_layer_issues_yaml,
                patch.object(infoq_cli, "generate_brief_markdown", return_value="# InfoQ 主题简报\n"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    infoq_cli.run_with_args(args, paths=paths)

            run_dirs = sorted((paths.reports_dir / "infoq_report").glob("infoq_search_*"))
            self.assertEqual(len(run_dirs), 1)
            run_dir = run_dirs[0]
            self.assertTrue((run_dir / "c114_step_1_5_checkpoint_20260412.json").exists() is False)
            self.assertTrue((run_dir / "infoq_step_6_brief_20260412.md").exists())
            self.assertIn("InfoQ step 1-2 完成 2026-04-12", buffer.getvalue())
            save_search_results.assert_called_once_with(run_dir / "infoq_step_3_search_results_20260412.yaml", search_payload)
            save_content_results.assert_called_once_with(run_dir / "infoq_step_4_content_20260412.yaml", content_payload)
            save_content_analysis_yaml.assert_called_once_with(
                run_dir / "infoq_step_5_content_analysis_20260412.yaml", analysis_payload
            )
            save_layer_issues_yaml.assert_called_once()


if __name__ == "__main__":
    unittest.main()
