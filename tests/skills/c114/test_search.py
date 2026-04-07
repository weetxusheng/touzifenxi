from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

from c114.c114_intelligence import provider_stats_name
from c114.c114_search import (
    ArticleSearchPayload,
    AutoSearchClient,
    QueryResultBucket,
    SearchArticleInput,
    SearchCategoryPayload,
    SearchOutputPaths,
    SearchQuery,
    SearchResult,
    SearchWorkflowPayload,
    apply_search_review_result,
    auto_review_search_payload,
    build_search_queries,
    choose_search_provider,
    classify_domain,
    enrich_selected_results,
    ensure_search_checklist_keywords,
    filter_recent_results,
    infer_published_at,
    load_search_checklist_yaml,
    normalize_google_results,
    normalize_metaso_results,
    rank_search_results,
    render_search_results_yaml,
    resolve_search_output_paths,
    run_search_workflow,
    save_search_results,
    select_results_for_extract,
    unique_search_results,
    validate_search_checklist_items,
)
from touzifenxi.settings import AppPaths


class ChecklistParsingTests(unittest.TestCase):
    def test_loads_article_inputs_from_search_checklist_yaml(self) -> None:
        payload = """report_date: '2026-03-30'
prompt_path: '/tmp/prompt.md'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '孙正义借巨资押注OpenAI 争夺下一代算力入口'
        channel: 'Cloud&AI'
        url: 'https://www.c114.com.cn/ai/5339/a1307668.html'
        original_published_at: '2026-03-30'
        keywords:
          - '孙正义 OpenAI'
          - 'OpenAI 算力入口'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "checklist.yaml"
            input_path.write_text(payload, encoding="utf-8")

            report_date, items = load_search_checklist_yaml(input_path)

        self.assertEqual(report_date, "2026-03-30")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].topic, "AI与算力")
        self.assertEqual(items[0].keywords, ["孙正义 OpenAI", "OpenAI 算力入口"])
        self.assertEqual(items[0].original_published_at, "2026-03-30")

    def test_builds_three_queries_in_fixed_order(self) -> None:
        article = SearchArticleInput(
            topic="AI与算力",
            channel="Cloud&AI",
            original_title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
            original_url="https://www.c114.com.cn/ai/5339/a1307668.html",
            original_published_at="2026-03-30",
            keywords=["孙正义 OpenAI", "OpenAI 算力入口"],
        )

        queries = build_search_queries(article)

        self.assertEqual(
            queries,
            [
                SearchQuery(query_type="title", value="孙正义借巨资押注OpenAI 争夺下一代算力入口"),
                SearchQuery(query_type="keyword", value="孙正义 OpenAI"),
                SearchQuery(query_type="keyword", value="OpenAI 算力入口"),
            ],
        )

    def test_rejects_checklist_items_without_two_keywords(self) -> None:
        article = SearchArticleInput(
            topic="AI与算力",
            channel="Cloud&AI",
            original_title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
            original_url="https://www.c114.com.cn/ai/5339/a1307668.html",
            original_published_at="2026-03-30",
            keywords=[],
        )

        with self.assertRaisesRegex(ValueError, "请先完成 step 2 自动关键词生成后再运行 c114-search"):
            validate_search_checklist_items([article])

    def test_auto_fills_missing_keywords_when_llm_client_is_available(self) -> None:
        class FakeLLMClient:
            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.system_prompt = system_prompt
                self.user_prompt = user_prompt
                return {"keywords": ["中国联通 智能体互联网", "智能体互联网 最后一块拼图"]}

        article = SearchArticleInput(
            topic="AI与算力",
            channel="Cloud&AI",
            original_title="中国联通曹畅：智能体互联网补齐AI时代互联网的最后一块拼图",
            original_url="https://www.c114.com.cn/test",
            original_published_at="2026-04-07",
            keywords=[],
        )

        completed = ensure_search_checklist_keywords([article], FakeLLMClient())

        self.assertEqual(
            completed[0].keywords,
            ["中国联通 智能体互联网", "智能体互联网 最后一块拼图"],
        )


class SearchRankingTests(unittest.TestCase):
    def test_unique_search_results_prefers_first_normalized_url(self) -> None:
        results = [
            SearchResult(
                query="原标题",
                query_type="title",
                result_title="OpenAI 新闻",
                url="https://example.com/news?id=1",
                domain="example.com",
                published_at="2026-03-30",
                snippet="a",
                score=0.9,
                is_official=False,
                source_tier="normal",
                matched_terms=["OpenAI"],
                extract_text="",
                extract_status="not_requested",
            ),
            SearchResult(
                query="关键词",
                query_type="keyword",
                result_title="OpenAI 新闻",
                url="https://example.com/news?id=1&utm_source=test",
                domain="example.com",
                published_at="2026-03-30",
                snippet="b",
                score=0.8,
                is_official=False,
                source_tier="normal",
                matched_terms=["OpenAI"],
                extract_text="",
                extract_status="not_requested",
            ),
        ]

        deduped = unique_search_results(results)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].snippet, "a")

    def test_rank_search_results_prefers_official_then_title_match_and_recency(self) -> None:
        title_query = SearchQuery(query_type="title", value="中国信科陈山枝 NTN赋能卫星互联网")
        keyword_query = SearchQuery(query_type="keyword", value="卫星互联网 规模经济促进普惠")
        results = [
            SearchResult(
                query=keyword_query.value,
                query_type=keyword_query.query_type,
                result_title="媒体转述：中国信科谈卫星互联网",
                url="https://news.example.com/a",
                domain="news.example.com",
                published_at="2026-03-30",
                snippet="卫星互联网 普惠",
                score=0.95,
                is_official=False,
                source_tier="normal",
                matched_terms=["卫星互联网"],
                extract_text="",
                extract_status="not_requested",
            ),
            SearchResult(
                query=title_query.value,
                query_type=title_query.query_type,
                result_title="中国信科陈山枝：NTN赋能卫星互联网，以规模经济促进普惠",
                url="https://www.datang.com.cn/article",
                domain="www.datang.com.cn",
                published_at="2026-03-29",
                snippet="中国信科 官方内容",
                score=0.75,
                is_official=True,
                source_tier="official",
                matched_terms=["中国信科陈山枝", "卫星互联网"],
                extract_text="",
                extract_status="not_requested",
            ),
        ]

        ranked = rank_search_results(results, report_date=date(2026, 3, 30), original_title=title_query.value)

        self.assertEqual(ranked[0].domain, "www.datang.com.cn")

    def test_select_results_for_extract_keeps_top_n(self) -> None:
        results = [
            SearchResult(
                query="q",
                query_type="title",
                result_title=f"title-{index}",
                url=f"https://example.com/{index}",
                domain="example.com",
                published_at="2026-03-30",
                snippet="x",
                score=0.9,
                is_official=False,
                source_tier="normal",
                matched_terms=["x"],
                extract_text="",
                extract_status="not_requested",
            )
            for index in range(6)
        ]

        selected = select_results_for_extract(results, extract_limit=5)

        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[-1].url, "https://example.com/4")

    def test_filter_recent_results_keeps_only_results_within_recent_window(self) -> None:
        results = [
            SearchResult(
                query="q",
                query_type="title",
                result_title="recent",
                url="https://example.com/recent",
                domain="example.com",
                published_at="2026-03-15",
                snippet="x",
                score=0.9,
                is_official=False,
                source_tier="normal",
                matched_terms=["x"],
                extract_text="",
                extract_status="not_requested",
            ),
            SearchResult(
                query="q",
                query_type="title",
                result_title="old",
                url="https://example.com/old",
                domain="example.com",
                published_at="2026-01-01",
                snippet="x",
                score=0.9,
                is_official=False,
                source_tier="normal",
                matched_terms=["x"],
                extract_text="",
                extract_status="not_requested",
            ),
            SearchResult(
                query="q",
                query_type="title",
                result_title="missing",
                url="https://example.com/missing",
                domain="example.com",
                published_at="",
                snippet="x",
                score=0.9,
                is_official=False,
                source_tier="normal",
                matched_terms=["x"],
                extract_text="",
                extract_status="not_requested",
            ),
        ]

        filtered = filter_recent_results(results, report_date=date(2026, 3, 31), max_age_days=30)

        self.assertEqual([item.url for item in filtered], ["https://example.com/recent"])

    def test_infer_published_at_from_url_or_snippet(self) -> None:
        self.assertEqual(
            infer_published_at(
                "https://finance.sina.com.cn/roll/2026-04-01/doc-inhsyfvi5070741.shtml",
                "算力怎么干、通信空间在哪里？中兴通讯有这些研判",
                "",
            ),
            "2026-04-01",
        )
        self.assertEqual(
            infer_published_at(
                "https://example.com/article",
                "示例标题",
                "发布时间：2026年4月2日 08:00",
            ),
            "2026-04-02",
        )

    def test_classify_domain_marks_official_normal_and_blocked_sources(self) -> None:
        domain_config = {
            "official_domains": ["corp.example.com"],
            "blocked_domains": ["blocked.example.com"],
        }

        self.assertEqual(classify_domain("sub.corp.example.com", domain_config), (True, "official"))
        self.assertEqual(classify_domain("news.example.com", domain_config), (False, "normal"))
        self.assertEqual(classify_domain("blocked.example.com", domain_config), (False, "blocked"))
        self.assertEqual(
            classify_domain("www.cnjjwb.com", {"official_domains": [], "blocked_domains": ["cnjjwb.com"]}),
            (False, "blocked"),
        )


class SearchProviderRoutingTests(unittest.TestCase):
    def test_choose_search_provider_uses_tavily_for_chinese_query_by_default(self) -> None:
        self.assertEqual(
            choose_search_provider(SearchQuery(query_type="title", value="朱敏 张教 空天地一体化")), "tavily"
        )

    def test_choose_search_provider_uses_tavily_for_english_query(self) -> None:
        self.assertEqual(
            choose_search_provider(SearchQuery(query_type="keyword", value="OpenAI compute platform")), "tavily"
        )

    def test_choose_search_provider_honors_forced_baidu_provider(self) -> None:
        self.assertEqual(
            choose_search_provider(
                SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"),
                forced_provider="baidu",
            ),
            "baidu",
        )

    def test_choose_search_provider_honors_forced_google_provider(self) -> None:
        self.assertEqual(
            choose_search_provider(
                SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"),
                forced_provider="google",
            ),
            "google",
        )

    def test_normalize_metaso_results_maps_webpages_shape(self) -> None:
        raw_results = [
            {
                "title": "6G洞见 | 朱敏&张教：空天地一体化，正迈入全新高速发展期",
                "link": "https://www.g6gconference.com/index/Details/index.html?id=1180",
                "score": "high",
                "snippet": "朱敏、张教：本次研讨会意义是多重的。",
                "date": "2026-01-26",
            }
        ]
        domain_config = {
            "official_domains": ["g6gconference.com"],
            "blocked_domains": [],
        }

        normalized = normalize_metaso_results(
            raw_results,
            query=SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"),
            original_title="6G洞见 | 朱敏&张教：空天地一体化，正迈入全新高速发展期",
            domain_config=domain_config,
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].url, "https://www.g6gconference.com/index/Details/index.html?id=1180")
        self.assertTrue(normalized[0].is_official)
        self.assertEqual(normalized[0].published_at, "2026-01-26")

    def test_normalize_baidu_results_maps_references_shape(self) -> None:
        from c114.c114_search import normalize_baidu_results

        raw_results = [
            {
                "url": "http://finance.sina.com.cn/tech/roll/2026-03-30/doc-inhstpia4138513.shtml",
                "title": "6G洞见 | 朱敏&张教:空天地一体化,正迈入全新高速发展期",
                "date": "2026-03-30 09:44:00",
                "snippet": "空天地一体化多模态融合通信技术的成熟，是6G实现全域无缝覆盖的关键支撑。",
                "rerank_score": 1,
                "authority_score": 0.5,
            }
        ]
        domain_config = {
            "official_domains": [],
            "blocked_domains": [],
        }

        normalized = normalize_baidu_results(
            raw_results,
            query=SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"),
            original_title="6G洞见 | 朱敏&张教：空天地一体化，正迈入全新高速发展期",
            domain_config=domain_config,
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].domain, "finance.sina.com.cn")
        self.assertEqual(normalized[0].source_tier, "normal")
        self.assertEqual(normalized[0].published_at, "2026-03-30")

    def test_normalize_google_results_maps_browser_shape(self) -> None:
        raw_results = [
            {
                "url": "https://finance.sina.com.cn/tech/roll/2025-12-29/doc-inhemysr0209956.shtml",
                "title": "中国联通曹畅：智能体互联网补齐AI时代互联网的“最后一块拼图”",
                "snippet": "中国联通曹畅表示，智能体互联网补齐AI时代互联网的最后一块拼图。",
                "score": 0.5,
            }
        ]
        domain_config = {
            "official_domains": [],
            "blocked_domains": [],
        }

        normalized = normalize_google_results(
            raw_results,
            query=SearchQuery(query_type="keyword", value="中国联通曹畅 智能体互联网"),
            original_title="《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”",
            domain_config=domain_config,
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].domain, "finance.sina.com.cn")
        self.assertEqual(normalized[0].source_tier, "normal")
        self.assertEqual(normalized[0].snippet, "中国联通曹畅表示，智能体互联网补齐AI时代互联网的最后一块拼图。")

    def test_rendered_yaml_includes_ai_review_template_only_when_requested(self) -> None:
        from c114.c114_search import render_result_list

        rendered_without_review = render_result_list(
            [
                SearchResult(
                    query="测试查询",
                    query_type="keyword",
                    result_title="测试结果",
                    url="https://example.com/a",
                    domain="example.com",
                    published_at="2026-04-02",
                    snippet="摘要",
                    score=0.8,
                    is_official=False,
                    source_tier="normal",
                    matched_terms=["测试"],
                    extract_text="",
                    extract_status="not_requested",
                )
            ],
            indent="          ",
        )
        rendered_with_review = render_result_list(
            [
                SearchResult(
                    query="测试查询",
                    query_type="keyword",
                    result_title="测试结果",
                    url="https://example.com/a",
                    domain="example.com",
                    published_at="2026-04-02",
                    snippet="摘要",
                    score=0.8,
                    is_official=False,
                    source_tier="normal",
                    matched_terms=["测试"],
                    extract_text="",
                    extract_status="not_requested",
                )
            ],
            indent="          ",
            include_ai_review=True,
        )

        text_without_review = "\n".join(rendered_without_review)
        text_with_review = "\n".join(rendered_with_review)
        self.assertNotIn("ai_review:", text_without_review)
        self.assertIn("ai_review:", text_with_review)
        self.assertIn("status: 'pending'", text_with_review)
        self.assertIn("keep_level: ''", text_with_review)
        self.assertIn("value_type: ''", text_with_review)

    def test_render_search_results_yaml_includes_review_prompt_metadata(self) -> None:
        payload = SearchWorkflowPayload(
            report_date="2026-04-02",
            provider="tavily",
            input_path=Path("/tmp/input.yaml"),
            generated_at="2026-04-02 12:00:00",
            categories=[
                SearchCategoryPayload(
                    topic="测试主题",
                    items=[
                        ArticleSearchPayload(
                            topic="测试主题",
                            channel="首页",
                            original_title="测试标题",
                            original_url="https://www.c114.com.cn/test",
                            original_published_at="2026-04-02",
                            queries=[
                                QueryResultBucket(query="测试标题", query_type="title", provider="tavily", results=[])
                            ],
                            search_results=[],
                            selected_results=[],
                        )
                    ],
                )
            ],
        )

        rendered = render_search_results_yaml(payload)

        self.assertIn("review_prompt_path:", rendered)
        self.assertIn("review_instructions:", rendered)
        self.assertIn("必须逐条填写所有 selected_results", rendered)

    def test_apply_search_review_result_requires_full_fields(self) -> None:
        result = SearchResult(
            query="原标题",
            query_type="title",
            result_title="外部文章",
            url="https://example.com/a",
            domain="example.com",
            published_at="2026-04-07",
            snippet="摘要",
            score=0.9,
            is_official=False,
            source_tier="normal",
            matched_terms=["中国联通"],
            extract_text="补充正文",
            extract_status="success",
        )

        completed = apply_search_review_result(
            result,
            {
                "keep_level": "strong",
                "reason": "标题与主体一致，且补充了新增动作。",
                "relevance_note": "属于同一事件的外部验证。",
                "value_type": "新增事实",
            },
        )

        self.assertEqual(completed.review_status, "reviewed")
        self.assertEqual(completed.keep_level, "strong")

    def test_auto_review_search_payload_fills_all_selected_results(self) -> None:
        class FakeLLMClient:
            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                return {
                    "keep_level": "weak",
                    "reason": "与主题高度相关，可作为背景补充。",
                    "relevance_note": "不是同一事件，但对主题判断有帮助。",
                    "value_type": "背景补充",
                }

        payload = SearchWorkflowPayload(
            report_date="2026-04-07",
            provider="tavily",
            input_path=Path("/tmp/input.yaml"),
            generated_at="2026-04-07T10:00:00",
            categories=[
                SearchCategoryPayload(
                    topic="AI与算力",
                    items=[
                        ArticleSearchPayload(
                            topic="AI与算力",
                            channel="首页",
                            original_title="标题A",
                            original_url="https://www.c114.com.cn/a",
                            original_published_at="2026-04-07",
                            queries=[],
                            search_results=[],
                            selected_results=[
                                SearchResult(
                                    query="原标题",
                                    query_type="title",
                                    result_title="外部文章A",
                                    url="https://example.com/a",
                                    domain="example.com",
                                    published_at="2026-04-07",
                                    snippet="摘要A",
                                    score=0.9,
                                    is_official=False,
                                    source_tier="normal",
                                    matched_terms=["A"],
                                    extract_text="补充正文A",
                                    extract_status="success",
                                )
                            ],
                        )
                    ],
                )
            ],
        )

        reviewed = auto_review_search_payload(payload, FakeLLMClient())

        result = reviewed.categories[0].items[0].selected_results[0]
        self.assertEqual(result.review_status, "reviewed")
        self.assertEqual(result.keep_level, "weak")

    def test_render_search_results_yaml_only_keeps_ai_review_under_selected_results(self) -> None:
        result = SearchResult(
            query="测试标题",
            query_type="title",
            result_title="测试结果",
            url="https://example.com/a",
            domain="example.com",
            published_at="2026-04-02",
            snippet="摘要",
            score=0.8,
            is_official=False,
            source_tier="normal",
            matched_terms=["测试"],
            extract_text="",
            extract_status="not_requested",
        )
        payload = SearchWorkflowPayload(
            report_date="2026-04-02",
            provider="tavily",
            input_path=Path("/tmp/input.yaml"),
            generated_at="2026-04-02 12:00:00",
            categories=[
                SearchCategoryPayload(
                    topic="测试主题",
                    items=[
                        ArticleSearchPayload(
                            topic="测试主题",
                            channel="首页",
                            original_title="测试标题",
                            original_url="https://www.c114.com.cn/test",
                            original_published_at="2026-04-02",
                            queries=[
                                QueryResultBucket(query="测试标题", query_type="title", provider="tavily", results=[result])
                            ],
                            search_results=[result],
                            selected_results=[result],
                        )
                    ],
                )
            ],
        )

        rendered = render_search_results_yaml(payload)

        self.assertEqual(rendered.count("ai_review:"), 1)

    def test_auto_search_client_routes_auto_mode_queries_to_tavily(self) -> None:
        class FakeTavily:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

            def extract(self, urls: list[str], query: str) -> dict[str, str]:
                return {}

        class FakeMetaso:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        class FakeBaidu:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        class FakeGoogle:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        tavily = FakeTavily()
        metaso = FakeMetaso()
        baidu = FakeBaidu()
        google = FakeGoogle()
        client = AutoSearchClient(  # type: ignore[arg-type]
            tavily_client=tavily, metaso_client=metaso, baidu_client=baidu, google_client=google
        )

        client.search(SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"), max_results=2)
        client.search(SearchQuery(query_type="keyword", value="OpenAI compute platform"), max_results=2)

        self.assertEqual(tavily.queries, ["朱敏 张教 空天地一体化", "OpenAI compute platform"])
        self.assertEqual(metaso.queries, [])
        self.assertEqual(baidu.queries, [])
        self.assertEqual(google.queries, [])

    def test_auto_search_client_falls_back_to_metaso_when_tavily_fails(self) -> None:
        class FakeTavily:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                raise RuntimeError("tavily failed")

            def extract(self, urls: list[str], query: str) -> dict[str, str]:
                return {}

        class FakeMetaso:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        class FakeBaidu:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                return []

        metaso = FakeMetaso()
        client = AutoSearchClient(
            tavily_client=FakeTavily(),  # type: ignore[arg-type]
            metaso_client=metaso,
            baidu_client=FakeBaidu(),  # type: ignore[arg-type]
        )

        provider, _ = client.search_with_provider(SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"), 2)

        self.assertEqual(provider, "metaso")
        self.assertEqual(metaso.queries, ["朱敏 张教 空天地一体化"])

    def test_auto_search_client_falls_back_to_baidu_when_tavily_and_metaso_fail(self) -> None:
        class FakeTavily:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                raise RuntimeError("tavily failed")

            def extract(self, urls: list[str], query: str) -> dict[str, str]:
                return {}

        class FakeMetaso:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                raise RuntimeError("metaso failed")

        class FakeBaidu:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        baidu = FakeBaidu()
        client = AutoSearchClient(
            tavily_client=FakeTavily(),  # type: ignore[arg-type]
            metaso_client=FakeMetaso(),  # type: ignore[arg-type]
            baidu_client=baidu,
        )

        provider, _ = client.search_with_provider(SearchQuery(query_type="title", value="朱敏 张教 空天地一体化"), 2)

        self.assertEqual(provider, "baidu")
        self.assertEqual(baidu.queries, ["朱敏 张教 空天地一体化"])

    def test_auto_search_client_honors_forced_metaso_provider(self) -> None:
        class FakeMetaso:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        metaso = FakeMetaso()
        client = AutoSearchClient(  # type: ignore[arg-type]
            tavily_client=None,
            metaso_client=metaso,
            baidu_client=None,
            provider_mode="metaso",
        )

        provider, _ = client.search_with_provider(SearchQuery(query_type="title", value="中国联通曹畅 智能体互联网"), 2)

        self.assertEqual(provider, "metaso")
        self.assertEqual(metaso.queries, ["中国联通曹畅 智能体互联网"])

    def test_auto_search_client_can_run_with_only_baidu_configured(self) -> None:
        class FakeBaidu:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        baidu = FakeBaidu()
        client = AutoSearchClient(  # type: ignore[arg-type]
            tavily_client=None,
            metaso_client=None,
            baidu_client=baidu,
        )

        provider, _ = client.search_with_provider(SearchQuery(query_type="title", value="中国联通曹畅 智能体互联网"), 2)

        self.assertEqual(provider, "baidu")
        self.assertEqual(baidu.queries, ["中国联通曹畅 智能体互联网"])

    def test_auto_search_client_routes_query_to_google_when_forced(self) -> None:
        class FakeTavily:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                return []

            def extract(self, urls: list[str], query: str) -> dict[str, str]:
                return {}

        class FakeMetaso:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                return []

        class FakeBaidu:
            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                return []

        class FakeGoogle:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                self.queries.append(query.value)
                return []

        google = FakeGoogle()
        client = AutoSearchClient(  # type: ignore[arg-type]
            tavily_client=FakeTavily(),
            metaso_client=FakeMetaso(),
            baidu_client=FakeBaidu(),
            google_client=google,
            provider_mode="google",
        )

        provider, _ = client.search_with_provider(SearchQuery(query_type="title", value="中国联通曹畅 智能体互联网"), 2)

        self.assertEqual(provider, "google")
        self.assertEqual(google.queries, ["中国联通曹畅 智能体互联网"])


class SearchOutputPathTests(unittest.TestCase):
    def test_resolve_search_output_paths_returns_consistent_defaults(self) -> None:
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

        resolved = resolve_search_output_paths(app_paths, date(2026, 3, 30))

        self.assertEqual(
            resolved.input_path, Path("/repo/reports/c114_report/c114_step_2_search_checklist_20260330.yaml")
        )
        self.assertEqual(
            resolved.output_path, Path("/repo/reports/c114_report/c114_step_3_search_results_20260330.yaml")
        )
        self.assertEqual(resolved.provider, "auto")


class SearchWorkflowTests(unittest.TestCase):
    def test_run_search_workflow_executes_article_queries_concurrently(self) -> None:
        payload = """report_date: '2026-03-30'
