from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .channels.email import load_email_channel_config, send_email
from .channels.renderers import render_c114_brief_email

DEFAULT_PROXY_HTTP = "http://127.0.0.1:7890"
DEFAULT_PROXY_ALL = "socks5://127.0.0.1:7890"
DEFAULT_C114_RECIPIENT = "zx944532395@sina.com"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
NETWORK_TEST_URL = "https://www.c114.com.cn/"


def resolve_project_python(project_root: Path) -> str:
    """Prefer project .venv interpreter; fall back to the current interpreter."""
    if sys.platform == "win32":
        candidate = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = project_root / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


@dataclass(frozen=True)
class ConnectivityProbeResult:
    ok: bool
    route: str
    block_point: str | None = None
    error_detail: str | None = None
    status_code: int | None = None


@dataclass(frozen=True)
class C114AutomationResult:
    report_date: date
    route_used: str | None
    succeeded: bool
    run_dir: Path | None
    step6_path: Path | None
    email_sent: bool
    failure_step: str | None = None
    failure_reason: str | None = None
    log_path: Path | None = None


@dataclass(frozen=True)
class C114SendLatestBriefResult:
    succeeded: bool
    step6_path: Path | None = None
    error_detail: str | None = None


def shanghai_today() -> date:
    return datetime.now(SHANGHAI_TZ).date()


def shanghai_yesterday() -> date:
    return shanghai_today() - timedelta(days=1)


def _merge_dotenv_into(env: dict[str, str], env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        env[key] = value.strip()


def load_env_file(project_root: Path) -> dict[str, str]:
    """合并 os.environ、项目根目录 `.env` 与 `.env.local`（后者覆盖前者）。"""
    env = dict(os.environ)
    for name in (".env", ".env.local"):
        _merge_dotenv_into(env, project_root / name)
    return env


def probe_connectivity(
    *,
    route: str,
    url: str = NETWORK_TEST_URL,
    timeout_seconds: float = 20.0,
) -> ConnectivityProbeResult:
    if route == "direct":
        proxy_handler = urllib.request.ProxyHandler({})
    elif route == "proxy":
        proxy_handler = urllib.request.ProxyHandler(
            {
                "http": DEFAULT_PROXY_HTTP,
                "https": DEFAULT_PROXY_HTTP,
                "all": DEFAULT_PROXY_ALL,
            }
        )
    else:
        raise ValueError(f"Unsupported route: {route}")

    opener = urllib.request.build_opener(proxy_handler)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            if status_code == 200:
                return ConnectivityProbeResult(ok=True, route=route, status_code=200)
            return ConnectivityProbeResult(
                ok=False,
                route=route,
                block_point="HTTP",
                error_detail=f"Unexpected status code: {status_code}",
                status_code=status_code,
            )
    except urllib.error.HTTPError as exc:
        return ConnectivityProbeResult(
            ok=False,
            route=route,
            block_point="HTTP",
            error_detail=f"{exc.code} {exc.reason}",
            status_code=exc.code,
        )
    except urllib.error.URLError as exc:
        detail = str(exc.reason)
        lowered = detail.lower()
        if "certificate" in lowered or "ssl" in lowered:
            block_point = "SSL"
        elif "proxy" in lowered or "127.0.0.1:7890" in lowered:
            block_point = "PROXY"
        elif "nameresolutionerror" in lowered or "nodename nor servname" in lowered or "resolve" in lowered:
            block_point = "DNS"
        else:
            block_point = "NETWORK"
        return ConnectivityProbeResult(ok=False, route=route, block_point=block_point, error_detail=detail)


def choose_network_route(base_env: dict[str, str]) -> tuple[str | None, dict[str, str], list[ConnectivityProbeResult]]:
    diagnostics: list[ConnectivityProbeResult] = []

    direct_result = probe_connectivity(route="direct")
    diagnostics.append(direct_result)
    if direct_result.ok:
        return "direct", build_route_env(base_env, "direct"), diagnostics

    proxy_result = probe_connectivity(route="proxy")
    diagnostics.append(proxy_result)
    if proxy_result.ok:
        return "proxy", build_route_env(base_env, "proxy"), diagnostics

    return None, base_env.copy(), diagnostics


def build_route_env(base_env: dict[str, str], route: str) -> dict[str, str]:
    env = dict(base_env)
    if route == "direct":
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        return env
    if route == "proxy":
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)
        env["HTTP_PROXY"] = DEFAULT_PROXY_HTTP
        env["HTTPS_PROXY"] = DEFAULT_PROXY_HTTP
        env["ALL_PROXY"] = DEFAULT_PROXY_ALL
        return env
    raise ValueError(f"Unsupported route: {route}")


