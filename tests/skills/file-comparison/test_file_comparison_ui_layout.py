from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[3] / "skills" / "file-comparison"


def test_file_comparison_page_uses_single_flow_layout():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "index.html").read_text(encoding="utf-8")

    assert "workflow-grid" in index_html
    assert "workflow-grid" in app_js
    assert "task-progress-summary" in app_js
    assert "Statistic" not in app_js
