"""项目级 SMTP 邮件发送渠道。"""

from __future__ import annotations

import ast
import html
import mimetypes
import os
import re
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.header import Header
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

QQ_SMTP_HOST = "smtp.qq.com"
QQ_SMTP_PORT = 465
CJHX_SMTP_HOST = "mail.cjhxfund.com"
CJHX_SMTP_PORT = 587


@dataclass(frozen=True)
class EmailChannelConfig:
    """表示 SMTP 邮件渠道所需的连接配置。"""

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    sender_email: str
    sender_name: str | None = None
    use_ssl: bool = True
    verify_tls: bool = True
    timeout_seconds: float = 30.0


def load_email_channel_config(env: Mapping[str, str] | None = None) -> EmailChannelConfig:
    """从环境变量或传入的 env 映射加载邮件渠道配置，并对 QQ 邮箱提供默认值。"""

    src: Mapping[str, str] = os.environ if env is None else env

    def getv(key: str) -> str:
        raw = src.get(key, "")
        return str(raw or "").strip()

    sender_email = getv("TOUZIFENXI_EMAIL_FROM")
    sender_name = getv("TOUZIFENXI_EMAIL_FROM_NAME") or None
    username = getv("TOUZIFENXI_EMAIL_USERNAME") or sender_email
    password = getv("TOUZIFENXI_EMAIL_PASSWORD")
    smtp_host = getv("TOUZIFENXI_EMAIL_SMTP_HOST")
    smtp_port_value = getv("TOUZIFENXI_EMAIL_SMTP_PORT")
    use_ssl_value = getv("TOUZIFENXI_EMAIL_USE_SSL").lower()
    verify_tls_value = getv("TOUZIFENXI_EMAIL_VERIFY_TLS").lower()
    insecure_value = getv("TOUZIFENXI_EMAIL_INSECURE").lower()
    timeout_value = getv("TOUZIFENXI_EMAIL_TIMEOUT_SECONDS")

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
    elif smtp_host == CJHX_SMTP_HOST:
        smtp_port = CJHX_SMTP_PORT
    else:
        smtp_port = 465

    if use_ssl_value:
        use_ssl = use_ssl_value not in {"0", "false", "no", "off"}
    else:
        # mail.cjhxfund.com 默认用 STARTTLS(587)，QQ 默认用 SSL(465)；其他 host 按端口推断
        use_ssl = False if smtp_host == CJHX_SMTP_HOST else smtp_port == 465

    if insecure_value:
        verify_tls = insecure_value in {"0", "false", "no", "off"}
    elif verify_tls_value:
        verify_tls = verify_tls_value not in {"0", "false", "no", "off"}
    else:
        verify_tls = True

    timeout_seconds = float(timeout_value) if timeout_value else 30.0

    return EmailChannelConfig(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        username=username,
        password=password,
        sender_email=sender_email,
        sender_name=sender_name,
        use_ssl=use_ssl,
        verify_tls=verify_tls,
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

    # 兼容企业/内网自签证书：允许关闭 TLS 校验
    context = ssl.create_default_context() if config.verify_tls else ssl._create_unverified_context()  # noqa: SLF001

    # 特殊兼容：mail.cjhxfund.com 的握手流程和你提供的脚本保持一致
    if config.smtp_host == CJHX_SMTP_HOST:
        if config.use_ssl:
            with smtplib.SMTP_SSL(
                host=config.smtp_host,
                port=config.smtp_port,
                context=context,
                timeout=config.timeout_seconds,
            ) as server:
                server.login(config.username, config.password)
                server.send_message(message)
            return

        with smtplib.SMTP(host=config.smtp_host, port=config.smtp_port, timeout=config.timeout_seconds) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(config.username, config.password)
            server.send_message(message)
        return

    # 其他 SMTP（含 QQ）保持原逻辑，只是补上 TLS context/verify 控制
    if config.use_ssl:
        with smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=config.timeout_seconds, context=context
        ) as server:
            server.login(config.username, config.password)
            server.send_message(message)
        return

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.timeout_seconds) as server:
        server.starttls(context=context)
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
    if effective_config.sender_name:
        display_from = f"{effective_config.sender_name} <{effective_config.sender_email}>"
        message.replace_header("From", str(Header(display_from, "utf-8")))
    send_email_message(config=effective_config, message=message)


