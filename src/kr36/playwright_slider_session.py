"""Reusable 36kr Playwright session: open browser, auto slider if needed.

关浏览器**仅**在：验证通过；或自导航起满 180s 硬超时。曾出现滑块/风控时，不得仅凭「未过滑块」的宽松 HTML 早关。
"""

from __future__ import annotations

import random
import time
from typing import Any

__all__ = ["fetch_36kr_page_html_with_playwright_slider"]


def fetch_36kr_page_html_with_playwright_slider(
    url: str,
    adapter: Any,
    *,
    log_phase: str = "step4",
    skip_cookies: bool = False,
) -> str:
    """
    用 Playwright 打开 URL，遇风控/滑块则自动拖滑块。

    关窗：仅当验证通过，或自打开起已满 180s（硬超时，返回当前 HTML 或空串）。
    ``log_phase`` 仅写日志，不影响逻辑。

    ``skip_cookies=True``：不加载本地 kr36_cookies.json，以全新身份访问。
    用于视频详情页——降级 Cookie 会导致 ByteDance 验证 CDN 拒绝下发验证图，
    而全新会话（无标记）可正常触发并解题。
    """
    from . import source_adapter as sa
    from .slider_captcha import page_suggests_captcha_iframe_or_images
    from .slider_captcha import wait_for_slider_captcha_ui_ready

    if "36kr.com" not in (url or "").lower():
        return ""

    _is_video_page = sa._is_kr36_detail_page_url(url) and "/video/" in url
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
    print(
        "[kr36] 浏览器即将打开。36kr SPA 在加载后通常会做 2-3 次客户端路由跳转，"
        "页面会短暂刷新——这是正常现象，请稍等验证图/滑块出现后再操作。"
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
            # 隐藏 Playwright 自动化特征，防止 ByteDance 验证中心拒绝下发验证图片
            sa._apply_playwright_stealth(context)
            if not skip_cookies:
                sa._load_kr36_cookies_into_browser_context(context)
            else:
                sa._append_kr36_debug_log(
                    f"[kr36] playwright_skip_cookies phase={log_phase!r} url={url}"
                )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(max(500, int(adapter.browser_wait_after_load_ms)))
            # 等待 SPA 客户端路由跳转稳定：36kr 在 load 后常做 2-3 次 hash/path 变更
            # 每隔 1.2s 检测 URL 是否变化，稳定则提前退出，最多等 7.2s
            _url_prev = page.url
            for _settle_i in range(6):
                page.wait_for_timeout(1200)
                _url_curr = page.url
                if _url_curr == _url_prev:
                    break
                _url_prev = _url_curr
            sa._append_kr36_debug_log(
                f"[kr36] playwright_slider_url_settled phase={log_phase!r} "
                f"final_url={page.url!r}"
            )
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
            # 连续 ui_ready=False 的次数；达阈值时延长等待、避免频繁轮询
            _captcha_ui_fail_streak: int = 0
            while time.time() < end:
                try:
                    html = page.content()
                except Exception:
                    html = ""
                age_s = time.time() - t0

                # ── "Please wait..." SPA 加载壳检测 ──────────────────────────────
                # 可见文字极短（≤200字）且含 "please wait"，说明 SPA 的 JS 尚未水化。
                # 此时跳过所有验证码判断，静默等 60~80s 让客户端路由完成，再重判。
                try:
                    _visible_body = page.inner_text("body", timeout=2000).strip()
                except Exception:
                    _visible_body = ""
                if (
                    "please wait" in _visible_body.lower()
                    and len(_visible_body) < 200
                ):
                    _pw_wait_s = random.randint(60, 80)
                    sa._append_kr36_debug_log(
                        f"[kr36] playwright_please_wait_shell phase={log_phase!r} "
                        f"url={url} age_s={age_s:.1f} wait_s={_pw_wait_s}"
                    )
                    print(
                        f"[kr36] 页面仍显示 'Please wait...'，等待 {_pw_wait_s}s 让 SPA 加载完成…"
                        "（请勿手动刷新）"
                    )
                    page.wait_for_timeout(_pw_wait_s * 1000)
                    continue

                need_slider = sa._looks_like_captcha_or_block(
                    html
                ) or page_suggests_captcha_iframe_or_images(page)
                if need_slider:
                    if not saw_captcha_risk:
                        print(
                            "[kr36] 已检测到36kr验证页面，正在等待验证图/滑块加载完成…"
                            "（页面会短暂刷新，属正常现象，请勿手动刷新）"
                        )
                    saw_captcha_risk = True
                    ui_ready = wait_for_slider_captcha_ui_ready(
                        page,
                        timeout_ms=int(adapter.step4_captcha_aware_pre_wait_ms),
                    )
                    sa._append_kr36_debug_log(
                        f"[kr36] playwright_slider_wait_ui phase={log_phase!r} url={url} "
                        f"ui_ready={str(ui_ready).lower()}"
                    )
                    if not ui_ready:
                        # 验证图片仍未加载：不触发解题动作（避免无效拖拽），
                        # 等待一段时间让 ByteDance CDN 恢复后再重试
                        _captcha_ui_fail_streak += 1
                        backoff_s = min(30 * _captcha_ui_fail_streak, 90)
                        sa._append_kr36_debug_log(
                            f"[kr36] playwright_slider_captcha_img_not_ready phase={log_phase!r} "
                            f"url={url} streak={_captcha_ui_fail_streak} backoff_s={backoff_s}"
                        )
                        print(
                            f"[kr36] 验证图片尚未加载（第 {_captcha_ui_fail_streak} 次），"
                            f"等待 {backoff_s}s 后重试，请勿手动刷新页面…"
                        )
                        page.wait_for_timeout(backoff_s * 1000)
                        continue
                    _captcha_ui_fail_streak = 0
                    print("[kr36] 验证图/滑块已就绪，正在自动拖拽解题…")
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
                            url=url,
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
                            url=url,
                        ):
                            sa._append_kr36_debug_log(
                                f"[kr36] playwright_slider_after_solve phase={log_phase!r} url={url} "
                                f"html_length={len(html)}"
                            )
                            if _is_video_page:
                                html = _enrich_html_with_js_initial_state(
                                    page, html, log_phase=log_phase, url=url
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
                    url=url,
                ) and (
                    step4_solved
                    or (
                        sa._is_usable_html(html)
                        and (
                            not sa._kr36_likely_36kr_csr_risk_listing_shell(html)
                            or sa._is_kr36_detail_page_url(url)
                        )
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
                    if _is_video_page:
                        html = _enrich_html_with_js_initial_state(
                            page, html, log_phase=log_phase, url=url
                        )
                    context.close()
                    browser.close()
                    sa._kr36_risk_recovery_reset(url)
                    return html
                page.wait_for_timeout(poll)
                # 详情页（/video/ /p/）提前退出：40s 内未出现验证页，说明 36kr
                # 不打算走验证流程，继续等只会空转；有验证则不受此限制。
                if (
                    not saw_captcha_risk
                    and sa._is_kr36_detail_page_url(url)
                    and (time.time() - t0) > 40.0
                ):
                    sa._append_kr36_debug_log(
                        f"[kr36] playwright_slider_detail_no_captcha_break "
                        f"phase={log_phase!r} url={url} age_s={time.time()-t0:.1f}"
                    )
                    print(
                        "[kr36] 视频/文章详情页 40s 内未出现验证页面，已提前退出浏览器。"
                        "如需更长等待，可调大 browser_verification_timeout_ms。"
                    )
                    break
            try:
                html = page.content()
            except Exception:
                html = ""
            if adapter.persist_browser_cookies:
                sa.save_kr36_cookies(
                    sa.extract_kr36_cookie_values(context.cookies())
                )
            sa._append_kr36_debug_log(
                f"[kr36] playwright_slider_hard_timeout phase={log_phase!r} url={url} "
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


def _enrich_html_with_js_initial_state(page: Any, html: str, *, log_phase: str, url: str) -> str:
    """
    视频页专用：页面加载完成后，通过 JS 直接读取 window.initialState，
    将其序列化后注入到返回的 HTML 中，供 step5_list_video_cdn_urls_from_subpage_html 解析。

    这比依赖 page.content() 更可靠，因为 SPA 的 initialState 由 JS 动态写入，
    page.content() 未必总能拿到最新值。
    """
    import json as _json

    from . import source_adapter as sa

    try:
        state_json: str = page.evaluate(
            "() => { try { return JSON.stringify(window.initialState || null); } catch(e) { return null; } }"
        )
        if not state_json or state_json == "null":
            return html
        # 简单验证：必须含 videoDetail
        parsed = _json.loads(state_json)
        if not isinstance(parsed, dict) or "videoDetail" not in parsed:
            return html
        # 注入到 HTML 末尾，供现有正则/JSON 解析器识别
        injected = f'<script>window.initialState={state_json};</script>'
        if "window.initialState=" in (html or ""):
            return html  # 已有，不重复注入
        enriched = (html or "") + "\n" + injected
        sa._append_kr36_debug_log(
            f"[kr36] playwright_js_initialstate_injected phase={log_phase!r} url={url} "
            f"state_len={len(state_json)}"
        )
        print(f"[kr36] JS 直读 initialState 成功（含 videoDetail），已注入 HTML")
        return enriched
    except Exception as exc:
        sa._append_kr36_debug_log(
            f"[kr36] playwright_js_initialstate_error phase={log_phase!r} url={url} err={exc!r}"
        )
        return html
