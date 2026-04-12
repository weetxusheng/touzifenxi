from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

from c114.c114_content_analysis import (
    auto_complete_brief_sections,
    auto_complete_content_analysis,
    build_brief_prompt_payload,
    build_content_analysis_prompt_payload,
    collect_missing_analysis_fields,
    generate_brief_markdown,
    generate_layer_issues,
    html_fallback_edge_limit,
    load_content_analysis_inputs,
    normalize_brief_sections,
    normalize_content_analysis_draft,
    render_brief_markdown,
    render_content_analysis_yaml,
    resolve_content_analysis_output_paths,
)
from c114.llm import StructuredLLMError
from touzifenxi.settings import AppPaths


class ContentAnalysisPathTests(unittest.TestCase):
    def test_resolve_content_analysis_output_paths_uses_same_run_directory(self) -> None:
        app_paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )

        resolved = resolve_content_analysis_output_paths(
            app_paths,
            date(2026, 3, 31),
            input_override="reports/c114_report/c114_search_202604011325/c114_step_4_content_20260331.yaml",
        )

        self.assertEqual(
            resolved.input_path,
            Path("/repo/reports/c114_report/c114_search_202604011325/c114_step_4_content_20260331.yaml"),
        )
        self.assertEqual(
            resolved.analysis_output,
            Path("/repo/reports/c114_report/c114_search_202604011325/c114_step_5_content_analysis_20260331.yaml"),
        )
        self.assertEqual(
            resolved.brief_output,
            Path("/repo/reports/c114_report/c114_search_202604011325/c114_step_6_brief_20260331.md"),
        )
        self.assertEqual(
            resolved.issues_output,
            Path("/repo/reports/c114_report/c114_search_202604011325/c114_layer_issues_20260331.yaml"),
        )


