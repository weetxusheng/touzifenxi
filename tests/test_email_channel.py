from __future__ import annotations

import os
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from touzifenxi.channels.email import (
    EmailChannelConfig,
    build_email_message,
    load_email_channel_config,
    send_email_message,
)
from touzifenxi.channels.renderers import render_c114_brief_email


class EmailChannelConfigTests(unittest.TestCase):
    def test_load_email_channel_config_uses_qq_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TOUZIFENXI_EMAIL_FROM": "525443496@qq.com",
                "TOUZIFENXI_EMAIL_PASSWORD": "auth-code",
            },
            clear=False,
        ):
            config = load_email_channel_config()

        self.assertEqual(config.smtp_host, "smtp.qq.com")
        self.assertEqual(config.smtp_port, 465)
        self.assertTrue(config.use_ssl)
        self.assertEqual(config.sender_email, "525443496@qq.com")
        self.assertEqual(config.password, "auth-code")


class EmailMessageTests(unittest.TestCase):
    def test_build_email_message_includes_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            attachment_path = Path(tmp_dir) / "brief.md"
            attachment_path.write_text("# 简报\n", encoding="utf-8")

            message = build_email_message(
                sender_email="525443496@qq.com",
                recipient_emails=["chengxusheng@cjhxfund.com"],
                subject="C114 简报",
                body_text="请查收今日简报。",
                attachments=[attachment_path],
            )

        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertEqual(parsed["From"], "525443496@qq.com")
        self.assertEqual(parsed["To"], "chengxusheng@cjhxfund.com")
        self.assertEqual(parsed["Subject"], "C114 简报")
        attachment_names = [part.get_filename() for part in parsed.iter_attachments()]
        self.assertEqual(attachment_names, ["brief.md"])

    def test_build_email_message_includes_html_alternative(self) -> None:
        message = build_email_message(
            sender_email="525443496@qq.com",
            recipient_emails=["chengxusheng@cjhxfund.com"],
            subject="C114 简报",
            body_text="请查收今日简报。",
            body_html="<html><body><h1>简报</h1><p>请查收。</p></body></html>",
            attachments=[],
        )

        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        body_parts = list(parsed.walk())
        plain_part = next(part for part in body_parts if part.get_content_type() == "text/plain")
        html_part = next(part for part in body_parts if part.get_content_type() == "text/html")
        self.assertIn("请查收今日简报。", plain_part.get_content())
        self.assertIn("<h1>简报</h1>", html_part.get_content())


class EmailRendererTests(unittest.TestCase):
    def test_render_c114_brief_email_outputs_html_sections_and_links(self) -> None:
        markdown = """# C114 主题简报（2026-04-09）

> 角色：资深研究员 agent

## 运行摘要

- 原始文章数：10
- 正文抓取成功数：20

## AI与算力

### 核心判断

AI 与网络融合加快。

### 增量信息

['新增白皮书发布', '组织架构调整']

### 源地址

- 原文一 | 2026-04-09 | https://example.com/a

### 补充地址

- 补充一 | 2026-04-08 | https://example.com/b
"""
        rendered = render_c114_brief_email(markdown)

        self.assertIn("AI与算力", rendered.html)
        self.assertIn("https://example.com/a", rendered.html)
        self.assertIn("2026-04-09", rendered.html)
        self.assertIn("新增白皮书发布", rendered.html)
        self.assertIn("C114 主题简报", rendered.subject)
        self.assertIn("AI与算力", rendered.text)
        self.assertNotIn("运行摘要", rendered.html)
        self.assertNotIn("原始文章数", rendered.html)
        self.assertIn('class="topic-nav"', rendered.html)
        self.assertIn('href="#ai与算力"', rendered.html)
        self.assertNotIn('class="sidebar"', rendered.html)
        self.assertNotIn("C114 Daily Brief", rendered.html)


class EmailSendTests(unittest.TestCase):
    def test_send_email_message_uses_smtp_ssl(self) -> None:
        config = EmailChannelConfig(
            smtp_host="smtp.qq.com",
            smtp_port=465,
            username="525443496@qq.com",
            password="auth-code",
            sender_email="525443496@qq.com",
            use_ssl=True,
            timeout_seconds=30.0,
        )
        message = build_email_message(
            sender_email=config.sender_email,
            recipient_emails=["chengxusheng@cjhxfund.com"],
            subject="C114 简报",
            body_text="请查收。",
            attachments=[],
        )

        with patch("touzifenxi.channels.email.smtplib.SMTP_SSL") as smtp_ssl:
            send_email_message(config=config, message=message)

        smtp_ssl.assert_called_once_with("smtp.qq.com", 465, timeout=30.0)
        server = smtp_ssl.return_value.__enter__.return_value
        server.login.assert_called_once_with("525443496@qq.com", "auth-code")
        server.send_message.assert_called_once()
