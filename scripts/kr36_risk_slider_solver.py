"""36Kr 风控页易盾拼图滑块：自动识别缺口位置并拖动（Playwright + OpenCV）。

由 `kr36.source_adapter` 在命中风控时通过 `risk_verification_command` 调用。
环境变量：

- KR36_RISK_URL: 当前风控/验证页 URL（由适配器注入）
- KR36_HEADED_LOAD_WAIT_MS: 有头（agent-browser）关闭窗口前等待页面加载/网络空闲的最长毫秒数，默认 120000

依赖（需单独安装）::

    pip install opencv-python-headless

示例（runtime 配置）::

    "risk_verification_command": "python scripts/kr36_risk_slider_solver.py"

若在仓库根目录外调用，请使用绝对路径并保证 PYTHONPATH 含 `src`，或::

    set PYTHONPATH=src && python /path/to/scripts/kr36_risk_slider_solver.py

退出码：0 易盾拼图已处理或页面已正常；2 缺少依赖；3 未见易盾拼图但页面仍像拦截/验证（需手动或其它验证方式）。
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _wait_for_page_ready_before_headed_close(page: Any, *, headless: bool) -> None:
    """有头模式：关闭浏览器前等待当前页 load + networkidle（网页刷新/加载告一段落）。"""
    if headless:
        return
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return
    max_ms = max(3000, int(os.environ.get("KR36_HEADED_LOAD_WAIT_MS", "120000") or "120000"))
    print("[kr36-slider] agent-browser：等待页面加载完成（load → networkidle）后再关闭窗口…", flush=True)
    try:
        page.wait_for_load_state("load", timeout=max_ms)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=max_ms)
    except PlaywrightTimeoutError:
        print(
            "[kr36-slider] 等待 networkidle 超时，继续关闭窗口。",
            file=sys.stderr,
            flush=True,
        )


def _load_cv2_np():
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np  # type: ignore[import-untyped]
    except ImportError as exc:
        print(
            "[kr36-slider] 需要 opencv-python-headless 与 numpy。"
            "请执行: pip install opencv-python-headless",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return cv2, np


def _piece_to_bgr(cv2: Any, np: Any, piece: Any) -> Any:
    if piece is None or piece.size == 0:
        return piece
    if piece.ndim == 2:
        return cv2.cvtColor(piece, cv2.COLOR_GRAY2BGR)
    if piece.shape[2] == 4:
        alpha = piece[:, :, 3:4].astype(np.float32) / 255.0
        rgb = piece[:, :, :3].astype(np.float32)
        white = np.full_like(rgb, 255.0)
        blended = (rgb * alpha + white * (1.0 - alpha)).astype(np.uint8)
        return blended
    return piece


def match_slide_x(cv2: Any, np: Any, bg_bgr: Any, piece_bgr: Any) -> tuple[int, float]:
    """在背景上匹配拼图块，返回最佳匹配左上角 x 与得分。"""
    piece_bgr = _piece_to_bgr(cv2, np, piece_bgr)
    bg_gray = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece_bgr, cv2.COLOR_BGR2GRAY)
    ph, pw = piece_gray.shape[:2]
    bh, bw = bg_gray.shape[:2]
    if ph >= bh or pw >= bw:
        scale = min((bh - 1) / ph, (bw - 1) / pw) * 0.98
        if scale < 1.0:
            piece_gray = cv2.resize(
                piece_gray,
                (max(1, int(pw * scale)), max(1, int(ph * scale))),
                interpolation=cv2.INTER_AREA,
            )
    res = cv2.matchTemplate(bg_gray, piece_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return int(max_loc[0]), float(max_val)


def tracks_for_distance(distance: float) -> tuple[list[float], list[float]]:
    """分段加减速 + 少量回拉，贴近常见自动化轨迹。"""
    value = round(random.uniform(0.55, 0.75), 2)
    distance = float(distance) + random.uniform(6.0, 14.0)
    v, t, total = 0.0, 0.3, 0.0
    plus: list[float] = []
    mid = distance * value
    while total < distance:
        if total < mid:
            a = round(random.uniform(2.5, 3.5), 1)
        else:
            a = -round(random.uniform(2.0, 3.0), 1)
        s = v * t + 0.5 * a * (t**2)
        v = v + a * t
        total += s
        plus.append(float(round(s, 2)))
    reduce = [-6.0, -4.0, -5.0, -3.0]
    return plus, reduce


def _natural_size(locator: Any) -> tuple[int, int]:
    handle = locator.element_handle()
    if not handle:
        return 0, 0
    return handle.evaluate("el => [el.naturalWidth || el.width, el.naturalHeight || el.height]")


def _find_yidun_frame(page: Any):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    selectors = [
        "img.yidun_bg-img",
        "img[class*='yidun_bg']",
        ".yidun_panel img",
    ]
    deadline = time.time() + 90.0
    while time.time() < deadline:
        for sel in selectors:
            try:
                if page.locator(sel).count() > 0:
                    return page, page.locator(sel).first
            except PlaywrightTimeoutError:
                pass
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            for sel in selectors:
                try:
                    loc = frame.locator(sel)
                    if loc.count() > 0:
                        return frame, loc.first
                except PlaywrightTimeoutError:
                    pass
        time.sleep(0.35)
    return None, None


def _pick_drag_handle(frame: Any):
    ordered = [
        ".yidun_slider__button",
        ".yidun_slider",
        "img.yidun_jigsaw",
        ".yidun_jigsaw",
    ]
    for sel in ordered:
        loc = frame.locator(sel)
        if loc.count() == 0:
            continue
        first = loc.first
        try:
            if first.is_visible(timeout=500):
                return first
        except Exception:
            continue
    return None


def _captcha_still_present(page: Any) -> bool:
    for frame in page.frames:
        try:
            if frame.locator("img.yidun_bg-img").count() > 0:
                return True
        except Exception:
            continue
    return False


def run_solver(*, url: str, headless: bool, timeout_ms: int, max_rounds: int) -> int:
    cv2, np = _load_cv2_np()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print("[kr36-slider] 未安装 playwright。", file=sys.stderr)
        raise SystemExit(2) from exc

    from kr36.source_adapter import (
        KR36_DEFAULT_USER_AGENT,
        _looks_like_captcha_or_block,
        extract_kr36_cookie_values,
        load_kr36_cookies,
        save_kr36_cookies,
        _load_kr36_cookies_into_browser_context,
    )

    full_ua = (
        f"{KR36_DEFAULT_USER_AGENT} AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            user_agent=full_ua,
            viewport={"width": 1440, "height": 1024},
        )
        _load_kr36_cookies_into_browser_context(context)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(800)

        def close_browser() -> None:
            _wait_for_page_ready_before_headed_close(page, headless=headless)
            browser.close()

        for round_i in range(1, max(1, max_rounds) + 1):
            if not _captcha_still_present(page):
                snapshot = page.content()
                if _looks_like_captcha_or_block(snapshot):
                    print(
                        "[kr36-slider] 页面仍像拦截/验证页，但未发现易盾拼图（可能为其它验证码）。"
                        "无法自动拖动，退出码 3。",
                        file=sys.stderr,
                    )
                    close_browser()
                    return 3
                print("[kr36-slider] 页面上已无易盾拼图，认为通过。")
                close_browser()
                return 0

            frame, bg_loc = _find_yidun_frame(page)
            if frame is None or bg_loc is None:
                print("[kr36-slider] 未找到易盾拼图节点（img.yidun_bg-img），可能非滑块风控。", file=sys.stderr)
                close_browser()
                return 1

            piece_sel = frame.locator("img.yidun_jigsaw, img[class*='yidun_jigsaw']")
            if piece_sel.count() == 0:
                print("[kr36-slider] 缺少拼图块 img.yidun_jigsaw。", file=sys.stderr)
                close_browser()
                return 1
            piece_loc = piece_sel.first

            try:
                bg_src = bg_loc.get_attribute("src") or ""
                pc_src = piece_loc.get_attribute("src") or ""
            except Exception as exc:
                print(f"[kr36-slider] 读取图片地址失败: {exc}", file=sys.stderr)
                close_browser()
                return 1

            if not bg_src or not pc_src:
                print("[kr36-slider] 背景或拼图 src 为空。", file=sys.stderr)
                close_browser()
                return 1

            try:
                r_bg = context.request.get(bg_src, timeout=timeout_ms)
                r_pc = context.request.get(pc_src, timeout=timeout_ms)
                bg_buf = r_bg.body()
                pc_buf = r_pc.body()
            except Exception as exc:
                print(f"[kr36-slider] 下载验证图片失败: {exc}", file=sys.stderr)
                close_browser()
                return 1

            bg_arr = np.frombuffer(bg_buf, dtype=np.uint8)
            pc_arr = np.frombuffer(pc_buf, dtype=np.uint8)
            bg_bgr = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)
            pc_bgr = cv2.imdecode(pc_arr, cv2.IMREAD_UNCHANGED)
            if bg_bgr is None or pc_bgr is None:
                print("[kr36-slider] 图片解码失败。", file=sys.stderr)
                close_browser()
                return 1

            mx, score = match_slide_x(cv2, np, bg_bgr, pc_bgr)
            print(f"[kr36-slider] 模板匹配 x={mx} score={score:.4f} (round {round_i})")

            nat_w, _ = _natural_size(bg_loc)
            box = bg_loc.bounding_box()
            if not box or not nat_w:
                print("[kr36-slider] 无法读取背景图布局尺寸。", file=sys.stderr)
                close_browser()
                return 1
            scale = float(box["width"]) / float(nat_w)
            drag_px = mx * scale

            handle = _pick_drag_handle(frame)
            if handle is None:
                print("[kr36-slider] 未找到可拖动把手。", file=sys.stderr)
                close_browser()
                return 1

            hbox = handle.bounding_box()
            if not hbox:
                print("[kr36-slider] 把手不可见。", file=sys.stderr)
                close_browser()
                return 1

            cx = hbox["x"] + hbox["width"] / 2
            cy = hbox["y"] + hbox["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            plus, minus = tracks_for_distance(drag_px)
            acc_x = cx
            for dx in plus:
                acc_x += dx
                wobble = random.uniform(-1.2, 1.2)
                page.mouse.move(acc_x, cy + wobble, steps=1)
            for dx in minus:
                acc_x += dx
                page.mouse.move(acc_x, cy + random.uniform(-0.8, 0.8), steps=1)
            page.wait_for_timeout(random.randint(80, 180))
            page.mouse.up()
            page.wait_for_timeout(1500)

            if not _captcha_still_present(page):
                merged = extract_kr36_cookie_values(context.cookies())
                if merged:
                    existing = load_kr36_cookies()
                    existing.update(merged)
                    save_kr36_cookies(existing)
                print("[kr36-slider] 验证控件已消失，认为通过。")
                close_browser()
                return 0

            refresh = frame.locator(".yidun_refresh, .yidun_refresh__icon")
            if refresh.count() > 0 and round_i < max_rounds:
                try:
                    refresh.first.click(timeout=2000)
                    page.wait_for_timeout(900)
                except Exception:
                    pass

        print("[kr36-slider] 多轮尝试后仍存在拼图，请人工处理。", file=sys.stderr)
        close_browser()
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="36Kr 风控易盾滑块自动拖动（KR36_RISK_URL）。")
    parser.add_argument(
        "--url",
        default=os.environ.get("KR36_RISK_URL", "").strip(),
        help="风控页 URL（默认读取环境变量 KR36_RISK_URL）",
    )
    parser.add_argument("--headless", action="store_true", help="无头模式（调试用）")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument("--max-rounds", type=int, default=4)
    args = parser.parse_args()
    if not args.url:
        print("[kr36-slider] 缺少 URL：请设置 KR36_RISK_URL 或传入 --url。", file=sys.stderr)
        raise SystemExit(2)
    code = run_solver(
        url=args.url,
        headless=args.headless,
        timeout_ms=args.timeout_ms,
        max_rounds=args.max_rounds,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
