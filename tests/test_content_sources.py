from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from c114.c114_hot_topics import ArticleMetadata, ChannelDailyReport
from touzifenxi.briefing.materialize import STEP1_CSV_FIELDNAMES, build_step1_csv_rows
from touzifenxi.content_sources.c114 import C114SourceAdapter
from touzifenxi.content_sources.infoq import InfoQSourceAdapter, extract_infoq_content_text
from touzifenxi.content_sources.models import RawArticleDetail, StandardArticle


class Step1MaterializationTests(unittest.TestCase):
    def test_builds_step1_rows_from_standard_articles(self) -> None:
        article = StandardArticle(
            source_site="infoq",
            source_bucket="热点",
            channel="AI",
            article_id="infoq-1",
            title="InfoQ 测试文章",
            url="https://www.infoq.cn/article/demo",
            published_at="2026-04-12",
            author="测试作者",
            tags=["AI", "工程效率"],
            keywords=["AI", "效率"],
            summary="一段摘要。",
            content_text="正文内容。",
            metadata={"channel_key": "ai", "channel_url": "https://www.infoq.cn/topic/AI"},
        )

        rows = build_step1_csv_rows("2026-04-12", [article])

        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), set(STEP1_CSV_FIELDNAMES))
        self.assertEqual(rows[0]["统计日期"], "2026-04-12")
        self.assertEqual(rows[0]["栏目键"], "ai")
        self.assertEqual(rows[0]["栏目名称"], "AI")
        self.assertEqual(rows[0]["文章标题"], "InfoQ 测试文章")
        self.assertEqual(rows[0]["关键词"], "AI|效率")
        self.assertEqual(rows[0]["摘要"], "一段摘要。")


class C114SourceAdapterTests(unittest.TestCase):
    def test_normalizes_channel_reports_to_standard_articles(self) -> None:
        adapter = C114SourceAdapter()
        reports = [
            ChannelDailyReport(
                channel_key="ai",
                channel_name="Cloud&AI",
                channel_url="https://www.c114.com.cn/ai/",
                article_count=1,
                hot_topics=[("OpenAI", 1)],
                articles=[
                    ArticleMetadata(
                        url="https://www.c114.com.cn/ai/5339/a1307668.html",
                        title="孙正义借巨资押注OpenAI 争夺下一代算力入口",
                        publish_date=date(2026, 4, 12),
                        keywords=["OpenAI", "软银"],
                        summary="软银继续加码 OpenAI。",
                    )
                ],
            )
        ]

        articles = adapter.standard_articles_from_reports(date(2026, 4, 12), reports)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].source_site, "c114")
        self.assertEqual(articles[0].source_bucket, "Cloud&AI")
        self.assertEqual(articles[0].channel, "Cloud&AI")
        self.assertEqual(articles[0].article_id, "c114:ai:https://www.c114.com.cn/ai/5339/a1307668.html")
        self.assertEqual(articles[0].tags, ["OpenAI"])
        self.assertEqual(articles[0].keywords, ["OpenAI", "软银"])


