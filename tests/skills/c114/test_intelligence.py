from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from c114.c114_intelligence import (
    AnalysisOutputPaths,
    ArticleAnalysis,
    RawArticleRecord,
    analyze_article,
    build_search_checklist_items,
    build_search_checklist_sections,
    build_topic_briefs,
    load_daily_articles_from_csv,
    load_search_agent_prompt,
    render_search_checklist_yaml,
    write_analysis_outputs,
)


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

        self.assertEqual(result.topic, "AI与算力")
        self.assertIn("OpenAI", result.entities)
        self.assertIn("投融资", result.signals)
        self.assertTrue(any("OpenAI" in query for query in result.followup_queries))


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
                topic="卫星互联网与商业航天",
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
                topic="卫星互联网与商业航天",
                core_summary="太空算力被视为商业航天下一阶段方向。",
                followup_queries=["太空算力 商业化 拐点"],
                url="https://www.c114.com.cn/satellite/2514/a1307660.html",
            ),
        ]

        briefs = build_topic_briefs(analyses)

        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0].topic, "卫星互联网与商业航天")
        self.assertEqual(briefs[0].article_count, 2)
        self.assertIn("政策", briefs[0].signals)
        self.assertIn("技术趋势", briefs[0].signals)


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
                topic="AI与算力",
                core_summary="资本继续围绕大模型和算力入口集中。",
                followup_queries=["OpenAI AI与算力", "AI与算力 投融资"],
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
                topic="6G与下一代通信",
                core_summary="空天地一体化持续推进。",
                followup_queries=["6G 6G与下一代通信"],
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
                topic="卫星互联网与商业航天",
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
                topic="AI与算力",
                core_summary="资本继续围绕大模型和算力入口集中。",
                followup_queries=["OpenAI AI与算力"],
                url="https://www.c114.com.cn/ai/5339/a1307668.html",
            ),
        ]
        sections = build_search_checklist_sections(build_search_checklist_items(analyses))
        self.assertEqual([section.topic for section in sections], ["AI与算力", "卫星互联网与商业航天"])

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
                topic="华为",
                core_summary="华为轮值董事长变更。",
                followup_queries=["汪涛 华为"],
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
                topic="卫星互联网与商业航天",
                core_summary="政策继续支持商业航天和卫星运营基础设施。",
                followup_queries=["商业航天 频率占用费 影响"],
                url="https://www.c114.com.cn/satellite/2514/a1307674.html",
            ),
        ]
        content = render_search_checklist_yaml("2026-03-27", build_search_checklist_items(analyses))
        self.assertIn("report_date: '2026-03-27'", content)
        self.assertIn("instructions: 'keywords 必须由调用本 skill 的 agent 先读取 prompt_path", content)
        self.assertIn("topic: '卫星互联网与商业航天'", content)
        self.assertIn("original_title: '商业航天再迎重磅利好 两部委优化无线电频率占用费标准'", content)
        self.assertIn("original_published_at: '2026-03-27'", content)
        self.assertIn("keywords:", content)
        self.assertIn("# 由 agent 根据标题生成并填写两组搜索关键词", content)


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


if __name__ == "__main__":
    unittest.main()
