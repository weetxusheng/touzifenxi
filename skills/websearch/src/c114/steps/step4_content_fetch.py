"""Step 4 正文抓取编排。

本模块负责读取 step 3 结果、并发抓取原文与补充链接正文，并产出 step 4 结构化结果。
抓取策略和 HTML 提取已经下沉到 content 包，避免 workflow 文件继续膨胀。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..content.fetch import AliyunIQSClient, FetchResult, fetch_article_contents, fetch_url_content
from ..content.types import (
    VALID_KEEP_LEVELS,
    ArticleContentPayload,
    ContentCategoryPayload,
    ContentWorkflowPayload,
    SearchContentArticleInput,
    SearchResultsInputPayload,
)
from ..content.yaml_io import load_search_results_yaml
from ..runtime.checkpoint import StepCheckpointStore
from ..runtime.config import load_c114_runtime_config


def run_content_fetch_workflow(
    input_path: Path,
    report_date: str,
    fetcher: Callable[[str], FetchResult] | None = None,
    aliyun_client: AliyunIQSClient | None = None,
    generated_at: str | None = None,
    checkpoint_store: StepCheckpointStore | None = None,
) -> ContentWorkflowPayload:
    """Execute step 4 for one report date and return the full content payload."""
    source = load_search_results_yaml(input_path)
    if source.report_date and source.report_date != report_date:
        raise ValueError(f"输入搜索结果日期为 {source.report_date}，与命令日期 {report_date} 不一致。")
    validate_content_fetch_inputs(source)

    runtime_config = load_c114_runtime_config(Path(__file__).resolve().parents[2])
    content_fetcher = fetcher or (lambda url: fetch_url_content(url, timeout=runtime_config.request_timeout_seconds))
    content_search_client = aliyun_client if aliyun_client is not None else AliyunIQSClient.from_env()
    cache: dict[str, FetchResult] = {}
    flattened_articles: list[tuple[int, int, SearchContentArticleInput]] = []
    for category_index, category in enumerate(source.categories):
        for item_index, article in enumerate(category.items):
            flattened_articles.append((category_index, item_index, article))

    max_workers = min(8, max(1, len(flattened_articles)))
    results_by_position: dict[tuple[int, int], ArticleContentPayload] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_pairs = [
            (
                (category_index, item_index),
                executor.submit(
                    fetch_article_contents,
                    article,
                    report_date=report_date,
                    fetcher=content_fetcher,
                    cache=cache,
                    aliyun_client=content_search_client,
                    allowed_keep_levels=runtime_config.content_fetch_keep_levels,
                    checkpoint_store=checkpoint_store,
                ),
            )
            for category_index, item_index, article in flattened_articles
        ]
        for position, future in future_pairs:
            results_by_position[position] = future.result()

    categories = []
    for category_index, category in enumerate(source.categories):
        items = [
            results_by_position[(category_index, item_index)] for item_index, _article in enumerate(category.items)
        ]
        categories.append(ContentCategoryPayload(topic=category.topic, items=items))
    return ContentWorkflowPayload(
        report_date=report_date,
        input_path=input_path,
        generated_at=generated_at or datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )


def validate_content_fetch_inputs(source: SearchResultsInputPayload) -> None:
    """Fail fast when step 3 still contains pending AI review decisions."""

    pending: list[str] = []
    for category in source.categories:
        for article in category.items:
            invalid = [
                result
                for result in article.selected_results
                if result.review_status != "reviewed" or result.keep_level not in VALID_KEEP_LEVELS
            ]
            if not invalid:
                continue
            preview = "；".join((result.result_title or result.url) for result in invalid[:2])
            pending.append(f"{article.original_title} -> {preview}")
    if not pending:
        return

    details = "\n".join(f"- {item}" for item in pending[:5])
    raise ValueError(
        "step 3 中仍有补充链接未完成 ai_review，请先重新运行 c114-search，"
        "由 skill 内置模型为每条 selected_results 补齐 review_status=reviewed 与 keep_level=strong/weak/drop，"
        "再运行 c114-fetch-content。\n"
        f"{details}"
    )
