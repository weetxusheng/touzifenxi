from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[3] / "skills" / "file-comparison"


def test_file_comparison_page_uses_single_flow_layout():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "index.html").read_text(encoding="utf-8")

    assert "workflow-grid" in index_html
    assert "workflow-grid" in app_js
    assert "upload-body-grid" in index_html
    assert "upload-body-grid" in app_js
    assert "task-progress-summary" in app_js
    assert "Statistic" not in app_js


def test_file_comparison_page_uses_local_favicon():
    index_html = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    favicon = SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "favicon.svg"

    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />' in index_html
    assert favicon.exists()


def test_file_comparison_page_keeps_light_blue_background_while_scrolling():
    index_html = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "index.html").read_text(encoding="utf-8")

    assert "--page-bg-solid" in index_html
    assert "html {" in index_html
    assert "background: var(--page-bg-solid);" in index_html
    assert "background-color: var(--page-bg-solid);" in index_html


def test_file_comparison_page_uses_task_summary_and_docx_primary_download():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert "文件对进度" in app_js
    assert "活跃模型" in app_js
    assert "下载 DOCX" in app_js
    assert "重新生成 DOCX" in app_js
    assert "下载 DOC（兼容版）" not in app_js
    assert "DOC 为转换文件，如样式异常请以 DOCX 为准" not in app_js
    assert "已确认" not in app_js
    assert "poll_interval_seconds" in app_js


def test_file_comparison_task_table_keeps_status_separate_and_merges_file_names():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'title: "文件"' in app_js
    assert 'title: "任务状态"' in app_js
    assert 'title: "文件对"' not in app_js
    assert 'title: "模型"' not in app_js
    assert "修改前文件：" in app_js
    assert "修改后文件：" in app_js


def test_file_comparison_page_rerender_button_allows_failed_pair_with_reusable_batches():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert "allPlannedBatchesSucceeded" in app_js
    assert "record.completed_batch_count" in app_js
    assert "record.planned_batch_count" in app_js
    assert 'record.status === "completed" || allPlannedBatchesSucceeded' in app_js


def test_file_comparison_task_table_avoids_horizontal_scroll():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'title: "文件"' in app_js
    assert 'width: 320' in app_js
    assert 'title: "结果"' in app_js
    assert 'width: 160' in app_js
    assert 'scroll=${{ x: 1370 }}' not in app_js
    assert 'tableLayout="fixed"' in app_js


def test_file_comparison_pair_preview_avoids_horizontal_scroll():
    app_js = (SKILL_ROOT / "src" / "file_comparison" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'title="配对预览"' in app_js
    assert 'scroll=${{ x: 990 }}' not in app_js
    assert 'title: "主键"' in app_js
    assert 'width: 120' in app_js
    assert 'direction="vertical"' in app_js
    assert 'tableLayout="fixed"' in app_js
