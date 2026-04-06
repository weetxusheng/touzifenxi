from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from c114.c114_content_analysis import (
    generate_layer_issues,
    load_content_analysis_inputs,
    render_brief_markdown,
    render_content_analysis_yaml,
    resolve_content_analysis_output_paths,
)
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
