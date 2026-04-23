"""
36kr 遇风控/滑块时的**唯一**封装：Playwright + 自动滑块 + 回写 Cookie。

- ``fetch``：对应 ``playwright_slider_session.fetch_36kr_page_html_with_playwright_slider`` 一处入口。
- ``maybe_recover``：在 HTML 已判为验证码页、且非 ``http_only`` 时调 ``fetch``，成功则 ``_kr36_risk_recovery_reset``。

``Kr36SourceAdapter.step4_fetch_html_via_playwright_slider``、``_maybe_recover_html_with_step4``、
``step4_risk.try_36kr_step4_html_after_risk``、联调脚本等**均应通过本类**，避免在多处各写一份 import 与条件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kr36.source_adapter import Kr36SourceAdapter


class Kr36RiskStep4Tool:
    """持有一个 :class:`~kr36.source_adapter.Kr36SourceAdapter` 实例，用于该配置下的所有 step4/自动滑块请求。"""

    __slots__ = ("_adapter",)

    def __init__(self, adapter: "Kr36SourceAdapter") -> None:
        self._adapter: Any = adapter

    def fetch(self, url: str, *, log_phase: str = "step4") -> str:
        """
        打开 ``url``，遇滑块则自动解，返回页面 HTML（与 ``playwright_slider_session`` 一致）。
        """
        from .playwright_slider_session import fetch_36kr_page_html_with_playwright_slider

        return fetch_36kr_page_html_with_playwright_slider(url, self._adapter, log_phase=log_phase)

    def maybe_recover(self, url: str, html: str, *, log_phase: str) -> str:
        """
        已有可用 HTML（非验证码页）时直接返回，不打开浏览器。
        只在以下情况才调 ``fetch``（Playwright + 自动滑块）：

        1. HTML 已像验证码/风控页（``_looks_like_captcha_or_block``）；
        2. ``http_only_mode`` 为 true → 任何情况都直接返回原 HTML，不启动 Playwright。

        ``risk_verification_auto_solver`` 仅控制「列表补全路径是否跳过轻量 Playwright」
        （见 ``_kr36_enrich_*``），**不**影响此方法的触发条件，避免数据正常时仍多开浏览器。

        成功恢复后重置该 URL 的风控恢复计数。
        """
        from .source_adapter import (
            _append_kr36_debug_log,
            _kr36_risk_recovery_reset,
            _looks_like_captcha_or_block,
        )

        if self._adapter.http_only_mode:
            if html and _looks_like_captcha_or_block(html):
                _append_kr36_debug_log(
                    f"[kr36] step4_recover_skipped reason=http_only url={url} phase={log_phase}"
                )
                print(
                    "[kr36] 已跳过 step4（Playwright+自动滑块）：`sources.kr36.http_only_mode` 为 true。"
                    "需要自动过滑块时请将 `http_only_mode` 设为 false。"
                )
            return html

        if not (html and _looks_like_captcha_or_block(html)):
            return html

        _append_kr36_debug_log(
            f"[kr36] step4_recover_trigger url={url} phase={log_phase} looks_like_captcha=true"
        )
        recovered = self.fetch(url, log_phase=log_phase)
        if recovered and not _looks_like_captcha_or_block(recovered):
            _kr36_risk_recovery_reset(url)
            return recovered
        return html