class ContentAnalysisTemplateTests(unittest.TestCase):
    def test_renders_content_analysis_template_with_prompt_and_blank_fields(self) -> None:
        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_content:
          url: 'https://www.c114.com.cn/news/41/a1307790.html'
          domain: 'www.c114.com.cn'
          content_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          content_summary: '摘要'
          content_text: '正文内容'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
          - query: '吴建军 6G转入产业实战阶段'
            query_type: 'keyword'
            url: 'https://example.com/6g'
            domain: 'example.com'
            result_title: '论坛观点：6G进入实战'
            published_at: '2026-03-31'
            content_title: '论坛观点：6G进入实战'
            content_summary: '补充摘要'
            content_text: '补充正文'
            content_source: 'aliyun'
            fetch_status: 'success'
            fetch_error: ''
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")

            analysis_input = load_content_analysis_inputs(input_path)
            rendered = render_content_analysis_yaml(analysis_input)

        self.assertIn("prompt_path:", rendered)
        self.assertIn("analysis:", rendered)
        self.assertIn("core_points:", rendered)
        self.assertIn("new_facts:", rendered)
        self.assertIn("signals:", rendered)
        self.assertIn("selected_contents:", rendered)
        self.assertEqual(rendered.count("layer_notes:"), 1)

    def test_renders_brief_markdown_grouped_by_topic(self) -> None:
        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_published_at: '2026-03-31'
        original_content:
          url: 'https://www.c114.com.cn/news/41/a1307790.html'
          domain: 'www.c114.com.cn'
          content_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          content_summary: '摘要'
          content_text: '正文内容'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
          - query: '吴建军 6G转入产业实战阶段'
            query_type: 'keyword'
            url: 'https://example.com/6g'
            domain: 'example.com'
            result_title: '论坛观点：6G进入实战'
            published_at: '2026-03-31'
            content_title: '论坛观点：6G进入实战'
            content_summary: '补充摘要'
            content_text: '补充正文'
            content_source: 'aliyun'
            fetch_status: 'success'
            fetch_error: ''
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")

            analysis_input = load_content_analysis_inputs(input_path)
            rendered = render_brief_markdown(analysis_input)

        self.assertIn("# C114 主题简报", rendered)
        self.assertIn("## AI与算力", rendered)
        self.assertIn("未来移动通信论坛吴建军：6G已转入产业实战阶段", rendered)
        self.assertIn("https://www.c114.com.cn/news/41/a1307790.html", rendered)
        self.assertIn("未来移动通信论坛吴建军：6G已转入产业实战阶段 | 2026-03-31 | https://www.c114.com.cn/news/41/a1307790.html", rendered)
        self.assertIn("论坛观点：6G进入实战 | 2026-03-31 | https://example.com/6g", rendered)
        self.assertIn("## 运行摘要", rendered)
        self.assertIn("### 核心判断", rendered)
        self.assertIn("### 增量信息", rendered)
        self.assertIn("### 产业/公司影响", rendered)
        self.assertIn("### 需要继续跟踪的点", rendered)
        self.assertIn("### 源地址", rendered)
        self.assertIn("### 补充地址", rendered)
        self.assertNotIn("- 输入文件：", rendered)
        self.assertNotIn("- 提示词：", rendered)
        self.assertNotIn("- 说明：", rendered)

    def test_normalize_content_analysis_draft_requires_summary_and_core_points(self) -> None:
        draft = normalize_content_analysis_draft(
            {
                "summary": "一句话摘要",
                "core_points": ["核心点1", "核心点2"],
            }
        )

        self.assertEqual(draft.summary, "一句话摘要")
        self.assertEqual(draft.core_points[0], "核心点1")
        self.assertEqual(draft.new_facts, [])
        self.assertEqual(draft.why_it_matters, "")

    def test_normalize_content_analysis_draft_accepts_single_string_for_list_fields(self) -> None:
        draft = normalize_content_analysis_draft(
            {
                "summary": "一句话摘要",
                "core_points": "核心点1",
                "new_facts": "新增事实1",
                "entities": "中国联通",
                "signals": "产业信号",
                "risk_or_uncertainty": "仍需观察的点",
                "why_it_matters": "这件事说明行业已经进入验证阶段。",
                "layer_notes": "补充链接与源稿高度相关。",
            }
        )

        self.assertEqual(draft.core_points, ["核心点1"])
        self.assertEqual(draft.layer_notes, ["补充链接与源稿高度相关。"])

    def test_normalize_content_analysis_draft_accepts_object_list_payload(self) -> None:
        draft = normalize_content_analysis_draft(
            [
                {
                    "summary": "旧摘要",
                    "core_points": ["核心点1"],
                },
                {
                    "summary": "新摘要",
                    "new_facts": ["新增事实1"],
                    "entities": ["中国联通"],
                    "signals": ["产业信号"],
                    "risk_or_uncertainty": ["仍需观察的点"],
                    "why_it_matters": "这件事说明行业已经进入验证阶段。",
                    "layer_notes": ["补充链接与源稿高度相关。"],
                },
            ]
        )

        self.assertEqual(draft.summary, "新摘要")
        self.assertEqual(draft.core_points, ["核心点1"])

    def test_normalize_content_analysis_draft_accepts_wrapped_analysis_object(self) -> None:
        draft = normalize_content_analysis_draft(
            {
                "analysis": {
                    "summary": "一句话摘要",
                    "core_points": ["核心点1", "核心点2"],
                    "new_facts": ["新增事实1"],
                    "entities": ["中国联通"],
                    "signals": ["产业信号"],
                    "risk_or_uncertainty": ["仍需观察的点"],
                    "why_it_matters": "这件事说明行业已经进入验证阶段。",
                    "layer_notes": ["补充链接与源稿高度相关。"],
                }
            }
        )

        self.assertEqual(draft.summary, "一句话摘要")
        self.assertEqual(draft.entities, ["中国联通"])

    def test_auto_complete_content_analysis_fills_analysis_via_llm(self) -> None:
        class FakeLLMClient:
            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                return {
                    "summary": "一句话摘要",
                    "core_points": ["核心点1", "核心点2"],
                    "new_facts": ["新增事实1"],
                    "entities": ["中国联通"],
                    "signals": ["产业信号"],
                    "risk_or_uncertainty": ["仍需观察的点"],
                    "why_it_matters": "这件事说明行业已经进入验证阶段。",
                    "layer_notes": ["补充链接与源稿高度相关。"],
                }

        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_content:
          url: 'https://www.c114.com.cn/news/41/a1307790.html'
          domain: 'www.c114.com.cn'
          content_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          content_summary: '摘要'
          content_text: '正文内容'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            completed = auto_complete_content_analysis(
                analysis_input,
                FakeLLMClient(),
                mode="per_item",
            )

        item = completed.categories[0].items[0]
        self.assertEqual(item.analysis.summary, "一句话摘要")
        self.assertEqual(item.analysis.why_it_matters, "这件事说明行业已经进入验证阶段。")

    def test_build_content_analysis_prompt_payload_uses_title_and_text_without_summary(self) -> None:
        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '标题'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://example.com/original'
        original_content:
          url: 'https://example.com/original'
          domain: 'example.com'
          content_title: '原标题'
          content_summary: '原文摘要'
          content_text: '原文正文'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
          - query: '扩展'
            query_type: 'keyword'
            url: 'https://example.com/ext'
            domain: 'example.com'
            result_title: '外链标题'
            published_at: '2026-03-31'
            content_title: '补充标题'
            content_summary: '补充摘要'
            content_text: '补充正文'
            content_source: 'aliyun'
            fetch_status: 'success'
            fetch_error: ''
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)

        built = build_content_analysis_prompt_payload(analysis_input.categories[0].items[0])

        self.assertEqual(built["original_content"]["title"], "原标题")
        self.assertEqual(built["original_content"]["text"], "原文正文")
        self.assertNotIn("summary", built["original_content"])
        self.assertEqual(built["selected_contents"][0]["title"], "补充标题")
        self.assertEqual(built["selected_contents"][0]["text"], "补充正文")
        self.assertNotIn("summary", built["selected_contents"][0])

    def test_build_content_analysis_prompt_payload_truncates_html_fallback_edges(self) -> None:
        zh_text = "前" * 140 + "中" * 80 + "后" * 140
        en_text = "A" * 260 + "B" * 120 + "C" * 260
        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '标题'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://example.com/original'
        original_content:
          url: 'https://example.com/original'
          domain: 'example.com'
          content_title: '原标题'
          content_summary: '原文摘要'
          content_text: '__ZH_TEXT__'
          content_source: 'html_fallback'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
          - query: '扩展'
            query_type: 'keyword'
            url: 'https://example.com/ext'
            domain: 'example.com'
            result_title: '外链标题'
            published_at: '2026-03-31'
            content_title: '补充标题'
            content_summary: '补充摘要'
            content_text: '__EN_TEXT__'
            content_source: 'html_fallback'
            fetch_status: 'success'
            fetch_error: ''
