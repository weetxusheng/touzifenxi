from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Protocol

from .brief_taxonomy import classify_topic, sort_topics_present
from .content_scrubber import scrub_extracted_noise
from .brief_validation import compact_four_dimension_brief, brief_passes_four_dimension_gate
from .models import Article, ArticleBrief
from .rss import parse_feed


class RssClient(Protocol):
    def fetch(self, url: str) -> str:
        pass


class ArticleTextClient(Protocol):
    def fetch_text(self, url: str) -> str:
        pass


class SummaryClient(Protocol):
    def summarize_article(self, article: Article, content: str) -> str:
        pass


class RequestsRssClient:
    def __init__(self, timeout: int = 20, user_agent: str = "FeedCoreNewsBrief/0.1") -> None:
        self.timeout = timeout
        self.user_agent = user_agent

    def fetch(self, url: str) -> str:
        import requests

        response = requests.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text


def run_pipeline(
    rss_urls: list[str],
    output_dir: Path,
    max_articles: int | None,
    rss_client: RssClient,
    article_client: ArticleTextClient,
    summary_client: SummaryClient,
    parse_feed_fn: Callable[[str, str], list[Article]] = parse_feed,
    article_fetch_timeout_seconds: float = 180.0,
    similar_article_threshold: float | None = 0.7,
    clean_content: Callable[[Article, str], str] | None = None,
    require_substantive_four_dimensions: bool = True,
) -> list[ArticleBrief]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fetch_logs: list[str] = []
    cap = "unlimited" if max_articles is None else str(max_articles)
    print(f"[INFO] Starting pipeline with {len(rss_urls)} RSS sources, max_articles={cap}", flush=True)
    articles = _collect_articles(
        rss_urls,
        max_articles,
        rss_client,
        parse_feed_fn,
        fetch_logs,
        similar_article_threshold=similar_article_threshold,
    )
    print(f"[INFO] Collected {len(articles)} unique article(s) for content fetching.", flush=True)
    briefs: list[ArticleBrief] = []
    apply_clean = clean_content if clean_content is not None else (lambda _a, t: scrub_extracted_noise(t))

    for index, article in enumerate(articles, start=1):
        print(f"[INFO] [{index}/{len(articles)}] Fetching article: {article.title}", flush=True)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_fetch_article_content, article, article_client)
        try:
            content, fetch_error = future.result(timeout=article_fetch_timeout_seconds)
        except FuturesTimeout:
            msg = (
                f"ARTICLE_SKIPPED_TIMEOUT | timeout_sec={article_fetch_timeout_seconds} | "
                f"title={article.title} | url={article.link}"
            )
            fetch_logs.append(msg)
            print(
                f"[WARN] Skipped article (fetch exceeded {article_fetch_timeout_seconds}s): {article.link}",
                flush=True,
            )
            executor.shutdown(wait=False)
            continue
        executor.shutdown(wait=True)
        if fetch_error:
            fetch_logs.append(
                f"ARTICLE_FETCH_FAIL | title={article.title} | url={article.link} | error={fetch_error}"
            )
            print(f"[WARN] Article fetch failed; using fallback content: {article.link}", flush=True)
        else:
            fetch_logs.append(f"ARTICLE_FETCH_OK | title={article.title} | url={article.link}")
            print(f"[INFO] Article fetch ok: {article.link}", flush=True)
        content = apply_clean(article, content)
        brief = summary_client.summarize_article(article, content)
        brief = compact_four_dimension_brief(brief, content)
        if require_substantive_four_dimensions and not getattr(
            summary_client, "skip_four_dimension_validation", False
        ):
            if not brief_passes_four_dimension_gate(brief):
                msg = f"BRIEF_SKIPPED_INCOMPLETE_FOUR_DIM | title={article.title} | url={article.link}"
                fetch_logs.append(msg)
                print(
                    f"[WARN] Skipped article (brief missing substantive four dimensions): {article.link}",
                    flush=True,
                )
                continue
        one_liner = _optional_one_liner(summary_client, article, brief)
        briefs.append(
            ArticleBrief(
                article=article,
                content=content,
                brief=brief,
                fetch_error=fetch_error,
                one_liner=one_liner,
            )
        )

    _write_json(output_dir / "articles.json", briefs)
    _write_markdown(output_dir / "brief.md", briefs)
    _write_fetch_log(output_dir / "fetch.log", fetch_logs)
    print(f"[INFO] Wrote outputs to: {output_dir}", flush=True)
    return briefs


def _optional_one_liner(client: object, article: Article, brief: str) -> str | None:
    fn = getattr(client, "one_sentence_from_brief", None)
    if not callable(fn):
        return None
    try:
        out = fn(article, brief)
        if isinstance(out, str):
            s = out.strip()
            return s if s else None
        return None
    except Exception as exc:
        print(f"[WARN] one_sentence_from_brief failed | title={article.title!r} | {exc}", flush=True)
        return None


def _fetch_article_content(article: Article, article_client: ArticleTextClient) -> tuple[str, str | None]:
    try:
        return scrub_extracted_noise(article_client.fetch_text(article.link)), None
    except Exception as exc:
        fallback = article.description or article.title
        return scrub_extracted_noise(fallback), str(exc)


