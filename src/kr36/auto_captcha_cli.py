"""
36Kr「自动滑块 / step4 联调」的单一实现：与主流程共用 ``Kr36SourceAdapter`` 与
``Kr36RiskStep4Tool``（唯一调用 ``playwright_slider_session`` 的路径），供
``scripts/kr36/run_kr36_auto_captcha_test.py`` 引用，避免在脚本里重复写
sources.kr36 合并与开关覆盖逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kr36.source_adapter import Kr36SourceAdapter


def project_root_from_kr36_package() -> Path:
    """本文件位于 ``src/kr36/`` 下，上两级为项目根。"""
    return Path(__file__).resolve().parents[2]


def load_sources_kr36_from_runtime(project_root: Path) -> dict[str, object]:
    """与联调脚本原逻辑一致：先 ``runtime.local.json``，空则取 example 中 ``sources.kr36``。"""
    from c114.runtime.config import read_c114_local_config

    cfg = read_c114_local_config(project_root)
    out = dict(cfg.get("sources", {}).get("kr36") or {})
    if not out:
        example = project_root / "config" / "runtime.example.json"
        if example.exists():
            data = json.loads(example.read_text(encoding="utf-8"))
            out = dict((data.get("sources") or {}).get("kr36") or {})
    return out


def merge_cli_test_overrides(
    base: dict[str, object], *, headless: bool, dwell_ms: int
) -> dict[str, object]:
    """
    联调专用：允许浏览器、自动滑块；关窗前驻留用 ``dwell_ms``（<=0 时写 1ms，避免
    ``_positive_int_config`` 把 0 当成未配置而落回 180s 默认）。
    """
    out: dict[str, object] = {**base}
    out["http_only_mode"] = False
    out["risk_verification_auto_solver"] = True
    dm = int(dwell_ms)
    out["risk_post_success_browser_dwell_ms"] = 1 if dm <= 0 else dm
    out["risk_verification_playwright_mode"] = "headless" if headless else "agent-browser"
    return out


def build_kr36_adapter_for_auto_captcha_test(
    project_root: Path,
    *,
    headless: bool = False,
    dwell_ms: int = 0,
) -> "Kr36SourceAdapter":
    from kr36.source_adapter import Kr36SourceAdapter

    base = load_sources_kr36_from_runtime(project_root)
    merged = merge_cli_test_overrides(base, headless=headless, dwell_ms=dwell_ms)
    return Kr36SourceAdapter(merged)


def run_step4_fetch_for_auto_captcha_test(adapter: "Kr36SourceAdapter", url: str) -> str:
    """
    联调脚本专用：与 ``Kr36SourceAdapter.step4_fetch_html_via_playwright_slider`` 等价，
    均委托 ``Kr36RiskStep4Tool``，避免另一套 import/调用链。
    """
    from kr36.risk_step4_tool import Kr36RiskStep4Tool

    return Kr36RiskStep4Tool(adapter).fetch(url, log_phase="auto-captcha-test")