def run_c114_daily_brief(
    *,
    project_root: Path,
    report_date: date | None = None,
    recipients: Iterable[str] = (DEFAULT_C114_RECIPIENT,),
    send_mail: bool = True,
) -> C114AutomationResult:
    effective_date = report_date or shanghai_yesterday()
    base_env = load_env_file(project_root)
    route, run_env, diagnostics = choose_network_route(base_env)
    if route is None:
        failure_reason = "; ".join(
            f"{item.route}:{item.block_point or 'UNKNOWN'}:{item.error_detail or 'probe failed'}" for item in diagnostics
        )
        return C114AutomationResult(
            report_date=effective_date,
            route_used=None,
            succeeded=False,
            run_dir=None,
            step6_path=None,
            email_sent=False,
            failure_step="network-self-check",
            failure_reason=failure_reason,
            log_path=None,
        )

    run_started_at = datetime.now()
    command = [
        resolve_project_python(project_root),
        "skills/websearch/scripts/websearch.py",
        "run",
        "--source",
        "c114",
        "--date",
        effective_date.isoformat(),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        env=run_env,
        check=False,
        capture_output=False,
        text=True,
    )
    # When capture_output=False, stdout/stderr may be None; child process output is already streamed live.
    stdout_text = getattr(completed, "stdout", None) or ""
    stderr_text = getattr(completed, "stderr", None) or ""
    run_dir = find_latest_run_directory(project_root=project_root, report_date=effective_date, started_after=run_started_at)
    step6_path = run_dir / step_6_file_name(effective_date) if run_dir else None
    if not step6_path or not step6_path.exists():
        failure_step = infer_failure_step(run_dir=run_dir, report_date=effective_date)
        failure_reason = stderr_text.strip() or stdout_text.strip() or "标准 run 未产出 step 6 文件。"
        return C114AutomationResult(
            report_date=effective_date,
            route_used=route,
            succeeded=False,
            run_dir=run_dir,
            step6_path=None,
            email_sent=False,
            failure_step=failure_step,
            failure_reason=failure_reason,
            log_path=find_relevant_log_path(run_dir=run_dir, report_date=effective_date, failure_step=failure_step),
        )

    markdown_text = step6_path.read_text(encoding="utf-8")
    rendered = render_c114_brief_email(markdown_text)
    html_path = step6_path.with_name(f"{step6_path.stem}_email.html")
    text_path = step6_path.with_name(f"{step6_path.stem}_email.txt")
    html_path.write_text(rendered.html, encoding="utf-8")
    text_path.write_text(rendered.text, encoding="utf-8")
    if send_mail:
        send_email(
            recipient_emails=list(recipients),
            subject=rendered.subject,
            body_text=rendered.text,
            body_html=rendered.html,
            config=load_email_channel_config(run_env),
        )
    return C114AutomationResult(
        report_date=effective_date,
        route_used=route,
        succeeded=True,
        run_dir=run_dir,
        step6_path=step6_path,
        email_sent=bool(send_mail),
    )


def find_latest_c114_step6_path(project_root: Path) -> Path | None:
    """按文件 mtime 选取最近一次生成的 Step6 简报 Markdown。"""
    newest: Path | None = None
    newest_mtime = -1.0
    for report_root in candidate_c114_report_roots(project_root):
        if not report_root.is_dir():
            continue
        for run_dir in report_root.iterdir():
            if not run_dir.is_dir() or not run_dir.name.startswith("c114_search_"):
                continue
            for path in run_dir.glob("c114_step_6_brief_*.md"):
                mtime = path.stat().st_mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = path
    return newest


