"""Step 1 CSV 读写能力。

本模块只负责原始 CSV 解析与 step 1 分析 CSV 输出。
它不做文章理解、不调用 LLM，也不决定运行目录。
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from .models import ArticleAnalysis, RawArticleRecord
from .normalization import is_c114_article_url, split_pipe_list

CSV_HEADERS = {
    "统计日期": "report_date",
    "栏目键": "channel_key",
    "栏目名称": "channel_name",
    "栏目链接": "channel_url",
    "栏目文章数": "channel_article_count",
    "栏目热点词": "channel_hot_topics",
    "文章标题": "title",
    "发布时间": "publish_date",
    "关键词": "keywords",
    "摘要": "summary",
    "文章链接": "url",
}

def load_daily_articles_from_csv(csv_text: str, report_date: str) -> list[RawArticleRecord]:
    """Load one day's raw article rows from the canonical C114 CSV export."""

    reader = csv.DictReader(StringIO(csv_text))
    rows: list[RawArticleRecord] = []
    seen_urls: set[str] = set()
    seen_c114_titles: set[str] = set()
    for row in reader:
        normalized = {CSV_HEADERS.get(key, key): (value or "").strip() for key, value in row.items()}
        if normalized.get("report_date") != report_date:
            continue
        title = normalized.get("title", "")
        url = normalized.get("url", "")
        if not title or not url:
            continue
        if url in seen_urls:
            continue
        if is_c114_article_url(url) and title in seen_c114_titles:
            continue
        seen_urls.add(url)
        if is_c114_article_url(url):
            seen_c114_titles.add(title)
        rows.append(
            RawArticleRecord(
                report_date=normalized["report_date"],
                channel_key=normalized["channel_key"],
                channel_name=normalized["channel_name"],
                channel_hot_topics=split_pipe_list(normalized.get("channel_hot_topics", "")),
                title=title,
                publish_date=normalized.get("publish_date", ""),
                keywords=split_pipe_list(normalized.get("keywords", "")),
                summary=normalized.get("summary", ""),
                url=url,
            )
        )
    return rows

def save_article_analysis_csv(output_path: Path, analyses: list[ArticleAnalysis]) -> None:
    """把 step 1 分析结果写成 CSV 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "统计日期",
                "栏目",
                "文章标题",
                "发布时间",
                "原始关键词",
                "归一关键词",
                "实体",
                "信号",
                "主题",
                "核心摘要",
                "延伸检索词",
                "文章链接",
            ],
        )
        writer.writeheader()
        for item in analyses:
            writer.writerow(
                {
                    "统计日期": item.report_date,
                    "栏目": item.channel_name,
                    "文章标题": item.title,
                    "发布时间": item.publish_date,
                    "原始关键词": "|".join(item.source_keywords),
                    "归一关键词": "|".join(item.normalized_keywords),
                    "实体": "|".join(item.entities),
                    "信号": "|".join(item.signals),
                    "主题": item.topic,
                    "核心摘要": item.core_summary,
                    "延伸检索词": "|".join(item.followup_queries),
                    "文章链接": item.url,
                }
            )
