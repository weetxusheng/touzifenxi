"""
专题子项「全文」索引：与 ``kr36_hot_topics_YYYYMMDD.json`` 同目录、同 report_date 的
``kr36_topic_fulltext_YYYYMMDD.json``，供整理/分析从磁盘汇总「全文」时直接读取，而不重复拉 URL。

- 视频：``*.transcript.txt``（ASR）
- 文章：专题子链保存的 ``*.html`` 文本

路径规则与 ``Kr36SourceAdapter._download_topic_item_asset`` 落盘一致（``kr36_topic_downloads/...``）。
"""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from dataclasses import replace

from utils.tools.analysis.models import ContentAnalysisInput

from ..core.source_adapter import (
    _safe_filename,
    _safe_path_component,
    effective_topic_item_kind_for_download,
)

# 与 step6_brief 中 _normalize_brief_lookup_url 行为一致，供建索引/查表共用
def normalize_brief_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    return u.rstrip("/")

# 与 hot_topics 主 JSON 同日期后缀，同目录
TOPIC_FULLTEXT_FILE_PREFIX = "kr36_topic_fulltext"
MAX_FULLTEXT_CHARS = 1_200_000


def topic_fulltext_json_name(report_date: date) -> str:
    return f"{TOPIC_FULLTEXT_FILE_PREFIX}_{report_date.strftime('%Y%m%d')}.json"


@runtime_checkable
class _ArticleLike(Protocol):
    title: str
    url: str
    article_id: str
    metadata: dict[str, object]


def _as_article_like(article: Any) -> _ArticleLike:
    if is_dataclass(article):
        return article  # type: ignore[return-value]
    if isinstance(article, dict):
        return _DictArticle(article)  # type: ignore[return-value]
    return article  # type: ignore[return-value]


class _DictArticle:
    def __init__(self, d: dict[str, Any]) -> None:
        self.title = str(d.get("title") or "")
        self.url = str(d.get("url") or "")
        self.article_id = str(d.get("article_id") or "")
        self.metadata = d.get("metadata") or {}
        if not isinstance(self.metadata, dict):
            self.metadata = {}


def read_topic_item_fulltext_from_disk(
    article: Any,
    *,
    report_date: date,
    topic_download_dir: Path,
) -> tuple[str, str, Path] | None:
    """
    从 ``topic_download_dir/kr36_topic_downloads/...`` 读专题子项全文。

    返回 ``(content_text, fulltext_source, absolute_path)``；无专题元数据或文件不存在则 ``None``。
    ``fulltext_source``: ``asr_transcript`` | ``topic_subpage_html`` | ``empty_file``
    """
    a = _as_article_like(article)
    metadata = a.metadata or {}
    if not str(metadata.get("topic_item_kind") or "").strip():
        return None
    item_kind = effective_topic_item_kind_for_download(a)  # type: ignore[arg-type]
    if not item_kind:
        return None

    topic_title = str(metadata.get("topic_title") or metadata.get("topic_url") or "topic")
    item_date = str(metadata.get("topic_item_date") or report_date.isoformat())
    basename = _safe_filename(f"{a.title}_{a.article_id}")
    sub = "videos" if item_kind == "video" else "articles"
    day_dir = (
        Path(topic_download_dir).resolve() / "kr36_topic_downloads" / _safe_path_component(topic_title) / item_date / sub
    )

    if item_kind == "video":
        candidates = (day_dir / f"{basename}.transcript.txt", day_dir / f"{basename}.html")
    else:
        candidates = (day_dir / f"{basename}.html",)

    for p in candidates:
        if p.is_file():
            raw = p.read_text(encoding="utf-8", errors="replace")
            if len(raw) > MAX_FULLTEXT_CHARS:
                raw = raw[:MAX_FULLTEXT_CHARS] + "\n... [truncated by topic_fulltext_index]"
            src = "asr_transcript" if p.suffix == ".txt" and "transcript" in p.name else "topic_subpage_html"
            if not raw.strip():
                return ("", "empty_file", p.resolve())
            return (raw, src, p.resolve())
    return None


