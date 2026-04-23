"""kr36-videos-from-json: parse hot_topics json rows into Refs and filter videos."""

from __future__ import annotations

import json
from pathlib import Path
from kr36.cli import _raw_article_ref_from_hot_topics_json_row, run_kr36_videos_from_hot_topics_json
from kr36.source_adapter import effective_topic_item_kind_for_download


def test_raw_ref_roundtrip_finds_video() -> None:
    row = {
        "source_site": "36kr",
        "article_id": "36kr:topic:video:1",
        "title": "t",
        "url": "https://36kr.com/video/99",
        "metadata": {"topic_item_kind": "video", "topic_title": "T", "topic_item_date": "2026-04-15"},
    }
    ref = _raw_article_ref_from_hot_topics_json_row(row)
    assert effective_topic_item_kind_for_download(ref) == "video"


def test_json_missing_report_date_errors(tmp_path: Path) -> None:
    j = tmp_path / "a.json"
    j.write_text(json.dumps({"articles": []}), encoding="utf-8")
    assert run_kr36_videos_from_hot_topics_json(j) == 2


def test_json_empty_articles_is_error(tmp_path: Path) -> None:
    j = tmp_path / "b.json"
    j.write_text(
        json.dumps({"report_date": "2026-04-22", "articles": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert run_kr36_videos_from_hot_topics_json(j) == 2
