"""步骤2正文 YAML → 阅读原文汇编。"""

from pathlib import Path

from kr36.content_fetch_document import (
    render_content_fetch_html,
    write_content_fetch_reading_docs,
)
from utils.tools.analysis.models import (
    ContentAnalysisDraft,
    ContentAnalysisInput,
    ContentAnalysisItem,
    ContentAnalysisSection,
    ContentDocument,
)

_MIN_STEP4_YAML = """report_date: '2026-04-23'
input_path: 'dummy.csv'
generated_at: '2026-04-23T00:00:00'
categories:
  - topic: '测试主题'
    items:
      - original_title: '样例标题'
        topic: '测试主题'
        channel: '专题'
        original_url: 'https://36kr.com/p/1'
        original_published_at: '2026-04-23'
        original_content:
          url: 'https://36kr.com/p/1'
          domain: '36kr.com'
          content_title: ''
          content_summary: '摘要一句'
          content_text: '正文段落'
          content_source: 'html_fallback'
          fetch_status: 'success'
          fetch_error: ''
        selected_contents:
"""


def test_render_content_fetch_html_includes_sections() -> None:
    doc = ContentDocument(
        url="https://ex.com/a",
        domain="ex.com",
        title="页内标题",
        summary="摘要",
        text="正文",
        source="html_fallback",
        status="success",
        error="",
    )
    item = ContentAnalysisItem(
        original_title="列表标题",
        topic="主题",
        channel="资讯",
        original_url="https://ex.com/a",
        original_published_at="",
        original_content=doc,
        selected_contents=[],
        analysis=ContentAnalysisDraft("", [], [], [], [], [], "", []),
    )
    payload = ContentAnalysisInput(
        report_date="2026-04-23",
        input_path=Path("in.yaml"),
        generated_at="g",
        categories=[ContentAnalysisSection(topic="聚合A", items=[item])],
    )
    html = render_content_fetch_html(payload)
    assert "聚合A" in html
    assert "列表标题" in html
    assert "正文" in html
    assert "https://ex.com/a" in html


def test_write_content_fetch_reading_docs(tmp_path: Path) -> None:
    y = tmp_path / "kr36_step_4_content_20260423.yaml"
    y.write_text(_MIN_STEP4_YAML, encoding="utf-8")
    html_p, md_p = write_content_fetch_reading_docs(y)
    assert html_p.is_file() and md_p.is_file()
    assert html_p.name.endswith("_reading.html")
    assert md_p.name.endswith("_reading.md")
    assert "样例标题" in html_p.read_text(encoding="utf-8")
    assert "正文段落" in md_p.read_text(encoding="utf-8")
