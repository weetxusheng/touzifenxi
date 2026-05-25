from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from feedcore.models import Article
from feedcore.rss import parse_feed
from feedcore.workflow.concurrent import (
    ConcurrentWorkflowClients,
    run_concurrent_workflow,
    run_concurrent_workflow_from_prefetched,
)
from feedcore.workflow.run_context import RunContext
from feedcore.workflow.steps import (
    ArticleClient,
    RssClient,
    SummaryClient,
    Translator,
    fetch_article_contents,
    fetch_feed_items,
    generate_article_four_dims,
    generate_subcategory_four_dims,
    group_validated_subcategories,
    plan_dynamic_subcategories,
    select_rss_sources,
)


@dataclass(frozen=True)
class WorkflowClients:
    rss: RssClient
    article: ArticleClient
    summary: SummaryClient
    translator: Translator | None = None


@dataclass(frozen=True)
class WorkflowResult:
    run_id: str
    run_dir: Path
    brief_md: Path
    brief_html: Path


def run_workflow(
    *,
    rss_urls: list[str],
    output_dir: Path,
    clients: WorkflowClients,
    parse_feed_fn: Callable[[str, str], list[Article]] = parse_feed,
    default_categories: dict[str, str] | None = None,
    run_id: str | None = None,
    quick_sample_size: int | None = None,
    max_articles: int | None = None,
    rss_concurrency: int = 10,
    article_fetch_concurrency: int = 5,
    model_concurrency: int = 6,
    type_classification_concurrency: int = 2,
    type_synthesis_concurrency: int = 2,
) -> WorkflowResult:
    result = run_concurrent_workflow(
        rss_urls=rss_urls,
        output_dir=output_dir,
        clients=ConcurrentWorkflowClients(
            rss=clients.rss,
            article=clients.article,
            summary=clients.summary,
            translator=clients.translator,
        ),
        parse_feed_fn=parse_feed_fn,
        default_categories=default_categories,
        run_id=run_id,
        quick_sample_size=quick_sample_size,
        max_articles=max_articles,
        rss_concurrency=rss_concurrency,
        article_fetch_concurrency=article_fetch_concurrency,
        model_concurrency=model_concurrency,
        type_classification_concurrency=type_classification_concurrency,
        type_synthesis_concurrency=type_synthesis_concurrency,
    )
    return WorkflowResult(
        run_id=result.run_id,
        run_dir=result.run_dir,
        brief_md=result.brief_md,
        brief_html=result.brief_html,
    )


def run_workflow_from_prefetched(
    *,
    output_dir: Path,
    clients: WorkflowClients,
    rss_sources: list[dict[str, object]],
    feed_items: list[dict[str, object]],
    article_contents: list[dict[str, object]],
    run_id: str | None = None,
    model_concurrency: int = 6,
    type_classification_concurrency: int = 2,
    type_synthesis_concurrency: int = 2,
) -> WorkflowResult:
    result = run_concurrent_workflow_from_prefetched(
        output_dir=output_dir,
        clients=ConcurrentWorkflowClients(
            rss=clients.rss,
            article=clients.article,
            summary=clients.summary,
            translator=clients.translator,
        ),
        rss_sources=rss_sources,
        feed_items=feed_items,
        article_contents=article_contents,
        run_id=run_id,
        model_concurrency=model_concurrency,
        type_classification_concurrency=type_classification_concurrency,
        type_synthesis_concurrency=type_synthesis_concurrency,
    )
    return WorkflowResult(
        run_id=result.run_id,
        run_dir=result.run_dir,
        brief_md=result.brief_md,
        brief_html=result.brief_html,
    )


def render_brief_markdown(subcategory_dims) -> str:
    lines = ["# 国际新闻简报", ""]
    current_category = ""
    for item in subcategory_dims:
        if item.parent_category != current_category:
            current_category = item.parent_category
            lines.extend([f"## {item.parent_category}", ""])
        lines.extend(
            [
                f"### {item.subcategory}",
                "",
                "#### 事实",
                *_bullet_lines(item.facts),
                "",
                "#### 背景",
                *_bullet_lines(item.background),
                "",
                "#### 产生的影响",
                *_bullet_lines(item.impact),
                "",
                "#### 反面观点 / 数据矛盾点",
                *_bullet_lines(item.contradictions),
                "",
            ]
        )
        if item.source_links:
            lines.extend(["来源：", *[f"- {link}" for link in item.source_links], ""])
    return "\n".join(lines).rstrip() + "\n"


