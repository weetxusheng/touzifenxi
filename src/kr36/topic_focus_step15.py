"""
专题聚焦 Step 1.5：从 /topics/ 列表到「子页真实 HTML」→ CDN 下载 → 视频抽音与转写。

与 Kr36SourceAdapter 中 listing→专题详情→/video/ 播放页→extract→download→ASR 的逻辑一一对应，拆成
可单测、可复用的多段，便于在其它入口（如独立脚本、CLI）复用同一套语义。

1. 在 /topics/ 全量解析专题卡片，再按「本周有大事」「36氪编辑精选」等关键词选出聚焦专题（及列表兜底）。
2. 对每个聚焦专题的详情页 HTML 做 parse，得到时间窗内视频/文章条目（子链可能是 m 站或中间形态）。
3. 按条目标注 kind：视频则规范到 PC 播放子页 `https://36kr.com/video/{id}`；文章保留 /p/ 子页，用于后续再次请求。
4. 对每条子链再请求，拿到含 `<video src>` 或 `window.initialState.videoDetail` 的真实 HTML
   （由 Adapter._fetch_text + 风控/滑块/壳页恢复完成）。
5. 从(4) 的 HTML 用 extract_video_media_urls 拉平 CDN 列表，再按 Referer+Cookie 下载到本地
   （与 topic_media 一致）。直链常类似：
   https://videos.36krcdn.com/{yyyymmdd}/v2_{...}_video_mp4{,_vN}
6. 对本地已下载的专题视频：ffmpeg 抽 ``*.asr.mp3`` → 火山豆包「大模型录音文件极速版」
   → 同目录 ``*.transcript.txt``（纯文本「全文」）+ ``*.asr.json``。配置见 ``sources.kr36`` 中
   volc_speech_*、topic_asr_*。专题视频的「全文」以转写为准，主流程 **Step2 不应对同一视频再拉 HTML 当正文**（见 ``docs/topic-focus-step15.md``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from utils.tools.content_models import RawArticleRef

#: 专题视频在下游「全文」字段中的语义：与网页正文区分，以 ASR 文件为准
TOPIC_ITEM_FULLTEXT_ROLE_ASR = "topic_video_asr_transcript"

#: Step5 典型 CDN 直链（与线上一致时可无 `.mp4`；`topic_media.kr36_video_url_candidates` 会再试加后缀）
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
    """Step 3: 单条专题子项在「再请求子页」阶段使用的 URL 与类型。"""

    item: RawArticleRef
    subpage_url: str
    kind: str  # "video" | "article"


def topic_time_window(
    report_date: date, *, mode: str = "weekday_split"
) -> tuple[date | None, date | None]:
    """专题详情子项时间窗；见 ``source_adapter.resolve_kr36_topic_subitem_date_window``。"""
    from . import source_adapter as m

    return m.resolve_kr36_topic_subitem_date_window(report_date, mode=mode)


def step1_parse_topics_listing_and_select_focus(
    topics_listing_html: str,
    *,
    report_date: date,
    focus_keywords: tuple[str, ...],
    focus_limit: int,
) -> tuple[list[RawArticleRef], list[RawArticleRef]]:
    """
    Step 1: 从 /topics/ 列表页 HTML 解析全部专题，再按关键词选出聚焦专题；无法命中时由 select 内部兜底为前 N 条。

    返回 (focus_topic_refs, all_topic_listing_refs)。
    """
    from . import source_adapter as m

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
    """Step 2: 专题详情页 HTML → 时间窗内视频/文章条目（RawArticleRef，metadata 含 topic_item_kind）。"""
    from . import source_adapter as m

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
    """
    Step 3: 从条目拆出待二次请求的「子页」：视频为规范化后的 /video/{id}；文章为 /p/... 等原文 URL。

    说明：专题列表里给到的视频链可能是 m 站，此处统一到 PC 播放页，便于与 Step 4/5 解析规则对齐。
    """
    from . import source_adapter as m

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
    """Step 5a: 从视频子页 HTML 提取可下载的 CDN 直链列表（顺序与 extract_video_media_urls 一致）。

    单条直链常类似 https://videos.36krcdn.com/日期目录/v2_*_video_mp4*；与 KR36_CDN_URL_SHAPE_EXAMPLE 同形。
    """
    from . import source_adapter as m

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
    """Step 5b: 将单条 CDN URL 落盘为文件（与专题下载时相同的 curl/urllib、Referer、Cookie 语义）。

    ``cdn_url`` 形式见 Step 5a / ``KR36_CDN_URL_SHAPE_EXAMPLE``；需带 ``https://``  scheme。
    """
    from . import source_adapter as m
    from . import topic_media

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
    """Step 6a：从本地视频抽单声道 16kHz MP3，供 Step 6b 使用。"""
    from . import topic_media

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
    """
    Step 6b：火山豆包大模型录音极速版，将 ``mp3_path`` 转写为 ``transcript_path`` 与 ``asr_json_path``。

    仅从 ``config``（如 ``runtime.local.json`` → ``sources.kr36``）读 ``volc_speech_*``，不读环境变量。

    返回 ``(x-api-status-code, 正文字符数)``。成功时 code 多为 ``20000000`` 或 ``20000003``（静音）。
    凭证缺失或 HTTP/业务错误时抛 ``RuntimeError``（由调用方记日志）。
    """
    from . import volc_speech as vs

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
