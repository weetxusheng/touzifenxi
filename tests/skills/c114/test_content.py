from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

from c114.c114_content import (
    AliyunSearchDocument,
    FetchResult,
    SearchContentArticleInput,
    build_aliyun_fetch_result,
    extract_c114_article_text,
    fetch_article_contents,
    load_search_results_yaml,
    normalize_request_url,
    render_content_yaml,
    resolve_content_output_paths,
    run_content_fetch_workflow,
)
from c114.c114_search import SearchResult
from touzifenxi.settings import AppPaths


class ContentPathTests(unittest.TestCase):
    def test_resolve_content_output_paths_uses_same_run_directory(self) -> None:
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

        resolved = resolve_content_output_paths(
            app_paths,
            date(2026, 3, 31),
            input_override="reports/c114_report/c114_search_202604011325/c114_step_3_search_results_20260331.yaml",
        )

        self.assertEqual(
            resolved.input_path,
            Path("/repo/reports/c114_report/c114_search_202604011325/c114_step_3_search_results_20260331.yaml"),
        )
        self.assertEqual(
            resolved.output_path,
            Path("/repo/reports/c114_report/c114_search_202604011325/c114_step_4_content_20260331.yaml"),
        )


class SearchResultsParsingTests(unittest.TestCase):
    def test_load_search_results_yaml_reads_original_and_selected_links(self) -> None:
        payload = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_published_at: '2026-03-31'
        queries:
        search_results:
        selected_results:
          - query: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
            query_type: 'title'
            result_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
            url: 'https://www.c114.com.cn/news/41/a1307790.html'
            domain: 'www.c114.com.cn'
            published_at: '2026-03-31'
            snippet: '摘要'
            score: 1.0000
            is_official: false
            source_tier: 'normal'
            extract_status: 'success'
            extract_text: '正文'
            matched_terms:
              - '吴建军'
          - query: '未来移动通信论坛 6G实战阶段'
            query_type: 'keyword'
            result_title: '论坛观点：6G进入实战'
            url: 'https://example.com/6g'
            domain: 'example.com'
            published_at: '2026-03-31'
            snippet: '补充摘要'
            score: 0.9000
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
              - '6G'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")

            workflow = load_search_results_yaml(input_path)

        self.assertEqual(workflow.report_date, "2026-03-31")
        self.assertEqual(len(workflow.categories), 1)
        item = workflow.categories[0].items[0]
        self.assertEqual(item.original_url, "https://www.c114.com.cn/news/41/a1307790.html")
        self.assertEqual(item.original_published_at, "2026-03-31")
        self.assertEqual(len(item.selected_results), 2)
        self.assertEqual(item.selected_results[1].url, "https://example.com/6g")


