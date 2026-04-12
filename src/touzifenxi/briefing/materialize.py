from __future__ import annotations

import csv
from collections import Counter, defaultdict
from io import StringIO
from pathlib import Path

from touzifenxi.content_sources.models import StandardArticle

STEP1_CSV_FIELDNAMES = [
    "统计日期",
    "栏目键",
    "栏目名称",
    "栏目链接",
    "栏目文章数",
    "栏目热点词",
    "文章标题",
    "发布时间",
    "关键词",
    "摘要",
    "文章链接",
]


def build_step1_csv_rows(report_date: str, articles: list[StandardArticle]) -> list[dict[str, str]]:
    """把标准文章结构桥接成现有 step 1 可消费的 CSV 行。"""

    grouped_articles: dict[tuple[str, str, str], list[StandardArticle]] = defaultdict(list)
    for article in articles:
        channel_key = str(article.metadata.get("channel_key") or _default_channel_key(article.channel))
        channel_name = str(article.metadata.get("channel_name") or article.channel or article.source_bucket or "未分类")
        channel_url = str(article.metadata.get("channel_url") or article.url)
        grouped_articles[(channel_key, channel_name, channel_url)].append(article)

    rows: list[dict[str, str]] = []
    for (channel_key, channel_name, channel_url), grouped in grouped_articles.items():
        hot_topics = _build_hot_topics(grouped)
        hot_topics_text = "|".join(f"{keyword}:{count}" for keyword, count in hot_topics)
        article_count = str(len(grouped))
        for article in grouped:
            rows.append(
                {
                    "统计日期": report_date,
                    "栏目键": channel_key,
                    "栏目名称": channel_name,
                    "栏目链接": channel_url,
                    "栏目文章数": article_count,
                    "栏目热点词": hot_topics_text,
                    "文章标题": article.title,
                    "发布时间": article.published_at,
                    "关键词": "|".join(article.keywords),
                    "摘要": article.summary,
                    "文章链接": article.url,
                }
            )
    return rows


def write_step1_csv(output_path: Path, report_date: str, articles: list[StandardArticle]) -> None:
    """写出兼容现有 step 1 的 CSV 文件。"""

    rows = build_step1_csv_rows(report_date, articles)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=STEP1_CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    output_path.write_text(buffer.getvalue(), encoding="utf-8")


def _build_hot_topics(articles: list[StandardArticle], limit: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for article in articles:
        seeds = article.keywords or article.tags
        for token in seeds:
            cleaned = str(token).strip()
            if cleaned:
                counter[cleaned] += 1
    return counter.most_common(limit)


def _default_channel_key(value: str) -> str:
    token = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    token = token.strip("-")
    return token or "general"