""".replace("__ZH_TEXT__", zh_text).replace("__EN_TEXT__", en_text)
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)

        built = build_content_analysis_prompt_payload(analysis_input.categories[0].items[0])

        self.assertEqual(html_fallback_edge_limit(zh_text), 100)
        self.assertEqual(html_fallback_edge_limit(en_text), 200)
        self.assertTrue(built["original_content"]["text"].startswith("前" * 100))
        self.assertTrue(built["original_content"]["text"].endswith("后" * 100))
        self.assertIn("\n...\n", built["original_content"]["text"])
        self.assertTrue(built["selected_contents"][0]["text"].startswith("A" * 200))
        self.assertTrue(built["selected_contents"][0]["text"].endswith("C" * 200))

    def test_auto_complete_content_analysis_runs_items_in_parallel(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.02)
                with self.lock:
                    self.active -= 1
                return {
                    "summary": "一句话摘要",
                    "core_points": ["核心点1", "核心点2"],
                    "new_facts": ["新增事实1"],
                    "entities": ["中国联通"],
                    "signals": ["产业信号"],
                    "risk_or_uncertainty": ["仍需观察的点"],
                    "why_it_matters": "这件事说明行业已经进入验证阶段。",
                    "layer_notes": ["补充链接与源稿高度相关。"],
                }

        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          url: 'https://www.c114.com.cn/news/1.html'
          domain: 'www.c114.com.cn'
          content_title: '文章1'
          content_summary: '摘要'
          content_text: '正文内容1'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
      - original_title: '文章2'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/2.html'
        original_content:
          url: 'https://www.c114.com.cn/news/2.html'
          domain: 'www.c114.com.cn'
          content_title: '文章2'
          content_summary: '摘要'
          content_text: '正文内容2'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            auto_complete_content_analysis(
                analysis_input,
                fake_client,
                mode="per_item",
            )

        self.assertGreater(fake_client.max_active, 1)

    def test_auto_complete_content_analysis_records_postprocess_error_before_raising(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.postprocess_errors: list[str] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                return {"content": ["not-an-object"]}

            def record_postprocess_error(self, *, error: Exception, response_payload: object | None = None) -> None:
                self.postprocess_errors.append(str(error))

        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          url: 'https://www.c114.com.cn/news/1.html'
          domain: 'www.c114.com.cn'
          content_title: '文章1'
          content_summary: '摘要'
          content_text: '正文内容1'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            with self.assertRaises(StructuredLLMError):
                auto_complete_content_analysis(
                    analysis_input,
                    fake_client,
                    mode="per_item",
                )

        self.assertEqual(len(fake_client.postprocess_errors), 1)
        self.assertIn("step 5 缺少 summary", fake_client.postprocess_errors[0])

    def test_auto_complete_content_analysis_per_topic_fills_all_items_from_single_response(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls.append((system_prompt, user_prompt))
                return {
                    "topic": "AI与算力",
                    "items": [
                        {
                            "original_title": "文章1",
                            "summary": "摘要1",
                            "core_points": ["核心点1", "核心点2"],
                        },
                        {
                            "original_title": "文章2",
                            "summary": "摘要2",
                            "core_points": ["核心点3", "核心点4"],
                        },
                    ],
                }

        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          url: 'https://www.c114.com.cn/news/1.html'
          domain: 'www.c114.com.cn'
          content_title: '文章1'
          content_summary: '摘要'
          content_text: '正文内容1'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
      - original_title: '文章2'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/2.html'
        original_content:
          url: 'https://www.c114.com.cn/news/2.html'
          domain: 'www.c114.com.cn'
          content_title: '文章2'
          content_summary: '摘要'
          content_text: '正文内容2'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            completed = auto_complete_content_analysis(
                analysis_input,
                fake_client,
                mode="per_topic",
                batch_retry_attempts=2,
            )

        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(completed.categories[0].items[0].analysis.summary, "摘要1")
        self.assertEqual(completed.categories[0].items[1].analysis.core_points, ["核心点3", "核心点4"])

    def test_auto_complete_content_analysis_per_topic_retries_after_postprocess_error(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = 0
                self.postprocess_errors: list[str] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls += 1
                if self.calls == 1:
                    return {
                        "topic": "AI与算力",
                        "items": [
                            {
                                "original_title": "文章1",
                                "summary": "",
                                "core_points": [],
                            }
                        ],
                    }
                return {
                    "topic": "AI与算力",
                    "items": [
                        {
                            "original_title": "文章1",
                            "summary": "补回摘要",
                            "core_points": ["核心点1"],
                        }
                    ],
                }

            def record_postprocess_error(self, *, error: Exception, response_payload: object | None = None) -> None:
                self.postprocess_errors.append(str(error))

        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          url: 'https://www.c114.com.cn/news/1.html'
          domain: 'www.c114.com.cn'
          content_title: '文章1'
          content_summary: '摘要'
          content_text: '正文内容1'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            completed = auto_complete_content_analysis(
                analysis_input,
                fake_client,
                mode="per_topic",
                batch_retry_attempts=2,
            )

        self.assertEqual(fake_client.calls, 2)
        self.assertEqual(len(fake_client.postprocess_errors), 1)
        self.assertIn("step 5 缺少 summary", fake_client.postprocess_errors[0])
        self.assertEqual(completed.categories[0].items[0].analysis.summary, "补回摘要")

    def test_auto_complete_content_analysis_per_topic_splits_large_topic_batches(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls.append(user_prompt)
                if '"original_title": "文章5"' in user_prompt:
                    return {
                        "topic": "首页",
                        "items": [
                            {
                                "original_title": "文章5",
                                "summary": "摘要5",
                                "core_points": ["核心点5"],
                            }
                        ],
                    }
                return {
                    "topic": "首页",
                    "items": [
                        {
                            "original_title": "文章1",
                            "summary": "摘要1",
                            "core_points": ["核心点1"],
                        },
                        {
                            "original_title": "文章2",
                            "summary": "摘要2",
                            "core_points": ["核心点2"],
                        },
                        {
                            "original_title": "文章3",
                            "summary": "摘要3",
                            "core_points": ["核心点3"],
                        },
                        {
                            "original_title": "文章4",
                            "summary": "摘要4",
                            "core_points": ["核心点4"],
                        },
                    ],
                }

        payload = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: '首页'
    items:
      - original_title: '文章1'
        topic: '首页'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          url: 'https://www.c114.com.cn/news/1.html'
          domain: 'www.c114.com.cn'
          content_title: '文章1'
          content_summary: '摘要'
          content_text: '正文内容1'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
      - original_title: '文章2'
        topic: '首页'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/2.html'
        original_content:
          url: 'https://www.c114.com.cn/news/2.html'
          domain: 'www.c114.com.cn'
          content_title: '文章2'
          content_summary: '摘要'
          content_text: '正文内容2'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
      - original_title: '文章3'
        topic: '首页'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/3.html'
        original_content:
          url: 'https://www.c114.com.cn/news/3.html'
          domain: 'www.c114.com.cn'
          content_title: '文章3'
          content_summary: '摘要'
          content_text: '正文内容3'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
      - original_title: '文章4'
        topic: '首页'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/4.html'
        original_content:
          url: 'https://www.c114.com.cn/news/4.html'
          domain: 'www.c114.com.cn'
          content_title: '文章4'
          content_summary: '摘要'
          content_text: '正文内容4'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
      - original_title: '文章5'
        topic: '首页'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/5.html'
        original_content:
          url: 'https://www.c114.com.cn/news/5.html'
          domain: 'www.c114.com.cn'
          content_title: '文章5'
          content_summary: '摘要'
          content_text: '正文内容5'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            completed = auto_complete_content_analysis(
                analysis_input,
                fake_client,
                mode="per_topic",
                batch_retry_attempts=2,
            )

        self.assertEqual(len(fake_client.calls), 2)
        self.assertEqual(
            [item.original_title for item in completed.categories[0].items],
            ["文章1", "文章2", "文章3", "文章4", "文章5"],
        )
        self.assertEqual(completed.categories[0].items[-1].analysis.summary, "摘要5")

    def test_normalize_brief_sections_requires_all_sections(self) -> None:
        sections = normalize_brief_sections(
            {
                "核心判断": "行业阶段正在从概念走向验证。",
                "增量信息": "新增了试点落地线索。",
                "产业/公司影响": "先影响设备与解决方案环节。",
                "需要继续跟踪的点": ["试点范围", "客户落地时间"],
            }
        )

        self.assertEqual(sections["核心判断"], "行业阶段正在从概念走向验证。")
        self.assertEqual(len(sections["需要继续跟踪的点"]), 2)

    def test_normalize_brief_sections_accepts_wrapped_payload(self) -> None:
        sections = normalize_brief_sections(
            {
                "result": {
                    "核心判断": "行业阶段正在从概念走向验证。",
                    "增量信息": "新增了试点落地线索。",
                    "产业/公司影响": "先影响设备与解决方案环节。",
                    "需要继续跟踪的点": ["试点范围", "客户落地时间"],
                }
            }
        )

        self.assertEqual(sections["核心判断"], "行业阶段正在从概念走向验证。")
        self.assertEqual(len(sections["需要继续跟踪的点"]), 2)

    def test_build_brief_prompt_payload_keeps_only_minimum_analysis_fields(self) -> None:
        payload = """report_date: '2026-03-31'
