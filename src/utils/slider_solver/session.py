"""通用 Playwright 风控会话：开浏览器、检测验证码、自动滑块、关窗与 Cookie 写回。

关浏览器**仅**在：验证通过；或自导航起满 180s 硬超时。曾出现滑块/风控时，不得仅凭「未过滑块」的宽松 HTML 早关。

滑块算法见同包 :mod:`utils.slider_solver.captcha`；编排入口为 :class:`SliderRiskTool`。
"""

from __future__ import annotations

import re
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kr36.source_adapter import Kr36SourceAdapter

__all__ = ["SliderRiskTool", "fetch_page_html_with_playwright_slider"]


def _dump_playwright_failure_snapshot(
    *,
    target_url: str,
    current_url: str,
    log_phase: str,
    error: Exception,
    page: Any | None,
) -> str:
    """
    失败快照：落一份元信息 + 当前页面 HTML，便于定位是风控页还是浏览器错误页。
    """
    try:
        project_root = Path(__file__).resolve().parents[3]
        out_dir = project_root / "data" / "raw" / "kr36_playwright_failures"
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        phase_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (log_phase or "step4")).strip("_") or "step4"
        url_slug = re.sub(r"[^a-zA-Z0-9]+", "_", (target_url or "url")).strip("_")[:48] or "url"
        base = f"{ts}_{phase_slug}_{url_slug}"
        meta_path = out_dir / f"{base}.txt"
        html_path = out_dir / f"{base}.html"

        html = ""
        if page is not None:
            try:
                html = page.content() or ""
            except Exception:
                html = ""

        meta_lines = [
            f"target_url={target_url}",
            f"current_url={current_url}",
            f"log_phase={log_phase}",
            f"error_type={type(error).__name__}",
            f"error={error}",
            f"html_length={len(html)}",
            f"snapshot_html={html_path.name if html else ''}",
        ]
        meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
        if html:
            html_path.write_text(html, encoding="utf-8")
        return str(meta_path)
    except Exception:
        return ""


