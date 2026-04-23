"""专题 Step 1.5 与 source_adapter 解析结果对齐。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kr36 import topic_focus_step15 as t15
from utils.tools.content_models import RawArticleRef


def test_step1_parses_and_selects() -> None:
    html = """
    <a href="/topics/1">other</a>
    <a href="https://36kr.com/topics/3770543132934659">本周有大事丨某标题</a>
    """
    focus, all_refs = t15.step1_parse_topics_listing_and_select_focus(
        html,
        report_date=date(2026, 4, 22),
        focus_keywords=("本周有大事", "36氪编辑精选"),
        focus_limit=2,
    )
    assert len(focus) >= 1
    assert any("本周有大事" in (r.title or "") for r in focus)
    assert len(all_refs) >= 1


def test_step2_empty_detail() -> None:
    items = t15.step2_parse_topic_items_from_detail_html(
        "<html></html>",
        report_date=date(2026, 4, 22),
        topic_title="t",
        listing_url="https://36kr.com/topics/1",
        window_start=None,
        window_end=None,
    )
    assert items == []


def test_step3_subpage_uses_url_not_only_metadata() -> None:
    """metadata 误标 article 时，只要 url 含 /video/ 仍按视频子页处理。"""
    ref = RawArticleRef(
        source_site="36kr",
        article_id="3766585318896386",
        title="t",
        url="https://m.36kr.com/video/3766585318896386",
        channel="专题",
        source_bucket="专题",
        metadata={"topic_item_kind": "article", "topic_title": "W"},
    )
    sub = t15.step3_list_subpage_targets([ref])
    assert len(sub) == 1
    assert sub[0].kind == "video"


def test_step3_subpage_video_normalizes() -> None:
    ref = RawArticleRef(
        source_site="36kr",
        article_id="3766585318896386",
        title="t",
        url="https://m.36kr.com/video/3766585318896386",
        channel="专题",
        source_bucket="专题",
        metadata={"topic_item_kind": "video", "topic_title": "W"},
    )
    sub = t15.step3_list_subpage_targets([ref])
    assert len(sub) == 1
    assert sub[0].subpage_url == "https://36kr.com/video/3766585318896386"
    assert sub[0].kind == "video"


def test_kr36_cdn_url_shape_example_documents_v2_stem() -> None:
    assert "20260414" in t15.KR36_CDN_URL_SHAPE_EXAMPLE
    assert "v2_1776165984015_video_mp4_v11" in t15.KR36_CDN_URL_SHAPE_EXAMPLE


def test_step5_cdn_list_from_subpage() -> None:
    html = """
    <script>window.initialState={};</script>
    <video src="https://videos.36krcdn.com/2026/x.mp4" class="video"></video>
    """
    urls = t15.step5_list_video_cdn_urls_from_subpage_html(html)
    assert "https://videos.36krcdn.com/2026/x.mp4" in urls


def test_step6b_requires_volc_in_config() -> None:
    with pytest.raises(RuntimeError, match="volc speech"):
        t15.step6b_transcribe_mp3_to_transcript_files(
            Path("nope.mp3"),
            transcript_path=Path("t.txt"),
            asr_json_path=Path("j.json"),
            video_path_for_json=Path("v.mp4"),
            config={},
        )


def test_topic_fulltext_role_constant() -> None:
    assert t15.TOPIC_ITEM_FULLTEXT_ROLE_ASR == "topic_video_asr_transcript"