def _collect_articles(
    rss_urls: list[str],
    max_articles: int | None,
    rss_client: RssClient,
    parse_feed_fn: Callable[[str, str], list[Article]],
    fetch_logs: list[str],
    similar_article_threshold: float | None = 0.7,
) -> list[Article]:
    articles: list[Article] = []
    seen_links: set[str] = set()
    for index, rss_url in enumerate(rss_urls, start=1):
        print(f"[INFO] [{index}/{len(rss_urls)}] Fetching RSS: {rss_url}", flush=True)
        try:
            feed_text = rss_client.fetch(rss_url)
            fetch_logs.append(f"RSS_FETCH_OK | url={rss_url}")
            print("[INFO] RSS fetch ok", flush=True)
        except Exception as exc:
            fetch_logs.append(f"RSS_FETCH_FAIL | url={rss_url} | error={exc}")
            print(f"[WARN] RSS fetch failed (skip feed): {rss_url} | {exc}", flush=True)
            continue

        parsed_articles = parse_feed_fn(feed_text, rss_url)
        fetch_logs.append(f"RSS_PARSE_OK | url={rss_url} | parsed={len(parsed_articles)}")
        print(f"[INFO] RSS parsed {len(parsed_articles)} article(s)", flush=True)
        for article in parsed_articles:
            if article.link and article.link not in seen_links:
                if similar_article_threshold is not None and _is_similar_to_any(
                    article.title, articles, similar_article_threshold
                ):
                    sim_to = _most_similar_title(article.title, articles)
                    fetch_logs.append(
                        f"ARTICLE_SKIP_SIMILAR | similarity>={similar_article_threshold} | "
                        f"title={article.title} | similar_to={sim_to}"
                    )
                    print(f"[INFO] Skipped similar title (>{similar_article_threshold:.0%}): {article.title}", flush=True)
                    continue
                articles.append(article)
                seen_links.add(article.link)
                fetch_logs.append(f"ARTICLE_QUEUE_OK | title={article.title} | url={article.link}")
                print(f"[INFO] Queued article #{len(articles)}: {article.title}", flush=True)
            elif article.link:
                fetch_logs.append(f"ARTICLE_SKIP_DUPLICATE | title={article.title} | url={article.link}")
                print(f"[INFO] Skipped duplicate: {article.title}", flush=True)
            if max_articles is not None and len(articles) >= max_articles:
                fetch_logs.append(f"ARTICLE_LIMIT_REACHED | max_articles={max_articles}")
                print(f"[INFO] Reached max_articles={max_articles}, stop RSS collection.", flush=True)
                return articles
    return articles


def _normalize_title_for_similarity(title: str) -> str:
    t = " ".join(title.split())
    t = re.sub(r"\s+-\s+[^-]+$", "", t).strip()
    return t.casefold()


def _title_similarity(a: str, b: str) -> float:
    na = _normalize_title_for_similarity(a)
    nb = _normalize_title_for_similarity(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _is_similar_to_any(title: str, existing: list[Article], threshold: float) -> bool:
    for art in existing:
        if _title_similarity(title, art.title) >= threshold:
            return True
    return False


def _most_similar_title(title: str, existing: list[Article]) -> str:
    best = ""
    best_score = -1.0
    for art in existing:
        s = _title_similarity(title, art.title)
        if s > best_score:
            best_score = s
            best = art.title
    return best


def _write_json(path: Path, briefs: list[ArticleBrief]) -> None:
    payload = [brief.to_dict() for brief in briefs]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_brief_lead_and_body(brief_md: str) -> tuple[str, str]:
    """要点总结（首个 #### 之前的正文）与剩余分节正文；写入版面时置于来源/一句话之后、`#### 事实` 之前。"""
    lines = brief_md.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    lead_start = i
    while i < len(lines):
        if re.match(r"^####\s+", lines[i].strip()):
            break
        i += 1
    lead = "\n".join(lines[lead_start:i]).strip()
    body = "\n".join(lines[i:]).strip()
    return lead, body


def _write_markdown(path: Path, briefs: list[ArticleBrief]) -> None:
    topic_to_items: dict[str, list[ArticleBrief]] = {}
    for item in briefs:
        topic = classify_topic(item.article, item.content)
        topic_to_items.setdefault(topic, []).append(item)

    ordered_topics = sort_topics_present(set(topic_to_items.keys()))
    cn = timezone(timedelta(hours=8))
    day = datetime.now(cn).strftime("%Y-%m-%d")
    sections: list[str] = [f"# 国际新闻简报（{day}）", ""]

    for topic in ordered_topics:
        items = topic_to_items.get(topic) or []
        if not items:
            continue
        sections.append(f"## {topic}")
        sections.append("")
        for item in items:
            lead, body = _split_brief_lead_and_body(item.brief)
            chunks = [f"### {item.article.title}", ""]
            chunks.extend(
                [
                    f"- 来源：{item.article.source}",
                    f"- 发布时间：{item.article.pub_date}",
                    f"- 链接：{item.article.link}",
                    *([f"- 抓取正文失败：{item.fetch_error}"] if item.fetch_error else []),
                    "",
                ]
            )
            if item.one_liner:
                chunks.append(f"> **一句话**：{item.one_liner}")
                chunks.append("")
            if lead:
                chunks.append(lead)
                chunks.append("")
            if body:
                chunks.append(body)
            sections.append("\n".join(chunks))
            sections.append("")
    path.write_text("\n\n".join(sections).rstrip() + "\n", encoding="utf-8")


def _write_fetch_log(path: Path, logs: list[str]) -> None:
    if not logs:
        logs = ["NO_FETCH_ACTIVITY"]
    path.write_text("\n".join(logs).strip() + "\n", encoding="utf-8")
