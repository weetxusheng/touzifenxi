from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from c114.c114_hot_topics import (
    CHANNELS,
    ArticleMetadata,
    ChannelDailyReport,
    build_keyword_summary,
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