prompt_path: '/tmp/prompt.md'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '孙正义借巨资押注OpenAI 争夺下一代算力入口'
        channel: 'Cloud&AI'
        url: 'https://www.c114.com.cn/ai/5339/a1307668.html'
        keywords:
          - '孙正义 OpenAI'
          - 'OpenAI 算力入口'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "checklist.yaml"
            input_path.write_text(payload, encoding="utf-8")

            active = 0
            peak = 0
            lock = threading.Lock()

            class FakeClient:
                def search_with_provider(
                    self, query: SearchQuery, max_results: int
                ) -> tuple[str, list[dict[str, object]]]:
                    nonlocal active, peak
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    time.sleep(0.03)
                    with lock:
                        active -= 1
                    return (
                        "tavily",
                        [
                            {
                                "title": f"{query.value} 相关报道",
                                "url": f"https://news.example.com/{query.query_type}/{len(query.value)}",
                                "content": "OpenAI 软银 算力入口",
                                "score": 0.9,
                                "published_date": "2026-03-30",
                            }
                        ],
                    )

                def extract(self, urls: list[str], query: str) -> dict[str, str]:
                    return {}

            run_search_workflow(
                input_path=input_path,
                report_date="2026-03-30",
                per_query_limit=2,
                per_article_limit=3,
                extract_limit=1,
                client=FakeClient(),  # type: ignore[arg-type]
            )

        self.assertGreaterEqual(peak, 2)

    def test_save_search_results_writes_provider_stats_report(self) -> None:
        payload = """report_date: '2026-03-30'
prompt_path: '/tmp/prompt.md'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: 'OpenAI compute platform'
        channel: 'Cloud&AI'
        url: 'https://www.c114.com.cn/ai/5339/a1307668.html'
        keywords:
          - 'OpenAI compute'
          - 'compute platform'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "checklist.yaml"
            output_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")

            class FakeClient:
                def search_with_provider(
                    self, query: SearchQuery, max_results: int
                ) -> tuple[str, list[dict[str, object]]]:
                    provider = "tavily" if "OpenAI" in query.value else "baidu"
                    return (
                        provider,
                        [
                            {
                                "title": f"{query.value} 相关报道",
                                "url": f"https://news.example.com/{query.query_type}/{len(query.value)}",
                                "content": "OpenAI compute platform",
                                "score": 0.9,
                                "published_date": "2026-03-30",
                            }
                        ],
                    )

                def extract(self, urls: list[str], query: str) -> dict[str, str]:
                    return {url.rstrip("/"): "正文" for url in urls}

            workflow = run_search_workflow(
                input_path=input_path,
                report_date="2026-03-30",
                per_query_limit=2,
                per_article_limit=3,
                extract_limit=2,
                client=FakeClient(),  # type: ignore[arg-type]
            )
            save_search_results(output_path, workflow)
            search_text = output_path.read_text(encoding="utf-8")
            stats_path = output_path.parent / provider_stats_name(date(2026, 3, 30))
            self.assertTrue(stats_path.exists())
            stats_text = stats_path.read_text(encoding="utf-8")

        self.assertIn("provider: 'tavily'", search_text)
        self.assertIn("provider: 'baidu'", search_text)
        self.assertIn("Tavily", stats_text)
        self.assertIn("Baidu", stats_text)
        self.assertIn("总调用次数", stats_text)

    def test_enrich_selected_results_only_extracts_top_n(self) -> None:
        article = SearchArticleInput(
            topic="AI与算力",
            channel="Cloud&AI",
            original_title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
            original_url="https://www.c114.com.cn/ai/5339/a1307668.html",
            original_published_at="2026-03-30",
            keywords=["孙正义 OpenAI", "OpenAI 算力入口"],
        )
        results = [
            SearchResult(
                query="原标题",
                query_type="title",
                result_title=f"结果{index}",
                url=f"https://example.com/{index}",
                domain="example.com",
                published_at="2026-03-30",
                snippet="摘要",
                score=1.0 - index / 10,
                is_official=False,
                source_tier="normal",
                matched_terms=["OpenAI"],
                extract_text="",
                extract_status="not_requested",
            )
            for index in range(3)
        ]

        class FakeClient:
            def __init__(self) -> None:
                self.extract_urls: list[str] = []

            def extract(self, urls: list[str], query: str) -> dict[str, str]:
                self.extract_urls = urls
                return {urls[0].rstrip("/"): "正文一", urls[1].rstrip("/"): "正文二"}

        client = FakeClient()
        enriched = enrich_selected_results(results, article, extract_limit=2, client=client)  # type: ignore[arg-type]

        self.assertEqual(client.extract_urls, ["https://example.com/0", "https://example.com/1"])
        self.assertEqual(enriched[0].extract_status, "success")
        self.assertEqual(enriched[1].extract_status, "success")
        self.assertEqual(enriched[2].extract_status, "not_requested")

    def test_run_search_workflow_keeps_running_when_extract_fails(self) -> None:
        payload = """report_date: '2026-03-30'
