from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from c114.c114_intelligence import (
    AnalysisOutputPaths,
    ArticleAnalysis,
    RawArticleRecord,
    StructuredLLMError,
    _normalize_keyword_response,
    analyze_article,
    auto_group_analysis_topics,
    autofill_search_checklist_items,
    build_search_checklist_items,
    build_search_checklist_sections,
    build_topic_briefs,
    load_daily_articles_from_csv,
    load_search_agent_prompt,
    render_search_checklist_yaml,
    write_analysis_outputs,
)
from c114.checkpoint import StepCheckpointStore, checkpoint_path_for_step


class LoadDailyArticlesFromCsvTests(unittest.TestCase):
    def test_loads_only_target_date_and_skips_blank_titles(self) -> None:
        csv_text = """统计日期,栏目键,栏目名称,栏目链接,栏目文章数,栏目热点词,文章标题,发布时间,关键词,摘要,文章链接
2026-03-27,ai,Cloud&AI,https://www.c114.com.cn/ai/,1,OpenAI:1,孙正义借巨资押注OpenAI 争夺下一代算力入口,2026-03-27,OpenAI|软银,摘要一,https://www.c114.com.cn/ai/5339/a1307668.html
2026-03-27,la,数智低空,https://www.c114.com.cn/la/,0,,,,,,
2026-03-30,quantum,量子信息,https://www.c114.com.cn/quantum/,1,量子:1,幺正量子完成数亿元Pre A轮融资,2026-03-30,幺正量子|融资,摘要二,https://www.c114.com.cn/quantum/5285/a1307759.html
"""
        rows = load_daily_articles_from_csv(csv_text, "2026-03-27")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "孙正义借巨资押注OpenAI 争夺下一代算力入口")

    def test_dedupes_exact_duplicate_c114_titles_but_keeps_non_c114_duplicates(self) -> None:
        csv_text = """统计日期,栏目键,栏目名称,栏目链接,栏目文章数,栏目热点词,文章标题,发布时间,关键词,摘要,文章链接
2026-04-04,home,首页,https://www.c114.com.cn/,2,运营商:1,应收账款超2200亿！运营商政企业务深陷“规模陷阱”,2026-04-04,运营商|政企业务,摘要一,https://www.c114.com.cn/news/6541/a1308071.html
2026-04-04,home,首页,https://www.c114.com.cn/,2,运营商:1,应收账款超2200亿！运营商政企业务深陷“规模陷阱”,2026-04-04,运营商|政企业务,摘要二,https://www.c114.com.cn/news/16/a1308068.html
2026-04-04,home,首页,https://www.c114.com.cn/,2,运营商:1,应收账款超2200亿！运营商政企业务深陷“规模陷阱”,2026-04-04,运营商|政企业务,摘要三,https://finance.sina.com.cn/tech/roll/2026-04-03/doc-inhtfeqx7123188.shtml
"""
        rows = load_daily_articles_from_csv(csv_text, "2026-04-04")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].url, "https://www.c114.com.cn/news/6541/a1308071.html")
        self.assertEqual(rows[1].url, "https://finance.sina.com.cn/tech/roll/2026-04-03/doc-inhtfeqx7123188.shtml")


