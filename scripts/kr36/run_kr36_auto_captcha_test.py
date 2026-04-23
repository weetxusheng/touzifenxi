"""
36Kr 自动验证码联调入口（薄封装）：实现见 ``kr36.auto_captcha_cli`` 与
``kr36.risk_step4_tool.Kr36RiskStep4Tool``，与主流程同一套滑块+拉页，无重复实现。

**默认使用真实可见浏览器**；加 ``--headless`` 为无头。

用法（在项目根）::

    .venv\\Scripts\\python.exe scripts\\kr36\\run_kr36_auto_captcha_test.py
    .venv\\Scripts\\python.exe scripts\\kr36\\run_kr36_auto_captcha_test.py --headless
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kr36.auto_captcha_cli import (  # noqa: E402
    build_kr36_adapter_for_auto_captcha_test,
    run_step4_fetch_for_auto_captcha_test,
)


def main() -> int:
    p = argparse.ArgumentParser(
        description="36Kr 自动滑块/验证联调（实现见 kr36.auto_captcha_cli）。"
        "默认真实可见窗口，加 --headless 才无头。"
    )
    p.add_argument("--url", default="https://36kr.com/topics/", help="打开并尝试自动过风控的 URL")
    p.add_argument(
        "--headless",
        action="store_true",
        help="使用无头浏览器。省略本参数时默认为真实可见窗口（有头）",
    )
    p.add_argument(
        "--dwell-ms",
        type=int,
        default=0,
        help="关窗前驻留毫秒；<=0 时写作 1ms（与 Kr36SourceAdapter 正整数配置一致）",
    )
    args = p.parse_args()
    url = str(args.url).strip()
    if not url:
        print("空 URL", file=sys.stderr)
        return 2

    adapter = build_kr36_adapter_for_auto_captcha_test(
        ROOT, headless=args.headless, dwell_ms=int(args.dwell_ms)
    )
    print(f"[kr36-auto-verify] url={url!r}", flush=True)
    print(
        f"[kr36-auto-verify] window={'headless' if args.headless else 'visible(默认)'} "
        f"playwright_mode={adapter.risk_verification_playwright_mode!r} "
        f"dwell_ms={adapter.risk_post_success_browser_dwell_ms}",
        flush=True,
    )

    html = run_step4_fetch_for_auto_captcha_test(adapter, url)
    n = len(html or "")
    print(f"[kr36-auto-verify] 返回 HTML 长度: {n}", flush=True)
    if n:
        print(f"[kr36-auto-verify] 前 200 字（repr）: {html[:200]!r}", flush=True)
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
