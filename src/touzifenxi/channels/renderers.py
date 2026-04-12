"""项目级邮件正文渲染器。"""

from __future__ import annotations

import ast
import html
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedEmail:
    """表示已经准备好的邮件标题与正文。"""

    subject: str
    text: str
    html: str


def render_c114_brief_email(markdown_text: str) -> RenderedEmail:
    """把 C114 step 6 Markdown 简报渲染成适合邮件发送的文本与 HTML。"""

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
    plain_text = build_plain_text(title=title, role_line=role_line, summary_items=summary_items, topics=topic_sections)
    html_body = build_html(title=title, role_line=role_line, summary_items=summary_items, topics=topic_sections)
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
            normalized.append(line[2:].strip())
            continue
        normalized.extend(parse_embedded_list(line))
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


def build_plain_text(
    *,
    title: str,
    role_line: str,
    summary_items: list[tuple[str, str]],
    topics: list[dict[str, object]],
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
                lines.append(format_plain_text_item(str(item)))
    return "\n".join(lines).strip()


def format_plain_text_item(item: str) -> str:
    """把一条明细转换成文本版项目。"""

    dated_link_match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<date>[^|]+?)\s+\|\s+(?P<url>https?://\S+)$", item)
    if dated_link_match:
        label = dated_link_match.group("label").strip()
        published_at = dated_link_match.group("date").strip()
        url = dated_link_match.group("url").strip()
        return f"- {label}（{published_at}）：{url}"
    link_match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<url>https?://\S+)$", item)
    if link_match:
        label = link_match.group("label").strip()
        url = link_match.group("url").strip()
        return f"- {label}：{url}"
    return f"- {item}"


def build_html(
    *,
    title: str,
    role_line: str,
    summary_items: list[tuple[str, str]],
    topics: list[dict[str, object]],
) -> str:
    """生成带卡片和分类的 HTML 邮件正文。"""

    topic_html = "".join(render_topic_section(topic) for topic in topics)
    navigation_html = "".join(render_navigation_item(topic) for topic in topics)
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
      .link-list li {{
        padding: 8px 10px;
        border-radius: 10px;
        background: #f8fafc;
        border: 1px solid #e8eef5;
        list-style-position: inside;
      }}
      .link-meta {{
        margin-top: 4px;
        font-size: 12px;
        color: #718191;
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
        <div class="subtitle">{html.escape(role_line or "项目自动生成的主题研究简报")}</div>
        <div class="hero-divider"></div>
      </div>
      <div class="topic-nav">
        <h2 class="topic-nav-title">主题导航</h2>
        <ul class="nav-list">
          {navigation_html}
        </ul>
      </div>
      {topic_html}
      <div class="footer">本邮件由 touzifenxi 项目公共邮件渠道自动发送</div>
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
    """渲染标题下方的主题导航项。"""

    raw_title = str(topic["title"])
    title = html.escape(raw_title)
    section_id = make_anchor_id(raw_title)
    return f'<li><a href="#{section_id}">{title}</a></li>'


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
    if subtitle in {"需要继续跟踪的点", "增量信息"}:
        return f"<ul>{''.join(render_list_item(item, subtitle) for item in items)}</ul>"
    text = " ".join(item.strip() for item in items if item.strip())
    return f'<p class="block-text">{html.escape(text)}</p>'


def render_list_item(item: str, subtitle: str) -> str:
    """渲染一条列表项，链接小节会生成可点击链接。"""

    if subtitle in {"源地址", "补充地址"}:
        dated_match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<date>[^|]+?)\s+\|\s+(?P<url>https?://\S+)$", item)
        if dated_match:
            label = html.escape(dated_match.group("label").strip())
            published_at = html.escape(dated_match.group("date").strip())
            url = html.escape(dated_match.group("url").strip(), quote=True)
            return (
                "<li>"
                f'<a href="{url}">{label}</a>'
                f'<div class="link-meta">{published_at}</div>'
                "</li>"
            )
        match = re.match(r"^(?P<label>.+?)\s+\|\s+(?P<url>https?://\S+)$", item)
        if match:
            label = html.escape(match.group("label").strip())
            url = html.escape(match.group("url").strip(), quote=True)
            return f'<li><a href="{url}">{label}</a></li>'
    return f"<li>{html.escape(item)}</li>"


def make_anchor_id(text: str) -> str:
    """把主题标题转换成 HTML 锚点 id。"""

    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return slug or "topic"
