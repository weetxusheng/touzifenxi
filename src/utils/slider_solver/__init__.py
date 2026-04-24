"""通用 Playwright + 滑块风控工具（验证码求解 + 会话编排）。

推荐子模块导入::

    from utils.slider_solver.session import SliderRiskTool, fetch_page_html_with_playwright_slider
    from utils.slider_solver.captcha import solve_slider_captcha

包级属性经 ``__getattr__`` 惰性加载；易盾 CLI：``python -m utils.slider_solver``；
本地手测：``python -m utils.slider_solver.dev_test``。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SliderRiskTool",
    "fetch_page_html_with_playwright_slider",
    "page_suggests_captcha_iframe_or_images",
    "run_yidun_cli",
    "run_yidun_solver",
    "solve_slider_captcha",
    "wait_for_slider_captcha_ui_ready",
]


def __getattr__(name: str) -> Any:
    if name == "SliderRiskTool":
        from .session import SliderRiskTool

        return SliderRiskTool
    if name == "fetch_page_html_with_playwright_slider":
        from .session import fetch_page_html_with_playwright_slider

        return fetch_page_html_with_playwright_slider
    if name in (
        "page_suggests_captcha_iframe_or_images",
        "run_yidun_cli",
        "run_yidun_solver",
        "solve_slider_captcha",
        "wait_for_slider_captcha_ui_ready",
    ):
        from . import captcha as _captcha

        return getattr(_captcha, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
