"""
36Kr 滑块验证码精准求解器。

对外接口：
    solve_slider_captcha(page, max_attempts=3) -> bool
        - page: Playwright Page 对象（已在验证码页面）
        - max_attempts: 最多尝试次数；每次失败后等待验证码刷新再重试
        - 返回 True 表示至少发起过一次拖拽
        - 停滑后短等待即判定：先 innerText(验证通过/成功)→轨道绿条截图→
          扩大区截图(含提示条+可选 pytesseract)→DOM/iframe 移除 等，未过则短轮询再重试

算法核心（cv2_find_hole）：
    缺口 = 把拼图块从背景扣掉后做灰色半透明填充：
        bg_at_hole ≈ piece_pixel * a + gray * (1-a)  — 线性变换
    拼图块与缺口处背景像素 Pearson 相关必然最高。
    用 TM_CCOEFF_NORMED + alpha_mask 全图搜索一次即得缺口位置。
    形状/方向/大小全由 alpha 蒙版决定，无需任何颜色阈值。
"""
from __future__ import annotations

import base64
import random
import re
import time
from typing import Any


# ── JS 片段 ──────────────────────────────────────────────────────────────────

# 取 iframe 内所有 img 的尺寸 + 位置 + naturalWidth
_JS_GET_IMAGES = """
() => {
    const imgs = [];
    for (const el of document.querySelectorAll('img')) {
        const r = el.getBoundingClientRect();
        imgs.push({
            src: el.src || el.currentSrc || '',
            cls: (el.className||'').toString().slice(0,80),
            w: Math.round(r.width), h: Math.round(r.height),
            x: Math.round(r.x),    y: Math.round(r.y),
            nw: el.naturalWidth || 0,
        });
    }
    return imgs;
}
"""

# 把已加载的 img 元素像素 dump 成 base64 PNG（canvas 读取）
_JS_IMG_TO_B64 = """
(sel) => {
    const el = document.querySelector(sel);
    if (!el || !el.naturalWidth) return null;
    const c = document.createElement('canvas');
    c.width  = el.naturalWidth;
    c.height = el.naturalHeight;
    c.getContext('2d').drawImage(el, 0, 0);
    return c.toDataURL('image/png').split(',')[1];
}
"""

# 等待背景图和拼图块都已渲染（naturalWidth > 0）
_JS_WAIT_NATURAL_WIDTH = """
() => {
    const bg = document.querySelector('img.captcha-verify-image');
    const sl = document.querySelector(
        'img.captcha-verify-image-slide, img[class*="slide"]');
    return !!(bg && bg.naturalWidth > 0 && sl && sl.naturalWidth > 0);
}
"""

# ByteDance 惯例：dragger-item 在 iframe 内的 x 偏移
_DRAGGER_IFRAME_X: int = 20

_IFRAME_SELS = (
    "iframe[src*='bytedance']",
    "iframe[src*='verifycenter']",
    "iframe",
)
_BTN_SEL = ".dragger-item, [class*='dragger-item']"


# ── 图片下载 ──────────────────────────────────────────────────────────────────

def _download_image(url: str) -> bytes | None:
    """HTTP 直接下载验证码图片，绕开 Playwright 灰图问题。"""
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        if r.status_code == 200 and len(r.content) > 500:
            return r.content
        print(f"[captcha] 下载失败 status={r.status_code} url={url[:80]}")
    except Exception as e:
        print(f"[captcha] 下载图片失败: {e}")
    return None


# ── CV2 缺口定位 ──────────────────────────────────────────────────────────────

