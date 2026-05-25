"""ChipSourceAdapter：把 chip channels 路由到 ContentSourceAdapter 协议。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable

from chip.channels.spec import CHANNELS
from utils.tools.content_models import (
    ContentSourceAdapter,
    RawArticleDetail,
    RawArticleRef,
    StandardArticle,
)

logger = logging.getLogger(__name__)


class ChipSourceAdapter(ContentSourceAdapter):
    """聚合 SEMI + 爱集微 channel 的 chip 单源适配器。"""

    source_site = "chip"

    def __init__(self) -> None:
        self.failed_channels: set[str] = set()

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        refs: list[RawArticleRef] = []
        for channel_key, spec in CHANNELS.items():
            try:
                channel_refs = spec.fetch_listing(spec, report_date)
            except Exception as exc:
                # 单 channel 故障不应拖垮整体抓取；记录后继续推进其他 channel
                logger.warning("chip channel listing failed: %s: %s", channel_key, exc)
                self.failed_channels.add(channel_key)
                continue
            refs.extend(channel_refs)
        # 1) 同 channel 内按 URL 去重
        refs = _dedupe_within_channel(refs)
        # 2) 跨 channel 标题去重：同事件在 SEMI / 爱集微 都出现时保留 SEMI，
        #    被合并的 URL 写入 metadata['alt_urls'] 便于回溯
        refs = cross_channel_dedupe(refs)
        return refs

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        spec = CHANNELS[ref.channel]
        return spec.fetch_article(spec, ref)

    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle:
        keywords = [str(k) for k in raw.metadata.get("keywords") or []]
        return StandardArticle(
            source_site=self.source_site,
            source_bucket=raw.source_bucket or raw.channel,
            channel=raw.channel,
            article_id=raw.article_id,
            title=raw.title,
            url=raw.url,
            published_at=raw.published_at,
            author=raw.author,
            tags=list(raw.tags),
            keywords=keywords,
            summary=raw.summary,
            content_text=raw.content_text,
            metadata=dict(raw.metadata),
        )


def _dedupe_within_channel(refs: Iterable[RawArticleRef]) -> list[RawArticleRef]:
    seen: set[tuple[str, str]] = set()
    out: list[RawArticleRef] = []
    for ref in refs:
        key = (ref.channel, ref.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def cross_channel_dedupe(
    refs: list[RawArticleRef],
    *,
    similarity_threshold: float = 0.6,
) -> list[RawArticleRef]:
    """Drop near-duplicate titles across channels; keep SEMI when both present.

    Uses 2-gram Jaccard similarity over normalized titles. Surviving ref carries
    the dropped sibling's URL in `metadata['alt_urls']`.
    """

    # SEMI 优先：当两个 channel 同条新闻命中时，留 SEMI，丢 laoyaoba，
    # 但把 laoyaoba 的 URL 存进幸存者的 metadata['alt_urls']。
    priority = {"semi": 0, "laoyaoba": 1}
    ordered = sorted(refs, key=lambda r: (priority.get(r.channel, 99), r.article_id))

    survivors: list[RawArticleRef] = []
    for ref in ordered:
        norm = _normalize_title(ref.title)
        merged = False
        for i, kept in enumerate(survivors):
            if _jaccard_2gram(norm, _normalize_title(kept.title)) >= similarity_threshold:
                alt = list(kept.metadata.get("alt_urls") or [])
                alt.append(ref.url)
                new_meta = dict(kept.metadata)
                new_meta["alt_urls"] = alt
                survivors[i] = _replace_metadata(kept, new_meta)
                merged = True
                break
        if not merged:
            survivors.append(ref)
    return survivors


def _replace_metadata(ref: RawArticleRef, metadata: dict[str, object]) -> RawArticleRef:
    return RawArticleRef(
        source_site=ref.source_site,
        article_id=ref.article_id,
        title=ref.title,
        url=ref.url,
        published_at=ref.published_at,
        channel=ref.channel,
        source_bucket=ref.source_bucket,
        summary=ref.summary,
        metadata=metadata,
    )


def _normalize_title(text: str) -> str:
    """Strip punctuation/whitespace, lowercase ASCII, full→half width."""

    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif ch in "　 \t\n\r":
            continue
        elif ch in "，。！？：；、,.!?:;\"'()（）「」【】":
            continue
        else:
            out.append(ch.lower())
    return "".join(out)


def _jaccard_2gram(a: str, b: str) -> float:
    if not a or not b:
        return 0.0

    def grams(s: str) -> set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}

    sa, sb = grams(a), grams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
