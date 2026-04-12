from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .base import ContentSourceAdapter
from .models import RawArticleDetail, RawArticleRef, StandardArticle

INFOQ_CN_ROOT = "https://www.infoq.cn"
INFOQ_HOT_LIST_ENDPOINT = f"{INFOQ_CN_ROOT}/public/v1/article/getHotListBySouce"
INFOQ_NEW_LIST_ENDPOINT = f"{INFOQ_CN_ROOT}/api/u/v1/index/get_new_list"
INFOQ_DETAIL_ENDPOINT = f"{INFOQ_CN_ROOT}/public/v1/article/getDetail"
INFOQ_REPORT_TZ = ZoneInfo("Asia/Shanghai")
INFOQ_DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


class InfoQSourceAdapter(ContentSourceAdapter):
    """InfoQ 中文站适配器。"""

    source_site = "infoq"

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        payload = self._curl_json(
            INFOQ_NEW_LIST_ENDPOINT,
            data={"size": 12},
            referer="https://xie.infoq.cn/",
        )
        return self.parse_listing_payload(payload, report_date=report_date)

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        uuid = str(ref.metadata.get("uuid") or ref.article_id.rsplit(":", 1)[-1])
        detail_payload = self._curl_json(
            INFOQ_DETAIL_ENDPOINT,
            data={"uuid": uuid},
            referer=f"{INFOQ_CN_ROOT}/",
        )
        detail = detail_payload.get("data") if isinstance(detail_payload, dict) else {}
        if not isinstance(detail, dict):
            raise RuntimeError(f"InfoQ 详情接口返回异常：{uuid}")
        content_url = str(detail.get("content_url") or "").strip()
        content_payload: object = {}
        if content_url:
            content_payload = self._curl_json(content_url, referer=f"{INFOQ_CN_ROOT}/")
        author = normalize_infoq_authors(detail.get("author"))
        tags = normalize_infoq_tags(detail.get("label"))
        published_at = infoq_timestamp_to_date_text(detail.get("publish_time"))
        return RawArticleDetail(
            source_site=self.source_site,
            article_id=ref.article_id,
            title=str(detail.get("article_title") or ref.title or ""),
            url=ref.url,
            published_at=published_at,
            author=author,
            channel=ref.channel or "热点",
            source_bucket=ref.source_bucket or "热点",
            tags=tags,
            summary=str(detail.get("article_summary") or ref.summary or ""),
            content_text=extract_infoq_content_text(content_payload),
            metadata={
                **ref.metadata,
                "uuid": uuid,
                "detail": detail,
                "content": content_payload,
            },
        )

    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle:
        detail = raw.metadata.get("detail") if isinstance(raw.metadata, dict) else None
        content_payload = raw.metadata.get("content") if isinstance(raw.metadata, dict) else None
        tags = list(raw.tags or [])
        keywords = tags or [raw.channel] if raw.channel else tags
        metadata = dict(raw.metadata)
        metadata.setdefault("channel_key", normalize_channel_key(raw.channel or raw.source_bucket or "hot"))
        metadata.setdefault("channel_name", raw.channel or raw.source_bucket or "热点")
        metadata.setdefault("channel_url", f"{INFOQ_CN_ROOT}/")
        return StandardArticle(
            source_site=self.source_site,
            source_bucket=raw.source_bucket or "热点",
            channel=raw.channel or "热点",
            article_id=raw.article_id,
            title=raw.title,
            url=raw.url,
            published_at=raw.published_at or infoq_timestamp_to_date_text((detail or {}).get("publish_time")),
            author=raw.author,
            tags=tags,
            keywords=keywords,
            summary=raw.summary,
            content_text=raw.content_text or extract_infoq_content_text(content_payload),
            metadata=metadata,
        )

    def parse_listing_payload(self, payload: object, *, report_date: date) -> list[RawArticleRef]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("InfoQ 列表接口返回缺少 data。")
        listing_kind = "new_list"
        listing = data.get("list")
        if not isinstance(listing, list):
            listing = data.get("hot_day_list")
            listing_kind = "hot_day_list"
        if not isinstance(listing, list):
            raise RuntimeError("InfoQ 列表接口返回缺少文章列表。")
        refs: list[RawArticleRef] = []
        for item in listing:
            if not isinstance(item, dict):
                continue
            published_at = infoq_timestamp_to_date_text(item.get("publish_time"))
            if published_at != report_date.isoformat():
                continue
            uuid = str(item.get("uuid") or "").strip()
            if not uuid:
                continue
            refs.append(
                RawArticleRef(
                    source_site=self.source_site,
                    article_id=f"{self.source_site}:{uuid}",
                    title=str(item.get("article_title") or "").strip(),
                    url=f"{INFOQ_CN_ROOT}/article/{uuid}",
                    published_at=published_at,
                    channel=infer_infoq_listing_bucket(item, listing_kind=listing_kind),
                    source_bucket=infer_infoq_listing_bucket(item, listing_kind=listing_kind),
                    summary=str(item.get("article_summary") or "").strip(),
                    metadata={
                        "uuid": uuid,
                        "author": normalize_infoq_authors(item.get("author")),
                        "tags": normalize_infoq_tags(item.get("label")),
                        "listing_kind": listing_kind,
                        "listing_item": item,
                    },
                )
            )
        return refs

    def default_source_config(self) -> dict[str, object]:
        return {"listing": "new_list", "size": 12}

    def _curl_json(self, url: str, *, data: dict[str, object] | None = None, referer: str) -> object:
        command = [
            "curl",
            "-sS",
            url,
            "-H",
            "content-type: application/json",
            "-H",
            f"user-agent: {INFOQ_DEFAULT_USER_AGENT}",
            "-H",
            f"origin: {INFOQ_CN_ROOT}",
            "-H",
            f"referer: {referer}",
        ]
        if data is not None:
            command.extend(["--data", json.dumps(data, ensure_ascii=False)])
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)


