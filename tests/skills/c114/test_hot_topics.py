from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from c114.c114_hot_topics import (
    CHANNELS,
    ArticleMetadata,
    ChannelDailyReport,
    build_keyword_summary,
    collect_daily_report,
    extract_article_metadata,
    filter_candidates_for_channel,
    parse_anchor_date,
    save_daily_report_csv,
)


class FilterCandidatesForChannelTests(unittest.TestCase):
    def test_filters_channel_links_and_keeps_anchor_text(self) -> None:
        html = """
        <html>
          <body>
            <a href="/quantum/5285/a1307796.html">福建移动量子城域网：信通数智量子最高价中标C114讯 3月31日消息（云青）...C114通信网 09:59</a>
            <a href="/video/5917/a1307799.html">中国移动原董事长杨杰出任世界数据组织秘书长3/31</a>
            <a href="/quantum/5285/a1307795.html">玻色量子完成10亿元B轮融资，锚定“十五五”规划C114讯 3月31日消息（南山）...C114通信网 09:45</a>
          </body>
        </html>
        """

        items = filter_candidates_for_channel(html, CHANNELS["quantum"])

        self.assertEqual(
            [item.url for item in items],
            [
                "https://www.c114.com.cn/quantum/5285/a1307796.html",
                "https://www.c114.com.cn/quantum/5285/a1307795.html",
            ],
        )
        self.assertIn("福建移动量子城域网", items[0].anchor_text)
        self.assertEqual(str(items[0].anchor_date), "2026-03-31")

    def test_extracts_date_when_time_is_inside_homepage_card_link(self) -> None:
        html = """
        <html>
          <body>
            <div class="center_list">
              <a href="https://www.c114.com.cn/news/118/a1308582.html">
                <div class="title">中国移动绿色多频段基站天线补采，规模为19.07万面</div>
                <div class="text_bottom"><div class="time">4/16 14:14</div></div>
              </a>
            </div>
          </body>
        </html>
        """

        items = filter_candidates_for_channel(html, CHANNELS["home"])

        self.assertEqual(len(items), 1)
        self.assertEqual(str(items[0].anchor_date), "2026-04-16")


class ParseAnchorDateTests(unittest.TestCase):
    def test_parses_explicit_year_and_short_month_day(self) -> None:
        self.assertEqual(
            str(parse_anchor_date("第二家大模型厂商MiniMax登陆港交所：市值超过800亿港元2026/1/9", fallback_year=2026)),
            "2026-01-09",
        )
        self.assertEqual(
            str(parse_anchor_date("中国移动原董事长杨杰出任世界数据组织秘书长3/31", fallback_year=2026)), "2026-03-31"
        )


class ExtractArticleMetadataTests(unittest.TestCase):
    def test_extracts_title_keywords_summary_and_publish_date(self) -> None:
        html = """
        <html>
          <head>
            <title>福建移动量子城域网：信通数智量子最高价中标 - 量子信息 — C114通信网</title>
            <meta name="keywords" content="量子,福建移动,量子城域网" />
            <meta name="description" content="福建移动量子城域网：信通数智量子最高价中标,C114讯 3月31日消息（云青）昨日，中国移动..." />
          </head>
          <body>
            <div class="info">2026-3-31 11:50</div>
          </body>
        </html>
        """

        item = extract_article_metadata(
            url="https://www.c114.com.cn/quantum/5285/a1307796.html",
            html=html,
        )

        self.assertEqual(item.title, "福建移动量子城域网：信通数智量子最高价中标")
        self.assertEqual(item.publish_date, date(2026, 3, 31))
        self.assertEqual(item.keywords, ["量子", "福建移动", "量子城域网"])
        self.assertIn("C114讯 3月31日消息", item.summary)

    def test_falls_back_to_description_date_when_timestamp_missing(self) -> None:
        html = """
        <html>
          <head>
            <title>中国信科陈山枝：NTN赋能卫星互联网，以规模经济促进普惠 - 卫星通信 — C114通信网</title>
            <meta name="keywords" content="卫星互联网,NTN" />
            <meta name="description" content="中国信科陈山枝：NTN赋能卫星互联网，以规模经济促进普惠,C114讯 3月30日消息（苡臻）近日，2026中关村论坛..." />
          </head>
        </html>
        """

        item = extract_article_metadata(
            url="https://www.c114.com.cn/satellite/2514/a1307786.html",
            html=html,
        )

        self.assertEqual(item.publish_date, date(2026, 3, 30))

    def test_prefers_article_publish_time_over_html_comment_timestamp(self) -> None:
        html = """
        <html>
          <head>
            <title>《对话》TM Forum CTO George Glass | AN L5将让网络真正拥有“生命力” - 访谈 — C114通信网</title>
            <meta name="keywords" content="自智网络,TM Forum" />
            <meta name="description" content="《对话》TM Forum CTO George Glass | AN L5将让网络真正拥有“生命力”" />
          </head>
          <body>
            <!--2026-4-16 3:13:03-->
            <div class="article-info">发布时间：2026-4-14 09:00</div>
          </body>
        </html>
        """

        item = extract_article_metadata(
            url="https://www.c114.com.cn/video/5918/a1305191.html",
            html=html,
        )

        self.assertEqual(item.publish_date, date(2026, 4, 14))

    def test_extracts_article_top_time_when_publish_label_missing(self) -> None:
        html = """
        <html>
          <head>
            <title>Gartner预测，到2030年生成式AI推理成本将降低 - Cloud&AI — C114通信网</title>
            <meta name="description" content="Gartner预测，到2030年，生成式AI提供商对大语言模型的推理成本将降低。" />
          </head>
          <body>
            <!--2026-4-17 3:13:03-->
            <div class="article_top">
              <div class="time">2026/4/16 14:13</div>
              <h1 class="article_title">Gartner预测，到2030年生成式AI推理成本将降低</h1>
            </div>
          </body>
        </html>
        """

        item = extract_article_metadata(
            url="https://www.c114.com.cn/ai/5339/a1308581.html",
            html=html,
        )

        self.assertEqual(item.publish_date, date(2026, 4, 16))