def cv2_find_hole(bg_bytes: bytes, piece_bytes: bytes | None = None) -> int | None:
    """
    在背景图里找目标缺口中心 x（图片像素坐标）。

    有 piece_bytes 时：TM_CCOEFF_NORMED + alpha_mask 精准匹配。
    无 piece_bytes 时：Canny 轮廓 fallback。
    """
    try:
        import cv2
        import numpy as np

        buf = np.frombuffer(bg_bytes, np.uint8)
        bg  = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if bg is None:
            return None
        ih, iw = bg.shape[:2]
        match_x: int | None = None

        if piece_bytes:
            pbuf  = np.frombuffer(piece_bytes, np.uint8)
            piece = cv2.imdecode(pbuf, cv2.IMREAD_UNCHANGED)

            if piece is None:
                print("[captcha] 拼图块解码失败，fallback")
                piece_bytes = None
            else:
                # 提取 alpha 蒙版
                if piece.ndim == 3 and piece.shape[2] == 4:
                    alpha_ch  = piece[:, :, 3]
                    piece_bgr = piece[:, :, :3].copy()
                else:
                    piece_bgr = piece.copy()
                    pg = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
                    alpha_ch = np.where(pg < 240, 255, 0).astype(np.uint8)

                mask_bin = (alpha_ch > 30).astype(np.uint8) * 255
                ph, pw   = mask_bin.shape[:2]

                if int(mask_bin.sum() // 255) < 50:
                    print("[captcha] alpha 蒙版有效像素太少，fallback")
                    piece_bytes = None
                else:
                    bg_gray    = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
                    piece_gray = cv2.cvtColor(piece_bgr, cv2.COLOR_BGR2GRAY)

                    # 跳过最左 10%（拼图块初始位置，避免自身命中）
                    sx = max(pw, iw * 10 // 100)

                    if iw - sx < pw or ih < ph:
                        print("[captcha] 搜索区域不足，fallback")
                        piece_bytes = None
                    else:
                        res = cv2.matchTemplate(
                            bg_gray[:, sx:], piece_gray,
                            cv2.TM_CCOEFF_NORMED, mask=mask_bin,
                        )

                        # 提取前 5 个局部极大值
                        cands: list[tuple[int, int, float]] = []
                        tmp = res.copy()
                        for _ in range(5):
                            _, v, _, loc = cv2.minMaxLoc(tmp)
                            if v < 0.05:
                                break
                            cx = loc[0] + sx + pw // 2
                            cy = loc[1] + ph // 2
                            cands.append((cx, cy, float(v)))
                            r0 = max(0, loc[1] - ph // 2)
                            r1 = min(tmp.shape[0], loc[1] + ph // 2)
                            c0 = max(0, loc[0] - pw // 2)
                            c1 = min(tmp.shape[1], loc[0] + pw // 2)
                            tmp[r0:r1, c0:c1] = -1.0

                        print(
                            f"[captcha] 像素相关候选(x, corr): "
                            f"{[(c, round(s, 3)) for c, _, s in cands]}"
                        )

                        if not cands:
                            print("[captcha] 无匹配候选，fallback")
                            piece_bytes = None
                        else:
                            match_x = cands[0][0]
                            print(
                                f"[captcha] 缺口 x={match_x}/{iw} "
                                f"corr={cands[0][2]:.3f}"
                            )

        if not piece_bytes:
            # fallback：Canny 轮廓找最大连通区域
            bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(bg_gray, (9, 9), 0)
            s, e    = iw * 25 // 100, iw * 95 // 100
            edges   = cv2.Canny(blurred, 25, 90)
            cnts, _ = cv2.findContours(
                edges[:, s:e], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            best_cx, best_score = None, 0.0
            for cnt in cnts:
                area = cv2.contourArea(cnt)
                if area < 150:
                    continue
                cx_c, _, cw, ch = cv2.boundingRect(cnt)
                cx_c += s
                if cw > 5 and ch > 5:
                    sq = min(cw, ch) / max(cw, ch)
                    if area * sq > best_score:
                        best_score = area * sq
                        best_cx = cx_c + cw // 2
            match_x = best_cx if best_cx is not None else iw * 55 // 100
            print(f"[captcha] Canny fallback center_x={match_x}/{iw}")

        return match_x

    except ImportError:
        print("[captcha] opencv 未安装，跳过 CV 定位")
    except Exception as ex:
        print(f"[captcha] cv2_find_hole 异常: {ex}")
    return None


# ── 人形拖拽 ──────────────────────────────────────────────────────────────────

def _slider_drag(page: Any, sx: float, sy: float, drag_px: float) -> None:
    """smooth-step 缓动 + 随机抖动的人形鼠标拖拽。"""
    page.mouse.move(sx, sy)
    time.sleep(random.uniform(0.40, 0.70))
    page.mouse.down()
    time.sleep(random.uniform(0.20, 0.40))
    steps = random.randint(40, 55)
    for i in range(1, steps + 1):
        t    = i / steps
        ease = t * t * (3.0 - 2.0 * t)   # smooth-step
        page.mouse.move(
            sx + drag_px * ease + random.uniform(-0.5, 0.5),
            sy + random.uniform(-0.8, 0.8),
        )
        time.sleep(random.uniform(0.018, 0.035))
    time.sleep(random.uniform(0.15, 0.30))
    page.mouse.up()
    # 只留极短让浏览器完成 mouseup/重绘；通过判定在 _wait_for_pass 里用「停滑即刻截图+文案」完成
    time.sleep(random.uniform(0.12, 0.35))


# ── 通过状态检测 ──────────────────────────────────────────────────────────────

# 截图时按钮区域宽高（像素），用于裁剪滑块轨道区域
_SLIDER_TRACK_PAD_X = 5    # 轨道左侧额外截取宽度（px）
_SLIDER_TRACK_HEIGHT = 36  # 轨道高度估计（px）

# ByteDance 验证通过后滑块轨道的绿色近似值 (BGR in OpenCV)
# 36kr/ByteDance 通过色约为 #52C41A 或 #00C1A7（青绿）
_PASS_GREEN_MIN_BGR = (30, 150, 30)   # 最低 G 门限（BGR）
_PASS_GREEN_RATIO   = 0.20            # 绿色像素占比阈值（仅轨道条）
# 停滑后扩大截图（含「验证通过」提示条）时，面积更大、阈值略低
_PASS_GREEN_RATIO_LOOSE = 0.07

# 与截图同时可用的可见文案（iframe / 主页面 innerText，非 OCR）
_PASS_TEXT_RE = re.compile(
    r"验证\s*通过|验证\s*成功|验证\s*已完成|校验\s*通过|安全\s*验证\s*通过",
    re.MULTILINE,
)

# JS: 在 captcha iframe 内检测通过状态
_JS_CAPTCHA_PASS_STATE = """
() => {
    const btn = document.querySelector('.dragger-item, [class*="dragger-item"]');
    const passEl = document.querySelector(
        '[class*="pass"], [class*="success"][class*="drag"], '
        + '[class*="done"][class*="drag"], [class*="captcha-pass"]'
    );
    let btnX = null;
    if (btn) {
        const r = btn.getBoundingClientRect();
        btnX = Math.round(r.x + r.width / 2);
    }
    return { has_btn: !!btn, btn_x: btnX, has_pass_el: !!passEl };
}
"""


def _screenshot_shows_pass(
    page: Any,
    btn_box: dict,
    drag_px: float,
) -> bool:
    """
    对滑块轨道区域截图，用绿色像素占比判断是否通过。
    btn_box: 拖拽按钮原始 bounding box（page 坐标）。
    drag_px: 已拖拽的距离（用于估算轨道终点 x）。
    """
    try:
        import io

        import numpy as np

        # 估算拖拽后按钮位置，截取从起点到终点的轨道区域
        track_x = int(btn_box["x"] - _SLIDER_TRACK_PAD_X)
        track_y = int(btn_box["y"] + btn_box.get("height", 30) / 2 - _SLIDER_TRACK_HEIGHT / 2)
        track_w = int(drag_px + btn_box.get("width", 40) + _SLIDER_TRACK_PAD_X * 2)
        track_h = _SLIDER_TRACK_HEIGHT

        png_bytes = page.screenshot(
            clip={
                "x": max(0, track_x),
                "y": max(0, track_y),
                "width": max(20, track_w),
                "height": track_h,
            }
        )
        try:
            import cv2
            arr = np.frombuffer(png_bytes, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # BGR
            if img is None:
                return False
            b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
            green_mask = (
                (g.astype(int) > _PASS_GREEN_MIN_BGR[1])
                & (g.astype(int) > b.astype(int) + 30)
                & (g.astype(int) > r.astype(int) + 30)
            )
            ratio = float(green_mask.sum()) / max(1, img.shape[0] * img.shape[1])
            print(f"[captcha] 截图绿色像素占比={ratio:.2%}")
            return ratio >= _PASS_GREEN_RATIO
        except ImportError:
            # cv2 未安装：回退到 Pillow
            try:
                from PIL import Image

                img_pil = Image.open(io.BytesIO(png_bytes)).convert("RGB")
                pixels = list(img_pil.getdata())
                total = len(pixels)
                green_cnt = sum(
                    1 for (rp, gp, bp) in pixels
                    if gp > _PASS_GREEN_MIN_BGR[1]
                    and gp > bp + 30
                    and gp > rp + 30
                )
                ratio = green_cnt / max(1, total)
                print(f"[captcha] 截图绿色像素占比(PIL)={ratio:.2%}")
                return ratio >= _PASS_GREEN_RATIO
            except Exception:
                return False
    except Exception as ex:
        print(f"[captcha] 截图检测异常: {ex}")
        return False


def _visible_text_blob_36kr(page: Any) -> str:
    """主 document + 各 frame 的 body.innerText，用于与『停滑后截图』同布判定验证通过。"""
    chunks: list[str] = []
    try:
        t = page.evaluate("() => (document.body && document.body.innerText) || ''")
        if isinstance(t, str) and t.strip():
            chunks.append(t[:6000])
    except Exception:
        pass
    for fr in list(page.frames)[:30]:
        try:
            t = fr.evaluate("() => (document.body && document.body.innerText) || ''")
            if isinstance(t, str) and t.strip():
                chunks.append(t[:5000])
        except Exception:
            pass
    return "\n".join(chunks)[:20000]


def _visible_text_says_captcha_passed(text: str) -> bool:
    if not (text and text.strip()):
        return False
    if _PASS_TEXT_RE.search(text):
        return True
    return "验证" in text and ("通过" in text or "成功" in text) and "人机" not in text[:200]


def _green_ratio_in_png_bgr(png_bytes: bytes) -> float:
    import io

    import numpy as np
    try:
        import cv2

        arr = np.frombuffer(png_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 0.0
        b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
        green_mask = (
            (g.astype(int) > _PASS_GREEN_MIN_BGR[1])
            & (g.astype(int) > b.astype(int) + 30)
            & (g.astype(int) > r.astype(int) + 30)
        )
        return float(green_mask.sum()) / max(1, img.shape[0] * img.shape[1])
    except ImportError:
        try:
            from PIL import Image

            img_pil = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            pixels = list(img_pil.getdata())
            n = 0
            for rp, gp, bp in pixels:
                if gp > _PASS_GREEN_MIN_BGR[1] and gp > bp + 30 and gp > rp + 30:
                    n += 1
            return n / max(1, len(pixels))
        except Exception:
            return 0.0
    except Exception:
        return 0.0


def _try_ocr_screenshot_for_pass(png_bytes: bytes) -> bool:
    if not png_bytes or len(png_bytes) < 200:
        return False
    try:
        from PIL import Image
        import io
        import pytesseract
    except Exception:
        return False
    try:
        img = Image.open(io.BytesIO(png_bytes))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        t = (text or "").strip()
        if not t:
            return False
        if _visible_text_says_captcha_passed(t) or _PASS_TEXT_RE.search(t):
            print("[captcha] pass_detected: OCR(可选 pytesseract) 识别为验证通过类文案")
            return True
    except Exception:
        pass
    return False


def _screenshot_expanded_track_area_shows_pass(
    page: Any,
    btn_box: dict,
    drag_px: float,
) -> bool:
    """
    滑停后立即截比轨道更「高」的矩形：含轨道下方常出现的「验证通过 / 对勾 + 青绿条」
    提示区；用略低的绿色占比阈值 + 可选 OCR，补绿轨截图漏检的情况。
    """
    try:
        ext_below = 110
        track_x = int(btn_box["x"] - _SLIDER_TRACK_PAD_X)
        track_y = int(btn_box["y"] - 8)
        track_w = int(max(90.0, float(drag_px) + float(btn_box.get("width", 40)) + 24.0))
        track_h = int(_SLIDER_TRACK_HEIGHT + ext_below)
        clip = {
            "x": max(0, track_x),
            "y": max(0, track_y),
            "width": min(880, max(20, track_w)),
            "height": min(280, max(20, track_h)),
        }
        png_bytes = page.screenshot(clip=clip, type="png")
        r = _green_ratio_in_png_bgr(png_bytes)
        print(f"[captcha] 扩大区截图(停滑)绿色占比={r:.2%} clip_h={clip['height']}")
        if r >= _PASS_GREEN_RATIO_LOOSE:
            return True
        if _try_ocr_screenshot_for_pass(png_bytes):
            return True
    except Exception as ex:
        print(f"[captcha] 扩大区截图检测异常: {ex}")
    return False


def _wait_for_pass(
    page: Any,
    btn_box: dict,
    drag_px: float,
    timeout_s: float = 4.5,
    poll_s: float = 0.5,
) -> bool:
    """
    拖拽后轮询检测验证是否通过（截图 + DOM 双检测）。

    通过判据（任一满足）：
      1. 截图：滑块轨道出现明显绿色（通过动画）
      2. DOM：captcha iframe 内拖拽按钮消失（验证码关闭）
      3. DOM：出现明确的 pass/success 元素

    返回 True=已通过，False=超时仍未通过。
    """
    deadline = time.time() + timeout_s
    attempt_no = 0
    while time.time() < deadline:
        attempt_no += 1
        # 1) 与「停滑后截图」同布：主页面/iframe 可见文字（不依赖 <html> 是否仍含 recaptcha 脚本等）
        try:
            blob = _visible_text_blob_36kr(page)
            if _visible_text_says_captcha_passed(blob):
                print("[captcha] pass_detected: 页面/iframe 可见 innerText 含验证通过/成功 类提示")
                return True
        except Exception:
            pass
        # 2) 原轨道条绿色（高阈值）
        if _screenshot_shows_pass(page, btn_box, drag_px):
            print("[captcha] pass_detected: 截图显示滑块轨道变绿")
            return True
        # 3) 停滑即时：扩大区截图（含提示条 + 略低绿占比 + 可选 pytesseract）
        if _screenshot_expanded_track_area_shows_pass(page, btn_box, drag_px):
            print("[captcha] pass_detected: 扩大区截图(停滑)判定为通过")
            return True

        # ── DOM 检测：在 captcha iframe 里查询 ───────────────────────────
        for iframe_sel in _IFRAME_SELS:
            try:
                frame_loc = page.frame_locator(iframe_sel)
                state = frame_loc.locator("body").first.evaluate(_JS_CAPTCHA_PASS_STATE)
                if isinstance(state, dict):
                    if state.get("has_pass_el"):
                        print(f"[captcha] pass_detected: iframe 出现 pass 元素 (sel={iframe_sel})")
                        return True
                    if not state.get("has_btn"):
                        print(f"[captcha] pass_detected: 拖拽按钮已消失 (sel={iframe_sel})")
                        return True
            except Exception:
                pass

        # ── 检查 captcha iframe 是否已从页面移除 ─────────────────────────
        try:
            if not any(
                "bytedance" in f.url or "verifycenter" in f.url
                for f in page.frames
            ):
                print("[captcha] pass_detected: captcha iframe 已从页面移除")
                return True
        except Exception:
            pass

        if time.time() < deadline:
            time.sleep(poll_s)

    return False


# ── 单次求解（抽取为独立函数，供重试循环复用）────────────────────────────────

def _solve_once(
    page: Any,
    *,
    btn_timeout_ms: int = 15000,
    captured: dict[str, bytes] | None = None,
) -> tuple[bool, dict | None, float]:
    """
    执行一次完整的"找按钮→分析图片→执行拖拽"流程。

    返回 (drag_attempted, btn_box, drag_px)：
      - drag_attempted: 是否成功发起了拖拽
      - btn_box: 拖拽按钮 bounding box（未找到时为 None）
      - drag_px: 实际拖拽距离（未拖拽时为 0.0）
    """
    if captured is None:
        captured = {}

    # ── 1. 找拖拽按钮 ──────────────────────────────────────────────────────
    btn_box: dict | None = None
    for iframe_sel in _IFRAME_SELS:
        try:
            fl      = page.frame_locator(iframe_sel)
            btn_loc = fl.locator(_BTN_SEL).first
            btn_loc.wait_for(state="attached", timeout=btn_timeout_ms)
            box = btn_loc.bounding_box()
            if box is not None:
                btn_box = box
                print(
                    f"[captcha] 拖拽按钮 x={box['x']:.0f} y={box['y']:.0f} "
                    f"w={box['width']:.0f} h={box['height']:.0f}"
                )
                break
        except Exception as exc:
            print(f"[captcha] {iframe_sel} miss: {exc}")

    if btn_box is None:
        print("[captcha] 未找到拖拽按钮")
        return False, None, 0.0

    btn_cx: float = btn_box["x"] + max(btn_box["width"],  30) / 2
    btn_cy: float = btn_box["y"] + max(btn_box["height"], 30) / 2

    # ── 2. 定位验证码 iframe frame 对象 ────────────────────────────────────
    captcha_frame = None
    for f in page.frames:
        if "bytedance" in f.url or "verifycenter" in f.url:
            captcha_frame = f
            break

    # ── 3. 网络拦截：捕获验证码图片字节（fallback 用）──────────────────────
    def _on_response(resp: Any) -> None:
        try:
            url = resp.url
            ct  = resp.headers.get("content-type", "")
            if (
                ("byteimg" in url or "netsec-img" in url or "captcha" in url)
                and ("image" in ct or url.endswith((".jpg", ".jpeg", ".png", ".webp")))
            ):
                captured[url] = resp.body()
        except Exception:
            pass

    page.on("response", _on_response)

    # ── 4. 从 iframe 取图片像素 ─────────────────────────────────────────────
    imgs: list      = []
    img_box_page: dict | None = None
    piece_w_page: float       = 68.0
    bg_b64:   str | None = None
    slide_b64: str | None = None

    if captcha_frame:
        try:
            for _ in range(30):
                try:
                    if captcha_frame.evaluate(_JS_WAIT_NATURAL_WIDTH):
                        break
                except Exception:
                    pass
                time.sleep(0.5)

            imgs = captcha_frame.evaluate(_JS_GET_IMAGES)
            print(
                f"[captcha] iframe 图片: "
                f"{[(i['cls'][:25], i['w'], i['h'], i['nw']) for i in imgs]}"
            )

            bg_meta = max(
                (i for i in imgs if i["w"] > 100 and i["nw"] > 0),
                key=lambda i: i["w"] * i["h"],
                default=None,
            )
            slide_meta = next(
                (i for i in imgs
                 if "slide" in i.get("cls", "").lower() and i["nw"] > 0),
                None,
            )
            if slide_meta:
                piece_w_page = float(slide_meta["w"])

            if bg_meta:
                iframe_x = btn_box["x"] - _DRAGGER_IFRAME_X
                img_box_page = {
                    "x":      iframe_x + bg_meta["x"],
                    "y":      btn_box["y"] - 185 + bg_meta["y"],
                    "width":  bg_meta["w"],
                    "height": bg_meta["h"],
                }

            bg_b64    = captcha_frame.evaluate(_JS_IMG_TO_B64, "img.captcha-verify-image")
            slide_b64 = captcha_frame.evaluate(
                _JS_IMG_TO_B64,
                "img.captcha-verify-image-slide, img[class*='slide']",
            )
            if bg_b64:
                print(f"[captcha] 背景图 dump OK len={len(bg_b64)}")
            if slide_b64:
                print(f"[captcha] 拼图块 dump OK len={len(slide_b64)}")

        except Exception as ex:
            print(f"[captcha] 取图片信息失败: {ex}")

    # ── 5. 解码图片字节 ─────────────────────────────────────────────────────
    img_bytes:   bytes | None = base64.b64decode(bg_b64)    if bg_b64    else None
    piece_bytes: bytes | None = base64.b64decode(slide_b64) if slide_b64 else None

    if not img_bytes and captured:
        print(f"[captcha] DOM dump 失败，尝试网络拦截（{len(captured)} 张）")
        sorted_imgs = sorted(captured.items(), key=lambda kv: len(kv[1]), reverse=True)
        for url, data in sorted_imgs:
            print(f"[captcha]   captured {url[-60:]} size={len(data)}")
        img_bytes   = sorted_imgs[0][1]
        if len(sorted_imgs) >= 2:
            piece_bytes = sorted_imgs[1][1]

    if not img_bytes and imgs:
        bg_src = next(
            (
                i["src"] for i in imgs
                if i["w"] > 100
                and i["src"]
                and "verifycenter" not in i["src"]
                and i["src"] != "__canvas__"
            ),
            None,
        )
        if bg_src:
            print(f"[captcha] requests 下载 url={bg_src[:80]}")
            img_bytes = _download_image(bg_src)

    if not img_box_page and imgs:
        bg_meta2 = max(
            (i for i in imgs if i["w"] > 100),
            key=lambda i: i["w"] * i["h"],
            default=None,
        )
        if bg_meta2:
            iframe_x = btn_box["x"] - _DRAGGER_IFRAME_X
            img_box_page = {
                "x":      iframe_x + bg_meta2["x"],
                "y":      btn_box["y"] - 185 + bg_meta2["y"],
                "width":  bg_meta2["w"],
                "height": bg_meta2["h"],
            }

    # ── 6. 识别缺口位置 ─────────────────────────────────────────────────────
    img_w_px: int = 340
    if img_bytes:
        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(img_bytes, np.uint8)
            im  = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if im is not None:
                img_w_px = im.shape[1]
        except Exception:
            pass

    hole_img_x: int | None = None
    if img_bytes:
        hole_img_x = cv2_find_hole(img_bytes, piece_bytes)

    # ── 7. 计算拖拽距离 ─────────────────────────────────────────────────────
    drag_px: float
    if hole_img_x is not None and img_box_page:
        x_scale           = img_box_page["width"] / img_w_px if img_w_px > 0 else 1.0
        hole_center_page  = img_box_page["x"] + hole_img_x * x_scale
        piece_init_center = img_box_page["x"] + piece_w_page / 2
        drag_px           = hole_center_page - piece_init_center
        side              = "右侧" if hole_img_x > img_w_px // 2 else "左侧"
        print(
            f"[captcha] 缺口在背景图 [{side}]  img_x={hole_img_x}/{img_w_px}  "
            f"page_x={hole_center_page:.0f}  drag={drag_px:.0f}px"
        )
    else:
        img_w   = img_box_page["width"] if img_box_page else 340.0
        drag_px = img_w * 0.55 - piece_w_page / 2
        print(f"[captcha] 无法定位缺口，fallback drag={drag_px:.0f}px")

    if drag_px < 15:
        fallback_w = img_box_page["width"] if img_box_page else 280.0
        drag_px    = fallback_w * 0.55
        print(f"[captcha] drag_px 过小，重置为 {drag_px:.0f}px")

    # ── 8. 执行拖拽 ─────────────────────────────────────────────────────────
    print(f"[captcha] 执行拖拽 btn=({btn_cx:.0f},{btn_cy:.0f}) drag={drag_px:.0f}px")
    _slider_drag(page, btn_cx, btn_cy, drag_px)
    return True, btn_box, drag_px


# ── 主入口 ────────────────────────────────────────────────────────────────────

def solve_slider_captcha(page: Any, max_attempts: int = 3) -> bool:
    """
    在已加载验证码的页面上自动完成滑块拼图，失败后自动重试。

    参数
    ----
    page : playwright.sync_api.Page
        当前已渲染验证码的页面对象。
    max_attempts : int
        最多尝试次数（默认 3）。每次未通过后等待 3s（验证码刷新），再重试。

    返回
    ----
    bool
        True  — 至少发起过一次拖拽
        False — 未找到拖拽按钮或流程异常
    """
    captured: dict[str, bytes] = {}
    attempted = False

    for attempt in range(1, max_attempts + 1):
        print(f"[captcha] ── 第 {attempt}/{max_attempts} 次尝试 ──")

        btn_timeout = 15000 if attempt == 1 else 8000
        drag_ok, btn_box, drag_px = _solve_once(
            page,
            btn_timeout_ms=btn_timeout,
            captured=captured,
        )

        if not drag_ok:
            if not attempted:
                return False
            break

        attempted = True

        # ── 拖拽后截图 + DOM 检测是否通过 ────────────────────────────────
        passed = _wait_for_pass(page, btn_box, drag_px, timeout_s=4.5)
        if passed:
            print(f"[captcha] 第 {attempt} 次验证通过 OK")
            return True

        print(f"[captcha] 第 {attempt} 次未通过，等待 3s 后重试（验证码将刷新）…")
        if attempt < max_attempts:
            time.sleep(3.0)
            # 等待新验证码图片加载（轮询按钮出现）
            _wait_for_btn_reset(page, timeout_s=6.0)

    print(f"[captcha] {max_attempts} 次均未检测到通过，返回已尝试状态")
    return attempted


def _wait_for_btn_reset(page: Any, timeout_s: float = 6.0) -> None:
    """等待验证码重置后拖拽按钮重新出现（新图片已加载）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for iframe_sel in _IFRAME_SELS:
            try:
                fl      = page.frame_locator(iframe_sel)
                btn_loc = fl.locator(_BTN_SEL).first
                btn_loc.wait_for(state="visible", timeout=1000)
                print("[captcha] 验证码已重置，拖拽按钮重新可见")
                return
            except Exception:
                pass
        time.sleep(0.5)
    print("[captcha] 等待验证码重置超时")