class AnalyzeArticleTests(unittest.TestCase):
    def test_extracts_topic_entities_signals_and_queries(self) -> None:
        article = RawArticleRecord(
            report_date="2026-03-27",
            channel_key="ai",
            channel_name="Cloud&AI",
            channel_hot_topics=["OpenAI:1"],
            title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
            publish_date="2026-03-27",
            keywords=["OpenAI", "软银"],
            summary="软银集团承诺向OpenAI追加投资300亿美元，试图争夺下一代算力入口。",
            url="https://www.c114.com.cn/ai/5339/a1307668.html",
        )

        result = analyze_article(article)

        self.assertEqual(result.topic, "Cloud&AI")
        self.assertIn("OpenAI", result.entities)
        self.assertIn("投融资", result.signals)
        self.assertTrue(any("OpenAI" in query for query in result.followup_queries))

    def test_home_article_uses_c114_url_prefix_as_topic(self) -> None:
        article = RawArticleRecord(
            report_date="2026-04-10",
            channel_key="home",
            channel_name="首页",
            channel_hot_topics=["量子:1"],
            title="国盾量子：量子计算的未来价值肯定是算力",
            publish_date="2026-04-10",
            keywords=["量子计算", "国盾量子"],
            summary="量子计算产业化仍在推进。",
            url="https://www.c114.com.cn/quantum/5285/a1308339.html",
        )

        result = analyze_article(article)

        self.assertEqual(result.topic, "量子信息")

    def test_home_news_article_falls_back_to_news_topic(self) -> None:
        article = RawArticleRecord(
            report_date="2026-04-10",
            channel_key="home",
            channel_name="首页",
            channel_hot_topics=["运营商:1"],
            title="刘冬任中国电信集团有限公司外部董事",
            publish_date="2026-04-10",
            keywords=["中国电信", "刘冬"],
            summary="中国电信高管信息更新。",
            url="https://www.c114.com.cn/news/6564/a1308304.html",
        )

        result = analyze_article(article)

        self.assertEqual(result.topic, "新闻")

    def test_non_home_article_keeps_original_channel_name(self) -> None:
        article = RawArticleRecord(
            report_date="2026-04-10",
            channel_key="ai",
            channel_name="Cloud&AI",
            channel_hot_topics=["AI:1"],
            title="Anthropic 计划自研 AI 芯片",
            publish_date="2026-04-10",
            keywords=["Anthropic", "AI"],
            summary="Cloud&AI 栏目文章。",
            url="https://www.c114.com.cn/ai/5339/a1308310.html",
        )

        result = analyze_article(article)

        self.assertEqual(result.topic, "Cloud&AI")


class TopicBriefTests(unittest.TestCase):
    def test_builds_topic_briefs_from_multiple_articles(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="satellite",
                channel_name="卫星互联网",
                title="商业航天再迎重磅利好 两部委优化无线电频率占用费标准",
                publish_date="2026-03-27",
                source_keywords=["卫星运营"],
                normalized_keywords=["商业航天", "卫星运营", "频率占用费"],
                entities=["国家发改委", "财政部"],
                signals=["政策"],
                topic="卫星互联网",
                core_summary="政策继续支持商业航天和卫星运营基础设施。",
                followup_queries=["商业航天 频率占用费 影响"],
                url="https://www.c114.com.cn/satellite/2514/a1307674.html",
            ),
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="satellite",
                channel_name="卫星互联网",
                title="中国工程院院士孙凝晖：太空算力预计2035年迎来商业化拐点",
                publish_date="2026-03-27",
                source_keywords=["太空算力"],
                normalized_keywords=["太空算力", "商业航天"],
                entities=["孙凝晖"],
                signals=["技术趋势"],
                topic="卫星互联网",
                core_summary="太空算力被视为商业航天下一阶段方向。",
                followup_queries=["太空算力 商业化 拐点"],
                url="https://www.c114.com.cn/satellite/2514/a1307660.html",
            ),
        ]

        briefs = build_topic_briefs(analyses)

        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0].topic, "卫星互联网")
        self.assertEqual(briefs[0].article_count, 2)
        self.assertIn("政策", briefs[0].signals)
        self.assertIn("技术趋势", briefs[0].signals)


