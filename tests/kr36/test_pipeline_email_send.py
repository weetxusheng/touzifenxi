import os
from datetime import date, datetime, timedelta, timezone

from kr36.email_send import send_latest_kr36_brief_email
from utils.tools.output.email import RenderedEmail


def _make_step4_brief(project_root, yyyymmdd: str, run_dir_name: str = "kr36_search_202604220900"):
    run_dir = project_root / "output" / "reports" / "kr36_report" / run_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    step4_md = run_dir / f"kr36_step4_brief_{yyyymmdd}.md"
    step4_md.write_text("# 36Kr 主题简报（2026-04-22）\n\n测试内容。", encoding="utf-8")
    return step4_md


def test_send_latest_kr36_brief_email_succeeds_with_step4_file(monkeypatch, tmp_path) -> None:
    step4_md = _make_step4_brief(tmp_path, "20260422")
    sent: dict[str, object] = {}

    def fake_render(markdown_text: str, step6_path=None):
        assert "36Kr" in markdown_text
        assert step6_path == step4_md
        return RenderedEmail(subject="test-subject", text="test-text", html="<p>test</p>")

    def fake_send_email(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr("kr36.email_send.render_kr36_brief_email", fake_render)
    monkeypatch.setattr("kr36.email_send.send_email", fake_send_email)

    result = send_latest_kr36_brief_email(
        project_root=tmp_path,
        recipients=["a@example.com", "b@example.com"],
        report_date=date(2026, 4, 22),
    )

    assert result.succeeded is True
    assert result.step6_path.endswith("kr36_step4_brief_20260422.md")
    assert sent["recipient_emails"] == ["a@example.com", "b@example.com"]
    assert sent["subject"] == "test-subject"


def test_send_latest_kr36_brief_email_fails_when_no_file(tmp_path) -> None:
    result = send_latest_kr36_brief_email(
        project_root=tmp_path,
        recipients=["a@example.com"],
        report_date=date(2026, 4, 22),
    )

    assert result.succeeded is False
    assert "未找到" in result.error_detail


def test_send_latest_kr36_brief_email_respects_modified_time_gate(monkeypatch, tmp_path) -> None:
    step4_md = _make_step4_brief(tmp_path, "20260422")
    tz = timezone(timedelta(hours=8))
    old_dt = datetime(2026, 4, 22, 9, 0, tzinfo=tz)
    os.utime(step4_md, (old_dt.timestamp(), old_dt.timestamp()))

    def should_not_send_email(**kwargs):
        raise AssertionError("send_email should not be called when gate check fails")

    monkeypatch.setattr("kr36.email_send.send_email", should_not_send_email)

    result = send_latest_kr36_brief_email(
        project_root=tmp_path,
        recipients=["a@example.com"],
        report_date=date(2026, 4, 22),
        require_modified_not_before=datetime(2026, 4, 22, 10, 0, tzinfo=tz),
    )

    assert result.succeeded is False
    assert "早于门控时间" in result.error_detail
