"""Chip 简报专属 HTML 邮件渲染器。

chip 的 step 6 markdown 与 c114 不同：每篇文章是一个 H3 卡片，卡片内是
**字段**：内容 / **字段**： + 列表 这种半结构化标记 + 一个 [原文链接](url)。
c114 渲染器把这些当成纯文本压进 <p class="block-text">，可读性极差。

本模块用 stdlib（re + html.escape）解析这份固定结构的 markdown，并复用
kr36 视觉规范（卡片化、白底圆角阴影）生成可读性高的 HTML 邮件正文。

输入边界：
  - 输入必须以 # 开头的 H1 起始，否则 subject 退化为 "半导体简报"
  - H2 桶下面没有 H3 时输出 empty-bucket 占位
  - "## 运行摘要" 或被识别为运行摘要的 H2 不渲染为 brief-section
  - 所有用户文本均经过 html.escape，url 也 escape（quote=True）
"""

from __future__ import annotations

import html
import re

from utils.tools.output.email import RenderedEmail

CHIP_EMAIL_FOOTER_DISCLAIMER = (
    "本简报由 chip（SEMI 中国 + 爱集微）产业新闻流水线生成，仅供内部研究参考。"
)

# 渠道前缀 → channel-tag 文本 / class 后缀
_CHANNEL_TAG_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("【SEMI】", "SEMI", "semi"),
    ("【爱集微】", "爱集微", "laoyaoba"),
)

# 字段名 → 是否预期为列表（决定遇到行首 `- ` 后是否折叠为 ol）
# 不限制具体字段名，因为不同 prompt 版本字段命名会漂移；解析时按"下一行是不是 -"动态判断。
_LINK_PATTERN = re.compile(r"^\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)\s*$")
_FIELD_LINE_PATTERN = re.compile(r"^\*\*(?P<label>[^*]+)\*\*\s*[：:]\s*(?P<rest>.*)$")
_BOLD_INLINE_PATTERN = re.compile(r"\*\*([^*]+)\*\*")


def render_chip_brief_email(
    markdown_text: str,
    *,
    footer_disclaimer: str = CHIP_EMAIL_FOOTER_DISCLAIMER,
) -> RenderedEmail:
    """把 chip step 6 markdown 渲染成卡片化 HTML 邮件正文。

    返回 ``RenderedEmail(subject, text, html)``。``text`` 与 c114 风格一致，
    作为 HTML 邮件的 fallback 文本。
    """

    document = _parse_markdown(markdown_text)
    subject = document.title or "半导体简报"
    html_body = _render_html(document, footer_disclaimer=footer_disclaimer)
    plain_text = _render_plain_text(document, footer_disclaimer=footer_disclaimer)
    return RenderedEmail(subject=subject, text=plain_text, html=html_body)


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------


class _Field:
    """一个 **字段**：xxx 块，可能是单行文本或列表。"""

    __slots__ = ("label", "text", "items")

    def __init__(self, label: str) -> None:
        self.label = label
        self.text: str = ""
        self.items: list[str] = []


class _Article:
    __slots__ = ("title", "channel_tag", "channel_class", "fields", "link_url", "link_label")

    def __init__(self) -> None:
        self.title: str = ""
        self.channel_tag: str = ""
        self.channel_class: str = ""
        self.fields: list[_Field] = []
        self.link_url: str = ""
        self.link_label: str = ""


class _Section:
    __slots__ = ("title", "articles")

    def __init__(self, title: str) -> None:
        self.title = title
        self.articles: list[_Article] = []


class _Document:
    __slots__ = ("title", "lead_lines", "sections")

    def __init__(self) -> None:
        self.title: str = ""
        # header 内的副标题/摘要行，按顺序保留，每条已经做了 inline-bold 处理
        self.lead_lines: list[str] = []
        self.sections: list[_Section] = []