"""项目级邮件正文渲染器（merged from channels/renderers.py）."""


C114_EMAIL_FOOTER_DISCLAIMER = "说明：本邮件由邮件渠道自动发送，简报内容由大模型生成，仅作为参考。"


@dataclass(frozen=True)
class RenderedEmail:
    """表示已经准备好的邮件标题与正文。"""

    subject: str
    text: str
    html: str


def render_c114_brief_email(
    markdown_text: str,
    *,
    footer_disclaimer: str = C114_EMAIL_FOOTER_DISCLAIMER,
) -> RenderedEmail:
    """把 step 6 Markdown 简报渲染成适合邮件发送的文本与 HTML。

    ``footer_disclaimer`` defaults to the c114 footer for backward compatibility;
    other sources (e.g., chip) can override it with their own disclaimer text.
    """

    lines = [line.rstrip() for line in markdown_text.splitlines()]
    title = "C114 主题简报"
    role_line = ""
    summary_items: list[tuple[str, str]] = []
    topic_sections: list[dict[str, object]] = []
    current_topic: dict[str, object] | None = None
    current_subtitle = ""
    current_buffer: list[str] = []

    def flush_subsection() -> None:
        nonlocal current_buffer, current_subtitle, current_topic
        if current_topic is None or not current_subtitle:
            current_buffer = []
            return
        subsections = current_topic.setdefault("subsections", {})
        assert isinstance(subsections, dict)
        subsections[current_subtitle] = normalize_block_lines(current_buffer)
        current_buffer = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if raw_line.startswith("# "):
            title = raw_line[2:].strip()
            continue
        if raw_line.startswith("> "):
            candidate_role = raw_line[2:].strip()
            if not candidate_role.startswith("角色："):
                role_line = candidate_role
            continue
        if raw_line.startswith("## "):
            flush_subsection()
            current_subtitle = ""
            topic_name = raw_line[3:].strip()
            if topic_name == "运行摘要":
                current_topic = None
            else:
                current_topic = {"title": topic_name, "subsections": {}}
                topic_sections.append(current_topic)
            continue
        if raw_line.startswith("### "):
            flush_subsection()
            current_subtitle = raw_line[4:].strip()
            continue
        if current_topic is None:
            if raw_line.startswith("- "):
                key, value = split_metric_line(raw_line[2:].strip())
                if key:
                    summary_items.append((key, value))
            continue
        current_buffer.append(raw_line)

    flush_subsection()

    subject = title
    plain_text = build_plain_text(
        title=title,
        role_line=role_line,
        summary_items=summary_items,
        topics=topic_sections,
        footer_disclaimer=footer_disclaimer,
    )
    html_body = build_html(
        title=title,
        role_line=role_line,
        summary_items=summary_items,
        topics=topic_sections,
        footer_disclaimer=footer_disclaimer,
    )
    return RenderedEmail(subject=subject, text=plain_text, html=html_body)


def split_metric_line(line: str) -> tuple[str, str]:
    """把运行摘要中的一行指标拆成键值对。"""

    if "：" in line:
        key, value = line.split("：", 1)
        return key.strip(), value.strip()
    if ":" in line:
        key, value = line.split(":", 1)
        return key.strip(), value.strip()
    return line.strip(), ""


def normalize_block_lines(lines: list[str]) -> list[str]:
    """归一化一个小节里的原始文本行。"""

    normalized: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            cleaned_line = sanitize_display_text(line[2:].strip())
            if cleaned_line:
                normalized.append(cleaned_line)
            continue
        for parsed in parse_embedded_list(line):
            cleaned_line = sanitize_display_text(parsed)
            if cleaned_line:
                normalized.append(cleaned_line)
    return normalized