class ContentFetchWorkflowTests(unittest.TestCase):
    def test_fetch_article_contents_only_keeps_strong_and_weak_selected_results(self) -> None:
        article = SearchContentArticleInput(
            topic="AI与算力",
            channel="首页",
            original_title="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            original_url="https://www.c114.com.cn/news/41/a1307790.html",
            original_published_at="2026-03-31",
            selected_results=[
                SearchResult(
                    query="原标题",
                    query_type="title",
                    result_title="强保留",
                    url="https://example.com/strong",
                    domain="example.com",
                    published_at="2026-03-31",
                    snippet="",
                    score=1.0,
                    is_official=False,
                    source_tier="normal",
                    matched_terms=["吴建军"],
                    extract_text="",
                    extract_status="not_requested",
                    review_status="reviewed",
                    keep_level="strong",
                    review_reason="同一事件",
                    relevance_note="主体一致",
                    value_type="新增事实",
                ),
                SearchResult(
                    query="关键词",
                    query_type="keyword",
                    result_title="弱保留",
                    url="https://example.com/weak",
                    domain="example.com",
                    published_at="2026-03-31",
                    snippet="",
                    score=0.9,
                    is_official=False,
                    source_tier="normal",
                    matched_terms=["6G"],
                    extract_text="",
                    extract_status="not_requested",
                    review_status="reviewed",
                    keep_level="weak",
                    review_reason="背景补充",
                    relevance_note="同主题",
                    value_type="背景补充",
                ),
                SearchResult(
                    query="关键词",
                    query_type="keyword",
                    result_title="应丢弃",
                    url="https://example.com/drop",
                    domain="example.com",
                    published_at="2026-03-31",
                    snippet="",
                    score=0.8,
                    is_official=False,
                    source_tier="normal",
                    matched_terms=["6G"],
                    extract_text="",
                    extract_status="not_requested",
                    review_status="reviewed",
                    keep_level="drop",
                    review_reason="主体跑偏",
                    relevance_note="不是同一事件",
                    value_type="跑偏",
                ),
            ],
        )

        def fake_fetch(url: str) -> FetchResult:
            return FetchResult(
                url=url,
                domain=url.split("/")[2],
                content_title=f"title:{url}",
                content_summary=f"summary:{url}",
                content_text=f"text:{url}",
                fetch_status="success",
                fetch_error="",
            )

        content_payload = fetch_article_contents(
            article,
            report_date="2026-03-31",
            fetcher=fake_fetch,
            allowed_keep_levels=("strong", "weak"),
        )

        self.assertEqual(
            [item.url for item in content_payload.selected_contents],
            [
                "https://example.com/strong",
                "https://example.com/weak",
            ],
        )

    def test_fetch_article_contents_dedupes_requests_but_keeps_both_sections(self) -> None:
        payload = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        channel: '首页'
        original_url: 'https://same.example.com/article'
        queries:
        search_results:
        selected_results:
          - query: '原标题'
            query_type: 'title'
            result_title: '原始站点'
            url: 'https://same.example.com/article'
            domain: 'same.example.com'
            published_at: '2026-03-31'
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
              value_type: '新增事实'
          - query: '关键词'
            query_type: 'keyword'
            result_title: '补充站点'
            url: 'https://other.example.com/article'
            domain: 'other.example.com'
            published_at: '2026-03-31'
            snippet: ''
            score: 0.9000
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
            ai_review:
              status: 'reviewed'
              keep_level: 'strong'
              reason: '同主题'
              relevance_note: '可补充'
              value_type: '背景补充'
