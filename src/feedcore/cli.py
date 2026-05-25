from __future__ import annotations

import argparse
import os
from pathlib import Path

import json

from .article_fetcher import ArticleClient
from .config import parse_config
from .env import load_env_file
from .openai_compatible_client import OpenAICompatibleClient
from .pipeline import RequestsRssClient
from .remote_client import fetch_remote_artifact
from .workflow.orchestrator import WorkflowClients, run_workflow, run_workflow_from_prefetched

_DEFAULT_REMOTE_FETCHER_URL = "http://81.69.47.226:3000"


def _resolve_remote_fetcher_url(explicit: str) -> str:
    return (explicit or os.environ.get("FEEDCORE_REMOTE_FETCHER_URL") or _DEFAULT_REMOTE_FETCHER_URL).rstrip("/")
def _resolve_remote_fetcher_token(explicit: str) -> str:
    return (explicit or os.environ.get("FEEDCORE_FETCHER_TOKEN", "")).strip()


class DeepTranslatorClient:
    max_chars = 5000

    def translate_to_chinese(self, text: str) -> str:
        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(source="auto", target="zh-CN")
        return "\n\n".join(translator.translate(chunk) for chunk in _split_text_for_translation(text, self.max_chars))


def _split_text_for_translation(text: str, max_chars: int) -> list[str]:
    clean = text or ""
    if len(clean) <= max_chars:
        return [clean] if clean else []

    chunks: list[str] = []
    current = ""
    for paragraph in clean.splitlines():
        if not paragraph.strip():
            continue
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars))
            continue
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def main() -> None:
    load_env_file(Path(".env"))

    parser = argparse.ArgumentParser(description="Generate Chinese news briefs from RSS feeds.")
    parser.add_argument(
        "-c",
        "--config",
        default="config.json",
        help="Path to JSON config file (same schema as the runtime dict). Defaults to config.json. Prefer scripts/feedcore_*.py profiles for normal use.",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        metavar="N",
        help="Override config: stop after collecting N feed items.",
    )
    parser.add_argument(
        "--quick-sample-size",
        type=int,
        default=None,
        metavar="N",
        help="Select N RSS sources across categories for a quick traceable run.",
    )
    parser.add_argument(
        "--remote-fetch",
        action="store_true",
        help="Run RSS/article fetching on remote_fetcher, then continue local model workflow from the downloaded artifact.",
    )
    parser.add_argument(
        "--remote-fetcher-url",
        default=os.environ.get("FEEDCORE_REMOTE_FETCHER_URL", "http://81.69.47.226:3000"),
        help="Remote fetcher base URL. Defaults to FEEDCORE_REMOTE_FETCHER_URL.",
    )
    parser.add_argument(
        "--remote-fetcher-token",
        default=os.environ.get("FEEDCORE_FETCHER_TOKEN", ""),
        help="Remote fetcher bearer token. Defaults to FEEDCORE_FETCHER_TOKEN.",
    )
    parser.add_argument(
        "--remote-poll-interval",
        type=int,
        default=3,
        metavar="SECONDS",
        help="Polling interval while waiting for remote fetch completion.",
    )
    args = parser.parse_args()

    config = parse_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
    quick_sample_size = config.quick_sample_size if args.quick_sample_size is None else args.quick_sample_size
    max_articles = args.max_articles if args.max_articles is not None else config.max_articles
    summary_client = _make_summary_client(config)
    clients = WorkflowClients(
        rss=RequestsRssClient(),
        article=ArticleClient(
            browser_enabled=config.browser.enabled,
            document_dir=None,
            save_html=False,
            save_pdf=False,
            browser_timeout=config.browser.timeout,
        ),
        summary=summary_client,
        translator=DeepTranslatorClient(),
    )
    if args.remote_fetch:
        remote_config = _with_cli_overrides(config, quick_sample_size=quick_sample_size, max_articles=max_articles)
        artifact = fetch_remote_artifact(
            config=remote_config,
            base_url=_resolve_remote_fetcher_url(args.remote_fetcher_url),
            token=_resolve_remote_fetcher_token(args.remote_fetcher_token),
            output_root=config.output_dir,
            poll_interval=args.remote_poll_interval,
        )
        result = run_workflow_from_prefetched(
            output_dir=config.output_dir,
            clients=clients,
            rss_sources=artifact.rss_sources,
            feed_items=artifact.feed_items,
            article_contents=artifact.article_contents,
            run_id=artifact.run_id,
            model_concurrency=config.workflow.model_concurrency,
            type_classification_concurrency=config.workflow.type_classification_concurrency,
            type_synthesis_concurrency=config.workflow.type_synthesis_concurrency,
        )
    else:
        result = run_workflow(
            rss_urls=config.rss_urls,
            output_dir=config.output_dir,
            clients=clients,
            default_categories=config.rss_default_categories,
            quick_sample_size=quick_sample_size,
            max_articles=max_articles,
            rss_concurrency=config.workflow.rss_concurrency,
            article_fetch_concurrency=config.workflow.article_fetch_concurrency,
            model_concurrency=config.workflow.model_concurrency,
            type_classification_concurrency=config.workflow.type_classification_concurrency,
            type_synthesis_concurrency=config.workflow.type_synthesis_concurrency,
        )
    print(f"Wrote workflow outputs to {result.run_dir}")


def _make_summary_client(config):
    if not config.model.enabled:
        raise ValueError("model.enabled must be true; local fallback is disabled")
    return OpenAICompatibleClient(timeout=config.model.timeout)


def _with_cli_overrides(config, *, quick_sample_size: int | None, max_articles: int | None):
    from dataclasses import replace

    return replace(config, quick_sample_size=quick_sample_size, max_articles=max_articles)


if __name__ == "__main__":
    main()
