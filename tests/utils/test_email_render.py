from __future__ import annotations

from utils.tools.output.email import build_email_message, render_c114_brief_email

SAMPLE_MD = """# 测试简报

## 🆕 新品发布
- 【SEMI】XX 公司新品 (link)

## 💰 价格变动
- 【SEMI】合约价上涨 (link)
"""


def test_default_footer_is_c114():
    rendered = render_c114_brief_email(SAMPLE_MD)
    # Default footer is the c114 disclaimer; check for a stable substring.
    haystack = rendered.html + "\n" + rendered.text
    assert "大模型" in haystack or "邮件渠道" in haystack


def test_custom_footer_passes_through():
    rendered = render_c114_brief_email(
        SAMPLE_MD,
        footer_disclaimer="本简报由 chip 流水线生成，仅供研究参考。",
    )
    haystack = rendered.html + "\n" + rendered.text
    assert "chip 流水线" in haystack


def test_build_email_message_encodes_chinese_subject():
    message = build_email_message(
        sender_email="sender@example.com",
        recipient_emails=["to@example.com"],
        subject="36Kr 主题简报",
        body_text="正文",
        attachments=(),
    )
    assert message["Subject"].startswith("=?utf-8?")


def test_load_ews_email_channel_config_cjhxfund_defaults():
    from utils.tools.output.ews_mail import load_ews_email_channel_config

    config = load_ews_email_channel_config(
        {
            "TOUZIFENXI_EMAIL_FROM": "tylxts@cjhxfund.com",
            "TOUZIFENXI_EMAIL_PASSWORD": "secret",
            "TOUZIFENXI_EMAIL_USERNAME": r"CJHX\tylxts",
            "TOUZIFENXI_EWS_AUTH_TYPE": "NTLM",
            "TOUZIFENXI_EMAIL_INSECURE": "true",
        }
    )
    assert config.auth_type == "NTLM"
    assert config.username == r"CJHX\tylxts"
    assert "mail.cjhxfund.com" in config.ews_url
    assert config.verify_tls is False


def test_load_qq_fallback_email_channel_config():
    from utils.tools.output.email import load_qq_fallback_email_channel_config

    config = load_qq_fallback_email_channel_config(
        {
            "TOUZIFENXI_EMAIL_QQ_FROM": "944532395@qq.com",
            "TOUZIFENXI_EMAIL_QQ_PASSWORD": "auth-code",
            "TOUZIFENXI_EMAIL_QQ_FROM_NAME": "投研简报机器人",
        }
    )
    assert config.smtp_host == "smtp.qq.com"
    assert config.smtp_port == 465
    assert config.use_ssl is True
    assert config.sender_email == "944532395@qq.com"
    assert config.sender_name == "投研简报机器人"


def test_send_email_auto_falls_back_to_qq(monkeypatch):
    from utils.tools.output import email as email_mod

    calls: list[str] = []

    def fake_ews(**kwargs):
        calls.append("ews")
        raise RuntimeError("ews down")

    def fake_smtp(**kwargs):
        calls.append("smtp")

    monkeypatch.setattr(email_mod, "_load_dotenv_files", lambda: None)
    monkeypatch.setenv("TOUZIFENXI_EMAIL_BACKEND", "auto")
    monkeypatch.setattr("utils.tools.output.ews_mail.send_email_via_ews", fake_ews)
    monkeypatch.setattr(email_mod, "_send_email_smtp", fake_smtp)
    monkeypatch.setattr(
        email_mod,
        "load_qq_fallback_email_channel_config",
        lambda env=None: email_mod.EmailChannelConfig(
            smtp_host="smtp.qq.com",
            smtp_port=465,
            username="944532395@qq.com",
            password="x",
            sender_email="944532395@qq.com",
        ),
    )

    email_mod.send_email(
        recipient_emails=["to@example.com"],
        subject="test",
        body_text="body",
    )
    assert calls == ["ews", "smtp"]

