"""正文获取：大佳啦 ``article_html`` 与直连文章页并行。"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from gzh_pipeline.audit.trace import TraceRecorder
from gzh_pipeline.dajiala.html import inject_source_url_into_page, wrap_html
from gzh_pipeline.dajiala.url_fetch import (
    UrlFetchMode,
    extract_weixin_content_html,
    fetch_weixin_article_by_mode,
    is_weixin_verify_page,
    parse_url_fetch_mode,
)
from gzh_pipeline.dajiala.weixin_media import normalize_weixin_media_html
from gzh_pipeline.parse.extract import _extract_body_fragment, _html_to_plain

BodySource = Literal["dajiala", "url"]

VALID_BODY_SOURCES = frozenset({"dajiala", "url"})


def parse_body_sources(raw: str | None) -> frozenset[BodySource]:
    """
    解析 ``GZH_DAJIALA_BODY_SOURCES``：

    - ``both`` / ``all`` / ``parallel`` → ``dajiala`` + ``url``（默认，用于对比）
    - ``dajiala`` / ``url`` 或逗号组合
    """
    text = (raw if raw is not None else os.getenv("GZH_DAJIALA_BODY_SOURCES", "both")).strip().lower()
    if not text or text in ("both", "all", "parallel"):
        return frozenset({"dajiala", "url"})
    parts = {p.strip() for p in text.replace(";", ",").split(",") if p.strip()}
    if parts <= VALID_BODY_SOURCES and parts:
        return frozenset(parts)  # type: ignore[return-value]
    raise ValueError(
        f"invalid GZH_DAJIALA_BODY_SOURCES={text!r}; use both | dajiala | url | dajiala,url"
    )


def fetch_article_bodies_parallel(
    client: Any,
    url: str,
    sources: frozenset[BodySource],
    *,
    trace: TraceRecorder | None = None,
    article_label: str = "",
) -> tuple[dict[BodySource, str], dict[BodySource, str]]:
    """并行拉取各来源正文片段（未 ``wrap_html``）。"""
    bodies: dict[BodySource, str] = {}
    errors: dict[BodySource, str] = {}
    url_mode: UrlFetchMode = getattr(client, "url_fetch_mode", None) or parse_url_fetch_mode()

    if trace:
        trace.add_step(
            "body_fetch_start",
            article_label or url[:120],
            True,
            {"source_url": url, "sources": sorted(sources), "url_fetch_mode": url_mode},
        )

    def _dajiala() -> str:
        return client.fetch_article_html(url)

    def _url() -> str:
        return fetch_weixin_article_by_mode(
            client.session,
            url,
            mode=url_mode,
            timeout=client.timeout,
            trace=trace,
        )

    tasks: dict[BodySource, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if "dajiala" in sources:
            tasks["dajiala"] = pool.submit(_dajiala)
        if "url" in sources:
            tasks["url"] = pool.submit(_url)
        for name, fut in tasks.items():
            try:
                bodies[name] = fut.result() or ""
            except Exception as exc:
                errors[name] = str(exc)

    if trace:
        detail: dict[str, Any] = {"source_url": url}
        for name in sources:
            if name in errors:
                detail[name] = {"ok": False, "error": errors[name]}
            else:
                raw = bodies.get(name, "")
                detail[name] = {
                    "ok": True,
                    "byte_length": len((raw or "").encode("utf-8", errors="replace")),
                    "empty": not (raw or "").strip(),
                }
        trace.add_step(
            "body_fetch_done",
            article_label or url[:120],
            not errors or len(errors) < len(sources),
            detail,
            err="; ".join(f"{k}:{v}" for k, v in errors.items()) if errors else None,
        )

    return bodies, errors


def plain_text_from_wrapped_html(wrapped: str) -> str:
    return _html_to_plain(_extract_body_fragment(wrapped))


def plain_text_for_body_compare(source: BodySource, raw: str, *, url_fetch_mode: UrlFetchMode = "full") -> str:
    """对比用纯文本：url+full 时从整页抽 ``#js_content``，否则按导出页结构解析。"""
    if not (raw or "").strip():
        return ""
    if source == "url" and url_fetch_mode == "full":
        chunk = extract_weixin_content_html(raw)
        if chunk:
            return _html_to_plain(chunk)
        return _html_to_plain(raw)
    return plain_text_from_wrapped_html(raw)


