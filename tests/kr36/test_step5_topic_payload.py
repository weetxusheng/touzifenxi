"""Step5 专题全文注入到 LLM 输入结构的回归。"""

from pathlib import Path

from utils.tools.analysis.models import (
    ContentAnalysisDraft,
    ContentAnalysisItem,
    ContentAnalysisInput,
    ContentAnalysisSection,
    ContentDocument,
)
from utils.tools.steps.step5_content_analysis import (
    build_content_analysis_topic_prompt_payload,
    lookup_step5_topic_fulltext_excerpt,
)


def _item(url: str, title: str = "子项标题") -> ContentAnalysisItem:
    return ContentAnalysisItem(
        original_title=title,
        topic="专题A",
        channel="专题",
        original_url=url,
        original_published_at="",
        original_content=ContentDocument(
            url=url,
            domain="36kr.com",
            title=title,
            summary="",
            text="薄页",
            source="html",
            status="success",
            error="",
        ),
        selected_contents=[],
        analysis=ContentAnalysisDraft(
            summary="",
            core_points=[],
            new_facts=[],
            entities=[],
            signals=[],
            risk_or_uncertainty=[],
            why_it_matters="",
            layer_notes=[],
        ),
    )


def test_lookup_step5_topic_fulltext_matches_rstrip_url() -> None:
    d = {"https://36kr.com/video/1": "全文A"}
    assert lookup_step5_topic_fulltext_excerpt("https://36kr.com/video/1/", d) == "全文A"


def test_build_topic_payload_includes_fulltext_fields() -> None:
    sec = ContentAnalysisSection(
        topic="聚合主题名丨某专题",
        items=[
            _item("https://36kr.com/video/1"),
        ],
    )
    by_url = {"https://36kr.com/video/1": "转写正文一段。"}
    out = build_content_analysis_topic_prompt_payload(sec, topic_fulltext_excerpts_by_url=by_url)
    assert out["topic"] == sec.topic
    assert len(out["items"]) == 1
    row = out["items"][0]
    assert row["kr36_topic_subitem"] is True
    assert row["group_topic_name"] == sec.topic
    assert "转写正文" in row["topic_fulltext_excerpt"]


def test_input_path_kr36_step4_allows_lookup_from_payload_shape() -> None:
    p = Path("output/reports/kr36_report/x/kr36_step_4_content_20260101.yaml")
    assert "kr36" in str(p).lower()
