from __future__ import annotations

import json
import sys
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests

from feedcore.config import AppConfig


@dataclass(frozen=True)
class RemoteFetchArtifact:
    run_id: str
    local_dir: Path
    artifact_path: Path
    manifest: dict[str, object]
    rss_sources: list[dict[str, object]]
    feed_items: list[dict[str, object]]
    article_contents: list[dict[str, object]]


def fetch_remote_artifact(
    *,
    config: AppConfig,
    base_url: str,
    token: str,
    output_root: Path,
    client_run_id: str | None = None,
    poll_interval: int = 3,
) -> RemoteFetchArtifact:
    if not token:
        raise ValueError("remote fetcher token is required")

    base_url = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    no_proxy = {"http": None, "https": None}
    payload = {
        "client_run_id": client_run_id or f"full_{int(time.time())}",
        "config": _remote_config_payload(config),
    }

    print(f"POST {base_url}/api/fetch-runs", flush=True)
    created = requests.post(
        f"{base_url}/api/fetch-runs",
        headers=headers,
        json=payload,
        timeout=60,
        proxies=no_proxy,
    )
    if created.status_code == 401:
        raise ValueError(
            "remote fetcher rejected the bearer token (401). "
            "Set FEEDCORE_FETCHER_TOKEN in .env to match the token on the remote_fetcher server "
            f"({base_url})."
        )
    created.raise_for_status()
    run_id = created.json()["run_id"]
    print(f"remote_run_id={run_id}", flush=True)

    # 远程 fetcher 在 playwright 重负载时，progress API 也可能 60s 没响应。
    # 单次失败不应炸掉整个流水线 — 容忍单次 timeout/连接错误，下一轮再问。
    while True:
        try:
            progress = requests.get(
                f"{base_url}/api/fetch-runs/{run_id}/progress",
                headers=headers,
                timeout=180,
                proxies=no_proxy,
            )
            progress.raise_for_status()
        except (requests.ReadTimeout, requests.ConnectionError) as exc:
            print(f"  [warn] progress poll error ({type(exc).__name__}: {exc}); retry in {poll_interval}s", flush=True)
            time.sleep(poll_interval)
            continue
        status = progress.json()
        state = status.get("status")
        print(
            f"  [{state}] rss={status.get('rss_sources', 0)} "
            f"feed_items={status.get('feed_items', 0)} "
            f"articles={status.get('articles_done', 0)}/{status.get('articles_total', 0)} "
            f"failed={status.get('fetch_failed', 0)}",
            flush=True,
        )
        if state in {"succeeded", "failed"}:
            break
        time.sleep(max(1, poll_interval))

    if status.get("status") != "succeeded":
        raise RuntimeError(json.dumps(status, ensure_ascii=False, indent=2))

    local_dir = output_root / run_id
    local_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = local_dir / "artifact.zip"
    print(f"GET {base_url}/api/fetch-runs/{run_id}/artifact", flush=True)
    artifact = requests.get(
        f"{base_url}/api/fetch-runs/{run_id}/artifact",
        headers=headers,
        timeout=120,
        proxies=no_proxy,
    )
    artifact.raise_for_status()
    artifact_path.write_bytes(artifact.content)
    _extract_zip(artifact.content, local_dir)

    result = RemoteFetchArtifact(
        run_id=run_id,
        local_dir=local_dir,
        artifact_path=artifact_path,
        manifest=_read_json_object(local_dir / "manifest.json"),
        rss_sources=_read_json_list(local_dir / "step1_rss_sources.json"),
        feed_items=_read_json_list(local_dir / "step2_feed_items.json"),
        article_contents=_read_json_list(local_dir / "step3_article_contents.json"),
    )
    _cleanup_remote_run(base_url, run_id, headers, no_proxy)
    return result


def _remote_config_payload(config: AppConfig) -> dict[str, object]:
    rss_sources: list[dict[str, str]] = []
    categories = config.rss_default_categories or {}
    for url in config.rss_urls:
        source = {"url": url}
        category = categories.get(url)
        if category:
            source["default_category"] = category
        rss_sources.append(source)

    return {
        "rss_sources": rss_sources,
        "quick_sample_size": config.quick_sample_size,
        "max_articles": 0 if config.max_articles is None else config.max_articles,
        "max_articles_per_source": config.max_articles_per_source,
        "workflow": {
            "rss_concurrency": config.workflow.rss_concurrency,
            "article_fetch_concurrency": config.workflow.article_fetch_concurrency,
        },
        "browser": {
            "enabled": config.browser.enabled,
            "timeout": config.browser.timeout,
        },
    }


def _extract_zip(content: bytes, target_dir: Path) -> None:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for member in archive.infolist():
            path = target_dir / member.filename
            resolved = path.resolve()
            if not str(resolved).startswith(str(target_dir.resolve())):
                raise RuntimeError(f"unsafe zip path: {member.filename}")
            if member.is_dir():
                resolved.mkdir(parents=True, exist_ok=True)
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(archive.read(member))


def _read_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _read_json_list(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise RuntimeError(f"expected JSON list: {path}")
    return [item for item in value if isinstance(item, dict)]


def _cleanup_remote_run(
    base_url: str,
    run_id: str,
    headers: dict[str, str],
    no_proxy: dict[str, None],
) -> None:
    try:
        resp = requests.delete(
            f"{base_url}/api/fetch-runs/{run_id}",
            headers=headers,
            timeout=30,
            proxies=no_proxy,
        )
        if resp.status_code == 404:
            print("remote cleanup: already deleted", flush=True)
            return
        resp.raise_for_status()
        print("remote cleanup: deleted", flush=True)
    except Exception as exc:
        print(f"remote cleanup: warning: {exc}", file=sys.stderr, flush=True)
