from __future__ import annotations

from utils.tools.output.chip_email import render_chip_brief_email

SAMPLE_MD = """# 半导体简报（2026-05-19）

> **当日核心信号**: 💼投融资/并购=2 · 🏭产业动态=3

数据来源：SEMI + 爱集微

## 💼 投融资/并购

### 【爱集微】长江存储 IPO

**概要**：长江存储启动 IPO 辅导。

**核心要点**：
- 5 月 19 日备案
- 中信联合辅导

**涉及实体**：长江存储、中信证券

[原文链接](https://www.laoyaoba.com/n/1037759)

---

## 💰 价格变动

(空桶，本桶下无文章)

## 🏭 产业动态

### 【SEMI】英特尔 14A

**概要**：英特尔 14A 制程量产。

[原文链接](https://www.semi.org.cn/article/abc.html)

---
"""


def test_renders_title():
    r = render_chip_brief_email(SAMPLE_MD)
    assert "<title>半导体简报（2026-05-19）</title>" in r.html
    assert r.subject == "半导体简报（2026-05-19）"


def test_renders_lead_with_summary():
    r = render_chip_brief_email(SAMPLE_MD)
    assert "当日核心信号" in r.html
    assert "💼投融资/并购=2" in r.html


def test_renders_brief_sections():
    r = render_chip_brief_email(SAMPLE_MD)
    assert 'class="brief-section"' in r.html
    assert "投融资/并购" in r.html
    assert "产业动态" in r.html


def test_renders_article_cards():
    r = render_chip_brief_email(SAMPLE_MD)
    assert 'class="article-card"' in r.html
    # 标题里的 channel-tag
    assert "channel-tag-laoyaoba" in r.html
    assert "channel-tag-semi" in r.html


def test_renders_field_list():
    r = render_chip_brief_email(SAMPLE_MD)
    # 核心要点列表
    assert '<ol class="field-list">' in r.html
    assert "<li>5 月 19 日备案</li>" in r.html


def test_renders_article_title_link():
    """文章标题应该是可点击的链接，URL 在 href 上而非单独成行。"""

    r = render_chip_brief_email(SAMPLE_MD)
    # URL 应该在 article-title-link 上
    assert 'class="article-title-link"' in r.html
    assert 'href="https://www.laoyaoba.com/n/1037759"' in r.html
    # 标题文本仍在
    assert "长江存储 IPO" in r.html
    # 不再有单独的"原文链接"行（HTML 里）
    assert '<p class="article-link">' not in r.html
    # 但邮件 plain text 仍保留 URL 引用（无法点击）
    assert "https://www.laoyaoba.com/n/1037759" in r.text


def test_handles_empty_bucket():
    r = render_chip_brief_email(SAMPLE_MD)
    # 价格变动桶为空
    assert "价格变动" in r.html
    assert "empty-bucket" in r.html or "无相关信号" in r.html


def test_footer_passes_through():
    r = render_chip_brief_email(SAMPLE_MD, footer_disclaimer="CUSTOM_FOOTER_XYZ")
    assert "CUSTOM_FOOTER_XYZ" in r.html


def test_escapes_html_in_content():
    md = """# 测试

## 测试

### 【SEMI】<script>alert(1)</script>

**概要**：含有 <evil> 标签
"""
    r = render_chip_brief_email(md)
    assert "<script>alert(1)</script>" not in r.html
    assert "&lt;script&gt;" in r.html or "&lt;evil&gt;" in r.html