def parse_embedded_list(line: str) -> list[str]:
    """兼容把列表直接序列化成字符串的情况。"""

    if line.startswith("[") and line.endswith("]"):
        try:
            value = ast.literal_eval(line)
        except (ValueError, SyntaxError):
            return [line]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    return [line]


def sanitize_display_text(value: str) -> str:
    """清理展示文本中的乱码占位符和不可见控制字符。"""

    if not value:
        return ""
    if len(re.findall(r"(?:&#x[0-9a-fA-F]+;|&#\d+;|&amp;#x[0-9a-fA-F]+;)", value)) >= 3:
        return ""
    cleaned = value.replace("\ufffd", "").replace("ï¿½", "")
    cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def build_plain_text(
    *,
    title: str,
    role_line: str,
    summary_items: list[tuple[str, str]],
    topics: list[dict[str, object]],
    footer_disclaimer: str = C114_EMAIL_FOOTER_DISCLAIMER,
) -> str:
    """生成文本版邮件正文，作为 HTML 邮件的备用正文。"""

    lines = [title]
    if role_line:
        lines.extend(["", role_line])
    for topic in topics:
        topic_title = str(topic["title"])
        lines.extend(["", topic_title])
        subsections = topic.get("subsections", {})
        assert isinstance(subsections, dict)
        for subtitle, items in subsections.items():
            lines.append(f"{subtitle}")
            for item in items:
                rendered_item = format_plain_text_item(str(item))
                if rendered_item:
                    lines.append(rendered_item)
    body = "\n".join(lines).strip()
    if footer_disclaimer:
        body = f"{body}\n\n{footer_disclaimer}"
    return body