class TopicGroupingTests(unittest.TestCase):
    def test_auto_groups_topics_from_single_batch_response(self) -> None:
        case = self
        analyses = [
            ArticleAnalysis(
                report_date="2026-04-11",
                channel_key="home",
                channel_name="首页",
                title="《对话》GTI主席高同庆 | 6G与AI的双轮驱动，是未来数智社会发展的必然",
                publish_date="2026-04-11",
                source_keywords=["GTI", "6G", "AI"],
                normalized_keywords=["GTI", "6G", "AI"],
                entities=["GTI", "高同庆"],
                signals=["技术趋势"],
                topic="视频",
                core_summary="视频方向出现技术趋势信号，重点涉及GTI、高同庆。",
                followup_queries=["GTI 视频"],
                url="https://www.c114.com.cn/video/5918/a1303073.html",
            ),
            ArticleAnalysis(
                report_date="2026-04-11",
                channel_key="home",
                channel_name="首页",
                title="《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”",
                publish_date="2026-04-11",
                source_keywords=["中国联通", "智能体互联网"],
                normalized_keywords=["中国联通", "智能体互联网", "AI"],
                entities=["中国联通", "曹畅"],
                signals=["技术趋势"],
                topic="视频",
                core_summary="视频方向出现技术趋势信号，重点涉及中国联通、曹畅。",
                followup_queries=["中国联通 视频"],
                url="https://www.c114.com.cn/video/5918/a1303172.html",
            ),
            ArticleAnalysis(
                report_date="2026-04-11",
                channel_key="home",
                channel_name="首页",
                title="刘冬任中国电信集团有限公司外部董事",
                publish_date="2026-04-11",
                source_keywords=["中国电信", "刘冬"],
                normalized_keywords=["中国电信", "刘冬", "外部董事"],
                entities=["中国电信", "刘冬"],
                signals=["信息更新"],
                topic="新闻",
                core_summary="新闻方向出现信息更新信号，重点涉及中国电信、刘冬。",
                followup_queries=["中国电信 新闻"],
                url="https://www.c114.com.cn/news/6564/a1308304.html",
            ),
        ]

        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls += 1
                case.assertIn('"article_id": "article-1"', user_prompt)
                case.assertIn('"source_bucket": "视频"', user_prompt)
                case.assertIn('"source_bucket": "新闻"', user_prompt)
                return {
                    "topics": [
                        {"topic_id": "t1", "topic_name": "6G与AI融合"},
                        {"topic_id": "t2", "topic_name": "运营商人事变动"},
                    ],
                    "items": [
                        {"article_id": "article-1", "topic_id": "t1", "reason": "同属6G与AI融合议题"},
                        {"article_id": "article-2", "topic_id": "t1", "reason": "同属智能网络演进议题"},
                        {"article_id": "article-3", "topic_id": "t2", "reason": "属于运营商高层任命信息"},
                    ],
                }

        grouped, briefs = auto_group_analysis_topics(analyses, FakeLLMClient(), report_date="2026-04-11")

        self.assertEqual([item.topic for item in grouped], ["6G与AI融合", "6G与AI融合", "运营商人事变动"])
        self.assertTrue(all("视频" not in item.core_summary for item in grouped[:2]))
        self.assertEqual([brief.topic for brief in briefs], ["6G与AI融合", "运营商人事变动"])

    def test_auto_groups_topics_reuses_checkpoint_without_recalling_llm(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-04-11",
                channel_key="home",
                channel_name="首页",
                title="专访周明宇｜我国商业航天迎来“第二阶段”：太空算力+星地激光成破局关键",
                publish_date="2026-04-11",
                source_keywords=["商业航天", "太空算力"],
                normalized_keywords=["商业航天", "太空算力", "星地激光"],
                entities=["周明宇", "商业航天"],
                signals=["技术趋势"],
                topic="卫星互联网",
                core_summary="卫星互联网方向出现技术趋势信号。",
                followup_queries=["商业航天 卫星互联网"],
                url="https://www.c114.com.cn/satellite/2514/a1305123.html",
            ),
        ]

        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls += 1
                return {
                    "topics": [{"topic_id": "t1", "topic_name": "商业航天与太空算力"}],
                    "items": [{"article_id": "article-1", "topic_id": "t1", "reason": "单篇成组"}],
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=root / "c114_step_1_analysis_20260411.csv",
                    step_name="step_1_5",
                    report_date="2026-04-11",
                ),
                step_name="step_1_5",
                report_date="2026-04-11",
                input_path=root / "c114_hot_topics.csv",
                output_path=root / "c114_step_1_analysis_20260411.csv",
            )
            llm_client = FakeLLMClient()

            grouped_first, _ = auto_group_analysis_topics(
                analyses,
                llm_client,
                report_date="2026-04-11",
                checkpoint_store=checkpoint_store,
            )
            grouped_second, _ = auto_group_analysis_topics(
                analyses,
                llm_client,
                report_date="2026-04-11",
                checkpoint_store=checkpoint_store,
            )

            self.assertEqual(llm_client.calls, 1)
            self.assertEqual(grouped_first[0].topic, "商业航天与太空算力")
            self.assertEqual(grouped_second[0].topic, "商业航天与太空算力")