def _parse_markdown(markdown_text: str) -> _Document:
    lines = markdown_text.splitlines()
    doc = _Document()
    current_section: _Section | None = None
    current_article: _Article | None = None
    current_field: _Field | None = None

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # 1. H1
        if raw.startswith("# "):
            doc.title = raw[2:].strip()
            current_section = None
            current_article = None
            current_field = None
            i += 1
            continue

        # 2. 引用块（lead 行）
        if raw.startswith("> "):
            text = raw[2:].strip()
            if text:
                doc.lead_lines.append(text)
            i += 1
            continue

        # 3. H2 → 新 section
        if raw.startswith("## "):
            title = raw[3:].strip()
            current_section = _Section(title=title)
            doc.sections.append(current_section)
            current_article = None
            current_field = None
            i += 1
            continue

        # 4. H3 → 新 article（仅在 section 内有效）
        if raw.startswith("### "):
            if current_section is None:
                # 没有 section 包裹的 H3 就丢掉（不应该出现）
                i += 1
                continue
            current_article = _Article()
            _assign_article_title(current_article, raw[4:].strip())
            current_section.articles.append(current_article)
            current_field = None
            i += 1
            continue

        # 5. 分隔符 → 关闭当前 article
        if stripped == "---":
            current_article = None
            current_field = None
            i += 1
            continue

        # 6. article 内：**字段**：rest / 链接 / 列表 / 普通文本
        if current_article is not None:
            # 6a. 原文链接 [原文链接](url)
            link_match = _LINK_PATTERN.match(stripped)
            if link_match:
                current_article.link_label = link_match.group("label").strip()
                current_article.link_url = link_match.group("url").strip()
                current_field = None
                i += 1
                continue

            # 6b. **字段**：xxx
            field_match = _FIELD_LINE_PATTERN.match(stripped)
            if field_match:
                label = field_match.group("label").strip()
                rest = field_match.group("rest").strip()
                current_field = _Field(label)
                if rest:
                    current_field.text = rest
                current_article.fields.append(current_field)
                i += 1
                continue

            # 6c. 列表项 `- xxx`：归属当前字段（如果没有字段就忽略）
            if stripped.startswith("- ") and current_field is not None:
                item = stripped[2:].strip()
                if item:
                    current_field.items.append(item)
                i += 1
                continue

            # 6d. 空行：清掉"列表正在收集"的状态，避免把后面没有 `-` 的散段误归到列表
            if not stripped:
                current_field = None
                i += 1
                continue

            # 6e. 其他段落：忽略（chip prompt 不会产出这种行；安全起见不静默吞文本）
            i += 1
            continue

        # 7. section 外、article 外的普通文本 → 作为 lead 行
        if stripped and current_section is None:
            doc.lead_lines.append(stripped)
        i += 1

    return doc


def _assign_article_title(article: _Article, raw_title: str) -> None:
    """从 H3 原文中拆出 channel-tag。"""

    for prefix, tag_text, class_suffix in _CHANNEL_TAG_PATTERNS:
        if raw_title.startswith(prefix):
            article.channel_tag = tag_text
            article.channel_class = class_suffix
            article.title = raw_title[len(prefix):].strip()
            return
    article.title = raw_title


# ---------------------------------------------------------------------------
# HTML 渲染层
# ---------------------------------------------------------------------------


