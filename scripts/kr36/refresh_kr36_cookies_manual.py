from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kr36.source_adapter import (  # noqa: E402
    KR36_DEFAULT_USER_AGENT,
    extract_kr36_cookie_values,
    load_kr36_cookies,
    save_kr36_cookies,
)


def _full_ua() -> str:
    return (
        f"{KR36_DEFAULT_USER_AGENT} AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


def run_manual_cookie_refresh(
    *,
    url: str,
    second_url: str,
    wait_seconds: int,
) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装，请先执行: pip install playwright && playwright install chromium")
        return 2

    existing = load_kr36_cookies()
    print(f"[kr36-cookie] 当前本地 cookies 条数: {len(existing)}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(
            locale="zh-CN",
            user_agent=_full_ua(),
            viewport={"width": 1400, "height": 920},
        )
        if existing:
            context.add_cookies(
                [
                    {
                        "name": name,
                        "value": value,
                        "domain": ".36kr.com",
                        "path": "/",
                        "httpOnly": False,
                        "secure": True,
                    }
                    for name, value in existing.items()
                ]
            )

        page = context.new_page()
        print(f"[kr36-cookie] 打开页面: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=120000)

        print(
            "[kr36-cookie] 请在浏览器中手动完成登录/验证；\n"
            f"[kr36-cookie] 完成后在 {wait_seconds} 秒内回到终端按回车继续保存 cookies。"
        )
        try:
            input()
        except EOFError:
            # 非交互运行时，给一个保底等待窗口。
            time.sleep(max(5, wait_seconds))

        if second_url:
            try:
                print(f"[kr36-cookie] 二次确认页面: {second_url}")
                page.goto(second_url, wait_until="domcontentloaded", timeout=120000)
            except Exception as error:  # noqa: BLE001
                print(f"[kr36-cookie] 二次确认页面访问失败: {error}")

        merged = extract_kr36_cookie_values(context.cookies())
        if existing:
            existing.update(merged)
            merged = existing
        save_kr36_cookies(merged)
        browser.close()

    print(f"[kr36-cookie] 已更新 cookies，条数: {len(merged)}")
    print(f"[kr36-cookie] 文件: {ROOT / 'config' / 'kr36_cookies.json'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="手动登录 36Kr 后刷新本地 cookies。")
    parser.add_argument("--url", default="https://36kr.com/topics/")
    parser.add_argument("--second-url", default="https://36kr.com/activity")
    parser.add_argument("--wait-seconds", type=int, default=180)
    args = parser.parse_args()
    raise SystemExit(
        run_manual_cookie_refresh(
            url=str(args.url).strip(),
            second_url=str(args.second_url).strip(),
            wait_seconds=max(10, int(args.wait_seconds)),
        )
    )


if __name__ == "__main__":
    main()