input_path: '/tmp/content.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          title: '文章1'
          summary: '摘要'
          text: '正文内容1'
          source: 'aliyun'
          status: 'success'
        selected_contents:
        analysis:
          summary: '摘要1'
          core_points:
            - '核心点1'
            - '核心点2'
            - '核心点3'
            - '核心点4'
          new_facts:
            - '新增事实1'
          entities:
            - '中国联通'
          signals:
            - '产业信号1'
          risk_or_uncertainty:
            - '不确定性1'
          why_it_matters: '重要性1'
          layer_notes:
            - '备注1'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)

        built = build_brief_prompt_payload(analysis_input.categories[0])

        self.assertEqual(built["topic"], "AI与算力")
        self.assertEqual(
            built["items"],
            [
                {
                    "original_title": "文章1",
                    "summary": "摘要1",
                    "core_points": ["核心点1", "核心点2", "核心点3"],
                }
            ],
        )

    def test_generate_brief_markdown_runs_topics_in_parallel(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.02)
                with self.lock:
                    self.active -= 1
                return {
                    "核心判断": "行业阶段正在从概念走向验证。",
                    "增量信息": "新增了试点落地线索。",
                    "产业/公司影响": "先影响设备与解决方案环节。",
                    "需要继续跟踪的点": ["试点范围", "客户落地时间"],
                }

        payload = """report_date: '2026-03-31'
input_path: '/tmp/content.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          title: '文章1'
          summary: '摘要'
          text: '正文内容1'
          source: 'aliyun'
          status: 'success'
        selected_contents:
        analysis:
          summary: '摘要1'
          core_points:
            - '核心点1'
          new_facts:
            - '新增事实1'
          entities:
            - '中国联通'
          signals:
            - '产业信号1'
          risk_or_uncertainty:
            - '不确定性1'
          why_it_matters: '重要性1'
          layer_notes:
            - '备注1'
  - topic: '量子技术'
    items:
      - original_title: '文章2'
        topic: '量子技术'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/2.html'
        original_content:
          title: '文章2'
          summary: '摘要'
          text: '正文内容2'
          source: 'aliyun'
          status: 'success'
        selected_contents:
        analysis:
          summary: '摘要2'
          core_points:
            - '核心点2'
          new_facts:
            - '新增事实2'
          entities:
            - '玻色量子'
          signals:
            - '产业信号2'
          risk_or_uncertainty:
            - '不确定性2'
          why_it_matters: '重要性2'
          layer_notes:
            - '备注2'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            generate_brief_markdown(analysis_input, fake_client)

        self.assertGreater(fake_client.max_active, 1)

    def test_auto_complete_brief_sections_records_postprocess_error_before_raising(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.postprocess_errors: list[str] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                return {"sections": ["not-an-object"]}

            def record_postprocess_error(self, *, error: Exception, response_payload: object | None = None) -> None:
                self.postprocess_errors.append(str(error))

        payload = """report_date: '2026-03-31'
