from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from touzifenxi.c114_automation import (
    ConnectivityProbeResult,
    find_step6_path_for_report_date,
    run_c114_daily_brief,
    send_latest_c114_brief_email,
)
from touzifenxi.channels.renderers import RenderedEmail

SHANGHAI = ZoneInfo("Asia/Shanghai")


class C114AutomationRunnerTests(unittest.TestCase):
    def test_runner_prefers_direct_route_and_sends_email_when_step6_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)
            run_dir = self._create_run_dir(project_root, "c114", "202604121010")
            step6_path = run_dir / "c114_step_6_brief_20260412.md"

            def fake_run(command: list[str], cwd: Path, env: dict[str, str], check: bool, capture_output: bool, text: bool):
                self.assertEqual(cwd, project_root)
                self.assertNotIn("HTTP_PROXY", env)
                self.assertNotIn("HTTPS_PROXY", env)
                self.assertNotIn("ALL_PROXY", env)
                step6_path.write_text("# C114 主题简报（2026-04-12）\n\n## 新闻\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            rendered = RenderedEmail(subject="C114 主题简报（2026-04-12）", text="text-body", html="<html>html-body</html>")

            with (
                patch("touzifenxi.c114_automation.probe_connectivity", return_value=ConnectivityProbeResult(ok=True, route="direct")),
                patch("touzifenxi.c114_automation.subprocess.run", side_effect=fake_run) as run_mock,
                patch("touzifenxi.c114_automation.render_c114_brief_email", return_value=rendered) as render_mock,
                patch("touzifenxi.c114_automation.send_email") as send_mock,
            ):
                result = run_c114_daily_brief(project_root=project_root, report_date=date(2026, 4, 12))
                self.assertTrue((run_dir / "c114_step_6_brief_20260412_email.html").exists())
                self.assertTrue((run_dir / "c114_step_6_brief_20260412_email.txt").exists())

        self.assertTrue(result.succeeded)
        self.assertEqual(result.route_used, "direct")
        self.assertEqual(result.step6_path, step6_path)
        self.assertTrue(result.email_sent)
        self.assertIsNone(result.failure_step)
        run_mock.assert_called_once()
        render_mock.assert_called_once()
        send_mock.assert_called_once()

    def test_runner_skips_smtp_when_send_mail_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)
            run_dir = self._create_run_dir(project_root, "c114", "202604121010")
            step6_path = run_dir / "c114_step_6_brief_20260412.md"

            def fake_run(command: list[str], cwd: Path, env: dict[str, str], check: bool, capture_output: bool, text: bool):
                step6_path.write_text("# C114 主题简报（2026-04-12）\n\n## 新闻\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            rendered = RenderedEmail(subject="C114 主题简报（2026-04-12）", text="text-body", html="<html>html-body</html>")

            with (
                patch("touzifenxi.c114_automation.probe_connectivity", return_value=ConnectivityProbeResult(ok=True, route="direct")),
                patch("touzifenxi.c114_automation.subprocess.run", side_effect=fake_run),
                patch("touzifenxi.c114_automation.render_c114_brief_email", return_value=rendered),
                patch("touzifenxi.c114_automation.send_email") as send_mock,
            ):
                result = run_c114_daily_brief(project_root=project_root, report_date=date(2026, 4, 12), send_mail=False)

        self.assertTrue(result.succeeded)
        self.assertFalse(result.email_sent)
        send_mock.assert_not_called()

    def test_runner_falls_back_to_proxy_when_direct_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)
            run_dir = self._create_run_dir(project_root, "c114", "202604121020")
            step6_path = run_dir / "c114_step_6_brief_20260412.md"

            probe_results = [
                ConnectivityProbeResult(ok=False, route="direct", block_point="DNS", error_detail="direct failed"),
                ConnectivityProbeResult(ok=True, route="proxy"),
            ]

            def fake_run(command: list[str], cwd: Path, env: dict[str, str], check: bool, capture_output: bool, text: bool):
                self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7890")
                self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7890")
                self.assertEqual(env["ALL_PROXY"], "socks5://127.0.0.1:7890")
                step6_path.write_text("# C114 主题简报（2026-04-12）\n\n## 新闻\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            rendered = RenderedEmail(subject="C114 主题简报（2026-04-12）", text="text-body", html="<html>html-body</html>")

            with (
                patch("touzifenxi.c114_automation.probe_connectivity", side_effect=probe_results),
                patch("touzifenxi.c114_automation.subprocess.run", side_effect=fake_run),
                patch("touzifenxi.c114_automation.render_c114_brief_email", return_value=rendered),
                patch("touzifenxi.c114_automation.send_email"),
            ):
                result = run_c114_daily_brief(project_root=project_root, report_date=date(2026, 4, 12))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.route_used, "proxy")

    def test_runner_stops_without_email_when_all_network_routes_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)

            probe_results = [
                ConnectivityProbeResult(ok=False, route="direct", block_point="DNS", error_detail="direct failed"),
                ConnectivityProbeResult(ok=False, route="proxy", block_point="PROXY", error_detail="proxy failed"),
            ]

            with (
                patch("touzifenxi.c114_automation.probe_connectivity", side_effect=probe_results),
                patch("touzifenxi.c114_automation.subprocess.run") as run_mock,
                patch("touzifenxi.c114_automation.send_email") as send_mock,
            ):
                result = run_c114_daily_brief(project_root=project_root, report_date=date(2026, 4, 12))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure_step, "network-self-check")
        self.assertIn("direct failed", result.failure_reason or "")
        self.assertIn("proxy failed", result.failure_reason or "")
        self.assertFalse(result.email_sent)
        run_mock.assert_not_called()
        send_mock.assert_not_called()

    def test_runner_reports_step6_failure_without_sending_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)

            def fake_run(command: list[str], cwd: Path, env: dict[str, str], check: bool, capture_output: bool, text: bool):
                run_dir = self._create_run_dir(project_root, "c114", "202604121030")
                log_dir = run_dir / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / "c114_llm_trace_step_6_20260412.jsonl"
                log_path.write_text('{"status":"error"}\n', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                patch("touzifenxi.c114_automation.probe_connectivity", return_value=ConnectivityProbeResult(ok=True, route="direct")),
                patch("touzifenxi.c114_automation.subprocess.run", side_effect=fake_run),
                patch("touzifenxi.c114_automation.send_email") as send_mock,
            ):
                result = run_c114_daily_brief(project_root=project_root, report_date=date(2026, 4, 12))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure_step, "step_6")
        self.assertIsNotNone(result.log_path)
        self.assertEqual(result.log_path.name, "c114_llm_trace_step_6_20260412.jsonl")
        self.assertFalse(result.email_sent)
        send_mock.assert_not_called()

    def _write_env(self, project_root: Path) -> None:
        (project_root / ".env.local").write_text(
            "TOUZIFENXI_EMAIL_FROM=525443496@qq.com\nTOUZIFENXI_EMAIL_PASSWORD=auth-code\n",
            encoding="utf-8",
        )

    def _create_run_dir(self, project_root: Path, source: str, suffix: str) -> Path:
        run_dir = (
            project_root
            / "skills"
            / "websearch"
            / "output"
            / "reports"
            / f"{source}_report"
            / f"{source}_search_{suffix}"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir


class SendLatestC114BriefEmailGateTests(C114AutomationRunnerTests):
    def test_find_step6_prefers_newer_when_duplicate_report_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            older = self._create_run_dir(project_root, "c114", "202604120900")
            newer = self._create_run_dir(project_root, "c114", "202604121200")
            name = "c114_step_6_brief_20260412.md"
            (older / name).write_text("# old\n", encoding="utf-8")
            (newer / name).write_text("# new\n", encoding="utf-8")
            picked = find_step6_path_for_report_date(project_root, date(2026, 4, 12))
            self.assertEqual(picked, newer / name)

    def test_send_fails_when_step6_older_than_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)
            run_dir = self._create_run_dir(project_root, "c114", "202604121010")
            step6 = run_dir / "c114_step_6_brief_20260412.md"
            step6.write_text("# x\n", encoding="utf-8")
            gate = datetime(2099, 1, 1, 10, 0, tzinfo=SHANGHAI)
            result = send_latest_c114_brief_email(
                project_root=project_root,
                recipients=["x@example.com"],
                report_date=date(2026, 4, 12),
                require_modified_not_before=gate,
            )
            self.assertFalse(result.succeeded)
            self.assertIsNotNone(result.error_detail)
            self.assertIn("早于要求", result.error_detail or "")

    def test_send_ok_when_step6_newer_than_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            self._write_env(project_root)
            run_dir = self._create_run_dir(project_root, "c114", "202604121010")
            step6 = run_dir / "c114_step_6_brief_20260412.md"
            step6.write_text("# C114 主题简报（2026-04-12）\n\n## 新闻\n", encoding="utf-8")
            gate = datetime(2000, 1, 1, 0, 0, tzinfo=SHANGHAI)
            rendered = RenderedEmail(subject="subj", text="t", html="<html>h</html>")
            with patch("touzifenxi.c114_automation.render_c114_brief_email", return_value=rendered), patch(
                "touzifenxi.c114_automation.send_email"
            ) as send_mock:
                result = send_latest_c114_brief_email(
                    project_root=project_root,
                    recipients=["x@example.com"],
                    report_date=date(2026, 4, 12),
                    require_modified_not_before=gate,
                )
            self.assertTrue(result.succeeded)
            send_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
