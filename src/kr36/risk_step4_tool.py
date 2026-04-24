"""
36kr 遇风控/滑块时的**唯一**封装：Playwright + 自动滑块 + 回写 Cookie。

- ``fetch``：对应 ``playwright_slider_session.fetch_36kr_page_html_with_playwright_slider`` 一处入口。
- ``maybe_recover``：在 HTML 已判为风控（验证码文案、字节系验证壳、或列表 CSR 壳）且非 ``http_only`` 时调 ``fetch``，成功则 ``_kr36_risk_recovery_reset``。

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

    def fetch(self, url: str, *, log_phase: str = "step4", skip_cookies: bool = False) -> str:
        """
        打开 ``url``，遇滑块则自动解，返回页面 HTML（与 ``playwright_slider_session`` 一致）。

        ``skip_cookies=True``：不注入本地 Cookie，以全新身份访问（用于视频页解除 CDN 限速）。
        """
        from .playwright_slider_session import fetch_36kr_page_html_with_playwright_slider

        return fetch_36kr_page_html_with_playwright_slider(
            url, self._adapter, log_phase=log_phase, skip_cookies=skip_cookies
        )

    def maybe_recover(self, url: str, html: str, *, log_phase: str) -> str:
        """
        非风控 HTML 直接返回；命中风控则调 ``fetch``（Playwright + 自动滑块）。

        风控判定（``_kr36_needs_playwright_slider_recovery``）包括：

        1. 验证码/拦截文案（``_looks_like_captcha_or_block``）；
        2. 字节系验证壳（``TTGCaptcha`` / ``sec_sdk_build`` 等小页，见 ``_kr36_search_html_has_risk_interstitial``）；
        3. 专题/活动/搜索**列表** URL 下 curl 仅为 CSR 壳、无可用条目（``_kr36_listing_requires_step4_recovery``）。

        ``http_only_mode`` 为 true → 不启动 Playwright，仅打日志提示。

        ``risk_verification_auto_solver`` 仅影响 ``_kr36_enrich_*`` 轻量拉页是否直跳 step4，
        **不**改变本方法的触发条件。

        成功恢复后重置该 URL 的风控恢复计数。
        """
        from .source_adapter import (
            _append_kr36_debug_log,
            _kr36_needs_playwright_slider_recovery,
            _kr36_risk_recovery_reset,
            _looks_like_captcha_or_block,
            _kr36_search_html_has_risk_interstitial,
            _kr36_listing_requires_step4_recovery,
        )

        if self._adapter.http_only_mode:
            if html and _kr36_needs_playwright_slider_recovery(url, html):
                _append_kr36_debug_log(
                    f"[kr36] step4_recover_skipped reason=http_only url={url} phase={log_phase}"
                )
                print(
                    "[kr36] 已跳过 step4（Playwright+自动滑块）：`sources.kr36.http_only_mode` 为 true。"
                    "遇风控需自动滑块时请设为 false。"
                )
            return html

        if not (html and _kr36_needs_playwright_slider_recovery(url, html)):
            return html

        reasons: list[str] = []
        if _looks_like_captcha_or_block(html):
            reasons.append("captcha_or_block_copy")
        if _kr36_search_html_has_risk_interstitial(html):
            reasons.append("risk_interstitial")
        if _kr36_listing_requires_step4_recovery(url, html):
            reasons.append("listing_csr_shell")
        reason_s = ",".join(reasons) if reasons else "wind_control"
        _append_kr36_debug_log(
            f"[kr36] step4_recover_trigger url={url} phase={log_phase} reason={reason_s}"
        )
        if "listing_csr_shell" in reasons and "captcha_or_block_copy" not in reasons:
            print("[kr36] 列表页 curl 仅为壳页或无法解析条目，启动 step4（Playwright+自动滑块）…")
        elif "risk_interstitial" in reasons and not _looks_like_captcha_or_block(html):
            print("[kr36] 检测到验证壳/风控脚本页，启动 step4（Playwright+自动滑块）…")

        recovered = self.fetch(url, log_phase=log_phase)
        if recovered and (not _kr36_needs_playwright_slider_recovery(url, recovered)):
            _kr36_risk_recovery_reset(url)
            return recovered
        return html