input_path: '/tmp/content.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章1'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/1.html'
        original_content:
          title: '文章1'
          summary: '摘要'
          text: '正文内容1'
          source: 'aliyun'
          status: 'success'
        selected_contents:
        analysis:
          summary: '摘要1'
          core_points:
            - '核心点1'
          new_facts:
          entities:
          signals:
          risk_or_uncertainty:
          why_it_matters: ''
          layer_notes:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")
            analysis_input = load_content_analysis_inputs(input_path)
            fake_client = FakeLLMClient()
            with self.assertRaises(StructuredLLMError):
                auto_complete_brief_sections(analysis_input, fake_client)

        self.assertEqual(len(fake_client.postprocess_errors), 1)
        self.assertIn("step 6", fake_client.postprocess_errors[0])

    def test_renders_brief_markdown_summary_from_sidecar_step3_file(self) -> None:
        content_payload = """report_date: '2026-04-07'
input_path: '/tmp/c114_step_4_content_20260407.yaml'
generated_at: '2026-04-07T10:51:31'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '从连接到Token！运营商“基本盘”的变与不变'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/16/a1308082.html'
        original_content:
          url: 'https://www.c114.com.cn/news/16/a1308082.html'
          domain: 'www.c114.com.cn'
          content_title: '从连接到Token！运营商“基本盘”的变与不变'
          content_summary: '摘要'
          content_text: '正文内容'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
          - query: '原标题'
            query_type: 'title'
            url: 'https://example.com/token'
            domain: 'example.com'
            result_title: '外部转载'
            published_at: '2026-04-07'
            content_title: '外部转载'
            content_summary: '补充摘要'
            content_text: '补充正文'
            content_source: 'aliyun'
            fetch_status: 'success'
            fetch_error: ''
"""
        search_payload = """report_date: '2026-04-07'
provider: 'tavily'
input_path: '/tmp/c114_step_2_search_checklist_20260407.yaml'
generated_at: '2026-04-07T10:49:58'
review_prompt_path: '/tmp/search-review-agent.md'
review_instructions: '说明'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '从连接到Token！运营商“基本盘”的变与不变'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/16/a1308082.html'
        original_published_at: '2026-04-07'
        queries:
        search_results:
        selected_results:
          - query: '原标题'
            query_type: 'title'
            result_title: '外部转载'
            url: 'https://example.com/token'
            domain: 'example.com'
            published_at: '2026-04-07'
            snippet: ''
            score: 1.0000
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
            ai_review:
              status: 'reviewed'
              keep_level: 'strong'
              reason: '同一事件'
              relevance_note: '主体一致'
              value_type: '同事件转载'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            content_path = Path(tmp_dir) / "c114_step_4_content_20260407.yaml"
            search_path = Path(tmp_dir) / "c114_step_3_search_results_20260407.yaml"
            content_path.write_text(content_payload, encoding="utf-8")
            search_path.write_text(search_payload, encoding="utf-8")

            analysis_input = load_content_analysis_inputs(content_path)
            rendered = render_brief_markdown(analysis_input)

        self.assertIn("- 补充链接数：1", rendered)
        self.assertIn("- strong（强保留）：1", rendered)
        self.assertIn("- weak（弱保留）：0", rendered)

    def test_collect_missing_analysis_fields_reports_only_minimum_required_fields(self) -> None:
        payload = """report_date: '2026-03-31'
