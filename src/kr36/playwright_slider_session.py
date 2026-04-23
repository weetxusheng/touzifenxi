"""Reusable 36kr Playwright session: open browser, auto slider if needed.

关浏览器**仅**在：验证通过；或自导航起满 180s 硬超时。曾出现滑块/风控时，不得仅凭「未过滑块」的宽松 HTML 早关。
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["fetch_36kr_page_html_with_playwright_slider"]


def fetch_36kr_page_html_with_playwright_slider(
    url: str,
    adapter: Any,
    *,
    log_phase: str = "step4",
) -> str:
    """
    用 Playwright 打开 URL，遇风控/滑块则自动拖滑块。

    关窗：仅当验证通过，或自打开起已满 180s（硬超时，返回当前 HTML 或空串）。
    ``log_phase`` 仅写日志，不影响逻辑。
    """
    from . import source_adapter as sa
    from .slider_captcha import page_suggests_captcha_iframe_or_images
    from .slider_captcha import wait_for_slider_captcha_ui_ready

    if "36kr.com" not in (url or "").lower():
        return ""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        sa._append_kr36_debug_log(
            f"[kr36] playwright_slider_skip reason=missing_playwright phase={log_phase!r}"
        )
        return ""
    max_rr = int(getattr(adapter, "risk_max_recovery_rounds_per_url", 3) or 3)
    if not sa._kr36_risk_recovery_try_begin(url, max_rr):
        sa._append_kr36_debug_log(
            f"[kr36] playwright_slider_skip reason=risk_recovery_limit url={url} "
            f"max_rounds={max_rr} phase={log_phase!r}"
        )
        print(
            f"[kr36] 该 URL 风控恢复已达上限（{max_rr} 次），跳过 Playwright/滑块，下一条：{url}"
        )
        return ""
    headless = adapter.risk_verification_playwright_mode == "headless"
    timeout_ms = max(35000, int(adapter.browser_timeout_ms))
    poll = max(300, int(adapter.browser_verification_poll_ms))
    # 与业务约定：本会话最多保持 180s，超时必须关窗（可返回当时快照）
    session_hard_max_s = 180.0
    ch_note = (adapter.playwright_chromium_channel or "bundled").strip() or "bundled"
    print(
        f"[kr36] Playwright 滑块会话 phase={log_phase!r} headless={str(headless).lower()} "
        f"channel={ch_note!r} url={url}"
    )
    sa._append_kr36_debug_log(
        f"[kr36] playwright_slider_start phase={log_phase!r} url={url} "
        f"headless={str(headless).lower()} channel={ch_note!r}"
    )
    try:
        with sync_playwright() as playwright:
            lkw = adapter._playwright_chromium_launch_kwargs(
                headless=headless, incognito=False
            )
            browser = adapter._playwright_launch_chromium(playwright, lkw)
            context = browser.new_context(
                locale="zh-CN",
                user_agent=sa.KR36_DEFAULT_USER_AGENT,
                viewport={"width": 1440, "height": 1024},
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
            )
            sa._load_kr36_cookies_into_browser_context(context)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(max(500, int(adapter.browser_wait_after_load_ms)))
            t0 = time.time()
            step4_solved = False
            sa._append_kr36_debug_log(
                f"[kr36] playwright_slider_preloop phase={log_phase!r} url={url} "
                f"wait_after_load_ms={int(adapter.browser_wait_after_load_ms)} "
                f"slider_ui_ready_max_ms={int(adapter.step4_captcha_aware_pre_wait_ms)}"
            )
            end = time.time() + session_hard_max_s
            html = ""
            saw_captcha_risk: bool = False
            while time.time() < end:
                try:
                    html = page.content()
                except Exception:
                    html = ""
                age_s = time.time() - t0
                need_slider = sa._looks_like_captcha_or_block(
                    html
                ) or page_suggests_captcha_iframe_or_images(page)
                if need_slider:
                    saw_captcha_risk = True
                    ui_ready = wait_for_slider_captcha_ui_ready(
                        page,
                        timeout_ms=int(adapter.step4_captcha_aware_pre_wait_ms),
                    )
                    sa._append_kr36_debug_log(
                        f"[kr36] playwright_slider_wait_ui phase={log_phase!r} url={url} "
                        f"ui_ready={str(ui_ready).lower()}"
                    )
                    solved = sa._kr36_try_solve_slider_captcha(page)
                    if solved:
                        step4_solved = True
                    sa._append_kr36_debug_log(
                        f"[kr36] playwright_slider_attempt phase={log_phase!r} url={url} "
                        f"attempted={str(solved).lower()}"
                    )
                    if solved:
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
                        try:
                            page.wait_for_load_state("load", timeout=15000)
                        except Exception:
                            pass
                        page.wait_for_timeout(3000)
                        # 搜索/活动/专题页（CSR）：验证通过后需等 JS 渲染出内容，
                        # 否则关窗时拿到的是中间跳转页而非实际结果页。
                        if sa._is_kr36_search_articles_url(url):
                            _csr_selector = ".kr-search-result-list-main"
                        elif sa._kr36_activity_listing_index_url(url):
                            _csr_selector = ".activity-item, .kr-activity-list, .activity-list"
                        elif sa._kr36_topics_listing_index_url(url) or sa._is_kr36_topic_detail_url(url):
                            _csr_selector = ".topic-list-item, .topic-item, .kr-topic-item"
                        else:
                            _csr_selector = ""
                        if _csr_selector:
                            try:
                                page.wait_for_selector(_csr_selector, timeout=12000)
                                sa._append_kr36_debug_log(
                                    f"[kr36] playwright_slider_csr_list_ready phase={log_phase!r} url={url} selector={_csr_selector!r}"
                                )
                            except Exception:
                                # 超时也继续：可能确实无结果（zero-hits 页面）
                                sa._append_kr36_debug_log(
                                    f"[kr36] playwright_slider_csr_list_wait_timeout phase={log_phase!r} url={url} selector={_csr_selector!r}"
                                )
                                page.wait_for_timeout(3000)
                        if adapter.persist_browser_cookies:
                            sa.save_kr36_cookies(
                                sa.extract_kr36_cookie_values(context.cookies())
                            )
                        try:
                            html = page.content()
                        except Exception:
                            html = ""
                        if not adapter._step4_playwright_allows_return_html(
                            html,
                            age_s=time.time() - t0,
                            after_slider_attempt=True,
                        ):
                            page.wait_for_timeout(2000)
                            try:
                                html = page.content()
                            except Exception:
                                html = ""
                        if adapter._step4_playwright_allows_return_html(
                            html,
                            age_s=time.time() - t0,
                            after_slider_attempt=True,
                        ):
                            sa._append_kr36_debug_log(
                                f"[kr36] playwright_slider_after_solve phase={log_phase!r} url={url} "
                                f"html_length={len(html)}"
                            )
                            if adapter.persist_browser_cookies:
                                sa.save_kr36_cookies(
                                    sa.extract_kr36_cookie_values(context.cookies())
                                )
                            print(f"[kr36] 验证通过，浏览器立即关闭 phase={log_phase!r}")
                            sa._append_kr36_debug_log(
                                f"[kr36] playwright_slider_close_on_pass phase={log_phase!r} url={url}"
                            )
                            context.close()
                            browser.close()
                            sa._kr36_risk_recovery_reset(url)
                            return html
                elif (not need_slider) and adapter._step4_playwright_allows_return_html(
                    html,
                    age_s=age_s,
                    after_slider_attempt=step4_solved,
                ) and (
                    step4_solved
                    or (
                        sa._is_usable_html(html)
                        and not sa._kr36_likely_36kr_csr_risk_listing_shell(html)
                        and not page_suggests_captcha_iframe_or_images(page)
                    )
                ):
                    if adapter.persist_browser_cookies:
                        sa.save_kr36_cookies(
                            sa.extract_kr36_cookie_values(context.cookies())
                        )
                    sa._append_kr36_debug_log(
                        f"[kr36] playwright_slider_ok phase={log_phase!r} url={url} "
                        f"html_length={len(html)}"
                    )
                    context.close()
                    browser.close()
                    sa._kr36_risk_recovery_reset(url)
                    return html
                page.wait_for_timeout(poll)
            try:
                html = page.content()
            except Exception:
                html = ""
            if adapter.persist_browser_cookies:
                sa.save_kr36_cookies(
                    sa.extract_kr36_cookie_values(context.cookies())
                )
            sa._append_kr36_debug_log(
                f"[kr36] playwright_slider_hard_180s phase={log_phase!r} url={url} "
                f"html_length={len(html or '')} saw_captcha_risk={str(saw_captcha_risk).lower()}"
            )
            context.close()
            browser.close()
            sa._kr36_risk_recovery_reset(url)
            return html or ""
    except (PlaywrightTimeoutError, PlaywrightError) as error:
        sa._append_kr36_debug_log(
            f"[kr36] playwright_slider_error phase={log_phase!r} url={url} error={error}"
        )
    except Exception as error:
        sa._append_kr36_debug_log(
            f"[kr36] playwright_slider_unexpected phase={log_phase!r} url={url} error={error}"
        )
    return ""
