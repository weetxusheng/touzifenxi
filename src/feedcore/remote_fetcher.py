from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from feedcore.config import parse_config
from feedcore.models import Article
from feedcore.rss import parse_feed
from feedcore.workflow.concurrent import (
    ConcurrentWorkflowClients,
    _assign_rss_ids,
    _collect_distinct_feed_items,
    _fetch_all_text_records,
    _fetch_rss_feeds,
    _group_by_rss_id,
    _restrict_feed_results_to_selected_items,
    _source_dict,
    _text_checkpoint,
)
from feedcore.workflow.run_context import RunContext
from feedcore.workflow.steps import ArticleClient, RssClient, Translator, select_rss_sources


@dataclass(frozen=True)
class RemoteFetchConfig:
    rss_urls: list[str]
    output_dir: Path
    run_id: str
    default_categories: dict[str, str] | None = None
    quick_sample_size: int | None = None
    max_articles: int | None = None
    max_articles_per_source: int | None = None
    rss_concurrency: int = 10
    article_fetch_concurrency: int = 5
    browser_enabled: bool = False
    browser_timeout: int = 30


@dataclass(frozen=True)
class FetchOnlyClients:
    rss: RssClient
    article: ArticleClient
    translator: Translator | None = None


@dataclass(frozen=True)
class FetchOnlyResult:
    run_id: str
    run_dir: Path
    artifact_path: Path
    manifest_path: Path
    article_contents_path: Path


@dataclass(frozen=True)
class FetchRunRequest:
    rss_urls: list[str]
    client_run_id: str | None = None
    default_categories: dict[str, str] | None = None
    quick_sample_size: int | None = None
    max_articles: int | None = None
    max_articles_per_source: int | None = None
    rss_concurrency: int = 10
    article_fetch_concurrency: int = 5
    browser_enabled: bool = False
    browser_timeout: int = 30

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "FetchRunRequest":
        raw_config = payload.get("config", payload)
        if not isinstance(raw_config, dict):
            raise ValueError("request config must be an object")
        app_config = parse_config(raw_config)
        return cls(
            rss_urls=app_config.rss_urls,
            client_run_id=_optional_str(payload.get("client_run_id")),
            default_categories=app_config.rss_default_categories,
            quick_sample_size=app_config.quick_sample_size,
            max_articles=app_config.max_articles,
            max_articles_per_source=app_config.max_articles_per_source,
            rss_concurrency=app_config.workflow.rss_concurrency,
            article_fetch_concurrency=app_config.workflow.article_fetch_concurrency,
            browser_enabled=app_config.browser.enabled,
            browser_timeout=app_config.browser.timeout,
        )


@dataclass(frozen=True)
class FetchRunStatus:
    run_id: str
    status: str
    client_run_id: str | None = None
    message: str = ""
    created_at: str = ""
    updated_at: str = ""
    artifact_path: Path | None = None
    run_dir: Path | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "client_run_id": self.client_run_id,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "artifact_path": str(self.artifact_path) if self.artifact_path else None,
            "run_dir": str(self.run_dir) if self.run_dir else None,
        }


class FetchRunner(Protocol):
    def __call__(self, config: RemoteFetchConfig) -> FetchOnlyResult:
        pass


