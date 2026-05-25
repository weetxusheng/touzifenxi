from pathlib import Path

from feedcore.models import RssSourceRecord


def test_run_context_writes_checkpoints_under_output_run_id(tmp_path: Path):
    from feedcore.workflow.run_context import RunContext

    ctx = RunContext.create(output_dir=tmp_path, run_id="news_brief_test")

    source = RssSourceRecord(
        url="https://example.com/rss",
        label="Example",
        default_category="科技",
        selected_reason="unit test",
    )
    checkpoint = ctx.write_json("step1_rss_sources.json", [source.to_dict()])

    assert checkpoint == tmp_path / "news_brief_test" / "step1_rss_sources.json"
    assert checkpoint.exists()
    assert ctx.run_dir == tmp_path / "news_brief_test"
    assert (ctx.run_dir / "checkpoints").is_dir()
    assert (ctx.run_dir / "logs").is_dir()
    assert not (ctx.run_dir / "documents").exists()
    assert "output/runs" not in checkpoint.as_posix()


def test_run_context_writes_execution_log(tmp_path: Path):
    from feedcore.workflow.run_context import RunContext

    ctx = RunContext.create(output_dir=tmp_path, run_id="news_brief_test")
    ctx.log_step("step1_rss_sources", input_count=2, output_count=1, skipped_count=1, message="selected sources")
    log_path = ctx.write_execution_log()

    text = log_path.read_text(encoding="utf-8")
    assert "# Execution Log" in text
    assert "step1_rss_sources" in text
    assert "selected sources" in text
    assert "input=2" in text
    workflow_log = ctx.run_dir / "logs" / "workflow.log"
    assert workflow_log.exists()
    assert "step1_rss_sources" in workflow_log.read_text(encoding="utf-8")
