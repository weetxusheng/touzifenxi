"""Step4 正文抓取：36kr 直抓遇风控时走 Playwright + 滑块。"""

from __future__ import annotations

from pathlib import Path


def try_36kr_step4_html_after_risk(url: str) -> str:
    """
    由 ``utils.tools.content.fetch.fetch_url_content`` 在以下情况调用（需配置
    ``content.kr36_step4_risk_playwright`` 为 true）：

    - 36kr 直抓 ``failed``（含 HTTP 错误、超时等）；
    - 直抓 ``empty`` 且判定为风控/验证页（``fetch_error`` 含 ``kr36_risk``，
      或页面标题像验证/封禁页）。

    实际执行：``Kr36SourceAdapter.step4_fetch_html_via_playwright_slider``（Chromium
    打开页面 + 自动滑块 + 可选写回 cookie）。未启用或失败时返回空串。
    """

    from c114.runtime.config import load_c114_runtime_config
    from kr36.source_adapter import Kr36SourceAdapter

    project = Path(__file__).resolve().parents[2]
    rc = load_c114_runtime_config(project)
    if not rc.content_kr36_step4_risk_playwright:
        return ""
    if "36kr.com" not in (url or "").lower():
        return ""
    conf = (rc.source_configs or {}).get("kr36", {}) or {}
    adapter = Kr36SourceAdapter(conf)
    return adapter.step4_fetch_html_via_playwright_slider(url)