class FetchRunManager:
    def __init__(
        self,
        *,
        output_dir: Path,
        runner: FetchRunner,
        run_inline: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.runner = runner
        self.run_inline = run_inline
        self._lock = threading.Lock()
        self._statuses: dict[str, FetchRunStatus] = {}

    def create_run(self, request: FetchRunRequest) -> FetchRunStatus:
        run_id = _make_remote_run_id(request.client_run_id)
        now = _utc_now()
        status = FetchRunStatus(
            run_id=run_id,
            status="queued",
            client_run_id=request.client_run_id,
            created_at=now,
            updated_at=now,
            run_dir=self.output_dir / run_id,
        )
        with self._lock:
            self._statuses[run_id] = status

        config = RemoteFetchConfig(
            rss_urls=request.rss_urls,
            output_dir=self.output_dir,
            run_id=run_id,
            default_categories=request.default_categories,
            quick_sample_size=request.quick_sample_size,
            max_articles=request.max_articles,
            max_articles_per_source=request.max_articles_per_source,
            rss_concurrency=request.rss_concurrency,
            article_fetch_concurrency=request.article_fetch_concurrency,
            browser_enabled=request.browser_enabled,
            browser_timeout=request.browser_timeout,
        )
        if self.run_inline:
            self._run(config)
        else:
            thread = threading.Thread(target=self._run, args=(config,), daemon=True)
            thread.start()
        return self.get_status(run_id) or status

    def get_status(self, run_id: str) -> FetchRunStatus | None:
        with self._lock:
            return self._statuses.get(run_id)

    def get_artifact_path(self, run_id: str) -> Path | None:
        status = self.get_status(run_id)
        if status is None or status.status != "succeeded" or status.artifact_path is None:
            return None
        return status.artifact_path

    def _run(self, config: RemoteFetchConfig) -> None:
        self._update(config.run_id, status="running", message="fetch-only workflow started")
        try:
            result = self.runner(config)
        except Exception as exc:
            self._update(config.run_id, status="failed", message=str(exc))
            return
        self._update(
            config.run_id,
            status="succeeded",
            message="artifact ready",
            artifact_path=result.artifact_path,
            run_dir=result.run_dir,
        )

    def _update(self, run_id: str, **changes: object) -> None:
        with self._lock:
            current = self._statuses[run_id]
            payload = {
                "run_id": current.run_id,
                "status": changes.get("status", current.status),
                "client_run_id": current.client_run_id,
                "message": changes.get("message", current.message),
                "created_at": current.created_at,
                "updated_at": _utc_now(),
                "artifact_path": changes.get("artifact_path", current.artifact_path),
                "run_dir": changes.get("run_dir", current.run_dir),
            }
            self._statuses[run_id] = FetchRunStatus(**payload)


def run_fetch_only(
    *,
    config: RemoteFetchConfig,
    clients: FetchOnlyClients,
    parse_feed_fn: Callable[[str, str], list[Article]] = parse_feed,
) -> FetchOnlyResult:
    ctx = RunContext.create(output_dir=config.output_dir, run_id=config.run_id)
    (ctx.run_dir / "rss_tasks").mkdir(exist_ok=True)

    sources = _assign_rss_ids(
        select_rss_sources(
            config.rss_urls,
            default_categories=config.default_categories,
            max_sources=config.quick_sample_size,
        )
    )
    ctx.write_json("step1_rss_sources.json", [_source_dict(source) for source in sources])
    ctx.log_step("step1_rss_sources", input_count=len(config.rss_urls), output_count=len(sources), message="selected RSS sources")

    workflow_clients = ConcurrentWorkflowClients(
        rss=clients.rss,
        article=clients.article,
        summary=_NoSummaryClient(),
        translator=clients.translator,
    )
    feed_results = _fetch_rss_feeds(
        ctx=ctx,
        sources=sources,
        clients=workflow_clients,
        parse_feed_fn=parse_feed_fn,
        max_articles=config.max_articles,
        rss_concurrency=config.rss_concurrency,
    )
    feed_items = _collect_distinct_feed_items(
        feed_results, max_articles=config.max_articles, max_per_source=config.max_articles_per_source
    )
    feed_results = _restrict_feed_results_to_selected_items(feed_results, feed_items)
    ctx.write_json("step2_feed_items.json", [item.to_dict() for item in feed_items])
    ctx.log_step(
        "step2_fetch_feeds",
        input_count=len(sources),
        output_count=len(feed_items),
        message="completed RSS feed fetch",
    )

    text_records = _fetch_all_text_records(
        ctx=ctx,
        feed_results=feed_results,
        clients=workflow_clients,
        article_fetch_concurrency=config.article_fetch_concurrency,
    )
    text_by_rss_id = _group_by_rss_id(text_records)
    for result in feed_results:
        ctx.write_json(
            f"rss_tasks/{result.rss_id}_articles_text.json",
            [item.to_dict() for item in text_by_rss_id.get(result.rss_id, [])],
        )
    article_contents_path = ctx.write_json("step3_article_contents.json", [_text_checkpoint(item) for item in text_records])
    fetch_failed = sum(1 for item in text_records if item.fetch_error or item.extract_error or item.translation_error)
    ctx.log_step(
        "step3_fetch_and_extract_text",
        input_count=len(feed_items),
        output_count=len(text_records),
        skipped_count=fetch_failed,
        message="fetched article HTML in memory and extracted text",
    )

    manifest_path = write_manifest(
        ctx.run_dir,
        run_id=ctx.run_id,
        config={
            "rss_urls": config.rss_urls,
            "default_categories": config.default_categories or {},
            "quick_sample_size": config.quick_sample_size,
            "max_articles": config.max_articles,
            "max_articles_per_source": config.max_articles_per_source,
            "rss_concurrency": config.rss_concurrency,
            "article_fetch_concurrency": config.article_fetch_concurrency,
            "browser_enabled": config.browser_enabled,
            "browser_timeout": config.browser_timeout,
        },
        counts={
            "rss_sources": len(sources),
            "feed_items": len(feed_items),
            "article_contents": len(text_records),
            "fetch_failed": fetch_failed,
            "translated": sum(1 for item in text_records if item.translated),
        },
    )
    ctx.write_execution_log()
    artifact_path = create_artifact_zip(ctx.run_dir)
    return FetchOnlyResult(ctx.run_id, ctx.run_dir, artifact_path, manifest_path, article_contents_path)


def write_manifest(run_dir: Path, *, run_id: str, config: dict[str, object], counts: dict[str, int]) -> Path:
    manifest = {
        "artifact_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "remote-fetcher",
        "config_hash": f"sha256:{_config_hash(config)}",
        "files": {
            "rss_sources": "step1_rss_sources.json",
            "feed_items": "step2_feed_items.json",
            "article_contents": "step3_article_contents.json",
        },
        "counts": counts,
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def create_artifact_zip(run_dir: Path) -> Path:
    artifact_path = run_dir / f"{run_dir.name}.zip"
    include_roots = {
        "manifest.json",
        "step1_rss_sources.json",
        "step2_feed_items.json",
        "step3_article_contents.json",
    }
    with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path == artifact_path or not path.is_file():
                continue
            rel = path.relative_to(run_dir).as_posix()
            if rel in include_roots or rel.startswith("rss_tasks/") or rel.startswith("logs/"):
                archive.write(path, rel)
    return artifact_path


def _config_hash(config: dict[str, object]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _NoSummaryClient:
    pass


def _make_remote_run_id(client_run_id: str | None) -> str:
    suffix = _safe_id(client_run_id or uuid.uuid4().hex[:12])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"fetch_{timestamp}_{suffix}"


def _safe_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return clean[:64] or uuid.uuid4().hex[:12]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