prompt_path: '/tmp/prompt.md'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '孙正义借巨资押注OpenAI 争夺下一代算力入口'
        channel: 'Cloud&AI'
        url: 'https://www.c114.com.cn/ai/5339/a1307668.html'
        keywords:
          - '孙正义 OpenAI'
          - 'OpenAI 算力入口'
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "checklist.yaml"
            output_path = Path(tmp_dir) / "search.yaml"
            input_path.write_text(payload, encoding="utf-8")

            class FakeClient:
                def search(self, query: SearchQuery, max_results: int) -> list[dict[str, object]]:
                    return [
                        {
                            "title": f"{query.value} 相关报道",
                            "url": f"https://news.example.com/{query.query_type}",
                            "content": "OpenAI 软银 算力入口",
                            "score": 0.9,
                            "published_date": "2026-03-30",
                        }
                    ]

                def extract(self, urls: list[str], query: str) -> dict[str, str]:
                    raise RuntimeError("extract failed")

            workflow = run_search_workflow(
                input_path=input_path,
                report_date="2026-03-30",
                per_query_limit=2,
                per_article_limit=3,
                extract_limit=2,
                client=FakeClient(),  # type: ignore[arg-type]
            )
            save_search_results(output_path, workflow)
            content = output_path.read_text(encoding="utf-8")

        self.assertIn("selected_results:", content)
        self.assertIn("extract_status: 'failed'", content)

    def test_enrich_selected_results_treats_extract_timeout_as_failed(self) -> None:
        article = SearchArticleInput(
            topic="AI与算力",
            channel="Cloud&AI",
            original_title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
            original_url="https://www.c114.com.cn/ai/5339/a1307668.html",
            original_published_at="2026-03-30",
            keywords=["孙正义 OpenAI", "OpenAI 算力入口"],
        )
        results = [
            SearchResult(
                query="原标题",
                query_type="title",
                result_title="结果",
                url="https://example.com/0",
                domain="example.com",
                published_at="2026-03-30",
                snippet="摘要",
                score=1.0,
                is_official=False,
                source_tier="normal",
                matched_terms=["OpenAI"],
                extract_text="",
                extract_status="not_requested",
            )
        ]

        class FakeClient:
            def extract(self, urls: list[str], query: str) -> dict[str, str]:
                raise socket.timeout("timed out")

        enriched = enrich_selected_results(results, article, extract_limit=1, client=FakeClient())  # type: ignore[arg-type]

        self.assertEqual(enriched[0].extract_status, "failed")

    def test_enrich_selected_results_skips_extract_when_client_has_no_extract(self) -> None:
        article = SearchArticleInput(
            topic="AI与算力",
            channel="Cloud&AI",
            original_title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
            original_url="https://www.c114.com.cn/ai/5339/a1307668.html",
            original_published_at="2026-03-30",
            keywords=["孙正义 OpenAI", "OpenAI 算力入口"],
        )
        results = [
            SearchResult(
                query="原标题",
                query_type="title",
                result_title="结果",
                url="https://example.com/0",
                domain="example.com",
                published_at="2026-03-30",
                snippet="摘要",
                score=1.0,
                is_official=False,
                source_tier="normal",
                matched_terms=["OpenAI"],
                extract_text="",
                extract_status="not_requested",
            )
        ]

        class SearchOnlyClient:
            pass

        enriched = enrich_selected_results(results, article, extract_limit=1, client=SearchOnlyClient())  # type: ignore[arg-type]

        self.assertEqual(enriched[0].extract_status, "not_requested")


class TavilyConfigTests(unittest.TestCase):
    def test_missing_tavily_api_key_is_detectable(self) -> None:
        previous = os.environ.pop("TAVILY_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                with self.assertRaisesRegex(RuntimeError, "TAVILY_API_KEY"):
                    SearchOutputPaths.require_api_key(project_root=Path(tmp_dir))
        finally:
            if previous is not None:
                os.environ["TAVILY_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