_CSS = """\
*,*::before,*::after{box-sizing:border-box;}
body{
  margin:0;padding:0;background:#f4f6f8;color:#1f2d3d;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",sans-serif;
}
.wrapper{max-width:960px;margin:0 auto;padding:28px 20px 40px;}
.header{
  background:#ffffff;color:#102235;border-radius:18px;
  padding:28px 30px;box-shadow:0 10px 28px rgba(17,34,53,0.06);
  border:1px solid rgba(16,34,53,0.08);
}
.title{margin:0;font-size:28px;line-height:1.25;letter-spacing:-0.02em;}
.lead{margin-top:10px;font-size:14px;line-height:1.8;color:#627384;}
.topic-nav{
  background:#ffffff;border-radius:18px;
  padding:16px 20px;margin-top:18px;
  box-shadow:0 10px 28px rgba(17,34,53,0.06);
  border:1px solid rgba(16,34,53,0.08);
}
.topic-nav-title{
  font-size:12px;font-weight:700;color:#627384;
  letter-spacing:0.06em;margin-bottom:10px;text-transform:uppercase;
}
.topic-nav-items{display:flex;flex-wrap:wrap;gap:6px;}
.topic-nav-link{
  display:inline-flex;align-items:center;gap:2px;
  padding:6px 12px;border-radius:999px;
  background:#edf2f7;color:#27435c;
  font-size:13px;font-weight:600;text-decoration:none;
  transition:background .15s,color .15s;
}
.topic-nav-link:hover{background:#dfe9f5;color:#0f5ea8;}
.topic-nav-count{
  color:#0f5ea8;font-size:12px;font-weight:700;
}
.brief-section{
  background:#ffffff;border-radius:18px;
  padding:24px 26px;margin-top:18px;
  box-shadow:0 10px 28px rgba(17,34,53,0.06);
  border:1px solid rgba(16,34,53,0.08);
  scroll-margin-top:20px;
}
.section-title{margin:0 0 12px;font-size:21px;color:#102235;letter-spacing:-0.01em;}
.article-card{
  border-left:3px solid #5b8def;
  padding:14px 18px;margin:14px 0;
  background:#fafbfd;border-radius:8px;
}
.article-title{
  margin:0 0 10px;font-size:16px;font-weight:700;line-height:1.45;
  color:#2d4d69;
}
.article-title-link{
  color:#0f5ea8;text-decoration:none;
}
.article-title-link:hover{
  text-decoration:underline;
}
.channel-tag{
  display:inline-block;font-size:11px;font-weight:600;
  padding:2px 8px;border-radius:999px;
  background:#edf2f7;color:#27435c;margin-right:6px;vertical-align:middle;
}
.channel-tag-semi{background:#dfe9f5;color:#0f5ea8;}
.channel-tag-laoyaoba{background:#fce8d5;color:#8a4a16;}
.article-summary{
  margin:8px 0 12px;font-size:14.5px;line-height:1.75;
  color:#1f2d3d;font-weight:500;
}
.article-field{margin:10px 0;}
.field-label{font-size:13px;font-weight:700;color:#314457;margin-bottom:4px;}
.field-list{margin:4px 0 0 0;padding-left:22px;list-style:decimal;}
.field-list > li{
  margin:4px 0;line-height:1.75;color:#314457;font-size:14px;
}
.field-text{margin:4px 0 0 0;line-height:1.75;color:#314457;font-size:14px;}
.article-entities{
  margin-top:10px;font-size:13px;color:#627384;
  padding-top:8px;border-top:1px dashed #d6dde6;
}
.article-link{margin:10px 0 0;font-size:13px;}
.article-link a{color:#0f5ea8;text-decoration:none;}
.article-link a:hover{text-decoration:underline;}
.footer{
  margin-top:24px;padding:18px 22px;font-size:12px;color:#627384;
  background:#ffffff;border-radius:14px;text-align:center;
  border:1px solid rgba(16,34,53,0.06);
}
.empty-bucket{
  margin:10px 0;padding:14px 18px;font-size:13px;color:#8090a3;
  background:#fafbfd;border-radius:8px;font-style:italic;
}
@media (max-width:640px){
  .wrapper{padding:20px 12px 28px;}
  .header{padding:20px 18px;}
  .title{font-size:24px;}
  .brief-section{padding:18px;}
}
"""