def render_subcategory_plan_yaml(*, report_date: str, input_path: str, plans, groups=None, articles=None) -> str:
    article_lookup = _category_article_lookup(articles or [])
    lines = [
        f"report_date: '{_yaml_quote(report_date)}'",
        f"input_path: '{_yaml_quote(input_path)}'",
        "categories:",
    ]
    for plan in plans:
        lines.extend(
            [
                f"  - topic: '{_yaml_quote(plan.parent_category)}'",
                "    subcategories:",
            ]
        )
        for subcategory in plan.subcategories:
            lines.extend(
                [
                    f"      - name: '{_yaml_quote(subcategory.name)}'",
                    f"        rationale: '{_yaml_quote(subcategory.rationale)}'",
                    "        items:",
                ]
            )
            for article_id in subcategory.article_ids:
                article = article_lookup.get((plan.parent_category, article_id))
                lines.extend(
                    [
                        f"          - article_id: '{_yaml_quote(article_id)}'",
                        f"            original_title: '{_yaml_quote(article.article.title if article else '')}'",
                        f"            original_url: '{_yaml_quote(article.article.link if article else '')}'",
                        "            review_groups:",
                        "              strong:",
                        "              weak:",
                        "              drop:",
                        "              pending:",
                    ]
                )
        for item in plan.ungrouped:
            lines.extend(
                [
                    "      - name: 'ungrouped'",
                    f"        article_id: '{_yaml_quote(item.get('article_id', ''))}'",
                    f"        reason: '{_yaml_quote(item.get('reason', ''))}'",
                    "        review_groups:",
                    "          strong:",
                    "          weak:",
                    "          drop:",
                    "          pending:",
                ]
            )
        if plan.validation_errors:
            lines.append("    validation_errors:")
            lines.extend([f"      - '{_yaml_quote(error)}'" for error in plan.validation_errors])
    lines.extend(["summary:", "  strong: 0", "  weak: 0", "  drop: 0", "  pending: 0", ""])
    return "\n".join(lines)


def render_brief_html(markdown: str) -> str:
    body_lines: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<h1>{html.escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<h2>{html.escape(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<h3>{html.escape(line[4:].strip())}</h3>")
        elif line.startswith("#### "):
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<h4>{html.escape(line[5:].strip())}</h4>")
        elif line.startswith("- "):
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{html.escape(line[2:].strip())}</li>")
        else:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        body_lines.append("</ul>")
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>国际新闻简报</title>",
            "  <style>body{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:900px;margin:40px auto;padding:0 20px;line-height:1.7;color:#172033}h1,h2,h3{line-height:1.3}h2{border-top:1px solid #e5e7eb;padding-top:24px}li{margin:6px 0}</style>",
            "</head>",
            "<body>",
            *body_lines,
            "</body>",
            "</html>",
            "",
        ]
    )


def _bullet_lines(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values] if values else ["- 未形成可追溯要点。"]


def _content_checkpoint(item) -> dict[str, object]:
    return {
        "article": item.article.to_dict(),
        "default_category": item.default_category,
        "detected_language": item.detected_language,
        "translated": item.translated,
        "fetch_error": item.fetch_error,
    }


def _report_date() -> str:
    cn = timezone(timedelta(hours=8))
    return datetime.now(cn).strftime("%Y-%m-%d")


def _yaml_quote(value: object) -> str:
    return str(value or "").replace("'", "''")


def _category_article_lookup(articles) -> dict[tuple[str, str], object]:
    lookup = {}
    counters: dict[str, int] = {}
    for article in articles:
        counters[article.final_category] = counters.get(article.final_category, 0) + 1
        lookup[(article.final_category, f"A{counters[article.final_category]}")] = article
    return lookup
