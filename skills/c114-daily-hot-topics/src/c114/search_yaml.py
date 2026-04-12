"""C114 step 3 输入输出 YAML 解析与渲染。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .c114_intelligence import provider_stats_name
from .search_types import SearchArticleInput, SearchQuery, SearchResult, SearchWorkflowPayload


def load_search_checklist_yaml(input_path: Path) -> tuple[str, list[SearchArticleInput]]:
    report_date = ""
    current_topic = ""
    current_item: dict[str, Any] | None = None
    items: list[SearchArticleInput] = []

    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line.startswith("report_date: "):
            report_date = parse_yaml_value(line)
        elif line.startswith("  - topic: "):
            if current_item:
                items.append(build_article_input(current_topic, current_item))
                current_item = None
            current_topic = parse_yaml_value(line)
        elif line.startswith("      - original_title: "):
            if current_item:
                items.append(build_article_input(current_topic, current_item))
            current_item = {
                "original_title": parse_yaml_value(line),
                "channel": "",
                "url": "",
                "original_published_at": "",
                "keywords": [],
            }
        elif line.startswith("        channel: ") and current_item is not None:
            current_item["channel"] = parse_yaml_value(line)
        elif line.startswith("        url: ") and current_item is not None:
            current_item["url"] = parse_yaml_value(line)
        elif line.startswith("        original_published_at: ") and current_item is not None:
            current_item["original_published_at"] = parse_yaml_value(line)
        elif line.startswith("          - ") and current_item is not None:
            current_item["keywords"].append(parse_yaml_list_item(line))
    if current_item:
        items.append(build_article_input(current_topic, current_item))
    return report_date, items


def validate_search_checklist_items(items: list[SearchArticleInput]) -> None:
    incomplete = [item for item in items if len(item.keywords) != 2]
    if not incomplete:
        return
    details = "；".join(f"《{item.original_title}》当前为 {len(item.keywords)} 组" for item in incomplete[:5])
    if len(incomplete) > 5:
        details = f"{details}；其余 {len(incomplete) - 5} 条未展开"
    raise ValueError(
        "搜索清单 YAML 中存在未补全 keywords 的条目。请先完成 step 2 自动关键词生成后再运行 c114-search。"
        f" {details}"
    )


def ensure_search_checklist_keywords(items: list[SearchArticleInput], llm_client: Any | None) -> list[SearchArticleInput]:
    from .c114_intelligence import ArticleAnalysis, SearchChecklistItem, autofill_search_checklist_items

    incomplete = [item for item in items if len(item.keywords) != 2]
    if not incomplete:
        return items
    if llm_client is None:
        validate_search_checklist_items(items)
        return items

    checklist_items = [
        SearchChecklistItem(
            report_date="",
            channel_name=item.channel,
            title=item.original_title,
            topic=item.topic,
            search_queries=list(item.keywords),
            publish_date=item.original_published_at,
            url=item.original_url,
        )
        for item in items
    ]
    analyses = [
        ArticleAnalysis(
            report_date="",
            channel_key="",
            channel_name=item.channel,
            title=item.original_title,
            publish_date=item.original_published_at,
            source_keywords=[],
            normalized_keywords=[],
            entities=[],
            signals=[],
            topic=item.topic,
            core_summary="",
            followup_queries=[],
            url=item.original_url,
        )
        for item in items
    ]
    completed = autofill_search_checklist_items(checklist_items, analyses, llm_client)
    return [
        SearchArticleInput(
            topic=item.topic,
            channel=item.channel_name,
            original_title=item.title,
            original_url=item.url,
            original_published_at=item.publish_date,
            keywords=list(item.search_queries),
        )
        for item in completed
    ]


def build_article_input(topic: str, payload: dict[str, Any]) -> SearchArticleInput:
    return SearchArticleInput(
        topic=topic,
        channel=payload["channel"],
        original_title=payload["original_title"],
        original_url=payload["url"],
        original_published_at=str(payload.get("original_published_at", "")),
        keywords=list(payload["keywords"]),
    )


def build_search_queries(article: SearchArticleInput) -> list[SearchQuery]:
    if len(article.keywords) != 2:
        raise ValueError(f"搜索清单中的关键词数量必须为 2，当前《{article.original_title}》为 {len(article.keywords)}。")
    return [
        SearchQuery(query_type="title", value=article.original_title),
        SearchQuery(query_type="keyword", value=article.keywords[0]),
        SearchQuery(query_type="keyword", value=article.keywords[1]),
    ]


def render_search_results_yaml(payload: SearchWorkflowPayload) -> str:
    from .search_review import SEARCH_REVIEW_PROMPT_PATH

    lines = [
        f"report_date: '{payload.report_date}'",
        f"provider: '{escape_yaml_scalar(payload.provider)}'",
        f"input_path: '{escape_yaml_scalar(str(payload.input_path))}'",
        f"review_prompt_path: '{escape_yaml_scalar(str(SEARCH_REVIEW_PROMPT_PATH))}'",
        "review_instructions:",
        "  - 'skill 内置模型会读取 review_prompt_path 指向的提示词文件。'",
        "  - '仅对 selected_results 下各条结果生成 ai_review 字段。'",
        "  - '必须逐条填写所有 selected_results；不得留空、不得跳过、不得只填一部分。'",
        "  - 'ai_review.status 固定填写 reviewed。'",
        "  - 'ai_review.keep_level 只能填写 strong、weak、drop 三档。'",
        "  - '不得改写原标题、原链接、搜索结果元数据。'",
        f"generated_at: '{escape_yaml_scalar(payload.generated_at)}'",
        "categories:",
    ]
    for category in payload.categories:
        lines.append(f"  - topic: '{escape_yaml_scalar(category.topic)}'")
        lines.append("    items:")
        for item in category.items:
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml_scalar(item.original_title)}'",
                    f"        channel: '{escape_yaml_scalar(item.channel)}'",
                    f"        original_url: '{escape_yaml_scalar(item.original_url)}'",
                    f"        original_published_at: '{escape_yaml_scalar(item.original_published_at)}'",
                    "        queries:",
                ]
            )
            for query in item.queries:
                lines.extend(
                    [
                        f"          - query: '{escape_yaml_scalar(query.query)}'",
                        f"            query_type: '{escape_yaml_scalar(query.query_type)}'",
                        f"            provider: '{escape_yaml_scalar(query.provider)}'",
                        "            results:",
                    ]
                )
                lines.extend(render_result_list(query.results, indent="              "))
            lines.append("        search_results:")
            lines.extend(render_result_list(item.search_results, indent="          "))
            lines.append("        selected_results:")
            lines.extend(render_result_list(item.selected_results, indent="          ", include_ai_review=True))
    return "\n".join(lines)


def render_result_list(results: list[SearchResult], indent: str, *, include_ai_review: bool = False) -> list[str]:
    lines: list[str] = []
    for result in results:
        lines.extend(
            [
                f"{indent}- query: '{escape_yaml_scalar(result.query)}'",
                f"{indent}  query_type: '{escape_yaml_scalar(result.query_type)}'",
                f"{indent}  result_title: '{escape_yaml_scalar(result.result_title)}'",
                f"{indent}  url: '{escape_yaml_scalar(result.url)}'",
                f"{indent}  domain: '{escape_yaml_scalar(result.domain)}'",
                f"{indent}  published_at: '{escape_yaml_scalar(result.published_at)}'",
                f"{indent}  snippet: '{escape_yaml_scalar(result.snippet)}'",
                f"{indent}  score: {result.score:.4f}",
                f"{indent}  is_official: {'true' if result.is_official else 'false'}",
                f"{indent}  source_tier: '{escape_yaml_scalar(result.source_tier)}'",
                f"{indent}  extract_status: '{escape_yaml_scalar(result.extract_status)}'",
                f"{indent}  extract_text: '{escape_yaml_scalar(result.extract_text)}'",
                f"{indent}  matched_terms:",
            ]
        )
        for term in result.matched_terms:
            lines.append(f"{indent}    - '{escape_yaml_scalar(term)}'")
        if include_ai_review:
            lines.extend(
                [
                    f"{indent}  ai_review:",
                    f"{indent}    status: '{escape_yaml_scalar(result.review_status)}'",
                    f"{indent}    keep_level: '{escape_yaml_scalar(result.keep_level)}'",
                    f"{indent}    reason: '{escape_yaml_scalar(result.review_reason)}'",
                    f"{indent}    relevance_note: '{escape_yaml_scalar(result.relevance_note)}'",
                    f"{indent}    value_type: '{escape_yaml_scalar(result.value_type)}'",
                ]
            )
    return lines


def save_search_results(output_path: Path, payload: SearchWorkflowPayload) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_search_results_yaml(payload), encoding="utf-8")
    stats_path = output_path.parent / provider_stats_name(date.fromisoformat(payload.report_date))
    stats_path.write_text(render_provider_usage_stats(payload), encoding="utf-8")
    review_view_path = output_path.parent / build_review_view_name(payload.report_date)
    review_view_path.write_text(render_search_review_view(payload), encoding="utf-8")


def build_review_view_name(report_date: str) -> str:
    return f"c114_search_review_view_{report_date.replace('-', '')}.yaml"


def render_search_review_view(payload: SearchWorkflowPayload) -> str:
    counts = {"strong": 0, "weak": 0, "drop": 0, "pending": 0}
    lines = [
        f"report_date: '{payload.report_date}'",
        f"input_path: '{escape_yaml_scalar(str(payload.input_path))}'",
        "categories:",
    ]
    for category in payload.categories:
        lines.append(f"  - topic: '{escape_yaml_scalar(category.topic)}'")
        lines.append("    items:")
        for item in category.items:
            grouped = {"strong": [], "weak": [], "drop": [], "pending": []}
            for result in item.selected_results:
                level = result.keep_level if result.keep_level in {"strong", "weak", "drop"} else "pending"
                counts[level] += 1
                grouped[level].append(result)
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml_scalar(item.original_title)}'",
                    f"        original_url: '{escape_yaml_scalar(item.original_url)}'",
                    "        review_groups:",
                ]
            )
            for level in ("strong", "weak", "drop", "pending"):
                lines.append(f"          {level}:")
                for result in grouped[level]:
                    lines.extend(
                        [
                            f"            - title: '{escape_yaml_scalar(result.result_title)}'",
                            f"              url: '{escape_yaml_scalar(result.url)}'",
                            f"              reason: '{escape_yaml_scalar(result.review_reason)}'",
                            f"              value_type: '{escape_yaml_scalar(result.value_type)}'",
                        ]
                    )
    lines.extend(
        [
            "summary:",
            f"  strong: {counts['strong']}",
            f"  weak: {counts['weak']}",
            f"  drop: {counts['drop']}",
            f"  pending: {counts['pending']}",
        ]
    )
    return "\n".join(lines)


def render_provider_usage_stats(payload: SearchWorkflowPayload) -> str:
    query_provider_counts: dict[str, int] = {}
    total_queries = 0
    selected_articles = 0
    extract_success = 0
    extract_failed = 0

    for category in payload.categories:
        for item in category.items:
            for query in item.queries:
                total_queries += 1
                query_provider_counts[query.provider] = query_provider_counts.get(query.provider, 0) + 1
            if item.selected_results:
                selected_articles += 1
            for result in item.selected_results:
                if result.extract_status == "success":
                    extract_success += 1
                elif result.extract_status == "failed":
                    extract_failed += 1

    extract_calls = selected_articles
    total_calls = sum(query_provider_counts.values()) + extract_calls
    lines = [
        f"# C114 {payload.report_date} Provider 用量统计",
        "",
        f"- 搜索模式：`{payload.provider}`",
        f"- 输入清单：`{payload.input_path}`",
        f"- query 总数：`{total_queries}`",
        f"- provider 总调用次数：`{total_calls}`",
        "",
        "## Query Search（搜索）调用",
        "",
    ]
    for provider_name in ("tavily", "baidu", "metaso", "google"):
        count = query_provider_counts.get(provider_name, 0)
        lines.append(f"- {provider_name.title()}: `{count}`")
    lines.extend(
        [
            "",
            "## Tavily Extract（补充正文摘要）调用",
            "",
            f"- Tavily extract 调用次数：`{extract_calls}`",
            f"- extract success（成功 URL 数）：`{extract_success}`",
            f"- extract failed（失败 URL 数）：`{extract_failed}`",
        ]
    )
    return "\n".join(lines)


def parse_yaml_value(line: str) -> str:
    value = line.split(":", 1)[1].strip()
    return unquote_yaml_scalar(value)


def parse_yaml_list_item(line: str) -> str:
    return unquote_yaml_scalar(line.split("- ", 1)[1].strip())


def unquote_yaml_scalar(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("'") and stripped.endswith("'"):
        return stripped[1:-1].replace("''", "'")
    return stripped


def escape_yaml_scalar(value: str) -> str:
    return value.replace("'", "''")
