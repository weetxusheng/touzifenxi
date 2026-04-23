"""``kr36.auto_captcha_cli`` 与联调脚本共用配置合并，避免无测回归。"""

from kr36.auto_captcha_cli import merge_cli_test_overrides


def test_merge_cli_test_overrides_headless_and_dwell() -> None:
    m = merge_cli_test_overrides({}, headless=True, dwell_ms=0)
    assert m["http_only_mode"] is False
    assert m["risk_verification_auto_solver"] is True
    assert m["risk_verification_playwright_mode"] == "headless"
    assert m["risk_post_success_browser_dwell_ms"] == 1

    m2 = merge_cli_test_overrides({}, headless=False, dwell_ms=3000)
    assert m2["risk_verification_playwright_mode"] == "agent-browser"
    assert m2["risk_post_success_browser_dwell_ms"] == 3000