# 当 field label 命中以下集合时，渲染成 article-entities（涉及实体那一行）
_ENTITIES_FIELD_LABELS = frozenset({"涉及实体", "相关公司", "相关实体"})
# 概要 / 摘要 类字段：直接当成文章导语展示，省略 "概要" 标签字样
_SUMMARY_FIELD_LABELS = frozenset({"概要", "摘要", "导语"})


_EMOJI_PREFIX_RE = re.compile(r"^[\U0001F000-\U0001FFFF☀-➿⬀-⯿️]+\s*")
_ANCHOR_SLUG_RE = re.compile(r"[^\w一-鿿]+")


def _strip_emoji_prefix(text: str) -> str:
    return _EMOJI_PREFIX_RE.sub("", text or "").strip()


def _section_anchor_id(section_title: str) -> str:
    clean = _strip_emoji_prefix(section_title)
    slug = _ANCHOR_SLUG_RE.sub("-", clean).strip("-")
    return slug or "section"


def _render_topic_nav(sections: list["_Section"]) -> str:
    """渲染顶部主题导航（每个非空 section 一个 chip 形按钮 + 文章数）"""

    items: list[str] = []
    for section in sections:
        clean_title = _strip_emoji_prefix(section.title)
        if not clean_title:
            continue
        anchor = _section_anchor_id(section.title)
        count = len(section.articles)
        items.append(
            f'<a class="topic-nav-link" href="#{html.escape(anchor, quote=True)}">'
            f'{html.escape(clean_title)}'
            f'<span class="topic-nav-count">（{count}篇）</span>'
            f"</a>"
        )
    if not items:
        return ""
    return (
        '    <nav class="topic-nav">\n'
        '      <div class="topic-nav-title">主题导航</div>\n'
        '      <div class="topic-nav-items">' + "".join(items) + "</div>\n"
        "    </nav>\n"
    )


def _render_html(document: _Document, *, footer_disclaimer: str) -> str:
    title_text = document.title or "半导体简报"
    title_html = html.escape(title_text)

    lead_html = "".join(f'      <p class="lead">{_render_inline(line)}</p>\n' for line in document.lead_lines)

    # 导航保留全部桶（含 0 篇，便于读者了解分类全貌）；正文只渲染有文章的桶，
    # 空桶不再输出 "今日无相关信号" 卡片，避免页面出现大量无信息的占位段落。
    nav_html = _render_topic_nav(document.sections)

    sections_html_parts: list[str] = []
    for section in document.sections:
        if not section.articles:
            continue
        sections_html_parts.append(_render_section(section))
    sections_html = "\n".join(sections_html_parts)

    footer_html = html.escape(footer_disclaimer) if footer_disclaimer else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title_html}</title>
  <style>
{_CSS}  </style>
</head>
<body>
  <div class="wrapper">
    <header class="header">
      <h1 class="title">{title_html}</h1>
{lead_html}    </header>
{nav_html}{sections_html}
    <div class="footer">{footer_html}</div>
  </div>
