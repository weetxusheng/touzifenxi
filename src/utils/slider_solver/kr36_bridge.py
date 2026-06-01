"""
kr36 与 slider_solver 的桥接入口。

将原先 ``kr36.auto_captcha_cli`` 和 ``kr36.step4_risk`` 的功能收敛到
``utils.slider_solver``，避免在 ``kr36`` 包下维护重复滑块入口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kr36.source_adapter import Kr36SourceAdapter


def project_root_from_slider_solver_package() -> Path:
    """本文件位于 ``src/utils/slider_solver/``，上三级为项目根。"""
    return Path(__file__).resolve().parents[3]


def load_sources_kr36_from_runtime(project_root: Path) -> dict[str, object]:
    """先读取 ``runtime.local.json``，为空时回退到 ``runtime.example.json``。"""
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
    联调专用：开启浏览器 + 自动滑块，并设置关窗驻留时间。
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
    """联调入口：直接复用 ``SliderRiskTool.fetch``。"""
    from utils.slider_solver.session import SliderRiskTool

    return SliderRiskTool(adapter).fetch(url, log_phase="auto-captcha-test")


def try_36kr_step4_html_after_risk(url: str) -> str:
    """
    正文抓取遇风控时的兜底入口：Playwright + 自动滑块。
    """
    from c114.runtime.config import load_c114_runtime_config
    from kr36.source_adapter import Kr36SourceAdapter
    from utils.slider_solver.session import SliderRiskTool

    project = project_root_from_slider_solver_package()
    rc = load_c114_runtime_config(project)
    if not rc.content_kr36_step4_risk_playwright:
        return ""
    if "36kr.com" not in (url or "").lower():
        return ""
    conf = (rc.source_configs or {}).get("kr36", {}) or {}
    adapter = Kr36SourceAdapter(conf)
    return SliderRiskTool(adapter).fetch(url, log_phase="content-fetch-step4")
