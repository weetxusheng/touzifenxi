from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelConfig:
    enabled: bool = True
    timeout: int = 60
    model_env: str = "MODEL"
    base_url_env: str = "BASE_URL"
    api_key_env: str = "API_KEY"


@dataclass(frozen=True)
class BrowserConfig:
    enabled: bool = False
    save_html: bool = True
    save_pdf: bool = False
    timeout: int = 30


@dataclass(frozen=True)
class WorkflowConfig:
    rss_concurrency: int = 10
    article_fetch_concurrency: int = 5
    model_concurrency: int = 6
    type_classification_concurrency: int = 2
    type_synthesis_concurrency: int = 2


@dataclass(frozen=True)
class AppConfig:
    rss_urls: list[str]
    rss_default_categories: dict[str, str] | None = None
    output_dir: Path = Path("output")
    #: None = collect all unique articles from every RSS (no cap). Positive int = hard cap.
    max_articles: int | None = None
    #: Skip an article entirely if full fetch (HTTP + optional browser) exceeds this many seconds.
    article_fetch_timeout_seconds: int = 180
    #: If title similarity to an already queued article is >= this (0–1), skip. None disables.
    similar_article_threshold: float | None = 0.7
    #: When True (default), drop articles whose brief lacks substantive content in any of the four dimensions.
    #: LocalBriefClient skips this check (heuristic output).
    require_substantive_four_dimensions: bool = True
    #: Positive int limits RSS sources for quick cross-category workflow validation.
    quick_sample_size: int | None = None
    #: Positive int caps feed items kept per RSS source before article text fetch (None = no cap).
    #: Applied remotely so high-volume sources (GN keyword search ~100/feed) can't explode the fetch.
    max_articles_per_source: int | None = None
    model: ModelConfig = ModelConfig()
    browser: BrowserConfig = BrowserConfig()
    workflow: WorkflowConfig = WorkflowConfig()


def parse_config(raw: dict[str, Any]) -> AppConfig:
    """Construct AppConfig from an already-decoded dict (JSON payload, profile dict, etc.).

    Stays as the internal contract because the remote fetcher receives configs over the wire.
    """

    rss_urls, rss_default_categories = _parse_rss_sources(raw)
    if not rss_urls:
        raise ValueError("config must include at least one rss_urls or rss_sources entry")

    model_raw = raw.get("model", {}) or {}
    model = ModelConfig(
        enabled=bool(model_raw.get("enabled", True)),
        timeout=int(model_raw.get("timeout", 60)),
    )
    browser_raw = raw.get("browser", {}) or {}
    browser = BrowserConfig(
        enabled=bool(browser_raw.get("enabled", False)),
        save_html=bool(browser_raw.get("save_html", True)),
        save_pdf=bool(browser_raw.get("save_pdf", False)),
        timeout=int(browser_raw.get("timeout", 30)),
    )
    workflow_raw = raw.get("workflow", {}) or {}
    workflow_defaults = WorkflowConfig()
    workflow = WorkflowConfig(
        rss_concurrency=_positive_int_or_default(
            workflow_raw.get("rss_concurrency"), workflow_defaults.rss_concurrency
        ),
        article_fetch_concurrency=_positive_int_or_default(
            workflow_raw.get("article_fetch_concurrency"), workflow_defaults.article_fetch_concurrency
        ),
        model_concurrency=_positive_int_or_default(
            workflow_raw.get("model_concurrency"), workflow_defaults.model_concurrency
        ),
        type_classification_concurrency=_positive_int_or_default(
            workflow_raw.get("type_classification_concurrency"),
            workflow_defaults.type_classification_concurrency,
        ),
        type_synthesis_concurrency=_positive_int_or_default(
            workflow_raw.get("type_synthesis_concurrency"),
            workflow_defaults.type_synthesis_concurrency,
        ),
    )
    raw_sim = raw.get("similar_article_threshold", 0.7)
    similar_article_threshold: float | None
    if raw_sim is None:
        similar_article_threshold = None
    else:
        similar_article_threshold = float(raw_sim)

    ma_raw = raw.get("max_articles", 0)
    max_articles: int | None
    if ma_raw is None:
        max_articles = None
    else:
        ma_int = int(ma_raw)
        max_articles = None if ma_int <= 0 else ma_int

    return AppConfig(
        rss_urls=rss_urls,
        rss_default_categories=rss_default_categories,
        output_dir=Path(raw.get("output_dir", "output")),
        max_articles=max_articles,
        article_fetch_timeout_seconds=int(raw.get("article_fetch_timeout_seconds", 180)),
        similar_article_threshold=similar_article_threshold,
        require_substantive_four_dimensions=bool(raw.get("require_substantive_four_dimensions", True)),
        quick_sample_size=_positive_int_or_none(raw.get("quick_sample_size")),
        max_articles_per_source=_positive_int_or_none(raw.get("max_articles_per_source")),
        model=model,
        browser=browser,
        workflow=workflow,
    )


def _parse_rss_sources(raw: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    urls: list[str] = []
    categories: dict[str, str] = {}

    for url in raw.get("rss_urls", []) or []:
        clean = str(url).strip()
        if clean:
            urls.append(clean)

    for item in raw.get("rss_sources", []) or []:
        if isinstance(item, str):
            clean = item.strip()
            if clean:
                urls.append(clean)
            continue
        if not isinstance(item, dict):
            continue
        clean = str(item.get("url", "")).strip()
        if not clean:
            continue
        urls.append(clean)
        category = str(item.get("default_category") or item.get("category") or "").strip()
        if category:
            categories[clean] = category

    return urls, categories


def _positive_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    number = int(value)
    return number if number > 0 else None


def _positive_int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    number = int(value)
    return number if number > 0 else default
