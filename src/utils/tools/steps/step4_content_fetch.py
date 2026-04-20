"""Step 4 正文抓取编排。

本模块负责读取 step 3 结果、并发抓取原文与补充链接正文，并产出 step 4 结构化结果。
抓取策略和 HTML 提取已经下沉到 content 包，避免 workflow 文件继续膨胀。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import re
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
from utils.tools.runtime.checkpoint import StepCheckpointStore
from c114.runtime.config import load_c114_runtime_config


def _count_selected_results_links(source: SearchResultsInputPayload) -> int:
    total = 0
    for category in source.categories:
        for article in category.items:
            total += len(article.selected_results)
    return total


def _count_fetchable_records(items: list[ArticleContentPayload]) -> tuple[int, int, int]:
    """返回 (总条数, 成功条数, 失败条数)，含原文与补充链接。"""

    def ok_status(fetch_status: str, fetch_error: str) -> bool:
        st = str(fetch_status or "").strip().lower()
        if st not in {"", "success"}:
            return False
        return str(fetch_error or "").strip() == ""

    total = 0
    ok = 0
    bad = 0
    for article in items:
        oc = article.original_content
        total += 1
        if ok_status(oc.fetch_status, oc.fetch_error):
            ok += 1
        else:
            bad += 1
        for sel in article.selected_contents:
            total += 1
            if ok_status(sel.fetch_status, sel.fetch_error):
                ok += 1
            else:
                bad += 1
    return total, ok, bad


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
    selected_before_dedup = _count_selected_results_links(source)
    source, removed_title_dupes = deduplicate_titles_before_step4(source)
    selected_after_title_dedup = _count_selected_results_links(source)

    runtime_config = load_c114_runtime_config(Path(__file__).resolve().parents[4])
    content_fetcher = fetcher or (lambda url: fetch_url_content(url, timeout=runtime_config.request_timeout_seconds))
    content_search_client = aliyun_client if aliyun_client is not None else AliyunIQSClient.from_env()
    if content_search_client is not None:
        reset_take = getattr(content_search_client, "take_search_http_rounds", None)
        if callable(reset_take):
            reset_take()
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
    removed_body_dupes = 0
    for category_index, category in enumerate(source.categories):
        items = [
            results_by_position[(category_index, item_index)] for item_index, _article in enumerate(category.items)
        ]
        items, removed_in_topic = deduplicate_selected_contents_in_topic(items)
        removed_body_dupes += removed_in_topic
        categories.append(ContentCategoryPayload(topic=category.topic, items=items))
    fetch_total, fetch_ok, fetch_bad = _count_fetchable_records(
        [article for category in categories for article in category.items]
    )
    iqs_rounds = 0
    if content_search_client is not None:
        take_iqs = getattr(content_search_client, "take_search_http_rounds", None)
        iqs_rounds = int(take_iqs()) if callable(take_iqs) else 0
    print(
        "[C114][Step4 正文抓取] "
        f"入选链接(去重前)={selected_before_dedup} | "
        f"标题去重剔除={removed_title_dupes} | "
        f"入选链接(标题去重后)={selected_after_title_dedup} | "
        f"补充正文合并去重剔除={removed_body_dupes} | "
        f"抓取条目(原文+补充)={fetch_total} | 成功={fetch_ok} | 失败={fetch_bad} | "
        f"阿里云IQS HTTP轮次={iqs_rounds}"
    )
    return ContentWorkflowPayload(
        report_date=report_date,
        input_path=input_path,
        generated_at=generated_at or datetime.now().isoformat(timespec="seconds"),
        categories=categories,
    )


def deduplicate_titles_before_step4(source: SearchResultsInputPayload) -> tuple[SearchResultsInputPayload, int]:
    """在进入 step 4 前按标题规范化键去重，避免重复抓取。"""

    removed = 0
    deduped_categories = []
    for category in source.categories:
        deduped_items = []
        for article in category.items:
            seen_titles: set[str] = set()
            deduped_results = []
            for result in article.selected_results:
                title_key = normalize_step4_result_title_key(result.result_title)
                if not title_key:
                    title_key = str(result.url).strip()
                if not title_key:
                    continue
                if title_key in seen_titles:
                    removed += 1
                    continue
                seen_titles.add(title_key)
                deduped_results.append(result)
            deduped_items.append(replace(article, selected_results=deduped_results))
        deduped_categories.append(replace(category, items=deduped_items))
    return replace(source, categories=deduped_categories), removed


def normalize_step4_result_title_key(title: str) -> str:
    """提取用于 step 4 去重的标题主干键。"""

    normalized = str(title or "").strip()
    if not normalized:
        return ""
    # 先去掉常见“来源站点”尾巴：` - 站点名` / `_站点名`
    normalized = re.sub(r"\s*[-_]\s*[^-_]+$", "", normalized)
    # 再截断常见聚合站拼接的 `|` 扩展片段
    normalized = normalized.split("|", 1)[0].strip()
    # 去掉全部空白（含半角/全角空格与换行），使「仅中间空格不同」的标题与无空格写法视为同一键
    return re.sub(r"\s+", "", normalized)


def deduplicate_selected_contents_in_topic(items: list[ArticleContentPayload]) -> tuple[list[ArticleContentPayload], int]:
    """在同一 topic 内按补充链接标题主干去重，URL 作为兜底键。"""

    removed = 0
    seen_title_keys: set[str] = set()
    seen_urls: set[str] = set()
    deduped_items: list[ArticleContentPayload] = []
    for item in items:
        deduped_selected = []
        for selected in item.selected_contents:
            normalized_url = str(selected.url or "").strip()
            if not normalized_url:
                continue
            if normalized_url in seen_urls:
                removed += 1
                continue
            raw_title = str(selected.content_title or selected.result_title or "").strip()
            title_key = normalize_step4_result_title_key(raw_title)
            if title_key and title_key in seen_title_keys:
                removed += 1
                continue
            seen_urls.add(normalized_url)
            if title_key:
                seen_title_keys.add(title_key)
            deduped_selected.append(selected)
        deduped_items.append(replace(item, selected_contents=deduped_selected))
    return deduped_items, removed


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
