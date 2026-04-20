"""C114 step 3 搜索结果归一化与通用工具。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .types import SearchArticleInput, SearchQuery, SearchResult

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.+-]{1,30}|[\u4e00-\u9fff]{2,16}")
DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[-_/](?P<month>\d{1,2})[-_/](?P<day>\d{1,2})"),
    re.compile(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日"),
)


def load_domain_config(config_path: Path) -> dict[str, list[str]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        "official_domains": payload.get("official_domains", []),
        "blocked_domains": payload.get("blocked_domains", []),
    }


def classify_domain(domain: str, domain_config: dict[str, list[str]]) -> tuple[bool, str]:
    normalized = domain.lower().lstrip(".")
    is_official = domain_matches(normalized, domain_config.get("official_domains", []))
    is_blocked = domain_matches(normalized, domain_config.get("blocked_domains", []))
    if is_blocked:
        return False, "blocked"
    if is_official:
        return True, "official"
    return False, "normal"


def domain_matches(domain: str, suffixes: list[str]) -> bool:
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes)


def normalize_search_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
    provider_name: str = "tavily",
) -> list[SearchResult]:
    if provider_name == "metaso":
        return normalize_metaso_results(raw_results, query=query, original_title=original_title, domain_config=domain_config)
    if provider_name == "baidu":
        return normalize_baidu_results(raw_results, query=query, original_title=original_title, domain_config=domain_config)
    if provider_name == "google":
        return normalize_google_results(raw_results, query=query, original_title=original_title, domain_config=domain_config)

    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("content") or item.get("snippet") or ""), limit=400)
        published_at = normalize_published_at(item.get("published_date") or item.get("published_at") or "")
        if not published_at:
            published_at = infer_published_at(url, title, snippet)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at=published_at,
                snippet=snippet,
                score=float(item.get("score") or 0.0),
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def normalize_metaso_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
) -> list[SearchResult]:
    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("link") or item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("snippet") or ""), limit=400)
        published_at = normalize_published_at(item.get("date") or item.get("published_at") or "")
        if not published_at:
            published_at = infer_published_at(url, title, snippet)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at=published_at,
                snippet=snippet,
                score=normalize_metaso_score(item.get("score")),
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def normalize_metaso_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
    return mapping.get(text, 0.5)


def normalize_baidu_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
) -> list[SearchResult]:
    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("snippet") or item.get("content") or ""), limit=400)
        published_at = normalize_published_at(item.get("date") or "")
        if not published_at:
            published_at = infer_published_at(url, title, snippet)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        score = float(item.get("rerank_score") or 0.0) + float(item.get("authority_score") or 0.0)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at=published_at,
                snippet=snippet,
                score=score,
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def normalize_google_results(
    raw_results: list[dict[str, Any]],
    query: SearchQuery,
    original_title: str,
    domain_config: dict[str, list[str]],
) -> list[SearchResult]:
    normalized: list[SearchResult] = []
    for item in raw_results:
        url = str(item.get("url") or "").strip()
        title = compact_text(str(item.get("title") or ""), limit=160)
        if not url or not title:
            continue
        domain = extract_domain(url)
        is_official, source_tier = classify_domain(domain, domain_config)
        snippet = compact_text(str(item.get("snippet") or ""), limit=400)
        matched_terms = compute_matched_terms(original_title, query.value, title, snippet)
        normalized.append(
            SearchResult(
                query=query.value,
                query_type=query.query_type,
                result_title=title,
                url=url,
                domain=domain,
                published_at="",
                snippet=snippet,
                score=float(item.get("score") or 0.5),
                is_official=is_official,
                source_tier=source_tier,
                matched_terms=matched_terms,
                extract_text="",
                extract_status="not_requested",
            )
        )
    return normalized


def result_is_blocked(result: SearchResult, domain_config: dict[str, list[str]]) -> bool:
    return classify_domain(result.domain, domain_config)[1] == "blocked"


def unique_search_results(results: list[SearchResult]) -> list[SearchResult]:
    by_url: dict[str, SearchResult] = {}
    title_seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        normalized_url = normalize_url(result.url)
        normalized_title = normalize_title(result.result_title)
        if normalized_url in by_url or normalized_title in title_seen:
            continue
        by_url[normalized_url] = result
        title_seen.add(normalized_title)
        unique.append(result)
    return unique


def rank_search_results(results: list[SearchResult], report_date: date, original_title: str) -> list[SearchResult]:
    return sorted(
        results,
        key=lambda item: (
            0 if item.is_official else 1,
            -len(item.matched_terms),
            recency_bucket(item.published_at, report_date),
            0 if item.query_type == "title" else 1,
            -item.score,
            normalize_title_distance(item.result_title, original_title),
            item.domain,
        ),
    )


def filter_recent_results(results: list[SearchResult], report_date: date, max_age_days: int) -> list[SearchResult]:
    """Keep only records whose published_at date exactly matches report_date.

    NOTE: max_age_days is retained for backward-compatible signature but no longer used.
    """

    filtered: list[SearchResult] = []
    for result in results:
        if not result.published_at:
            continue
        try:
            published_date = date.fromisoformat(result.published_at[:10])
        except ValueError:
            continue
        if published_date == report_date:
            filtered.append(result)
    return filtered


def infer_published_at(url: str, title: str, snippet: str) -> str:
    candidates = (url, title, snippet)
    for candidate in candidates:
        text = str(candidate)
        for pattern in DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                inferred = date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            return inferred.isoformat()
    return ""


def select_results_for_extract(results: list[SearchResult], extract_limit: int) -> list[SearchResult]:
    return results[:extract_limit]


def enrich_selected_results(
    search_results: list[SearchResult],
    article: SearchArticleInput,
    extract_limit: int,
    client: Any,
) -> list[SearchResult]:
    selected = [replace(item) for item in search_results]
    if not selected or not hasattr(client, "extract"):
        return selected
    to_extract = select_results_for_extract(selected, extract_limit)
    extracted_map: dict[str, str] = {}
    try:
        extracted_map = client.extract([item.url for item in to_extract], query=article.original_title)
    except Exception:
        for item in to_extract:
            index = selected.index(item)
            selected[index] = replace(selected[index], extract_status="failed")
        return selected

    for index, item in enumerate(selected):
        if index >= extract_limit:
            break
        extract_text = extracted_map.get(normalize_url(item.url), "")
        selected[index] = replace(
            item,
            extract_text=extract_text,
            extract_status="success" if extract_text else "failed",
        )
    return selected


def extract_domain(url: str) -> str:
    return urlsplit(url).netloc.lower()


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def normalize_title(title: str) -> str:
    return compact_text(title, limit=200).lower()


def normalize_published_at(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    candidates = [text, text[:19], text[:10]]
    for candidate in candidates:
        for parser in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(candidate, parser).strftime("%Y-%m-%d")
            except ValueError:
                continue
    if len(text) >= 10:
        return text[:10]
    return text


def recency_bucket(published_at: str, report_date: date) -> int:
    if not published_at:
        return 2
    try:
        delta = abs((report_date - date.fromisoformat(published_at[:10])).days)
    except ValueError:
        return 2
    return 0 if delta <= 3 else 1


def tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(value):
        cleaned = token.strip()
        if len(cleaned) < 2:
            continue
        if cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def compute_matched_terms(original_title: str, query: str, result_title: str, snippet: str) -> list[str]:
    text = f"{result_title} {snippet}"
    candidates = tokenize(original_title) + tokenize(query)
    matched: list[str] = []
    for token in candidates:
        if token in text and token not in matched:
            matched.append(token)
    return matched[:8]


def normalize_title_distance(result_title: str, original_title: str) -> int:
    return abs(len(result_title) - len(original_title))


def compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit].strip()