def send_latest_c114_brief_email(
    *,
    project_root: Path,
    recipients: Iterable[str],
) -> C114SendLatestBriefResult:
    """对最近一次 Step6 简报渲染并发送邮件，不重新跑流水线。"""
    step6_path = find_latest_c114_step6_path(project_root)
    if step6_path is None:
        return C114SendLatestBriefResult(succeeded=False, error_detail="未找到任何 c114_step_6_brief_*.md。")
    env = load_env_file(project_root)
    try:
        config = load_email_channel_config(env)
    except RuntimeError as exc:
        return C114SendLatestBriefResult(succeeded=False, error_detail=str(exc))
    markdown_text = step6_path.read_text(encoding="utf-8")
    rendered = render_c114_brief_email(markdown_text)
    html_path = step6_path.with_name(f"{step6_path.stem}_email.html")
    text_path = step6_path.with_name(f"{step6_path.stem}_email.txt")
    html_path.write_text(rendered.html, encoding="utf-8")
    text_path.write_text(rendered.text, encoding="utf-8")
    send_email(
        recipient_emails=list(recipients),
        subject=rendered.subject,
        body_text=rendered.text,
        body_html=rendered.html,
        config=config,
    )
    return C114SendLatestBriefResult(succeeded=True, step6_path=step6_path)


def step_6_file_name(report_date: date) -> str:
    return f"c114_step_6_brief_{report_date.strftime('%Y%m%d')}.md"


def candidate_c114_report_roots(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "skills" / "websearch" / "output" / "reports" / "c114_report",
        project_root / "reports" / "c114_report",
    )


def find_latest_run_directory(*, project_root: Path, report_date: date, started_after: datetime) -> Path | None:
    target_files = {
        f"c114_step_1_analysis_{report_date.strftime('%Y%m%d')}.csv",
        f"c114_step_2_search_checklist_{report_date.strftime('%Y%m%d')}.yaml",
        step_6_file_name(report_date),
    }
    candidates: list[Path] = []
    fallback_candidates: list[Path] = []
    threshold = started_after.timestamp() - 2.0
    for report_root in candidate_c114_report_roots(project_root):
        if not report_root.exists():
            continue
        for entry in report_root.iterdir():
            if not entry.is_dir() or not entry.name.startswith("c114_search_"):
                continue
            if entry.stat().st_mtime < threshold:
                continue
            fallback_candidates.append(entry)
            if any((entry / file_name).exists() for file_name in target_files):
                candidates.append(entry)
    if candidates:
        return sorted(candidates, key=lambda item: (item.stat().st_mtime, item.name))[-1]
    if fallback_candidates:
        return sorted(fallback_candidates, key=lambda item: (item.stat().st_mtime, item.name))[-1]
    return None


def infer_failure_step(*, run_dir: Path | None, report_date: date) -> str:
    if run_dir is None:
        return "run"
    sequence = (
        ("step_6", step_6_file_name(report_date)),
        ("step_5", f"c114_step_5_content_analysis_{report_date.strftime('%Y%m%d')}.yaml"),
        ("step_4", f"c114_step_4_content_{report_date.strftime('%Y%m%d')}.yaml"),
        ("step_3", f"c114_step_3_search_results_{report_date.strftime('%Y%m%d')}.yaml"),
        ("step_2", f"c114_step_2_search_checklist_{report_date.strftime('%Y%m%d')}.yaml"),
        ("step_1_5", f"c114_step_1_5_topic_grouping_{report_date.strftime('%Y%m%d')}.yaml"),
        ("step_1", f"c114_step_1_analysis_{report_date.strftime('%Y%m%d')}.csv"),
    )
    for step_name, file_name in sequence:
        if (run_dir / file_name).exists():
            continue
        return step_name
    return "step_6"


def find_relevant_log_path(*, run_dir: Path | None, report_date: date, failure_step: str | None) -> Path | None:
    if run_dir is None:
        return None
    logs_dir = run_dir / "logs"
    if not logs_dir.exists():
        return None
    normalized = (failure_step or "run").replace("step_", "step_")
    step_log = logs_dir / f"c114_llm_trace_{normalized}_{report_date.strftime('%Y%m%d')}.jsonl"
    if step_log.exists():
        return step_log
    if failure_step == "step_3":
        search_log = logs_dir / f"c114_search_trace_step_3_{report_date.strftime('%Y%m%d')}.jsonl"
        if search_log.exists():
            return search_log
    candidates = sorted(logs_dir.glob("*.jsonl"))
    return candidates[-1] if candidates else None


__all__ = [
    "C114AutomationResult",
    "C114SendLatestBriefResult",
    "ConnectivityProbeResult",
    "DEFAULT_C114_RECIPIENT",
    "build_route_env",
    "choose_network_route",
    "find_latest_c114_step6_path",
    "load_env_file",
    "probe_connectivity",
    "run_c114_daily_brief",
    "send_latest_c114_brief_email",
    "shanghai_today",
]
