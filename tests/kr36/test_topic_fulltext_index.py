from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from kr36.topic_fulltext_index import (
    build_topic_fulltext_doc,
    load_content_by_article_id_from_topic_fulltext,
    topic_fulltext_json_name,
    write_kr36_topic_fulltext_json,
)


def test_topic_fulltext_json_name() -> None:
    assert topic_fulltext_json_name(date(2026, 4, 22)) == "kr36_topic_fulltext_20260422.json"


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    from kr36.source_adapter import _safe_filename

    rd = tmp_path / "r1"
    rd.mkdir()
    tdir = rd / "kr36_topic_downloads" / "T" / "2026-04-13" / "videos"
    tdir.mkdir(parents=True)
    art = {
        "article_id": "aid1",
        "title": "T",
        "url": "https://36kr.com/video/1",
        "metadata": {
            "topic_item_kind": "video",
            "topic_title": "T",
            "topic_item_date": "2026-04-13",
        },
    }
    base = _safe_filename(f"{art['title']}_{art['article_id']}")
    assert base == "T_aid1"
    (tdir / f"{base}.transcript.txt").write_text("full text ok", encoding="utf-8")

    p = write_kr36_topic_fulltext_json([art], run_dir=rd, report_date=date(2026, 4, 22), topic_download_dir=rd)
    assert p.is_file()
    m = load_content_by_article_id_from_topic_fulltext(rd, date(2026, 4, 22))
    assert m.get("aid1") == "full text ok"

    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc.get("schema") == "kr36_topic_fulltext"
    first = (doc.get("items") or [None])[0]
    assert isinstance(first, dict)
    assert first.get("content_text") == "full text ok"


def test_build_doc_skips_non_topic_items(tmp_path: Path) -> None:
    doc = build_topic_fulltext_doc(
        [{"article_id": "1", "title": "x", "url": "https://36kr.com/p/1", "metadata": {}}],
        run_dir=tmp_path,
        report_date=date(2026, 4, 22),
    )
    assert doc.get("items") == []
    assert doc.get("items_note")
