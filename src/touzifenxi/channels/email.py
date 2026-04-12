"""项目级 SMTP 邮件发送渠道。"""

from __future__ import annotations

import mimetypes
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

QQ_SMTP_HOST = "smtp.qq.com"
QQ_SMTP_PORT = 465


@dataclass(frozen=True)
class EmailChannelConfig:
    """表示 SMTP 邮件渠道所需的连接配置。"""

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    sender_email: str
    use_ssl: bool = True
    timeout_seconds: float = 30.0


def load_email_channel_config() -> EmailChannelConfig:
    """从环境变量加载邮件渠道配置，并对 QQ 邮箱提供默认值。"""

    sender_email = os.getenv("TOUZIFENXI_EMAIL_FROM", "").strip()
    username = os.getenv("TOUZIFENXI_EMAIL_USERNAME", "").strip() or sender_email
    password = os.getenv("TOUZIFENXI_EMAIL_PASSWORD", "").strip()
    smtp_host = os.getenv("TOUZIFENXI_EMAIL_SMTP_HOST", "").strip()
    smtp_port_value = os.getenv("TOUZIFENXI_EMAIL_SMTP_PORT", "").strip()
    use_ssl_value = os.getenv("TOUZIFENXI_EMAIL_USE_SSL", "").strip().lower()
    timeout_value = os.getenv("TOUZIFENXI_EMAIL_TIMEOUT_SECONDS", "").strip()

    if not sender_email:
        raise RuntimeError("未配置 TOUZIFENXI_EMAIL_FROM，无法发送邮件。")
    if not password:
        raise RuntimeError("未配置 TOUZIFENXI_EMAIL_PASSWORD，无法发送邮件。")

    if not smtp_host and sender_email.lower().endswith("@qq.com"):
        smtp_host = QQ_SMTP_HOST
    if not smtp_host:
        raise RuntimeError("未配置 TOUZIFENXI_EMAIL_SMTP_HOST，且无法根据发件邮箱自动推断 SMTP 主机。")

    if smtp_port_value:
        smtp_port = int(smtp_port_value)
    elif smtp_host == QQ_SMTP_HOST:
        smtp_port = QQ_SMTP_PORT
    else:
        smtp_port = 465

    if use_ssl_value:
        use_ssl = use_ssl_value not in {"0", "false", "no", "off"}
    else:
        use_ssl = smtp_port == 465

    timeout_seconds = float(timeout_value) if timeout_value else 30.0

    return EmailChannelConfig(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        username=username,
        password=password,
        sender_email=sender_email,
        use_ssl=use_ssl,
        timeout_seconds=timeout_seconds,
    )


def build_email_message(
    *,
    sender_email: str,
    recipient_emails: Iterable[str],
    subject: str,
    body_text: str,
    attachments: Iterable[Path],
    body_html: str | None = None,
) -> EmailMessage:
    """构造一封可直接发送的邮件消息对象。"""

    recipients = [email.strip() for email in recipient_emails if email.strip()]
    if not recipients:
        raise ValueError("至少需要一个收件人地址。")

    message = EmailMessage()
    message["From"] = sender_email
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject.strip() or "无主题"
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    for attachment in attachments:
        attachment_path = Path(attachment).resolve()
        if not attachment_path.exists():
            raise FileNotFoundError(f"附件不存在：{attachment_path}")
        mime_type, _ = mimetypes.guess_type(str(attachment_path))
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            attachment_path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment_path.name,
        )
    return message


def send_email_message(*, config: EmailChannelConfig, message: EmailMessage) -> None:
    """按配置发送一封已经构造好的邮件。"""

    smtp_factory = smtplib.SMTP_SSL if config.use_ssl else smtplib.SMTP
    with smtp_factory(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds) as server:
        if not config.use_ssl:
            server.starttls()
        server.login(config.username, config.password)
        server.send_message(message)


def send_email(
    *,
    recipient_emails: Iterable[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    attachments: Iterable[Path] = (),
    config: EmailChannelConfig | None = None,
) -> None:
    """加载配置、构造消息并发送邮件。"""

    effective_config = config or load_email_channel_config()
    message = build_email_message(
        sender_email=effective_config.sender_email,
        recipient_emails=recipient_emails,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )
    send_email_message(config=effective_config, message=message)