def finalize_body_html(
    source: BodySource,
    raw: str,
    *,
    title: str,
    source_url: str,
    url_fetch_mode: UrlFetchMode = "full",
) -> str:
    """生成落盘 HTML：url+full 为整页响应并在 body 内注入原文链接；其余为片段 + ``wrap_html``。"""
    if source == "url":
        raw = normalize_weixin_media_html(raw or "")
    if source == "url" and url_fetch_mode == "full":
        return inject_source_url_into_page(raw, title, source_url)
    return wrap_html(title, source_url, raw or "")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_body_compare_sidecar(
    path: Path,
    *,
    title: str,
    source_url: str,
    entries: dict[str, dict[str, Any]],
    plain_by_source: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        k: {kk: vv for kk, vv in v.items() if kk != "plain_text"}
        for k, v in entries.items()
        if k in ("dajiala", "url")
    }
    payload = {
        "title": title,
        "source_url": source_url,
        "sources": summary,
        "plain_text_equal": len(set(plain_by_source.values())) <= 1 if len(plain_by_source) >= 2 else None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_export_html_files(
    *,
    account_dir: Path,
    filename_stem: str,
    title: str,
    source_url: str,
    bodies: dict[BodySource, str],
    errors: dict[BodySource, str],
    sources: frozenset[BodySource],
    url_fetch_mode: UrlFetchMode | None = None,
) -> tuple[list[Path], dict[str, dict[str, Any]]]:
    """
    写入导出 HTML。

    - 对比模式（同时启用两种来源）：主文件 ``{stem}.html`` 为大佳啦；``_compare/`` 下各一份 + ``.compare.json``
    - 单来源：仅写 ``{stem}.html``
    """
    url_mode = url_fetch_mode or parse_url_fetch_mode()
    compare_mode = sources == frozenset({"dajiala", "url"})
    compare_dir = account_dir / "_compare"
    if compare_mode:
        compare_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    entries: dict[str, dict[str, Any]] = {}
    wrapped_by_source: dict[BodySource, str] = {}
    plain_by_source: dict[str, str] = {}

    for name in ("dajiala", "url"):
        if name not in sources:
            continue
        if name in errors:
            entries[name] = {"error": errors[name]}
            continue
        raw = bodies.get(name, "")
        wrapped = finalize_body_html(
            name,
            raw,
            title=title,
            source_url=source_url,
            url_fetch_mode=url_mode if name == "url" else "extract",
        )
        wrapped_by_source[name] = wrapped
        plain = plain_text_for_body_compare(name, wrapped, url_fetch_mode=url_mode if name == "url" else "extract")
        plain_by_source[name] = plain
        meta: dict[str, Any] = {
            "byte_length": len(wrapped.encode("utf-8")),
            "sha256_hex": sha256_text(wrapped),
            "plain_text_chars": len(plain),
            "fragment_empty": not (raw or "").strip(),
            "url_fetch_mode": url_mode if name == "url" else None,
            "verify_page": is_weixin_verify_page(raw) if name == "url" and url_mode == "full" else False,
            "full_page": name == "url" and url_mode == "full",
        }
        if compare_mode:
            out_path = compare_dir / f"{filename_stem}.{name}.html"
        else:
            out_path = account_dir / f"{filename_stem}.html"
        out_path.write_text(wrapped, encoding="utf-8")
        meta["path"] = str(out_path)
        written.append(out_path)
        entries[name] = meta

    if compare_mode:
        primary_name: BodySource | None = "dajiala" if "dajiala" in wrapped_by_source else ("url" if "url" in wrapped_by_source else None)
        if primary_name is not None:
            primary = account_dir / f"{filename_stem}.html"
            primary.write_text(wrapped_by_source[primary_name], encoding="utf-8")
            if primary not in written:
                written.append(primary)
            entries["primary"] = {"path": str(primary), "source": primary_name}
        sidecar = compare_dir / f"{filename_stem}.compare.json"
        write_body_compare_sidecar(
            sidecar,
            title=title,
            source_url=source_url,
            entries=entries,
            plain_by_source=plain_by_source,
        )
        written.append(sidecar)

    return written, entries