def fetch_page_html_with_playwright_slider(
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
    from kr36 import source_adapter as sa
    from .captcha import page_suggests_captcha_iframe_or_images
    from .captcha import wait_for_slider_captcha_ui_ready

    if "36kr.com" not in (url or "").lower():
        return ""

    _is_video_page = sa._is_kr36_video_detail_page_url(url)
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        sa._append_kr36_debug_log(
            f"[slider] playwright_slider_skip reason=missing_playwright phase={log_phase!r}"
        )
        return ""
    max_rr = int(getattr(adapter, "risk_max_recovery_rounds_per_url", 3) or 3)
    if not sa._kr36_risk_recovery_try_begin(url, max_rr):
        sa._append_kr36_debug_log(
            f"[slider] playwright_slider_skip reason=risk_recovery_limit url={url} "
            f"max_rounds={max_rr} phase={log_phase!r}"
        )
        print(
            f"[slider] 该 URL 风控恢复已达上限（{max_rr} 次），跳过 Playwright/滑块，下一条：{url}"
        )
        return ""
    headless = adapter.risk_verification_playwright_mode == "headless"
    timeout_ms = max(35000, int(adapter.browser_timeout_ms))
    poll = max(300, int(adapter.browser_verification_poll_ms))
    # 与业务约定：本会话最多保持 180s，超时必须关窗（可返回当时快照）
    session_hard_max_s = 180.0
    ch_note = (adapter.playwright_chromium_channel or "bundled").strip() or "bundled"
    print(
        f"[slider] Playwright 滑块会话 phase={log_phase!r} headless={str(headless).lower()} "
        f"channel={ch_note!r} url={url}"
    )
    print(
        "[slider] 浏览器即将打开。SPA 在加载后通常会做 2-3 次客户端路由跳转，"
        "页面会短暂刷新——这是正常现象，请稍等验证图/滑块出现后再操作。"
    )
    sa._append_kr36_debug_log(
        f"[slider] playwright_slider_start phase={log_phase!r} url={url} "
        f"headless={str(headless).lower()} channel={ch_note!r}"
    )
    page: Any | None = None
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
                    f"[slider] playwright_skip_cookies phase={log_phase!r} url={url}"
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
                f"[slider] playwright_slider_url_settled phase={log_phase!r} "
                f"final_url={page.url!r}"
            )
            t0 = time.time()
            step4_solved = False
            sa._append_kr36_debug_log(
                f"[slider] playwright_slider_preloop phase={log_phase!r} url={url} "
                f"wait_after_load_ms={int(adapter.browser_wait_after_load_ms)} "
                f"slider_ui_ready_max_ms={int(adapter.step4_captcha_aware_pre_wait_ms)}"
            )
            end = time.time() + session_hard_max_s
            html = ""
            saw_captcha_risk: bool = False
            # 连续 ui_ready=False 的次数；达阈值时延长等待、避免频繁轮询
            _captcha_ui_fail_streak: int = 0
            # 连续命中「Please wait…」短壳次数（站点可能风控软刷新，不等同于 SPA 水化慢）
            _please_wait_streak: int = 0
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
                    _please_wait_streak += 1
                    _pw_wait_s = random.randint(60, 80)
                    sa._append_kr36_debug_log(
                        f"[slider] playwright_please_wait_shell phase={log_phase!r} "
                        f"url={url} age_s={age_s:.1f} wait_s={_pw_wait_s} "
                        f"streak={_please_wait_streak} page_url={page.url!r}"
                    )
                    print(
                        f"[slider] 页面仍显示 'Please wait...'，等待 {_pw_wait_s}s 让 SPA 加载完成…"
                        "（请勿手动刷新）"
                    )
                    if _please_wait_streak >= 2:
                        print(
                            "[slider] 已连续多次停留在此短页：若你观察到地址栏/页面在反复刷新，"
                            "多为站点风控或挑战页循环，而非单纯「加载慢」。"
                            "可尝试：有头模式、有效 Cookie（kr36_cookies.json）、降低自动化特征；"
                            "必要时人工完成一次验证后再跑脚本。"
                        )
                    page.wait_for_timeout(_pw_wait_s * 1000)
                    continue

                _please_wait_streak = 0
                need_slider = sa._looks_like_captcha_or_block(
                    html
                ) or page_suggests_captcha_iframe_or_images(page)
                if need_slider:
                    if not saw_captcha_risk:
                        print(
                            "[slider] 已检测到验证页面，正在等待验证图/滑块加载完成…"
                            "（页面会短暂刷新，属正常现象，请勿手动刷新）"
                        )
                    saw_captcha_risk = True
                    ui_ready = wait_for_slider_captcha_ui_ready(
                        page,
                        timeout_ms=int(adapter.step4_captcha_aware_pre_wait_ms),
                    )
                    sa._append_kr36_debug_log(
                        f"[slider] playwright_slider_wait_ui phase={log_phase!r} url={url} "
                        f"ui_ready={str(ui_ready).lower()}"
                    )
                    if not ui_ready:
                        # 验证图片仍未加载：不触发解题动作（避免无效拖拽），
                        # 等待一段时间让 ByteDance CDN 恢复后再重试
                        _captcha_ui_fail_streak += 1
                        backoff_s = min(30 * _captcha_ui_fail_streak, 90)
                        sa._append_kr36_debug_log(
                            f"[slider] playwright_slider_captcha_img_not_ready phase={log_phase!r} "
                            f"url={url} streak={_captcha_ui_fail_streak} backoff_s={backoff_s}"
                        )
                        print(
                            f"[slider] 验证图片尚未加载（第 {_captcha_ui_fail_streak} 次），"
                            f"等待 {backoff_s}s 后重试，请勿手动刷新页面…"
                        )
                        page.wait_for_timeout(backoff_s * 1000)
                        continue
                    _captcha_ui_fail_streak = 0
                    print("[slider] 验证图/滑块已就绪，正在自动拖拽解题…")
                    solved = sa._kr36_try_solve_slider_captcha(page)
                    if solved:
                        step4_solved = True
                    sa._append_kr36_debug_log(
                        f"[slider] playwright_slider_attempt phase={log_phase!r} url={url} "
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
                                    f"[slider] playwright_slider_csr_list_ready phase={log_phase!r} url={url} selector={_csr_selector!r}"
                                )
                            except Exception:
                                # 超时也继续：可能确实无结果（zero-hits 页面）
                                sa._append_kr36_debug_log(
                                    f"[slider] playwright_slider_csr_list_wait_timeout phase={log_phase!r} url={url} selector={_csr_selector!r}"
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
                                f"[slider] playwright_slider_after_solve phase={log_phase!r} url={url} "
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
                            print(f"[slider] 验证通过，浏览器立即关闭 phase={log_phase!r}")
                            sa._append_kr36_debug_log(
                                f"[slider] playwright_slider_close_on_pass phase={log_phase!r} url={url}"
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
                        f"[slider] playwright_slider_ok phase={log_phase!r} url={url} "
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
                        f"[slider] playwright_slider_detail_no_captcha_break "
                        f"phase={log_phase!r} url={url} age_s={time.time()-t0:.1f}"
                    )
                    print(
                        "[slider] 视频/文章详情页 40s 内未出现验证页面，已提前退出浏览器。"
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
                f"[slider] playwright_slider_hard_timeout phase={log_phase!r} url={url} "
                f"html_length={len(html or '')} saw_captcha_risk={str(saw_captcha_risk).lower()}"
            )
            context.close()
            browser.close()
            sa._kr36_risk_recovery_reset(url)
            return html or ""
    except (PlaywrightTimeoutError, PlaywrightError) as error:
        current_url = ""
        if page is not None:
            try:
                current_url = str(page.url or "")
            except Exception:
                current_url = ""
        snapshot_path = _dump_playwright_failure_snapshot(
            target_url=url,
            current_url=current_url,
            log_phase=log_phase,
            error=error,
            page=page,
        )
        sa._append_kr36_debug_log(
            f"[slider] playwright_slider_error phase={log_phase!r} url={url} error={error}"
        )
        if snapshot_path:
            sa._append_kr36_debug_log(
                f"[slider] playwright_snapshot_saved phase={log_phase!r} url={url} path={snapshot_path}"
            )
            print(f"[slider] 失败快照已保存: {snapshot_path}", flush=True)
        print(
            f"[slider] Playwright 异常 phase={log_phase!r} url={url} "
            f"err={type(error).__name__}: {error}",
            flush=True,
        )
    except Exception as error:
        current_url = ""
        if page is not None:
            try:
                current_url = str(page.url or "")
            except Exception:
                current_url = ""
        snapshot_path = _dump_playwright_failure_snapshot(
            target_url=url,
            current_url=current_url,
            log_phase=log_phase,
            error=error,
            page=page,
        )
        sa._append_kr36_debug_log(
            f"[slider] playwright_slider_unexpected phase={log_phase!r} url={url} error={error}"
        )
        if snapshot_path:
            sa._append_kr36_debug_log(
                f"[slider] playwright_snapshot_saved phase={log_phase!r} url={url} path={snapshot_path}"
            )
            print(f"[slider] 失败快照已保存: {snapshot_path}", flush=True)
        print(
            f"[slider] Playwright 非预期异常 phase={log_phase!r} url={url} "
            f"err={type(error).__name__}: {error}",
            flush=True,
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

    from kr36 import source_adapter as sa

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
            f"[slider] playwright_js_initialstate_injected phase={log_phase!r} url={url} "
            f"state_len={len(state_json)}"
        )
        print(f"[slider] JS 直读 initialState 成功（含 videoDetail），已注入 HTML")
        return enriched
    except Exception as exc:
        sa._append_kr36_debug_log(
            f"[slider] playwright_js_initialstate_error phase={log_phase!r} url={url} err={exc!r}"
        )
        return html


class SliderRiskTool:
    """
    遇风控/滑块时的统一封装：Playwright + 自动滑块 + 回写 Cookie（与具体站点适配器配合）。

    - ``fetch``：打开 URL，遇滑块则解，返回 HTML。
    - ``maybe_recover``：HTML 已判为风控时调 ``fetch``，成功则重置该 URL 恢复计数。
    """

    __slots__ = ("_adapter",)

    def __init__(self, adapter: "Kr36SourceAdapter") -> None:
        self._adapter: Any = adapter

    def fetch(self, url: str, *, log_phase: str = "step4", skip_cookies: bool = False) -> str:
        return fetch_page_html_with_playwright_slider(
            url, self._adapter, log_phase=log_phase, skip_cookies=skip_cookies
        )

    def maybe_recover(self, url: str, html: str, *, log_phase: str) -> str:
        from kr36.source_adapter import (
            _append_kr36_debug_log,
            _kr36_listing_requires_step4_recovery,
            _kr36_needs_playwright_slider_recovery,
            _kr36_risk_recovery_reset,
            _kr36_search_html_has_risk_interstitial,
            _looks_like_captcha_or_block,
        )

        if self._adapter.http_only_mode:
            if html and _kr36_needs_playwright_slider_recovery(url, html):
                _append_kr36_debug_log(
                    f"[slider] step4_recover_skipped reason=http_only url={url} phase={log_phase}"
                )
                print(
                    "[slider] 已跳过 step4（Playwright+自动滑块）：`sources.kr36.http_only_mode` 为 true。"
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
            f"[slider] step4_recover_trigger url={url} phase={log_phase} reason={reason_s}"
        )
        if "listing_csr_shell" in reasons and "captcha_or_block_copy" not in reasons:
            print("[slider] 列表页 curl 仅为壳页或无法解析条目，启动 step4（Playwright+自动滑块）…")
        elif "risk_interstitial" in reasons and not _looks_like_captcha_or_block(html):
            print("[slider] 检测到验证壳/风控脚本页，启动 step4（Playwright+自动滑块）…")

        recovered = self.fetch(url, log_phase=log_phase)
        if recovered and (not _kr36_needs_playwright_slider_recovery(url, recovered)):
            _kr36_risk_recovery_reset(url)
            return recovered
        return html
