from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import requests

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

DEFAULT_URLS = (
    "https://36kr.com/",
    "https://36kr.com/topics/",
    "https://36kr.com/activity",
)


def _full_user_agent() -> str:
    return (
        f"{KR36_DEFAULT_USER_AGENT} AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


def collect_with_requests(*, urls: tuple[str, ...], timeout_seconds: int) -> dict[str, str]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _full_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    result: dict[str, str] = {}
    for url in urls:
        try:
            response = session.get(url, timeout=timeout_seconds, allow_redirects=True)
            print(f"[kr36-cookie-auto] requests {url} status={response.status_code} len={len(response.text)}")
        except Exception as error:  # noqa: BLE001
            print(f"[kr36-cookie-auto] requests {url} failed: {error}")
            continue
        for cookie in session.cookies:
            if "36kr.com" in (cookie.domain or "") and cookie.name and cookie.value:
                result[cookie.name] = cookie.value
        time.sleep(random.uniform(0.5, 1.6))
    return result


def collect_with_playwright(
    *,
    urls: tuple[str, ...],
    timeout_ms: int,
) -> dict[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[kr36-cookie-auto] playwright 未安装，跳过无头补充。")
        return {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            locale="zh-CN",
            user_agent=_full_user_agent(),
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                print(f"[kr36-cookie-auto] playwright {url} ok")
            except Exception as error:  # noqa: BLE001
                print(f"[kr36-cookie-auto] playwright {url} failed: {error}")
            time.sleep(random.uniform(0.8, 2.2))
        extracted = extract_kr36_cookie_values(context.cookies())
        browser.close()
        return extracted


def run(*, timeout_seconds: int, timeout_ms: int) -> int:
    existing = load_kr36_cookies()
    print(f"[kr36-cookie-auto] existing cookies={len(existing)}")
    req = collect_with_requests(urls=DEFAULT_URLS, timeout_seconds=timeout_seconds)
    print(f"[kr36-cookie-auto] requests cookies={len(req)}")
    pw = collect_with_playwright(urls=DEFAULT_URLS, timeout_ms=timeout_ms)
    print(f"[kr36-cookie-auto] playwright cookies={len(pw)}")

    merged = dict(existing)
    merged.update(req)
    merged.update(pw)
    if not merged:
        print("[kr36-cookie-auto] no cookies collected")
        return 1
    save_kr36_cookies(merged)
    print(f"[kr36-cookie-auto] merged cookies={len(merged)}")
    print(f"[kr36-cookie-auto] saved: {ROOT / 'config' / 'kr36_cookies.json'}")
    print(f"[kr36-cookie-auto] s_v_web_id={merged.get('s_v_web_id', '')}")
    print(f"[kr36-cookie-auto] sensorsdata2015jssdkcross={merged.get('sensorsdata2015jssdkcross', '')}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="36Kr 自动刷新 cookies（无需手动登录）。")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    args = parser.parse_args()
    raise SystemExit(
        run(
            timeout_seconds=max(5, int(args.timeout_seconds)),
            timeout_ms=max(10000, int(args.timeout_ms)),
        )
    )


if __name__ == "__main__":
    main()