class CollectDailyReportTests(unittest.TestCase):
    def test_applies_candidate_limit_after_date_filtering(self) -> None:
        listing_html = """
        <html>
          <body>
            <a href="https://www.c114.com.cn/video/5918/a1305191.html">旧视频没有列表日期</a>
            <div class="center_list">
              <a href="https://www.c114.com.cn/news/118/a1308582.html">
                <div class="title">中国移动绿色多频段基站天线补采，规模为19.07万面</div>
                <div class="text_bottom"><div class="time">4/16 14:14</div></div>
              </a>
            </div>
          </body>
        </html>
        """
        old_detail_html = """
        <html>
          <head>
            <title>旧视频没有列表日期 - 访谈 — C114通信网</title>
            <meta name="description" content="旧视频没有列表日期" />
          </head>
          <body><!--2026-4-16 3:13:03--></body>
        </html>
        """
        today_detail_html = """
        <html>
          <head>
            <title>中国移动绿色多频段基站天线补采，规模为19.07万面 - C114通信网</title>
            <meta name="description" content="中国移动绿色多频段基站天线补采，规模为19.07万面,C114讯 4月16日消息（焦焦）..." />
          </head>
          <body>
            <div class="article_top">
              <div class="time">2026/4/16 14:14</div>
            </div>
          </body>
        </html>
        """

        def fake_fetch_text(url: str, timeout: float = 20.0) -> str:
            del timeout
            if url == CHANNELS["home"].url:
                return listing_html
            if url.endswith("a1305191.html"):
                return old_detail_html
            if url.endswith("a1308582.html"):
                return today_detail_html
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("c114.c114_hot_topics.fetch_text", side_effect=fake_fetch_text):
            reports = collect_daily_report(
                date(2026, 4, 16),
                channel_keys=["home"],
                candidate_limit=1,
            )

        self.assertEqual(reports[0].article_count, 1)
        self.assertEqual(reports[0].articles[0].url, "https://www.c114.com.cn/news/118/a1308582.html")


class BuildKeywordSummaryTests(unittest.TestCase):
    def test_counts_keywords_from_articles(self) -> None:
        summary = build_keyword_summary(
            [
                ["量子", "融资", "量子城域网"],
                ["量子", "福建移动"],
                ["卫星互联网", "NTN"],
            ],
            limit=4,
        )

        self.assertEqual(summary[0], ("量子", 2))
        self.assertIn(("融资", 1), summary)


class SaveDailyReportCsvTests(unittest.TestCase):
    def test_prepends_latest_rows_and_deduplicates_by_date_and_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "c114_hot_topics.csv"
            csv_path.write_text(
                "统计日期,栏目键,栏目名称,栏目链接,栏目文章数,栏目热点词,文章标题,发布时间,关键词,摘要,文章链接\n"
                "2026-03-31,home,首页,https://www.c114.com.cn/,1,AI:1,旧文章,2026-03-31,AI,旧摘要,https://www.c114.com.cn/news/1/a1.html\n",
                encoding="utf-8",
            )
            reports = [
                ChannelDailyReport(
                    channel_key="home",
                    channel_name="首页",
                    channel_url="https://www.c114.com.cn/",
                    article_count=1,
                    hot_topics=[("量子", 1)],
                    articles=[
                        ArticleMetadata(
                            url="https://www.c114.com.cn/news/2/a2.html",
                            title="新文章",
                            publish_date=date(2026, 4, 2),
                            keywords=["量子"],
                            summary="新摘要",
                        )
                    ],
                )
            ]

            save_daily_report_csv(csv_path, date(2026, 4, 2), reports)

            with csv_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(rows[0]["统计日期"], "2026-04-02")
            self.assertEqual(rows[0]["文章标题"], "新文章")
            self.assertEqual(rows[1]["统计日期"], "2026-03-31")
            self.assertEqual(rows[1]["文章标题"], "旧文章")


if __name__ == "__main__":
    unittest.main()