def normalize_infoq_authors(payload: object) -> str:
    if isinstance(payload, list):
        names = [str(item.get("nickname") or "").strip() for item in payload if isinstance(item, dict)]
        names = [name for name in names if name]
        return "、".join(names)
    if isinstance(payload, dict):
        return str(payload.get("nickname") or "").strip()
    return ""


def normalize_infoq_tags(payload: object) -> list[str]:
    tags: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or "").strip()
            else:
                name = str(item).strip()
            if name and name not in tags:
                tags.append(name)
    return tags


def normalize_channel_key(value: str) -> str:
    token = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    token = token.strip("-")
    return token or "general"


def infer_infoq_listing_bucket(item: dict[str, object], *, listing_kind: str) -> str:
    topic = item.get("topic")
    if isinstance(topic, dict):
        name = str(topic.get("name") or topic.get("title") or "").strip()
        if name:
            return name
    if listing_kind == "hot_day_list":
        return "热点"
    return "首页"


def infoq_timestamp_to_date_text(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=INFOQ_REPORT_TZ).date().isoformat()


def extract_infoq_content_text(payload: object) -> str:
    """把 InfoQ 的 ProseMirror JSON 文档抽成纯文本。"""

    lines: list[str] = []
    for block in _iter_infoq_text_blocks(payload):
        compact = " ".join(block.split())
        if compact:
            lines.append(compact)
    return "\n".join(lines)


def _iter_infoq_text_blocks(node: object) -> list[str]:
    if isinstance(node, dict):
        node_type = str(node.get("type") or "")
        if node_type == "text":
            text = str(node.get("text") or "").strip()
            return [text] if text else []
        child_blocks: list[str] = []
        for child in node.get("content") or []:
            child_blocks.extend(_iter_infoq_text_blocks(child))
        if node_type in {"paragraph", "heading", "blockquote"}:
            joined = "".join(child_blocks).strip()
            return [joined] if joined else []
        return child_blocks
    if isinstance(node, list):
        blocks: list[str] = []
        for item in node:
            blocks.extend(_iter_infoq_text_blocks(item))
        return blocks
    return []
