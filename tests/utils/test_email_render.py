from __future__ import annotations

from utils.tools.output.email import render_c114_brief_email

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
