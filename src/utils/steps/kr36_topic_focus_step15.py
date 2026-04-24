"""
专题聚焦 Step 1.5：从 /topics/ 列表到「子页真实 HTML」→ CDN 下载 → 视频抽音与转写。

该模块是 ``kr36.topic_focus_step15`` 的通用步骤实现，供 kr36 入口和其他流程复用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from utils.tools.content_models import RawArticleRef

TOPIC_ITEM_FULLTEXT_ROLE_ASR = "topic_video_asr_transcript"
KR36_CDN_URL_SHAPE_EXAMPLE = (
    "https://videos.36krcdn.com/20260414/v2_1776165984015_video_mp4_v11"
)

__all__ = [
    "KR36_CDN_URL_SHAPE_EXAMPLE",
    "TOPIC_ITEM_FULLTEXT_ROLE_ASR",
    "SubpageForDownload",
    "step1_parse_topics_listing_and_select_focus",
    "step2_parse_topic_items_from_detail_html",
    "step3_list_subpage_targets",
    "step5_list_video_cdn_urls_from_subpage_html",
    "step5_download_cdn_url_to_path",
    "step6a_extract_mp3_for_asr",
    "step6b_transcribe_mp3_to_transcript_files",
    "topic_time_window",
]


@dataclass(frozen=True)
class SubpageForDownload:
    item: RawArticleRef
    subpage_url: str
    kind: str  # "video" | "article"


def topic_time_window(
    report_date: date, *, mode: str = "weekday_split"
) -> tuple[date | None, date | None]:
    from kr36 import source_adapter as m

    return m.resolve_kr36_topic_subitem_date_window(report_date, mode=mode)


def step1_parse_topics_listing_and_select_focus(
    topics_listing_html: str,
    *,
    report_date: date,
    focus_keywords: tuple[str, ...],
    focus_limit: int,
) -> tuple[list[RawArticleRef], list[RawArticleRef]]:
    from kr36 import source_adapter as m

    all_refs = m.parse_topics_listing_html(
        topics_listing_html,
        base_url=m.KR36_ROOT,
        listing_url=m.KR36_TOPICS_URL,
        report_date=report_date,
    )
    focus = m.select_focus_topics(
        all_refs, focus_keywords=focus_keywords, limit=focus_limit
    )
    if not focus:
        return (all_refs[: max(1, focus_limit)], all_refs)
    return (focus, all_refs)


def step2_parse_topic_items_from_detail_html(
    topic_detail_html: str,
    *,
    report_date: date,
    topic_title: str,
    listing_url: str,
    window_start: date | None,
    window_end: date | None,
) -> list[RawArticleRef]:
    from kr36 import source_adapter as m

    return m.parse_topic_detail_html(
        topic_detail_html,
        base_url=m.KR36_ROOT,
        listing_url=listing_url,
        report_date=report_date,
        topic_title=topic_title,
        window_start=window_start,
        window_end=window_end,
    )


def step3_list_subpage_targets(
    items: list[RawArticleRef],
) -> list[SubpageForDownload]:
    from kr36 import source_adapter as m

    out: list[SubpageForDownload] = []
    for it in items:
        kind = m.effective_topic_item_kind_for_download(it)
        if kind == "video":
            sub = m.resolve_kr36_video_detail_page_url(it.url)
            if not sub:
                continue
            out.append(SubpageForDownload(item=it, subpage_url=sub, kind="video"))
        elif kind == "article":
            out.append(SubpageForDownload(item=it, subpage_url=it.url, kind="article"))
    return out


def step5_list_video_cdn_urls_from_subpage_html(video_subpage_html: str) -> list[str]:
    from kr36 import source_adapter as m

    return m.extract_video_media_urls(video_subpage_html)


def step5_download_cdn_url_to_path(
    cdn_url: str,
    output_path: Path,
    *,
    video_page_referer: str,
    cookie_header: str,
    user_agent: str,
    curl_max_time_seconds: int = 600,
) -> tuple[bool, str]:
    from kr36 import source_adapter as m
    from kr36.topic import media as topic_media

    ua = (user_agent or "").strip() or m.KR36_DEFAULT_USER_AGENT
    return topic_media.download_kr36_topic_video(
        cdn_url,
        output_path,
        user_agent=ua,
        referer=video_page_referer,
        cookie_header=cookie_header,
        curl_max_time_seconds=curl_max_time_seconds,
    )


def step6a_extract_mp3_for_asr(video_path: Path, mp3_path: Path) -> bool:
    from kr36.topic import media as topic_media

    return topic_media.extract_mp3_for_speech(video_path, mp3_path)


def step6b_transcribe_mp3_to_transcript_files(
    mp3_path: Path,
    *,
    transcript_path: Path,
    asr_json_path: Path,
    video_path_for_json: Path,
    config: Mapping[str, Any] | None,
    timeout_seconds: float = 300.0,
) -> tuple[str, int]:
    from utils import volc_speech as vs

    creds = vs.resolve_volc_speech_credentials(config=config)
    if not creds:
        raise RuntimeError(
            "volc speech: set sources.kr36 volc_speech_api_key or "
            "volc_speech_app_key+volc_speech_access_key in config/runtime.local.json"
        )
    data, resp_headers = vs.bigmodel_flash_recognize_file(
        mp3_path,
        creds,
        timeout_seconds=timeout_seconds,
    )
    code = str(resp_headers.get("x-api-status-code") or "")
    if code not in ("20000000", "20000003"):
        msg = resp_headers.get("x-api-message") or ""
        raise RuntimeError(f"volc speech status {code}: {msg}")
    text = ""
    if isinstance(data.get("result"), dict):
        text = str(data["result"].get("text") or "").strip()
    transcript_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
    asr_json_path.write_text(
        json.dumps(
            {
                "video_path": str(video_path_for_json),
                "audio_path": str(mp3_path),
                "x_api_status_code": code,
                "x_api_message": resp_headers.get("x-api-message"),
                "response": data,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return (code, len(text))