def format_plain_text_item(item: str) -> str:
    """把一条明细转换成文本版项目。"""

    cleaned_item = sanitize_display_text(item)
    if not cleaned_item:
        return ""
    dated_link_match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<date>[^|]+?)\s+\|\s+(?P<url>https?://\S+)$", cleaned_item)
    if dated_link_match:
        label = dated_link_match.group("label").strip()
        published_at = dated_link_match.group("date").strip()
        url = dated_link_match.group("url").strip()
        return f"- {label}（{published_at}）：{url}"
    link_match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<url>https?://\S+)$", cleaned_item)
    if link_match:
        label = link_match.group("label").strip()
        url = link_match.group("url").strip()
        return f"- {label}：{url}"
    return f"- {cleaned_item}"


def build_html(
    *,
    title: str,
    role_line: str,
    summary_items: list[tuple[str, str]],
    topics: list[dict[str, object]],
    footer_disclaimer: str = C114_EMAIL_FOOTER_DISCLAIMER,
) -> str:
    """生成带卡片和分类的 HTML 邮件正文。"""

    topic_html = "".join(render_topic_section(topic) for topic in topics)
    navigation_html = "".join(render_navigation_item(topic) for topic in topics)
    subtitle_block = ""
    if role_line.strip():
        subtitle_block = f'        <div class="subtitle">{html.escape(role_line.strip())}</div>\n'
    footer_text = html.escape(footer_disclaimer)
    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{html.escape(title)}</title>
    <style>
      body {{
        margin: 0;
        padding: 0;
        background: #f4f6f8;
        color: #1f2d3d;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      }}
      .wrapper {{
        max-width: 960px;
        margin: 0 auto;
        padding: 28px 20px 40px;
      }}
      .hero {{
        background: #ffffff;
        color: #102235;
        border-radius: 18px;
        padding: 28px 30px;
        box-shadow: 0 10px 28px rgba(17, 34, 53, 0.06);
        border: 1px solid rgba(16, 34, 53, 0.08);
      }}
      h1 {{
        margin: 0;
        font-size: 28px;
        line-height: 1.25;
        letter-spacing: -0.02em;
      }}
      .subtitle {{
        margin-top: 10px;
        font-size: 14px;
        line-height: 1.8;
        color: #627384;
      }}
      .hero-divider {{
        width: 64px;
        height: 3px;
        margin-top: 18px;
        border-radius: 999px;
        background: linear-gradient(90deg, #0f5ea8 0%, #78a8d4 100%);
      }}
      .topic-nav {{
        margin-top: 18px;
        background: #ffffff;
        border-radius: 18px;
        padding: 16px 18px 8px;
        box-shadow: 0 10px 28px rgba(17, 34, 53, 0.06);
        border: 1px solid rgba(16, 34, 53, 0.08);
      }}
      .topic-nav-title {{
        margin: 0 0 12px;
        font-size: 13px;
        font-weight: 700;
        color: #395066;
        letter-spacing: 0.04em;
      }}
      .nav-list {{
        list-style: none;
        margin: 0;
        padding: 0;
        font-size: 0;
      }}
      .nav-list li {{
        display: inline-block;
        vertical-align: top;
        margin: 0 8px 8px 0;
      }}
      .nav-list a {{
        display: inline-block;
        padding: 9px 12px;
        border-radius: 999px;
        background: #edf2f7;
        color: #27435c;
        font-size: 13px;
        font-weight: 600;
        line-height: 1.5;
        border: 1px solid rgba(39, 67, 92, 0.08);
      }}
      .topic-card {{
        background: #ffffff;
        border-radius: 18px;
        padding: 24px 26px;
        margin-top: 18px;
        box-shadow: 0 10px 28px rgba(17, 34, 53, 0.06);
        border: 1px solid rgba(16, 34, 53, 0.08);
      }}
      .topic-title {{
        margin: 0 0 16px;
        font-size: 21px;
        color: #102235;
        letter-spacing: -0.01em;
      }}
      .block {{
        margin-top: 18px;
        padding-top: 18px;
        border-top: 1px solid #e9eef3;
      }}
      .block:first-of-type {{
        margin-top: 0;
        padding-top: 0;
        border-top: 0;
      }}
      .block-title {{
        margin: 0 0 10px;
        font-size: 14px;
        font-weight: 700;
        color: #2d4d69;
        letter-spacing: 0.01em;
      }}
      .block-text {{
        margin: 0;
        line-height: 1.9;
        color: #314457;
        font-size: 14px;
      }}
      .indented-text {{
        text-indent: 2em;
      }}
      ul {{
        margin: 0;
        padding-left: 18px;
      }}
      li {{
        margin: 8px 0;
        line-height: 1.8;
        color: #314457;
        font-size: 14px;
      }}
      a {{
        color: #0f5ea8;
        text-decoration: none;
      }}
      .link-list {{
        list-style: disc;
        margin: 0;
        padding: 0 0 0 24px;
      }}
      .link-list li {{
        margin: 0 0 4px;
      }}
      .link-list li:last-child {{
        margin-bottom: 0;
      }}
      .footer {{
        margin-top: 24px;
        font-size: 12px;
        color: #7a8897;
        text-align: center;
      }}
    </style>
  </head>
  <body>
    <div class="wrapper">
      <div class="hero">
        <h1>{html.escape(title)}</h1>
{subtitle_block}        <div class="hero-divider"></div>
      </div>
      <div class="topic-nav">
        <h2 class="topic-nav-title">主题导航</h2>
        <ul class="nav-list">
          {navigation_html}
        </ul>
      </div>
      {topic_html}
      <div class="footer">{footer_text}</div>
    </div>
  </body>
</html>
"""


def render_topic_section(topic: dict[str, object]) -> str:
    """渲染单个主题卡片。"""

    raw_title = str(topic["title"])
    title = html.escape(raw_title)
    section_id = make_anchor_id(raw_title)
    subsections = topic.get("subsections", {})
    assert isinstance(subsections, dict)
    blocks = "".join(render_subsection_block(str(subtitle), list(items)) for subtitle, items in subsections.items())
    return f"""
    <section class="topic-card" id="{section_id}">
      <h2 class="topic-title">{title}</h2>
      {blocks}
    </section>
    """


def render_navigation_item(topic: dict[str, object]) -> str:
    """渲染标题下方的主题导航项；分类后缀文章数「（N篇）」。

    篇数取该分类「源地址」小节的条目数（= 该桶对应的原始文章数）。
    """

    raw_title = str(topic["title"])
    title = html.escape(raw_title)
    section_id = make_anchor_id(raw_title)
    subsections = topic.get("subsections", {})
    count = 0
    if isinstance(subsections, dict):
        sources = subsections.get("源地址", [])
        if isinstance(sources, list):
            count = len([s for s in sources if str(s).strip()])
    suffix = f"（{count}篇）" if count else ""
    return f'<li><a href="#{section_id}">{title}{suffix}</a></li>'


def render_subsection_block(subtitle: str, items: list[str]) -> str:
    """渲染主题卡片中的一个小节。"""

    rendered_body = render_subsection_body(subtitle, items)
    return f"""
    <div class="block">
      <h3 class="block-title">{html.escape(subtitle)}</h3>
      {rendered_body}
    </div>
    """


def render_subsection_body(subtitle: str, items: list[str]) -> str:
    """根据小节类型渲染 HTML。"""

    if subtitle in {"源地址", "补充地址"}:
        return f'<ul class="link-list">{"".join(render_list_item(item, subtitle) for item in items)}</ul>'
    if subtitle in {"需要继续跟踪的点", "增量信息", "描述"}:
        return f"<ul>{''.join(render_list_item(item, subtitle) for item in items)}</ul>"
    text = " ".join(item.strip() for item in items if item.strip())
    text = sanitize_display_text(text)
    if not text:
        return ""
    text_class = "block-text"
    if subtitle in {"核心判断", "产业/公司影响", "产品/公司影响"}:
        text_class = "block-text indented-text"
    return f'<p class="{text_class}">{_break_enumerations(html.escape(text))}</p>'


# 句末标点后的"一、二、…"(中文数字)与"1、2、…"(阿拉伯数字)枚举标记，
# 是 LLM 长段落里常见的分点写法，挤在一段里可读性差。渲染时在这些枚举前换行：
#  - 中文数字主分点 → <br> 顶格
#  - 阿拉伯数字次分点 → <br> + 全角空格缩进
# 只匹配「句末标点（。！？：）后」的枚举，避免误伤正文里的普通顿号（如"设备、材料"）。
_ENUM_CN_RE = re.compile(r"([。！？：])\s*([一二三四五六七八九十]+、)")
_ENUM_AR_RE = re.compile(r"([。！？：])\s*([1-9]\d?、)")


def _break_enumerations(escaped_text: str) -> str:
    s = _ENUM_CN_RE.sub(r"\1<br>\2", escaped_text)
    s = _ENUM_AR_RE.sub(r"\1<br>　　\2", s)
    return s


def render_list_item(item: str, subtitle: str) -> str:
    """渲染一条列表项，链接小节会生成可点击链接。"""

    cleaned_item = sanitize_display_text(item)
    if not cleaned_item:
        return ""
    if subtitle in {"源地址", "补充地址"}:
        dated_match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<date>[^|]+?)\s+\|\s+(?P<url>https?://\S+)$", cleaned_item)
        if dated_match:
            label = html.escape(dated_match.group("label").strip())
            published_at = html.escape(dated_match.group("date").strip())
            url = html.escape(dated_match.group("url").strip(), quote=True)
            return f'<li><a href="{url}">{label}（{published_at}）</a></li>'
        match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<url>https?://\S+)$", cleaned_item)
        if match:
            label = html.escape(match.group("label").strip())
            url = html.escape(match.group("url").strip(), quote=True)
            return f'<li><a href="{url}">{label}</a></li>'
    return f"<li>{html.escape(cleaned_item)}</li>"


def make_anchor_id(text: str) -> str:
    """把主题标题转换成 HTML 锚点 id。"""

    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return slug or "topic"