input_path: '/tmp/content.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_content:
          url: 'https://www.c114.com.cn/news/41/a1307790.html'
          domain: 'www.c114.com.cn'
          content_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          content_summary: '摘要'
          content_text: '正文内容'
          content_source: 'aliyun'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
        analysis:
          summary: ''
          core_points:
          new_facts:
          entities:
          signals:
          risk_or_uncertainty:
          why_it_matters: ''
          layer_notes:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "content.yaml"
            input_path.write_text(payload, encoding="utf-8")

            analysis_input = load_content_analysis_inputs(input_path)
            missing = collect_missing_analysis_fields(analysis_input)

        self.assertEqual(len(missing), 1)
        self.assertEqual(
            missing[0]["missing_fields"],
            [
                "summary",
                "core_points",
            ],
        )

    def test_load_content_analysis_inputs_supports_step5_field_names(self) -> None:
        payload = """report_date: '2026-03-31'
input_path: '/tmp/content.yaml'
prompt_path: '/tmp/content-analysis-agent.md'
instructions: '说明'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_published_at: '2026-03-31'
        original_content:
          title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          summary: '摘要'
          text: '正文内容'
          source: 'aliyun'
          status: 'success'
        selected_contents:
          - query: '吴建军 6G转入产业实战阶段'
            query_type: 'keyword'
            url: 'https://example.com/6g'
            title: '论坛观点：6G进入实战'
            summary: '补充摘要'
            text: '补充正文'
            source: 'aliyun'
            status: 'success'
        analysis:
          summary: '这篇内容核心在于6G进入产业实战阶段。'
          core_points:
            - '6G判断从技术验证转向产业实战。'
          new_facts:
            - '论坛明确给出产业实战阶段判断。'
          entities:
            - '吴建军'
          signals:
            - '6G产业化'
          risk_or_uncertainty:
            - '节奏仍待后续验证。'
          why_it_matters: '说明6G主题的产业化表述在增强。'
          layer_notes:
            - '补充链接相关性较高。'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "step5.yaml"
            input_path.write_text(payload, encoding="utf-8")

            analysis_input = load_content_analysis_inputs(input_path)
            missing = collect_missing_analysis_fields(analysis_input)

        self.assertEqual(analysis_input.categories[0].items[0].original_content.title, "未来移动通信论坛吴建军：6G已转入产业实战阶段")
        self.assertEqual(analysis_input.categories[0].items[0].selected_contents[0].document.title, "论坛观点：6G进入实战")
        self.assertEqual(analysis_input.categories[0].items[0].original_published_at, "2026-03-31")
        self.assertEqual(missing, [])


class LayerIssueTests(unittest.TestCase):
    def test_generate_layer_issues_reports_unfilled_keywords_and_content_fallback(self) -> None:
        raw_csv = """统计日期,栏目键,栏目名称,栏目链接,栏目文章数,栏目热点词,文章标题,发布时间,关键词,摘要,文章链接