</body>
</html>
"""


def _render_section(section: _Section) -> str:
    # 去除 section title 前的 emoji 前缀；id 用 slug 形式供 nav 锚点跳转
    clean_title = _strip_emoji_prefix(section.title)
    title_html = html.escape(clean_title)
    anchor_id = _section_anchor_id(section.title)
    if not section.articles:
        body = '      <p class="empty-bucket">今日无相关信号。</p>\n'
    else:
        body = "".join(_render_article(article) for article in section.articles)
    return (
        f'    <section class="brief-section" id="{html.escape(anchor_id, quote=True)}">\n'
        f'      <h2 class="section-title">{title_html}</h2>\n'
        f'{body}'
        "    </section>"
    )


def _render_article(article: _Article) -> str:
    title_text = html.escape(article.title)
    # 标题做成 article 原文链接：可点击直接跳转
    if article.link_url:
        title_html = (
            f'<a class="article-title-link" '
            f'href="{html.escape(article.link_url, quote=True)}" '
            f'target="_blank" rel="noopener">{title_text}</a>'
        )
    else:
        title_html = title_text

    if article.channel_tag:
        tag_class = f"channel-tag channel-tag-{article.channel_class}" if article.channel_class else "channel-tag"
        tag_html = (
            f'<span class="{html.escape(tag_class)}">{html.escape(article.channel_tag)}</span>\n          '
        )
    else:
        tag_html = ""

    fields_html_parts: list[str] = []
    for field in article.fields:
        fields_html_parts.append(_render_field(field))
    fields_html = "".join(fields_html_parts)

    return (
        '      <article class="article-card">\n'
        f'        <h3 class="article-title">\n'
        f'          {tag_html}{title_html}\n'
        f'        </h3>\n'
        f'{fields_html}'
        '      </article>\n'
    )


def _render_field(field: _Field) -> str:
    label_html = html.escape(field.label)
    # 概要/摘要类字段：直接当作文章导语段落展示，省略 label
    if field.label in _SUMMARY_FIELD_LABELS and field.text and not field.items:
        return (
            f'        <p class="article-summary">{_render_inline(field.text)}</p>\n'
        )
    # 涉及实体一类的字段渲染成单行 article-entities，视觉上区别于 field 块
    if field.label in _ENTITIES_FIELD_LABELS and field.text and not field.items:
        return (
            '        <p class="article-entities">'
            f'<strong>{label_html}</strong>：{_render_inline(field.text)}</p>\n'
        )

    if field.items:
        items_html = "".join(f'            <li>{_render_inline(item)}</li>\n' for item in field.items)
        return (
            '        <div class="article-field">\n'
            f'          <div class="field-label">{label_html}</div>\n'
            f'          <ol class="field-list">\n{items_html}          </ol>\n'
            '        </div>\n'
        )

    if not field.text:
        return ""

    return (
        '        <div class="article-field">\n'
        f'          <div class="field-label">{label_html}</div>\n'
        f'          <p class="field-text">{_render_inline(field.text)}</p>\n'
        '        </div>\n'
    )


def _render_inline(text: str) -> str:
    """对一段 inline 文本做 escape + 把 **bold** 转 <strong>。"""

    escaped = html.escape(text)
    # escape 之后的内容不含 HTML 标签字符；** 不会被 escape 改写。
    return _BOLD_INLINE_PATTERN.sub(r"<strong>\1</strong>", escaped)


# ---------------------------------------------------------------------------
# Plain text 渲染层（作为邮件 fallback）
# ---------------------------------------------------------------------------


def _render_plain_text(document: _Document, *, footer_disclaimer: str) -> str:
    parts: list[str] = []
    if document.title:
        parts.append(document.title)
    if document.lead_lines:
        parts.append("")
        for line in document.lead_lines:
            # 文本版去掉 **bold** 标记
            parts.append(_strip_inline(line))
    for section in document.sections:
        parts.append("")
        parts.append(section.title)
        if not section.articles:
            parts.append("  今日无相关信号。")
            continue
        for article in section.articles:
            parts.append("")
            channel_prefix = f"【{article.channel_tag}】" if article.channel_tag else ""
            parts.append(f"  {channel_prefix}{article.title}")
            for field in article.fields:
                if field.items:
                    parts.append(f"    {field.label}：")
                    for idx, item in enumerate(field.items, start=1):
                        parts.append(f"      {idx}. {_strip_inline(item)}")
                elif field.text:
                    parts.append(f"    {field.label}：{_strip_inline(field.text)}")
            if article.link_url:
                label = article.link_label or "原文链接"
                parts.append(f"    {label}：{article.link_url}")
    body = "\n".join(parts).strip()
    if footer_disclaimer:
        body = f"{body}\n\n{footer_disclaimer}"
    return body


def _strip_inline(text: str) -> str:
    """把 **bold** 标记去掉，保留内部文本。"""

    return _BOLD_INLINE_PATTERN.sub(r"\1", text)
