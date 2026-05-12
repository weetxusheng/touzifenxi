"""36Kr 运维告警邮件：失败通知不应阻塞主流程异常。"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_OPS_TO = "944532395@qq.com"


def default_kr36_ops_alert_recipients() -> list[str]:
    raw = (os.environ.get("KR36_OPS_ALERT_TO") or _DEFAULT_OPS_TO).strip()
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def send_kr36_ops_email_safe(
    *,
    subject: str,
    body_text: str,
    recipients: list[str] | None = None,
) -> bool:
    """发送运维邮件；任意异常只记日志，返回是否成功。"""
    to = recipients if recipients is not None else default_kr36_ops_alert_recipients()
    if not to:
        logger.warning("KR36 ops alert skipped: no recipients.")
        return False
    try:
        from utils.tools.output.email import send_email

        send_email(
            recipient_emails=to,
            subject=subject,
            body_text=body_text,
            body_html=None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("KR36 ops alert email failed: %s", exc)
        return False
    return True


def send_kr36_pipeline_failure_alert(
    *,
    project_root: Path,
    command: str,
    report_date: str,
    run_dir: Path | None,
    exc: BaseException,
) -> None:
    """36Kr 流水线异常告警（不中断原异常链）。"""
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    lines = [
        f"project_root: {project_root}",
        f"command: {command}",
        f"report_date: {report_date}",
        f"run_dir: {run_dir or ''}",
        f"exception: {type(exc).__name__}: {exc}",
        "",
        "traceback:",
        tb_text.rstrip(),
    ]
    send_kr36_ops_email_safe(
        subject=f"[36Kr告警] 日报流水线失败（{report_date}）",
        body_text="\n".join(lines),
    )