"""
        calls: list[str] = []

        def fake_fetch(url: str) -> FetchResult:
            calls.append(url)
            return FetchResult(
                url=url,
                domain=url.split("/")[2],
                content_title=f"title:{url}",
                content_summary=f"summary:{url}",
                content_text=f"text:{url}",
                fetch_status="success",
                fetch_error="",
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")

            workflow = load_search_results_yaml(input_path)
            article_payload = workflow.categories[0].items[0]
            content_payload = fetch_article_contents(article_payload, report_date="2026-03-31", fetcher=fake_fetch)

        self.assertEqual(
            calls,
            [
                "https://same.example.com/article",
                "https://other.example.com/article",
            ],
        )
        self.assertEqual(content_payload.original_content.url, "https://same.example.com/article")
        self.assertEqual(len(content_payload.selected_contents), 2)
        self.assertEqual(content_payload.selected_contents[0].url, "https://same.example.com/article")

    def test_run_content_fetch_workflow_keeps_failed_entries(self) -> None:
        payload = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        channel: '首页'
        original_url: 'https://fail.example.com/article'
        queries:
        search_results:
        selected_results:
          - query: '关键词'
            query_type: 'keyword'
            result_title: '补充站点'
            url: 'https://ok.example.com/article'
            domain: 'ok.example.com'
            published_at: '2026-03-31'
            snippet: ''
            score: 0.9000
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
            ai_review:
              status: 'reviewed'
              keep_level: 'strong'
              reason: '可保留'
              relevance_note: '同主题'
              value_type: '背景补充'
"""

        def fake_fetch(url: str) -> FetchResult:
            if "fail" in url:
                return FetchResult(
                    url=url,
                    domain=url.split("/")[2],
                    content_title="",
                    content_summary="",
                    content_text="",
                    fetch_status="failed",
                    fetch_error="network failed",
                )
            return FetchResult(
                url=url,
                domain=url.split("/")[2],
                content_title="ok",
                content_summary="summary",
                content_text="text",
                fetch_status="success",
                fetch_error="",
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")

            class FakeAliyun:
                def search(self, query_text: str) -> list[AliyunSearchDocument]:
                    return []

            workflow = run_content_fetch_workflow(
                input_path=input_path,
                report_date="2026-03-31",
                fetcher=fake_fetch,
                aliyun_client=FakeAliyun(),  # type: ignore[arg-type]
            )
            rendered = render_content_yaml(workflow)

        item = workflow.categories[0].items[0]
        self.assertEqual(item.original_content.fetch_status, "failed")
        self.assertEqual(item.selected_contents[0].fetch_status, "success")
        self.assertIn("fetch_error: 'network failed'", rendered)

    def test_prefers_aliyun_content_when_document_matches_target(self) -> None:
        fallback_calls: list[str] = []
        document = AliyunSearchDocument(
            link="https://www.c114.com.cn/news/41/a1307790.html",
            title="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            published_at="2026-03-31",
            main_text="阿里云正文",
            rich_main_body="",
        )

        def fallback_fetch(url: str) -> FetchResult:
            fallback_calls.append(url)
            return FetchResult(
                url=url,
                domain="www.c114.com.cn",
                content_title="fallback",
                content_summary="fallback",
                content_text="fallback",
                fetch_status="success",
                fetch_error="",
                content_source="html_fallback",
            )

        result = build_aliyun_fetch_result(
            target_url="https://www.c114.com.cn/news/41/a1307790.html",
            query_text="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            target_title="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            report_date="2026-03-31",
            documents=[document],
            fallback_fetcher=fallback_fetch,
        )

        self.assertEqual(result.content_source, "aliyun")
        self.assertEqual(result.content_text, "阿里云正文")
        self.assertEqual(fallback_calls, [])

    def test_falls_back_when_aliyun_documents_do_not_match_target(self) -> None:
        fallback_calls: list[str] = []
        document = AliyunSearchDocument(
            link="https://example.com/other",
            title="其他文章标题",
            published_at="2026-03-31",
            main_text="错误正文",
            rich_main_body="",
        )

        def fallback_fetch(url: str) -> FetchResult:
            fallback_calls.append(url)
            return FetchResult(
                url=url,
                domain="www.c114.com.cn",
                content_title="fallback",
                content_summary="fallback",
                content_text="fallback text",
                fetch_status="success",
                fetch_error="",
                content_source="html_fallback",
            )

        result = build_aliyun_fetch_result(
            target_url="https://www.c114.com.cn/news/41/a1307790.html",
            query_text="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            target_title="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            report_date="2026-03-31",
            documents=[document],
            fallback_fetcher=fallback_fetch,
        )

        self.assertEqual(result.content_source, "html_fallback")
        self.assertEqual(result.content_text, "fallback text")
        self.assertEqual(fallback_calls, ["https://www.c114.com.cn/news/41/a1307790.html"])

    def test_fetch_once_falls_back_when_aliyun_search_raises(self) -> None:
        calls: list[str] = []

        def fallback_fetch(url: str) -> FetchResult:
            calls.append(url)
            return FetchResult(
                url=url,
                domain="www.c114.com.cn",
                content_title="fallback",
                content_summary="fallback summary",
                content_text="fallback text",
                fetch_status="success",
                fetch_error="",
                content_source="html_fallback",
            )

        class FailingAliyun:
            def search(self, query_text: str) -> list[AliyunSearchDocument]:
                raise RuntimeError("阿里云 IQS 请求失败: 429 throttled")

        from c114.c114_content import fetch_once

        result = fetch_once(
            url="https://www.c114.com.cn/news/41/a1307790.html",
            query_text="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            target_title="未来移动通信论坛吴建军：6G已转入产业实战阶段",
            report_date="2026-03-31",
            fetcher=fallback_fetch,
            cache={},
            aliyun_client=FailingAliyun(),  # type: ignore[arg-type]
        )

        self.assertEqual(result.content_source, "html_fallback")
        self.assertEqual(result.content_text, "fallback text")
        self.assertEqual(calls, ["https://www.c114.com.cn/news/41/a1307790.html"])

    def test_run_content_fetch_workflow_processes_articles_concurrently(self) -> None:
        payload = """report_date: '2026-03-31'
provider: 'auto'
input_path: '/tmp/checklist.yaml'
generated_at: '2026-04-01T13:26:15'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '文章一'
        channel: '首页'
        original_url: 'https://example.com/article-1'
        queries:
        search_results:
        selected_results:
      - original_title: '文章二'
        channel: '首页'
        original_url: 'https://example.com/article-2'
        queries:
        search_results:
        selected_results:
"""
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_fetch(url: str) -> FetchResult:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return FetchResult(
                url=url,
                domain=url.split("/")[2],
                content_title="ok",
                content_summary="summary",
                content_text="text",
                fetch_status="success",
                fetch_error="",
            )

        class FakeAliyun:
            def search(self, query_text: str) -> list[AliyunSearchDocument]:
                return []

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")
            run_content_fetch_workflow(
                input_path=input_path,
                report_date="2026-03-31",
                fetcher=fake_fetch,
                aliyun_client=FakeAliyun(),  # type: ignore[arg-type]
            )

        self.assertGreaterEqual(peak, 2)

    def test_skips_selected_content_for_blocked_domain(self) -> None:
        payload = """report_date: '2026-03-31'
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
            result_title: 'AI与6G融合驱动自主网络新纪元：中国实践引领全球标准升级'
            url: 'http://www.maxyic.com/Mini-Circuitsxw/aiy6grhqdzzwlxjy_zgs.html'
            domain: 'www.maxyic.com'
            published_at: ''
            snippet: ''
            score: 0.9000
            is_official: false
            source_tier: 'normal'
            extract_status: 'not_requested'
            extract_text: ''
            matched_terms:
"""
        calls: list[str] = []

        def fake_fetch(url: str) -> FetchResult:
            calls.append(url)
            return FetchResult(
                url=url,
                domain=url.split("/")[2],
                content_title="ok",
                content_summary="summary",
                content_text="text",
                fetch_status="success",
                fetch_error="",
            )

        class FakeAliyun:
            def search(self, query_text: str) -> list[AliyunSearchDocument]:
                return []

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")
            workflow = run_content_fetch_workflow(
                input_path=input_path,
                report_date="2026-03-31",
                fetcher=fake_fetch,
                aliyun_client=FakeAliyun(),  # type: ignore[arg-type]
            )

        item = workflow.categories[0].items[0]
        self.assertEqual(calls, ["https://www.c114.com.cn/video/5918/a1304983.html"])
        self.assertEqual(item.selected_contents, [])


class C114ExtractionTests(unittest.TestCase):
    def test_normalize_request_url_encodes_non_ascii_path(self) -> None:
        url = "https://baike.baidu.com/item/自智网络/60273252"

        normalized = normalize_request_url(url)

        self.assertEqual(normalized, "https://baike.baidu.com/item/%E8%87%AA%E6%99%BA%E7%BD%91%E7%BB%9C/60273252")

    def test_extract_c114_article_text_keeps_body_and_cuts_shell_sections(self) -> None:
        html = """
        <html><body>
        <div class="menu menu_article mt">导航</div>
        <div class="article_text">
          <div class="text" id="text1">
            <p>C114讯 3月27日消息（苡臻）第一段。</p>
            <p>第二段。</p>
          </div>
        </div>
        <div class="related_links"><div class="related_links_tit">相关链接</div></div>
        <div class="bo new_video"><span class="bo_tit">热门文章</span></div>
        </body></html>
        """

        text = extract_c114_article_text(html)

        self.assertIn("第一段", text)
        self.assertIn("第二段", text)
        self.assertNotIn("导航", text)
        self.assertNotIn("相关链接", text)
        self.assertNotIn("热门文章", text)
