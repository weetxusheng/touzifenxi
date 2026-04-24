"""
本地手测入口：不走 pytest，用于实机验证 Playwright + 滑块。

默认走与主流程相同的 ``SliderRiskTool`` + ``Kr36SourceAdapter``（读 runtime 配置）；
``--mode yidun`` 仅跑易盾子进程等价逻辑（``run_yidun_solver``）。

项目根目录执行（需已 ``pip install`` 项目依赖 + Playwright 浏览器）::

    set PYTHONPATH=src
    python -m utils.slider_solver.dev_test
    python -m utils.slider_solver.dev_test --url https://36kr.com/topics/ --headless
    python -m utils.slider_solver.dev_test --mode yidun --url https://example.com/yidun-page
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _project_root() -> Path:
    # src/utils/slider_solver/dev_test.py -> parents[3] == 仓库根
    return Path(__file__).resolve().parents[3]


def _ensure_src_on_path() -> Path:
    root = _project_root()
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return root


def _run_session(url: str, *, headless: bool, dwell_ms: int) -> int:
    root = _ensure_src_on_path()
    from utils.slider_solver.kr36_bridge import (
        build_kr36_adapter_for_auto_captcha_test,
        run_step4_fetch_for_auto_captcha_test,
    )

    adapter = build_kr36_adapter_for_auto_captcha_test(
        root, headless=headless, dwell_ms=dwell_ms
    )
    print(
        f"[slider-dev] mode=session url={url!r} "
        f"window={'headless' if headless else 'visible'} "
        f"playwright_mode={adapter.risk_verification_playwright_mode!r}",
        flush=True,
    )
    html = run_step4_fetch_for_auto_captcha_test(adapter, url)
    n = len(html or "")
    print(f"[slider-dev] HTML 长度: {n}", flush=True)
    if n:
        print(f"[slider-dev] 前 200 字: {html[:200]!r}", flush=True)
    return 0 if n else 1


def _run_yidun(
    url: str,
    *,
    headless: bool,
    timeout_ms: int,
    max_rounds: int,
) -> int:
    _ensure_src_on_path()
    from utils.slider_solver.captcha import run_yidun_solver

    print(
        f"[slider-dev] mode=yidun url={url!r} headless={headless} "
        f"timeout_ms={timeout_ms} max_rounds={max_rounds}",
        flush=True,
    )
    return int(
        run_yidun_solver(
            url=url,
            headless=headless,
            timeout_ms=timeout_ms,
            max_rounds=max_rounds,
        )
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="utils.slider_solver 本地手测（Playwright + 滑块）。")
    p.add_argument(
        "--mode",
        choices=("session", "yidun"),
        default="session",
        help="session=完整会话+Kr36 适配器（默认）；yidun=仅易盾求解流程",
    )
    p.add_argument(
        "--url",
        default="https://36kr.com/video/3766585318896386",
        help="目标 URL（yidun 模式须为易盾验证页）",
    )
    p.add_argument("--headless", action="store_true", help="无头浏览器")
    p.add_argument(
        "--dwell-ms",
        type=int,
        default=0,
        help="session 模式：关窗前驻留毫秒（<=0 时 1ms，含义同联调脚本）",
    )
    p.add_argument("--timeout-ms", type=int, default=120_000, help="yidun 模式：页面超时")
    p.add_argument("--max-rounds", type=int, default=4, help="yidun 模式：最大刷新轮次")
    args = p.parse_args(argv)
    url = str(args.url).strip()
    if not url:
        print("[slider-dev] 空 URL", file=sys.stderr)
        return 2

    if args.mode == "yidun":
        return _run_yidun(
            url,
            headless=args.headless,
            timeout_ms=int(args.timeout_ms),
            max_rounds=int(args.max_rounds),
        )
    return _run_session(url, headless=args.headless, dwell_ms=int(args.dwell_ms))


if __name__ == "__main__":
    raise SystemExit(main())
