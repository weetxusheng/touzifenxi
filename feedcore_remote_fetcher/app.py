from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import os
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


@dataclass(frozen=True)
class Article:
    title: str
    link: str
    pub_date: str
    description: str
    source: str
    feed_url: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class RssSource:
    rss_id: str
    url: str
    label: str
    default_category: str
    selected_reason: str = "configured"
    status: str = "selected"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FeedItem:
    article: Article
    default_category: str
    source_label: str = ""
    status: str = "queued"
    skip_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "source_label": self.source_label,
            "status": self.status,
            "skip_reason": self.skip_reason,
        }


@dataclass(frozen=True)
class ArticleText:
    rss_id: str
    article_id: str
    article: Article
    default_category: str
    text: str
    detected_language: str = "unknown"
    translated: bool = False
    fetch_error: str | None = None
    extract_error: str | None = None
    translation_error: str | None = None
    stored_html_path: str | None = None
    stored_html_url: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rss_id": self.rss_id,
            "article_id": self.article_id,
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "text": self.text,
            "detected_language": self.detected_language,
            "translated": self.translated,
            "fetch_error": self.fetch_error,
            "extract_error": self.extract_error,
            "translation_error": self.translation_error,
            "stored_html_path": self.stored_html_path,
            "stored_html_url": self.stored_html_url,
        }