def build_topic_fulltext_doc(
    articles: list[Any],
    *,
    run_dir: Path,
    report_date: date,
    topic_download_dir: Path | None = None,
) -> dict[str, Any]:
    """构造待写入的 JSON 对象（不写盘）。"""
    tdir = (topic_download_dir or run_dir).resolve()
    items: list[dict[str, Any]] = []
    for article in articles:
        got = read_topic_item_fulltext_from_disk(
            article,
            report_date=report_date,
            topic_download_dir=tdir,
        )
        a = _as_article_like(article)
        metadata = a.metadata or {}
        if not str(metadata.get("topic_item_kind") or "").strip():
            continue
        entry: dict[str, Any] = {
            "article_id": a.article_id,
            "url": a.url,
            "title": a.title,
            "topic_item_kind": str(metadata.get("topic_item_kind") or ""),
            "topic_title": str(metadata.get("topic_title") or ""),
            "topic_item_date": str(metadata.get("topic_item_date") or ""),
        }
        if got:
            text, src, abspath = got
            entry["fulltext_source"] = src
            entry["content_text"] = text
            try:
                entry["asset_relative_to_run"] = str(abspath.relative_to(run_dir.resolve()))
            except ValueError:
                entry["asset_path"] = str(abspath)
        else:
            entry["fulltext_source"] = "missing"
            entry["content_text"] = ""
            entry["note"] = "未找到 transcript/html；可能未下载或路径不一致"
        items.append(entry)
    if not items:
        return {
            "schema": "kr36_topic_fulltext",
            "schema_version": 1,
            "report_date": report_date.isoformat(),
            "run_dir": str(run_dir.resolve()),
            "topic_download_dir": str(tdir),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "items": [],
            "items_note": "无带 topic_item_kind 的条目，或尚无异步落盘文件",
        }
    return {
        "schema": "kr36_topic_fulltext",
        "schema_version": 1,
        "report_date": report_date.isoformat(),
        "run_dir": str(run_dir.resolve()),
        "topic_download_dir": str(tdir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_doc": "与 step 1.5 专题落盘一致；整理步骤请优先读本文件 content_text 字段。",
        "items": items,
    }


def write_kr36_topic_fulltext_json(
    articles: list[Any],
    *,
    run_dir: Path,
    report_date: date,
    topic_download_dir: Path | None = None,
) -> Path:
    """
    写入 ``<run_dir>/kr36_topic_fulltext_YYYYMMDD.json``（与 ``kr36_hot_topics_`` 同目录同日期后缀）。

    返回写入路径。``articles`` 可为 ``StandardArticle`` 或 fetch 产物的 ``dict`` 列表。
    """
    run_dir = run_dir.resolve()
    doc = build_topic_fulltext_doc(
        articles,
        run_dir=run_dir,
        report_date=report_date,
        topic_download_dir=topic_download_dir,
    )
    out = run_dir / topic_fulltext_json_name(report_date)
    out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def load_kr36_topic_fulltext_json(path: Path) -> dict[str, Any]:
    """供整理/后处理读取。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_content_by_article_id_from_topic_fulltext(run_dir: Path, report_date: date) -> dict[str, str]:
    """
    从 ``<run_dir>/kr36_topic_fulltext_*.json`` 建 ``article_id -> content_text`` 映射；文件不存在则返回空 dict。
    """
    p = run_dir / topic_fulltext_json_name(report_date)
    if not p.is_file():
        return {}
    doc = load_kr36_topic_fulltext_json(p)
    out: dict[str, str] = {}
    for row in doc.get("items") or []:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("article_id") or "").strip()
        if not aid:
            continue
        out[aid] = str(row.get("content_text") or "")
    return out


def build_topic_fulltext_excerpts_by_url_for_brief(
    run_dir: Path,
    report_date: date,
    *,
    max_chars_per_url: int = 16_000,
) -> dict[str, str]:
    """
    从 ``kr36_topic_fulltext_*.json`` 构造供 step6 ``topic_fulltext_excerpt`` 使用的 ``规范化 URL -> 正文`` 表。
    """
    p = run_dir / topic_fulltext_json_name(report_date)
    if not p.is_file():
        return {}
    doc = load_kr36_topic_fulltext_json(p)
    out: dict[str, str] = {}
    for row in doc.get("items") or []:
        if not isinstance(row, dict):
            continue
        url = normalize_brief_url(str(row.get("url") or ""))
        if not url:
            continue
        text = str(row.get("content_text") or "").strip()
        if not text:
            continue
        if len(text) > max_chars_per_url:
            text = text[:max_chars_per_url] + "\n... [为简报截断]"
        out[url] = text
    return out


def enrich_content_analysis_input_with_topic_fulltext(
    payload: ContentAnalysisInput,
    *,
    run_dir: Path,
    report_date: date,
    max_chars_per_url: int = 16_000,
) -> ContentAnalysisInput:
    """若 run 目录存在专题全文 JSON，则写入 ``ContentAnalysisInput.topic_fulltext_excerpts_by_url`` 供 step5 与 step6 使用。"""
    m = build_topic_fulltext_excerpts_by_url_for_brief(
        run_dir, report_date, max_chars_per_url=max_chars_per_url
    )
    if not m:
        return payload
    return replace(payload, topic_fulltext_excerpts_by_url=m)