class SearchChecklistTests(unittest.TestCase):
    def test_loads_agent_prompt_from_project(self) -> None:
        prompt = load_search_agent_prompt()
        self.assertIn("不要失去原标题原有味道", prompt)
        self.assertIn("二次搜索关键词", prompt)

    def test_builds_search_checklist_items(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="ai",
                channel_name="Cloud&AI",
                title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
                publish_date="2026-03-27",
                source_keywords=["OpenAI", "软银"],
                normalized_keywords=["OpenAI", "算力", "软银"],
                entities=["OpenAI", "软银"],
                signals=["投融资"],
                topic="Cloud&AI",
                core_summary="资本继续围绕大模型和算力入口集中。",
                followup_queries=["OpenAI Cloud&AI", "Cloud&AI 投融资"],
                url="https://www.c114.com.cn/ai/5339/a1307668.html",
            ),
        ]
        items = build_search_checklist_items(analyses)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "孙正义借巨资押注OpenAI 争夺下一代算力入口")
        self.assertEqual(items[0].search_queries, [])
        self.assertEqual(items[0].publish_date, "2026-03-27")

    def test_builds_search_checklist_template_item_without_python_keywords(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-03-30",
                channel_key="news",
                channel_name="首页",
                title="6G洞见 | 朱敏&张教：空天地一体化，正迈入全新高速发展期",
                publish_date="2026-03-30",
                source_keywords=["6G"],
                normalized_keywords=["6G", "空天地一体化"],
                entities=["朱敏&张教"],
                signals=["技术趋势"],
                topic="首页",
                core_summary="空天地一体化持续推进。",
                followup_queries=["6G 首页"],
                url="https://www.c114.com.cn/news/16/a1307713.html",
            ),
        ]

        items = build_search_checklist_items(analyses)

        self.assertEqual(items[0].search_queries, [])
        self.assertEqual(items[0].title, "6G洞见 | 朱敏&张教：空天地一体化，正迈入全新高速发展期")

    def test_groups_search_checklist_items_by_topic(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="satellite",
                channel_name="卫星互联网",
                title="商业航天再迎重磅利好 两部委优化无线电频率占用费标准",
                publish_date="2026-03-27",
                source_keywords=["卫星运营"],
                normalized_keywords=["商业航天", "卫星运营", "频率占用费"],
                entities=["国家发改委", "财政部"],
                signals=["政策"],
                topic="卫星互联网",
                core_summary="政策继续支持商业航天和卫星运营基础设施。",
                followup_queries=["商业航天 频率占用费 影响"],
                url="https://www.c114.com.cn/satellite/2514/a1307674.html",
            ),
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="ai",
                channel_name="Cloud&AI",
                title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
                publish_date="2026-03-27",
                source_keywords=["OpenAI", "软银"],
                normalized_keywords=["OpenAI", "算力", "软银"],
                entities=["OpenAI", "软银"],
                signals=["投融资"],
                topic="Cloud&AI",
                core_summary="资本继续围绕大模型和算力入口集中。",
                followup_queries=["OpenAI Cloud&AI"],
                url="https://www.c114.com.cn/ai/5339/a1307668.html",
            ),
        ]
        sections = build_search_checklist_sections(build_search_checklist_items(analyses))
        self.assertEqual([section.topic for section in sections], ["卫星互联网", "Cloud&AI"])

    def test_builds_search_checklist_template_for_titles_without_clear_split_markers(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-03-31",
                channel_key="news",
                channel_name="首页",
                title="汪涛当值华为轮值董事长",
                publish_date="2026-03-31",
                source_keywords=["汪涛", "华为"],
                normalized_keywords=["汪涛", "华为"],
                entities=["汪涛", "华为"],
                signals=[],
                topic="首页",
                core_summary="华为轮值董事长变更。",
                followup_queries=["汪涛 首页"],
                url="https://www.c114.com.cn/news/126/a1307842.html",
            ),
        ]

        items = build_search_checklist_items(analyses)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].search_queries, [])
        self.assertTrue(all(isinstance(query, str) for query in items[0].search_queries))
        self.assertEqual(items[0].title, "汪涛当值华为轮值董事长")

    def test_renders_search_checklist_yaml(self) -> None:
        analyses = [
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="satellite",
                channel_name="卫星互联网",
                title="商业航天再迎重磅利好 两部委优化无线电频率占用费标准",
                publish_date="2026-03-27",
                source_keywords=["卫星运营"],
                normalized_keywords=["商业航天", "卫星运营", "频率占用费"],
                entities=["国家发改委", "财政部"],
                signals=["政策"],
                topic="卫星互联网",
                core_summary="政策继续支持商业航天和卫星运营基础设施。",
                followup_queries=["商业航天 频率占用费 影响"],
                url="https://www.c114.com.cn/satellite/2514/a1307674.html",
            ),
        ]
        content = render_search_checklist_yaml("2026-03-27", build_search_checklist_items(analyses))
        self.assertIn("report_date: '2026-03-27'", content)
        self.assertIn("instructions: 'keywords 由 skill 内置模型读取 prompt_path 后自动生成", content)
        self.assertIn("topic: '卫星互联网'", content)
        self.assertIn("original_title: '商业航天再迎重磅利好 两部委优化无线电频率占用费标准'", content)
        self.assertIn("original_published_at: '2026-03-27'", content)
        self.assertIn("keywords:", content)
        self.assertIn("# 由 skill 内置模型根据标题自动生成两组搜索关键词", content)

    def test_autofills_keywords_once_per_topic(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls.append(user_prompt)
                return {
                    "items": [
                        {
                            "original_title": "孙正义借巨资押注OpenAI 争夺下一代算力入口",
                            "keywords": ["孙正义 OpenAI", "OpenAI 算力入口"],
                        },
                        {
                            "original_title": "OpenAI 发布最新推理能力升级",
                            "keywords": ["OpenAI 推理升级", "OpenAI 推理能力"],
                        },
                    ]
                }

        analyses = [
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="ai",
                channel_name="Cloud&AI",
                title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
                publish_date="2026-03-27",
                source_keywords=["OpenAI", "软银"],
                normalized_keywords=["OpenAI", "算力", "软银"],
                entities=["OpenAI", "软银"],
                signals=["投融资"],
                topic="Cloud&AI",
                core_summary="资本继续围绕大模型和算力入口集中。",
                followup_queries=["OpenAI Cloud&AI"],
                url="https://www.c114.com.cn/ai/5339/a1307668.html",
            ),
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="ai",
                channel_name="Cloud&AI",
                title="OpenAI 发布最新推理能力升级",
                publish_date="2026-03-27",
                source_keywords=["OpenAI", "推理"],
                normalized_keywords=["OpenAI", "推理", "能力升级"],
                entities=["OpenAI"],
                signals=["技术趋势"],
                topic="Cloud&AI",
                core_summary="OpenAI 持续强化推理能力路线。",
                followup_queries=["OpenAI Cloud&AI"],
                url="https://www.c114.com.cn/ai/5339/a1307669.html",
            ),
        ]

        items = build_search_checklist_items(analyses)
        completed = autofill_search_checklist_items(items, analyses, FakeLLMClient())

        self.assertEqual(
            [item.search_queries for item in completed],
            [
                ["孙正义 OpenAI", "OpenAI 算力入口"],
                ["OpenAI 推理升级", "OpenAI 推理能力"],
            ],
        )

    def test_autofill_matches_titles_with_quote_variants(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls += 1
                return {
                    "items": [
                        {
                            "original_title": '《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的"最后一块拼图"',
                            "keywords": ["中国联通 智能体互联网", "曹畅 智能体互联网"],
                        }
                    ]
                }

        analyses = [
            ArticleAnalysis(
                report_date="2026-04-11",
                channel_key="home",
                channel_name="首页",
                title="《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”",
                publish_date="2026-04-11",
                source_keywords=["中国联通", "智能体互联网"],
                normalized_keywords=["中国联通", "智能体互联网", "AI"],
                entities=["中国联通", "曹畅"],
                signals=["技术趋势"],
                topic="智能体互联网",
                core_summary="聚焦智能体互联网与 AI 时代网络演进。",
                followup_queries=["中国联通 智能体互联网"],
                url="https://www.c114.com.cn/video/5918/a1303172.html",
            ),
        ]

        completed = autofill_search_checklist_items(
            build_search_checklist_items(analyses),
            analyses,
            FakeLLMClient(),
        )

        self.assertEqual(completed[0].search_queries, ["中国联通 智能体互联网", "曹畅 智能体互联网"])

    def test_autofill_checkpoints_partial_batch_success_then_retries_missing_items(self) -> None:
        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls += 1
                if (
                    '"topic": "Cloud&AI"' in user_prompt
                    and "孙正义借巨资押注OpenAI 争夺下一代算力入口" in user_prompt
                    and "OpenAI 发布最新推理能力升级" in user_prompt
                ):
                    return {
                        "items": [
                            {
                                "original_title": "孙正义借巨资押注OpenAI 争夺下一代算力入口",
                                "keywords": ["孙正义 OpenAI", "OpenAI 算力入口"],
                            }
                        ]
                    }
                return {"keywords": ["OpenAI 推理升级", "OpenAI 推理能力"]}

        analyses = [
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="ai",
                channel_name="Cloud&AI",
                title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
                publish_date="2026-03-27",
                source_keywords=["OpenAI", "软银"],
                normalized_keywords=["OpenAI", "算力", "软银"],
                entities=["OpenAI", "软银"],
                signals=["投融资"],
                topic="Cloud&AI",
                core_summary="资本继续围绕大模型和算力入口集中。",
                followup_queries=["OpenAI Cloud&AI"],
                url="https://www.c114.com.cn/ai/5339/a1307668.html",
            ),
            ArticleAnalysis(
                report_date="2026-03-27",
                channel_key="ai",
                channel_name="Cloud&AI",
                title="OpenAI 发布最新推理能力升级",
                publish_date="2026-03-27",
                source_keywords=["OpenAI", "推理"],
                normalized_keywords=["OpenAI", "推理", "能力升级"],
                entities=["OpenAI"],
                signals=["技术趋势"],
                topic="Cloud&AI",
                core_summary="OpenAI 持续强化推理能力路线。",
                followup_queries=["OpenAI Cloud&AI"],
                url="https://www.c114.com.cn/ai/5339/a1307669.html",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            checklist_output = root / "c114_search_202604102139" / "c114_step_2_search_checklist_20260410.yaml"
            checkpoint_store = StepCheckpointStore.load_or_create(
                checkpoint_path=checkpoint_path_for_step(
                    output_path=checklist_output,
                    step_name="step_2",
                    report_date="2026-04-10",
                ),
                step_name="step_2",
                report_date="2026-04-10",
                input_path=root / "c114_step_1_analysis_20260410.csv",
                output_path=checklist_output,
            )
            llm_client = FakeLLMClient()
            items = build_search_checklist_items(analyses)

            first_completed = autofill_search_checklist_items(
                items,
                analyses,
                llm_client,
                checkpoint_store=checkpoint_store,
            )

            self.assertEqual(llm_client.calls, 2)
            self.assertEqual(
                [item.search_queries for item in first_completed],
                [
                    ["孙正义 OpenAI", "OpenAI 算力入口"],
                    ["OpenAI 推理升级", "OpenAI 推理能力"],
                ],
            )
            self.assertEqual(
                checkpoint_store.get_result("article::Cloud&AI::孙正义借巨资押注OpenAI 争夺下一代算力入口"),
                {"keywords": ["孙正义 OpenAI", "OpenAI 算力入口"]},
            )
            self.assertEqual(
                checkpoint_store.get_result("article::Cloud&AI::OpenAI 发布最新推理能力升级"),
                {"keywords": ["OpenAI 推理升级", "OpenAI 推理能力"]},
            )

            second_completed = autofill_search_checklist_items(
                build_search_checklist_items(analyses),
                analyses,
                llm_client,
                checkpoint_store=checkpoint_store,
            )

            self.assertEqual(llm_client.calls, 2)
            self.assertEqual(
                [item.search_queries for item in second_completed],
                [
                    ["孙正义 OpenAI", "OpenAI 算力入口"],
                    ["OpenAI 推理升级", "OpenAI 推理能力"],
                ],
            )


class AnalysisOutputTests(unittest.TestCase):
    def test_empty_day_stops_at_step_1_and_skips_step_2_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            analysis_output = root / "c114_step_1_analysis_20260405.csv"
            checklist_output = root / "c114_step_2_search_checklist_20260405.yaml"
            checklist_output.write_text("stale: true\n", encoding="utf-8")

            output_paths = AnalysisOutputPaths(
                input_path=root / "c114_hot_topics.csv",
                analysis_output=analysis_output,
                checklist_output=checklist_output,
            )

            checklist_items = write_analysis_outputs(output_paths, "2026-04-05", [])

            self.assertEqual(checklist_items, [])
            self.assertTrue(analysis_output.exists())
            self.assertFalse(checklist_output.exists())

    def test_rebuilds_step2_yaml_from_checkpoint_without_recalling_llm(self) -> None:
        analysis = ArticleAnalysis(
            report_date="2026-04-10",
            channel_key="ai",
            channel_name="Cloud&AI",
            title="Anthropic 计划自研 AI 芯片",
            publish_date="2026-04-10",
            source_keywords=["Anthropic", "AI"],
            normalized_keywords=["Anthropic", "AI 芯片"],
            entities=["Anthropic"],
            signals=["技术趋势"],
            topic="Cloud&AI",
            core_summary="Cloud&AI 栏目文章。",
            followup_queries=["Anthropic AI 芯片"],
            url="https://www.c114.com.cn/ai/5339/a1308310.html",
        )

        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                self.calls += 1
                return {
                    "items": [
                        {
                            "original_title": "Anthropic 计划自研 AI 芯片",
                            "keywords": ["Anthropic 芯片", "AI 芯片 自研"],
                        }
                    ]
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_paths = AnalysisOutputPaths(
                input_path=root / "c114_hot_topics.csv",
                analysis_output=root / "c114_search_202604102139" / "c114_step_1_analysis_20260410.csv",
                checklist_output=root / "c114_search_202604102139" / "c114_step_2_search_checklist_20260410.yaml",
            )
            llm_client = FakeLLMClient()

            first_items = write_analysis_outputs(output_paths, "2026-04-10", [analysis], llm_client=llm_client)
            self.assertEqual(first_items[0].search_queries, ["Anthropic 芯片", "AI 芯片 自研"])
            self.assertEqual(llm_client.calls, 1)

            output_paths.checklist_output.unlink()
            rebuilt_items = write_analysis_outputs(output_paths, "2026-04-10", [analysis], llm_client=llm_client)

            self.assertTrue(output_paths.checklist_output.exists())
            self.assertEqual(rebuilt_items[0].search_queries, ["Anthropic 芯片", "AI 芯片 自研"])
            self.assertEqual(llm_client.calls, 1)


class KeywordNormalizationTests(unittest.TestCase):
    def test_accepts_json_string_payload(self) -> None:
        keywords = _normalize_keyword_response(
            '{"keywords": ["高同庆 6G与AI", "6G AI双轮驱动"]}',
            title="GTI主席高同庆",
        )

        self.assertEqual(keywords, ["高同庆 6G与AI", "6G AI双轮驱动"])

    def test_trims_extra_keywords_to_first_two(self) -> None:
        keywords = _normalize_keyword_response(
            {
                "keywords": [
                    "中国联通 智能体互联网",
                    "曹畅 智能体互联网",
                    "AI 智能体",
                ]
            },
            title="中国联通曹畅",
        )

        self.assertEqual(keywords, ["中国联通 智能体互联网", "曹畅 智能体互联网"])

    def test_requires_at_least_two_keywords(self) -> None:
        with self.assertRaisesRegex(StructuredLLMError, "关键词数量至少为 2"):
            _normalize_keyword_response({"keywords": ["仅一条"]}, title="测试标题")


if __name__ == "__main__":
    unittest.main()