class InfoQSourceAdapterTests(unittest.TestCase):
    def test_uses_configured_listing_size_when_fetching_new_list(self) -> None:
        class CapturingInfoQAdapter(InfoQSourceAdapter):
            def __init__(self) -> None:
                super().__init__({"listing_size": 20})
                self.captured_data: dict[str, object] | None = None

            def _curl_json(self, url: str, *, data: dict[str, object] | None = None, referer: str) -> object:
                self.captured_data = data
                return {"data": {"list": []}}

        adapter = CapturingInfoQAdapter()

        refs = adapter.fetch_listing(date(2026, 4, 12))

        self.assertEqual(refs, [])
        self.assertEqual(adapter.captured_data, {"size": 20})

    def test_defaults_infoq_listing_size_to_twelve(self) -> None:
        adapter = InfoQSourceAdapter()

        self.assertEqual(adapter.default_source_config()["listing_size"], 12)
        self.assertEqual(adapter.listing_size, 12)

    def test_filters_new_list_refs_by_report_date(self) -> None:
        adapter = InfoQSourceAdapter()
        listing_payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "uuid": "today-1",
                        "article_title": "当天文章",
                        "article_summary": "当天摘要",
                        "publish_time": self._ts_ms(2026, 4, 12, 9, 30),
                        "author": [{"nickname": "作者甲"}],
                        "label": [{"name": "AI"}],
                    },
                    {
                        "uuid": "old-1",
                        "article_title": "前一天文章",
                        "article_summary": "旧摘要",
                        "publish_time": self._ts_ms(2026, 4, 11, 20, 0),
                        "author": [{"nickname": "作者乙"}],
                        "label": [{"name": "架构"}],
                    },
                ]
            },
        }

        refs = adapter.parse_listing_payload(listing_payload, report_date=date(2026, 4, 12))

        self.assertEqual([item.article_id for item in refs], ["infoq:today-1"])
        self.assertEqual(refs[0].title, "当天文章")
        self.assertEqual(refs[0].channel, "首页")
        self.assertEqual(refs[0].source_bucket, "首页")

    def test_still_accepts_hot_day_list_payload(self) -> None:
        adapter = InfoQSourceAdapter()
        listing_payload = {
            "code": 0,
            "data": {
                "hot_day_list": [
                    {
                        "uuid": "today-2",
                        "article_title": "热榜文章",
                        "article_summary": "热榜摘要",
                        "publish_time": self._ts_ms(2026, 4, 12, 10, 0),
                        "author": [{"nickname": "作者甲"}],
                        "label": [{"name": "AI"}],
                    }
                ]
            },
        }

        refs = adapter.parse_listing_payload(listing_payload, report_date=date(2026, 4, 12))

        self.assertEqual([item.article_id for item in refs], ["infoq:today-2"])
        self.assertEqual(refs[0].source_bucket, "热点")

    def test_normalizes_detail_payload_and_content_json(self) -> None:
        adapter = InfoQSourceAdapter()
        raw_detail = RawArticleDetail(
            source_site="infoq",
            article_id="infoq:f8ff9c9c5d90cf8bcc38e15fb",
            title="今天，我决定把「卡兹克风格创作.skill」开源了。",
            url="https://www.infoq.cn/article/f8ff9c9c5d90cf8bcc38e15fb",
            published_at="2026-04-06",
            author="数字生命卡兹克",
            channel="热点",
            source_bucket="热点",
            tags=["开源", "Skill"],
            summary="故事是这样的。",
            content_text="",
            metadata={
                "detail": {
                    "uuid": "f8ff9c9c5d90cf8bcc38e15fb",
                    "article_title": "今天，我决定把「卡兹克风格创作.skill」开源了。",
                    "article_summary": "故事是这样的。",
                    "publish_time": self._ts_ms(2026, 4, 6, 9, 0),
                    "author": [{"nickname": "数字生命卡兹克"}],
                    "label": [{"name": "开源"}, {"name": "Skill"}],
                },
                "content": {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "故事是这样的。"}]},
                        {"type": "paragraph", "content": [{"type": "text", "text": "最近各种把同事蒸馏成 Skill 的东西特别火。"}]},
                    ],
                },
            },
        )

        article = adapter.normalize_article(raw_detail)

        self.assertEqual(article.source_site, "infoq")
        self.assertEqual(article.channel, "热点")
        self.assertEqual(article.author, "数字生命卡兹克")
        self.assertEqual(article.tags, ["开源", "Skill"])
        self.assertIn("故事是这样的。", article.content_text)
        self.assertIn("最近各种把同事蒸馏成 Skill", article.content_text)

    def test_extract_infoq_content_text_flattens_document_blocks(self) -> None:
        content_payload = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "第一段"}]},
                {
                    "type": "bullet_list",
                    "content": [
                        {"type": "list_item", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "列表一"}]}]},
                        {"type": "list_item", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "列表二"}]}]},
                    ],
                },
            ],
        }

        text = extract_infoq_content_text(content_payload)

        self.assertEqual(text, "第一段\n列表一\n列表二")

    @staticmethod
    def _ts_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
        return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)


if __name__ == "__main__":
    unittest.main()
