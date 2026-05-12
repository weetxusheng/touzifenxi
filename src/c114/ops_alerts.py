"""C114 运维告警邮件：发送失败不得阻塞主流程。"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_OPS_TO = "944532395@qq.com"


def default_c114_ops_alert_recipients() -> list[str]:
    raw = (os.environ.get("C114_OPS_ALERT_TO") or _DEFAULT_OPS_TO).strip()
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def send_c114_ops_email_safe(
    *,
    subject: str,
    body_text: str,
    recipients: list[str] | None = None,
) -> bool:
    """发送运维邮件；任意异常只记日志，返回是否成功。"""
    to = recipients if recipients is not None else default_c114_ops_alert_recipients()
    if not to:
        logger.warning("C114 ops alert skipped: no recipients.")
        return False
    try:
        from utils.tools.output.email import send_email

        send_email(
            recipient_emails=to,
            subject=subject,
            body_text=body_text,
            body_html=None,
        )
    except Exception as exc:  # noqa: BLE001 — alert path must not raise
        logger.exception("C114 ops alert email failed: %s", exc)
        return False
    return True


def send_c114_pipeline_failure_alert(*, project_root: Path, exc: BaseException, soft_notes: list[str]) -> None:
    """流水线异常退出时发一封告警（与简报邮件独立）。"""
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    lines = [
        f"project_root: {project_root}",
        f"exception: {type(exc).__name__}: {exc}",
        "",
        "traceback:",
        tb_text.rstrip(),
    ]
    if soft_notes:
        lines.extend(["", "step 5 软跳过 / 恢复类记录（本 run 内，在失败前）:"])
        lines.extend(f"- {note}" for note in soft_notes)
    body = "\n".join(lines)
    send_c114_ops_email_safe(
        subject="[C114告警] 日报流水线失败",
        body_text=body,
    )


def send_c114_pipeline_soft_digest(*, project_root: Path, messages: list[str]) -> None:
    """流水线成功结束但存在软告警时发一封摘要。"""
    if not messages:
        return
    body = "\n".join(
        [
            f"project_root: {project_root}",
            "流水线已正常结束，下列为运行过程中的软告警（如 Step 5 跳过未对齐条目）：",
            "",
            *[f"- {m}" for m in messages],
        ]
    )
    send_c114_ops_email_safe(
        subject="[C114提示] 日报流水线软告警摘要（已成功）",
        body_text=body,
    )