2026-03-31,home,首页,https://www.c114.com.cn/,1,,未来移动通信论坛吴建军：6G已转入产业实战阶段,2026-03-31,,摘要,https://www.c114.com.cn/news/41/a1307790.html
"""
        checklist_yaml = """report_date: '2026-03-31'
prompt_path: '/tmp/prompt.md'
categories:
  - topic: '6G与下一代通信'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        channel: '首页'
        url: 'https://www.c114.com.cn/news/41/a1307790.html'
        keywords:
"""
        search_yaml = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: '6G与下一代通信'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        queries:
        search_results:
        selected_results:
"""
        content_yaml = """report_date: '2026-03-31'
input_path: '/tmp/search_results.yaml'
generated_at: '2026-04-01T17:37:59'
categories:
  - topic: '6G与下一代通信'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: '6G与下一代通信'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_content:
          url: 'https://www.c114.com.cn/news/41/a1307790.html'
          domain: 'www.c114.com.cn'
          content_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          content_summary: '摘要'
          content_text: '正文内容'
          content_source: 'html_fallback'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            raw_csv_path = tmp / "c114_hot_topics.csv"
            raw_csv_path.write_text(raw_csv, encoding="utf-8")
            checklist_path = tmp / "checklist.yaml"
            checklist_path.write_text(checklist_yaml, encoding="utf-8")
            search_path = tmp / "search.yaml"
            search_path.write_text(search_yaml, encoding="utf-8")
            content_path = tmp / "content.yaml"
            content_path.write_text(content_yaml, encoding="utf-8")
            issues = generate_layer_issues(
                report_date="2026-03-31",
                raw_csv_path=raw_csv_path,
                checklist_path=checklist_path,
                search_results_path=search_path,
                content_path=content_path,
            )

        self.assertIn("raw_fetch", issues)
        self.assertIn("search_checklist", issues)
        self.assertIn("search_results", issues)
        self.assertIn("content_fetch", issues)
        self.assertEqual(issues["search_checklist"][0]["code"], "keywords_unfilled")
        self.assertEqual(issues["content_fetch"][0]["code"], "html_fallback_used")
        self.assertEqual(issues["search_results"][0]["code"], "selected_results_empty")

    def test_generate_layer_issues_reports_selected_normal_sources_instead_of_untrusted_media(self) -> None:
        search_yaml = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: '6G与下一代通信'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        queries:
        search_results:
        selected_results:
          - query: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
            query_type: 'title'
            result_title: '转载稿'
            url: 'https://finance.sina.com.cn/example'
            domain: 'finance.sina.com.cn'
            published_at: '2026-03-31'
            snippet: '摘要'
            score: 1.0
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
              - '吴建军'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            search_path = Path(tmp_dir) / "search.yaml"
            search_path.write_text(search_yaml, encoding="utf-8")
            issues = generate_layer_issues(
                report_date="2026-03-31",
                search_results_path=search_path,
            )

        self.assertEqual(issues["search_results"][0]["code"], "selected_normal_sources")

    def test_generate_layer_issues_reports_filtered_selected_domains_from_search_results(self) -> None:
        search_yaml = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '《对话》TM Forum CEO Nik Willetts | AI+6G 重塑电信格局，中国优势将决定未来十年走向'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/video/5918/a1304983.html'
        queries:
        search_results:
        selected_results:
          - query: 'TM Forum Nik Willetts AI+6G'
            query_type: 'keyword'
            result_title: '噪音站点'
            url: 'http://www.maxyic.com/noise'
            domain: 'www.maxyic.com'
            published_at: ''
            snippet: ''
            score: 0.1000
            is_official: false
            source_tier: 'blocked'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            search_path = Path(tmp_dir) / "search.yaml"
            search_path.write_text(search_yaml, encoding="utf-8")
            issues = generate_layer_issues(
                report_date="2026-03-31",
                search_results_path=search_path,
            )

        self.assertEqual(issues["search_results"][0]["code"], "filtered_selected_domains")

    def test_generate_layer_issues_reports_keep_level_distribution(self) -> None:
        search_yaml = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '测试标题'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/test.html'
        original_published_at: '2026-03-31'
        queries:
        search_results:
        selected_results:
          - query: 'q1'
            query_type: 'title'
            result_title: '强保留'
            url: 'https://example.com/1'
            domain: 'example.com'
            published_at: '2026-03-31'
            snippet: ''
            score: 1.0
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
            ai_review:
              status: 'reviewed'
              keep_level: 'strong'
              reason: '同一事件'
              relevance_note: '主体一致'
              value_type: '新增事实'
          - query: 'q2'
            query_type: 'keyword'
            result_title: '弱保留'
            url: 'https://example.com/2'
            domain: 'example.com'
            published_at: '2026-03-31'
            snippet: ''
            score: 0.9
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
            ai_review:
              status: 'reviewed'
              keep_level: 'weak'
              reason: '背景补充'
              relevance_note: '同主题'
              value_type: '背景补充'
          - query: 'q3'
            query_type: 'keyword'
            result_title: '丢弃'
            url: 'https://example.com/3'
            domain: 'example.com'
            published_at: '2026-03-31'
            snippet: ''
            score: 0.8
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
            ai_review:
              status: 'reviewed'
              keep_level: 'drop'
              reason: '跑偏'
              relevance_note: '主体不一致'
              value_type: '跑偏'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            search_path = Path(tmp_dir) / "search.yaml"
            search_path.write_text(search_yaml, encoding="utf-8")
            issues = generate_layer_issues(
                report_date="2026-03-31",
                search_results_path=search_path,
            )

        codes = [issue["code"] for issue in issues["search_results"]]
        self.assertIn("keep_level_distribution", codes)