@dataclass(frozen=True)
class FetchRunRequest:
    rss_urls: list[str]
    client_run_id: str | None = None
    default_categories: dict[str, str] | None = None
    quick_sample_size: int | None = None
    max_articles: int | None = None
    #: 每个 RSS 源在抓正文之前最多保留多少条 feed item。None=不限。
    #: Why: GN 关键词搜索源单源 ~100 条,60 源全开会膨胀到数千篇,逐篇抓正文会拖垮服务。
    #: 在抓正文之前按源截断,既限总量又保跨类均衡(每类 15 源各 ≤N 条)。
    max_articles_per_source: int | None = None
    rss_concurrency: int = 10
    article_fetch_concurrency: int = 10
    browser_enabled: bool = False
    browser_timeout: int = 30

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FetchRunRequest":
        raw_config = payload.get("config", payload)
        if not isinstance(raw_config, dict):
            raise ValueError("request config must be an object")
        rss_urls, categories = _parse_rss_sources(raw_config)
        if not rss_urls:
            raise ValueError("config must include at least one rss_urls or rss_sources entry")
        workflow = raw_config.get("workflow") or {}
        browser = raw_config.get("browser") or {}
        return cls(
            rss_urls=rss_urls,
            client_run_id=_optional_str(payload.get("client_run_id")),
            default_categories=categories,
            quick_sample_size=_positive_int_or_none(raw_config.get("quick_sample_size")),
            max_articles=_max_articles(raw_config.get("max_articles", 0)),
            max_articles_per_source=_positive_int_or_none(raw_config.get("max_articles_per_source")),
            rss_concurrency=_positive_int_or_default(workflow.get("rss_concurrency"), 10),
            article_fetch_concurrency=_positive_int_or_default(workflow.get("article_fetch_concurrency"), 10),
            browser_enabled=bool(browser.get("enabled", False)),
            browser_timeout=int(browser.get("timeout", 30)),
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


@dataclass(frozen=True)
class FetchOnlyResult:
    run_id: str
    run_dir: Path
    artifact_path: Path
    manifest_path: Path
    article_contents_path: Path


class FetchRunManager:
    def __init__(self, *, output_dir: Path, run_inline: bool = False) -> None:
        self.output_dir = output_dir
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
        if self.run_inline:
            self._run(run_id, request)
        else:
            thread = threading.Thread(target=self._run, args=(run_id, request), daemon=True)
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

    def get_progress(self, run_id: str) -> dict[str, object] | None:
        status = self.get_status(run_id)
        if status is None:
            return None
        run_dir = status.run_dir or (self.output_dir / run_id)
        return load_run_progress(
            run_id=run_id,
            run_dir=run_dir,
            status=status.status,
            message=status.message,
            artifact_path=status.artifact_path,
        )

    def delete_run(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            status = self._statuses.pop(run_id, None)
        if status is None:
            return None
        run_dir = status.run_dir or (self.output_dir / run_id)
        if run_dir.exists() and run_dir.is_dir():
            _remove_tree(run_dir)
        return {"run_id": run_id, "deleted": True}

    def _run(self, run_id: str, request: FetchRunRequest) -> None:
        self._update(run_id, status="running", message="fetch-only workflow started")
        try:
            result = run_fetch_only(request=request, output_dir=self.output_dir, run_id=run_id)
        except Exception as exc:
            self._update(run_id, status="failed", message=str(exc))
            return
        self._update(
            run_id,
            status="succeeded",
            message="artifact ready",
            artifact_path=result.artifact_path,
            run_dir=result.run_dir,
        )

    def _update(self, run_id: str, **changes: object) -> None:
        with self._lock:
            current = self._statuses[run_id]
            self._statuses[run_id] = FetchRunStatus(
                run_id=current.run_id,
                status=str(changes.get("status", current.status)),
                client_run_id=current.client_run_id,
                message=str(changes.get("message", current.message)),
                created_at=current.created_at,
                updated_at=_utc_now(),
                artifact_path=changes.get("artifact_path", current.artifact_path),
                run_dir=changes.get("run_dir", current.run_dir),
            )


def run_fetch_only(*, request: FetchRunRequest, output_dir: Path, run_id: str) -> FetchOnlyResult:
    run_dir = output_dir / run_id
    for child in (run_dir, run_dir / "rss_tasks", run_dir / "logs"):
        child.mkdir(parents=True, exist_ok=True)

    sources = _select_sources(request)
    _write_json(run_dir / "step1_rss_sources.json", [source.to_dict() for source in sources])
    _append_workflow_log(run_dir, "step1_rss_sources", len(request.rss_urls), len(sources), 0, "selected RSS sources")

    feed_by_rss = _fetch_all_feeds(run_dir, sources, request)
    selected_items = _collect_distinct_feed_items(feed_by_rss, request.max_articles, request.max_articles_per_source)
    selected_links = {item.article.link for item in selected_items}
    feed_by_rss = {
        rss_id: [item for item in items if item.article.link in selected_links]
        for rss_id, items in feed_by_rss.items()
    }
    _write_json(run_dir / "step2_feed_items.json", [item.to_dict() for item in selected_items])
    _append_workflow_log(run_dir, "step2_fetch_feeds", len(sources), len(selected_items), 0, "completed RSS feed fetch")

    article_records = _fetch_all_article_text(run_dir, sources, feed_by_rss, request)
    article_records = store_article_htmls_for_server(output_dir, article_records)
    _write_json(run_dir / "step3_article_contents.json", [record.to_dict() for record in article_records])
    fetch_failed = sum(1 for item in article_records if item.fetch_error or item.extract_error or item.translation_error)
    _append_workflow_log(
        run_dir,
        "step3_fetch_and_extract_text",
        len(selected_items),
        len(article_records),
        fetch_failed,
        "fetched article HTML and extracted text",
    )
    manifest_path = write_manifest(run_dir, run_id=run_id, request=request, sources=sources, feed_items=selected_items, article_records=article_records)
    artifact_path = create_artifact_zip(run_dir)
    return FetchOnlyResult(run_id, run_dir, artifact_path, manifest_path, run_dir / "step3_article_contents.json")


def write_manifest(
    run_dir: Path,
    *,
    run_id: str,
    request: FetchRunRequest,
    sources: list[RssSource],
    feed_items: list[FeedItem],
    article_records: list[ArticleText],
) -> Path:
    config = {
        "rss_urls": request.rss_urls,
        "default_categories": request.default_categories or {},
        "quick_sample_size": request.quick_sample_size,
        "max_articles": request.max_articles,
        "max_articles_per_source": request.max_articles_per_source,
        "rss_concurrency": request.rss_concurrency,
        "article_fetch_concurrency": request.article_fetch_concurrency,
        "browser_enabled": request.browser_enabled,
        "browser_timeout": request.browser_timeout,
    }
    manifest = {
        "artifact_version": 1,
        "run_id": run_id,
        "created_at": _utc_now(),
        "source": "remote-fetcher",
        "config_hash": f"sha256:{_config_hash(config)}",
        "files": {
            "rss_sources": "step1_rss_sources.json",
            "feed_items": "step2_feed_items.json",
            "article_contents": "step3_article_contents.json",
        },
        "counts": {
            "rss_sources": len(sources),
            "feed_items": len(feed_items),
            "article_contents": len(article_records),
            "fetch_failed": sum(1 for item in article_records if item.fetch_error or item.extract_error or item.translation_error),
            "translated": sum(1 for item in article_records if item.translated),
        },
    }
    path = run_dir / "manifest.json"
    _write_json(path, manifest)
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
            if rel in include_roots or rel.startswith("rss_tasks/") or rel.startswith("logs/") or rel.startswith("articles/"):
                archive.write(path, rel)
    return artifact_path


def load_artifact_result_json(artifact_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(artifact_path) as archive:
        manifest = _read_artifact_json(archive, "manifest.json")
        return {
            "run_id": str(manifest.get("run_id") or artifact_path.stem),
            "manifest": manifest,
            "rss_sources": _read_artifact_json(archive, "step1_rss_sources.json"),
            "feed_items": _read_artifact_json(archive, "step2_feed_items.json"),
            "article_contents": _read_artifact_json(archive, "step3_article_contents.json"),
        }


def _read_artifact_json(archive: zipfile.ZipFile, name: str) -> Any:
    return json.loads(archive.read(name).decode("utf-8"))


def load_run_progress(
    *,
    run_id: str,
    run_dir: Path,
    status: str,
    message: str,
    artifact_path: Path | None = None,
) -> dict[str, object]:
    rss_sources = _safe_len_json_list(run_dir / "step1_rss_sources.json")
    feed_items = _safe_len_json_list(run_dir / "step2_feed_items.json")
    article_records = _load_partial_article_records(run_dir)
    fetch_failed = sum(
        1
        for record in article_records
        if record.get("fetch_error") or record.get("extract_error") or record.get("translation_error")
    )
    return {
        "run_id": run_id,
        "status": status,
        "message": message,
        "rss_sources": rss_sources,
        "feed_items": feed_items,
        "articles_total": feed_items,
        "articles_done": len(article_records),
        "fetch_failed": fetch_failed,
        "artifact_ready": bool(artifact_path and artifact_path.exists()),
    }


def _safe_len_json_list(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def _load_partial_article_records(run_dir: Path) -> list[dict[str, object]]:
    final_path = run_dir / "step3_article_contents.json"
    if final_path.exists():
        try:
            data = json.loads(final_path.read_text(encoding="utf-8"))
        except Exception:
            data = []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

    records: list[dict[str, object]] = []
    for path in sorted((run_dir / "rss_tasks").glob("*_articles_text.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            records.extend(item for item in data if isinstance(item, dict))
    return records


def store_article_htmls_for_server(output_dir: Path, article_records: list[ArticleText]) -> list[ArticleText]:
    day = _today_yyyymmdd()
    cleanup_old_article_html_dirs(output_dir, today=day)
    day_dir = output_dir / "articles" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    stored_records: list[ArticleText] = []
    for record in article_records:
        filename = f"{_safe_id(record.article_id)}.html"
        relative_path = f"articles/{day}/{filename}"
        html_path = day_dir / filename
        html_path.write_text(_render_stored_article_html(record), encoding="utf-8")
        stored_html_url = f"{_public_base_url()}/{relative_path}"
        stored_records.append(
            ArticleText(
                rss_id=record.rss_id,
                article_id=record.article_id,
                article=record.article,
                default_category=record.default_category,
                text=record.text,
                detected_language=record.detected_language,
                translated=record.translated,
                fetch_error=record.fetch_error,
                extract_error=record.extract_error,
                translation_error=record.translation_error,
                stored_html_path=relative_path,
                stored_html_url=stored_html_url,
            )
        )
    return stored_records


def store_article_htmls_for_artifact(run_dir: Path, article_records: list[ArticleText]) -> list[ArticleText]:
    return store_article_htmls_for_server(run_dir.parent, article_records)


def cleanup_old_article_html_dirs(output_dir: Path, *, today: str | None = None, keep_days: int = 7) -> None:
    root = output_dir / "articles"
    if not root.exists():
        return
    current = datetime.strptime(today or _today_yyyymmdd(), "%Y%m%d").date()
    cutoff = current - timedelta(days=keep_days)
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            day = datetime.strptime(child.name, "%Y%m%d").date()
        except ValueError:
            continue
        if day < cutoff:
            _remove_tree(child)


def _today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _public_base_url() -> str:
    return os.environ.get("FEEDCORE_FETCHER_PUBLIC_BASE_URL", "http://81.69.47.226:3000").rstrip("/")


def _render_stored_article_html(record: ArticleText) -> str:
    article = record.article
    paragraphs = "\n".join(f"<p>{escape(part)}</p>" for part in record.text.splitlines() if part.strip())
    pub_date = _format_pub_date_china(article.pub_date)
    original_link = escape(article.link, quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(article.title)}</title>
  <style>
    body {{ max-width: 860px; margin: 40px auto; padding: 0 20px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.75; color: #111827; }}
    .title-row {{ display: flex; gap: 16px; align-items: baseline; justify-content: space-between; border-bottom: 1px solid #e5e7eb; padding-bottom: 16px; margin-bottom: 16px; }}
    h1 {{ font-size: 30px; line-height: 1.25; margin: 0; }}
    .original-link {{ flex: 0 0 auto; font-size: 14px; font-weight: 500; text-decoration: none; }}
    .meta {{ color: #6b7280; font-size: 14px; margin-bottom: 24px; }}
    a {{ color: #2563eb; }}
    p {{ margin: 0 0 1em; }}
  </style>
</head>
<body>
  <div class="title-row">
    <h1>{escape(article.title)}</h1>
    <a class="original-link" href="{original_link}" target="_blank" rel="noopener noreferrer">原文链接</a>
  </div>
  <div class="meta">
    <div>来源：{escape(article.source or "未知")}</div>
    <div>发布时间：{escape(pub_date)}</div>
  </div>
  <article>
    {paragraphs}
  </article>
</body>
</html>
"""


def _format_pub_date_china(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _remove_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _remove_tree(child)
        else:
            child.unlink()
    path.rmdir()


def fetch_url_result(url: str, *, timeout: int = 45) -> dict[str, object]:
    target = url.strip()
    _validate_fetch_url(target)
    html = _http_get_text(target, timeout=timeout)
    text = extract_article_text(html, url=target)
    return {
        "url": target,
        "html": html,
        "text": text,
        "text_len": len(text),
    }


def fetch_url_html(url: str, *, timeout: int = 45) -> str:
    target = url.strip()
    _validate_fetch_url(target)
    return _http_get_text(target, timeout=timeout)


def _validate_fetch_url(url: str) -> None:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url must start with http:// or https://")


def create_app(*, output_dir: Path, token: str | None = None, manager: FetchRunManager | None = None):
    try:
        from fastapi import Depends, Header, HTTPException, Query
        from fastapi.responses import FileResponse, Response
        from fastapi import FastAPI
    except ImportError as exc:
        raise RuntimeError("Install remote_fetcher dependencies first: python -m pip install -r requirements.txt") from exc

    app = FastAPI(title="FeedCore Remote Fetcher", version="0.1.0")
    run_manager = manager or FetchRunManager(output_dir=output_dir)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid bearer token")

    def require_token_header_or_query(
        authorization: str | None = Header(default=None),
        token_query: str | None = Query(default=None, alias="token"),
    ) -> None:
        if not token:
            return
        expected = f"Bearer {token}"
        if authorization == expected or token_query == token:
            return
        raise HTTPException(status_code=401, detail="invalid bearer token")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/fetch-runs", dependencies=[Depends(require_token)])
    def create_fetch_run(payload: dict[str, Any]) -> dict[str, object]:
        try:
            request = FetchRunRequest.from_payload(payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run_manager.create_run(request).to_dict()

    @app.get("/api/fetch-runs/{run_id}", dependencies=[Depends(require_token)])
    def get_fetch_run(run_id: str) -> dict[str, object]:
        status = run_manager.get_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail="fetch run not found")
        return status.to_dict()

    @app.get("/api/fetch-runs/{run_id}/progress", dependencies=[Depends(require_token)])
    def get_fetch_run_progress(run_id: str) -> dict[str, object]:
        progress = run_manager.get_progress(run_id)
        if progress is None:
            raise HTTPException(status_code=404, detail="fetch run not found")
        return progress

    @app.get("/api/fetch-runs/{run_id}/artifact", dependencies=[Depends(require_token)])
    def download_artifact(run_id: str):
        artifact_path = run_manager.get_artifact_path(run_id)
        if artifact_path is None or not artifact_path.exists():
            raise HTTPException(status_code=404, detail="artifact is not ready")
        return FileResponse(artifact_path, media_type="application/zip", filename=artifact_path.name)

    @app.delete("/api/fetch-runs/{run_id}", dependencies=[Depends(require_token)])
    def delete_fetch_run(run_id: str) -> dict[str, object]:
        result = run_manager.delete_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="fetch run not found")
        return result

    @app.get("/api/fetch-runs/{run_id}/result", dependencies=[Depends(require_token)])
    def get_artifact_result(run_id: str) -> Response:
        artifact_path = run_manager.get_artifact_path(run_id)
        if artifact_path is None or not artifact_path.exists():
            raise HTTPException(status_code=404, detail="artifact is not ready")
        return _json_response(load_artifact_result_json(artifact_path))

    @app.get("/api/fetch-url", dependencies=[Depends(require_token)])
    def fetch_single_url(url: str) -> Response:
        try:
            return _json_response(fetch_url_result(url))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/articles/{date}/{filename:path}")
    def get_stored_article_html(date: str, filename: str) -> Response:
        if not re.fullmatch(r"\d{8}", date) or ".." in filename:
            raise HTTPException(status_code=400, detail="invalid article path")
        path = (output_dir / "articles" / date / filename).resolve()
        root = (output_dir / "articles").resolve()
        if not str(path).startswith(str(root)) or not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="article html not found")
        return Response(content=path.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8")

    @app.get("/raw", dependencies=[Depends(require_token_header_or_query)])
    def fetch_raw_url(url: str) -> Response:
        try:
            html = fetch_url_html(url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.get("/raw/{target_url:path}", dependencies=[Depends(require_token_header_or_query)])
    def fetch_raw_url_from_path(target_url: str) -> Response:
        try:
            html = fetch_url_html(target_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=html, media_type="text/html; charset=utf-8")

    return app


def _json_response(payload: object) -> Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Response(content=body, media_type="application/json; charset=utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the standalone FeedCore remote fetcher HTTP service.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--output-dir", default="remote_output")
    parser.add_argument(
        "--token",
        default=os.environ.get("FEEDCORE_FETCHER_TOKEN"),
        help="Bearer token for remote fetch APIs. Defaults to FEEDCORE_FETCHER_TOKEN.",
    )
    parser.add_argument(
        "--proxy",
        default=os.environ.get("FEEDCORE_FETCHER_PROXY"),
        help="HTTP/SOCKS proxy for RSS and article fetch (e.g. http://127.0.0.1:7890). "
        "Defaults to FEEDCORE_FETCHER_PROXY, then standard http_proxy env vars.",
    )
    parser.add_argument(
        "--worker-fetch-url",
        default=os.environ.get("FEEDCORE_WORKER_FETCH_URL"),
        help="Cloudflare Worker /fetch endpoint used for RSS and article HTML fetch.",
    )
    parser.add_argument(
        "--worker-token",
        default=os.environ.get("FEEDCORE_WORKER_TOKEN"),
        help="Bearer token for FEEDCORE_WORKER_FETCH_URL.",
    )
    parser.add_argument(
        "--public-base-url",
        default=os.environ.get("FEEDCORE_FETCHER_PUBLIC_BASE_URL"),
        help="Public base URL used in stored_html_url values.",
    )
    args = parser.parse_args()
    if args.proxy:
        os.environ["FEEDCORE_FETCHER_PROXY"] = args.proxy
    if args.worker_fetch_url:
        os.environ["FEEDCORE_WORKER_FETCH_URL"] = args.worker_fetch_url
    if args.worker_token:
        os.environ["FEEDCORE_WORKER_TOKEN"] = args.worker_token
    if args.public_base_url:
        os.environ["FEEDCORE_FETCHER_PUBLIC_BASE_URL"] = args.public_base_url

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Install remote_fetcher dependencies first: python -m pip install -r requirements.txt") from exc

    app = create_app(output_dir=Path(args.output_dir), token=args.token)
    uvicorn.run(app, host=args.host, port=args.port)


def _select_sources(request: FetchRunRequest) -> list[RssSource]:
    categories = request.default_categories or {}
    urls = request.rss_urls
    if request.quick_sample_size is not None and request.quick_sample_size > 0:
        urls = urls[: request.quick_sample_size]
    return [
        RssSource(
            rss_id=f"rss_{index:03d}",
            url=url,
            label=_label_from_url(url),
            default_category=categories.get(url, _guess_source_category(url)),
        )
        for index, url in enumerate(urls, start=1)
    ]


def _fetch_all_feeds(run_dir: Path, sources: list[RssSource], request: FetchRunRequest) -> dict[str, list[FeedItem]]:
    if not sources:
        return {}
    max_workers = max(1, min(request.rss_concurrency, len(sources)))
    out: dict[str, list[FeedItem]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_fetch_one_feed, run_dir, source, request): source.rss_id
            for source in sources
        }
        for future in as_completed(future_map):
            out[future_map[future]] = future.result()
    return {rss_id: out[rss_id] for rss_id in sorted(out)}


def _fetch_one_feed(run_dir: Path, source: RssSource, request: FetchRunRequest) -> list[FeedItem]:
    started = time.perf_counter()
    log_path = run_dir / "logs" / f"{source.rss_id}.log"
    _log_json(log_path, {"rss_id": source.rss_id, "event": "rss_fetch_start", "url": source.url})
    try:
        xml_text = _http_get_text(source.url, timeout=20)
        articles = parse_feed(xml_text, source.url)
        if request.max_articles is not None and request.max_articles > 0:
            articles = articles[: request.max_articles]
        feed_items = [
            FeedItem(article=article, default_category=source.default_category, source_label=source.label)
            for article in articles
            if article.link
        ]
        _log_json(
            log_path,
            {
                "rss_id": source.rss_id,
                "event": "rss_fetch_done",
                "status": "ok",
                "duration_ms": _duration_ms(started),
                "article_count": len(feed_items),
            },
        )
    except Exception as exc:
        feed_items = []
        _log_json(log_path, {"rss_id": source.rss_id, "event": "rss_fetch_done", "status": "error", "error": str(exc), "duration_ms": _duration_ms(started)})
    _write_json(run_dir / "rss_tasks" / f"{source.rss_id}_feed_items.json", [item.to_dict() for item in feed_items])
    return feed_items


def _collect_distinct_feed_items(
    feed_by_rss: dict[str, list[FeedItem]],
    max_articles: int | None,
    max_per_source: int | None = None,
) -> list[FeedItem]:
    seen_links: set[str] = set()
    out: list[FeedItem] = []
    for rss_id in sorted(feed_by_rss):
        kept_for_source = 0
        for item in feed_by_rss[rss_id]:
            if max_per_source is not None and max_per_source > 0 and kept_for_source >= max_per_source:
                break
            link = item.article.link
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            out.append(item)
            kept_for_source += 1
            if max_articles is not None and max_articles > 0 and len(out) >= max_articles:
                return out
    return out


def _fetch_all_article_text(
    run_dir: Path,
    sources: list[RssSource],
    feed_by_rss: dict[str, list[FeedItem]],
    request: FetchRunRequest,
) -> list[ArticleText]:
    source_by_id = {source.rss_id: source for source in sources}
    rss_ids = sorted(feed_by_rss)
    if not rss_ids:
        return []
    max_workers = max(1, min(request.article_fetch_concurrency, len(rss_ids)))
    records: list[ArticleText] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_fetch_article_text_for_source, run_dir, source_by_id[rss_id], feed_by_rss[rss_id], request): rss_id
            for rss_id in rss_ids
            if rss_id in source_by_id
        }
        for future in as_completed(future_map):
            records.extend(future.result())
    return sorted(records, key=lambda item: item.article_id)


def _fetch_article_text_for_source(
    run_dir: Path,
    source: RssSource,
    feed_items: list[FeedItem],
    request: FetchRunRequest,
) -> list[ArticleText]:
    log_path = run_dir / "logs" / f"{source.rss_id}.log"
    rss_records: list[ArticleText] = []
    for index, item in enumerate(feed_items, start=1):
        article_id = f"{source.rss_id.replace('_', '')}_a{index:03d}"
        rss_records.append(_fetch_one_article_text(source, article_id, item, request, log_path))
    _write_json(run_dir / "rss_tasks" / f"{source.rss_id}_articles_text.json", [item.to_dict() for item in rss_records])
    return rss_records


def _fetch_one_article_text(
    source: RssSource,
    article_id: str,
    item: FeedItem,
    request: FetchRunRequest,
    log_path: Path,
) -> ArticleText:
    started = time.perf_counter()
    fetch_error = None
    extract_error = None
    try:
        html = _http_get_text(item.article.link, timeout=45)
        text = extract_article_text(html, url=item.article.link)
        if not text:
            raise ValueError("no article text extracted")
    except Exception as exc:
        fetch_error = str(exc)
        if request.browser_enabled:
            try:
                html = _fetch_with_browser(item.article.link, timeout=request.browser_timeout)
                text = extract_article_text(html, url=item.article.link)
                if not text:
                    raise ValueError("no article text extracted by browser")
                fetch_error = None
            except Exception as browser_exc:
                extract_error = str(browser_exc)
                text = item.article.description or item.article.title
        else:
            text = item.article.description or item.article.title

    language = detect_language(text)
    _log_json(
        log_path,
        {
            "rss_id": source.rss_id,
            "event": "article_text_extracted",
            "article_id": article_id,
            "url": item.article.link,
            "status": "error" if fetch_error or extract_error else "ok",
            "duration_ms": _duration_ms(started),
            "text_chars": len(text or ""),
            "error": fetch_error or extract_error,
        },
    )
    return ArticleText(
        rss_id=source.rss_id,
        article_id=article_id,
        article=item.article,
        default_category=item.default_category,
        text=text,
        detected_language=language,
        fetch_error=fetch_error,
        extract_error=extract_error,
    )


def parse_feed(xml_text: str, feed_url: str) -> list[Article]:
    try:
        import feedparser
    except ImportError:
        return _parse_feed_with_element_tree(xml_text, feed_url)

    parsed = feedparser.parse(xml_text)
    articles: list[Article] = []
    for entry in parsed.entries:
        source = ""
        if getattr(entry, "source", None):
            source = getattr(entry.source, "title", "") or getattr(entry.source, "href", "")
        articles.append(
            Article(
                title=_clean(getattr(entry, "title", "")),
                link=_clean(getattr(entry, "link", "")),
                pub_date=_clean(getattr(entry, "published", "") or getattr(entry, "updated", "")),
                description=_clean(getattr(entry, "summary", "")),
                source=_clean(source),
                feed_url=feed_url,
            )
        )
    return articles


def _parse_feed_with_element_tree(xml_text: str, feed_url: str) -> list[Article]:
    root = ElementTree.fromstring(xml_text)
    articles: list[Article] = []
    for item in root.findall(".//item"):
        source_element = item.find("source")
        articles.append(
            Article(
                title=_clean(_find_text(item, "title")),
                link=_clean(_find_text(item, "link")),
                pub_date=_clean(_find_text(item, "pubDate")),
                description=_clean(_find_text(item, "description")),
                source=_clean(source_element.text if source_element is not None else ""),
                feed_url=feed_url,
            )
        )
    return articles


def extract_article_text(html: str, url: str | None = None) -> str:
    trafilatura_text = _extract_with_trafilatura(html, url)
    if trafilatura_text:
        return trafilatura_text
    soup_text = _extract_with_bs4(html)
    if soup_text:
        return soup_text
    return _extract_with_stdlib(html)


def _extract_with_trafilatura(html: str, url: str | None) -> str:
    try:
        import trafilatura
    except ImportError:
        return ""
    extracted = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
    return _normalize_text(extracted or "")


def _extract_with_bs4(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    root = soup.find("article") or soup.body or soup
    paragraphs = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    return _normalize_text("\n\n".join(p for p in paragraphs if p))


def _extract_with_stdlib(html: str) -> str:
    parser = _ParagraphParser()
    parser.feed(html)
    return _normalize_text("\n\n".join(parser.paragraphs))


class _ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self._in_paragraph = False
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer"}:
            self._skip_depth += 1
        if tag == "p" and self._skip_depth == 0:
            self._in_paragraph = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "p" and self._in_paragraph:
            paragraph = _normalize_text(" ".join(self._parts))
            if paragraph:
                self.paragraphs.append(paragraph)
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_paragraph and self._skip_depth == 0:
            self._parts.append(data)


def _proxy_url() -> str | None:
    explicit = os.environ.get("FEEDCORE_FETCHER_PROXY", "").strip()
    if explicit:
        return explicit
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _requests_proxies() -> dict[str, str] | None:
    proxy = _proxy_url()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _worker_fetch_url() -> str | None:
    value = os.environ.get("FEEDCORE_WORKER_FETCH_URL", "").strip()
    return value or None


def _worker_token() -> str | None:
    value = os.environ.get("FEEDCORE_WORKER_TOKEN", "").strip()
    return value or None


def _http_get_text(url: str, timeout: int) -> str:
    import requests

    worker_url = _worker_fetch_url()
    worker_token = _worker_token()
    if worker_url:
        if not worker_token:
            raise RuntimeError("FEEDCORE_WORKER_TOKEN is required when FEEDCORE_WORKER_FETCH_URL is set")
        return _http_get_text_via_worker(requests, worker_url, worker_token, url, timeout)

    kwargs: dict[str, object] = {
        "headers": {"User-Agent": "FeedCoreRemoteFetcher/0.1"},
        "timeout": timeout,
    }
    proxies = _requests_proxies()
    if proxies is not None:
        kwargs["proxies"] = proxies
    response = requests.get(url, **kwargs)
    response.raise_for_status()
    return response.text


def _http_get_text_via_worker(requests_module: Any, worker_url: str, worker_token: str, target_url: str, timeout: int) -> str:
    response = requests_module.get(
        worker_url,
        params={"url": target_url},
        headers={
            "Authorization": f"Bearer {worker_token}",
            "Accept": "*/*",
            "X-FeedCore-User-Agent": "FeedCoreRemoteFetcher/0.1",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def _fetch_with_browser(url: str, timeout: int) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Run: python -m playwright install chromium") from exc
    proxy_url = _proxy_url()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context_kwargs: dict[str, object] = {"user_agent": "FeedCoreRemoteFetcher/0.1"}
        if proxy_url:
            context_kwargs["proxy"] = {"server": proxy_url}
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout * 1000, 15_000))
        except Exception:
            pass
        html = page.content()
        context.close()
        browser.close()
    return html


def detect_language(text: str) -> str:
    sample = text[:2000]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    ascii_letters = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    if cjk >= max(10, ascii_letters // 3):
        return "zh"
    if ascii_letters >= 40:
        return "en"
    return "unknown"


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _log_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _append_workflow_log(run_dir: Path, name: str, input_count: int, output_count: int, skipped_count: int, message: str) -> None:
    path = run_dir / "logs" / "workflow.log"
    line = f"{_utc_now()} | {name} | input={input_count} | output={output_count} | skipped={skipped_count} | {message}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _find_text(item: ElementTree.Element, tag: str) -> str:
    element = item.find(tag)
    return element.text or "" if element is not None else ""


def _clean(value: object) -> str:
    return " ".join(unescape(str(value or "")).split())


def _normalize_text(value: str) -> str:
    value = unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _label_from_url(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0]


def _guess_source_category(url: str) -> str:
    lowered = url.lower()
    if any(token in lowered for token in ["ai", "openai", "nvidia", "tech", "google", "microsoft"]):
        return "人工智能与科技"
    if any(token in lowered for token in ["stock", "market", "fed", "inflation", "bitcoin", "crypto"]):
        return "金融市场与宏观"
    return "社会与其它"


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _positive_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    number = int(value)
    return number if number > 0 else None


def _positive_int_or_default(value: object, default: int) -> int:
    if value is None:
        return default
    number = int(value)
    return number if number > 0 else default


def _max_articles(value: object) -> int | None:
    if value is None:
        return None
    number = int(value)
    return None if number <= 0 else number


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _make_remote_run_id(client_run_id: str | None) -> str:
    suffix = _safe_id(client_run_id or uuid.uuid4().hex[:12])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"fetch_{timestamp}_{suffix}"


def _safe_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return clean[:64] or uuid.uuid4().hex[:12]


def _config_hash(config: dict[str, object]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
