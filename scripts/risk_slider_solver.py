"""易盾拼图滑块：自动识别缺口并拖动（Playwright + OpenCV）。

由适配器 ``risk_verification_command`` 调用。实现位于 ``utils.slider_solver.captcha``。

环境变量：

- RISK_VERIFICATION_URL: 当前验证页 URL（适配器会注入；兼容旧名 KR36_RISK_URL）
- PLAYWRIGHT_HEADED_LOAD_WAIT_MS: 有头模式关窗前等待 load/networkidle 的最长毫秒数（兼容 KR36_HEADED_LOAD_WAIT_MS），默认 120000

依赖::

    pip install opencv-python-headless

示例（runtime 配置）::

    "risk_verification_command": "python scripts/risk_slider_solver.py"

仓库外调用须保证 PYTHONPATH 含 ``src``::

    set PYTHONPATH=src && python /path/to/scripts/risk_slider_solver.py

退出码：0 已处理或页面正常；2 缺依赖；3 非易盾拦截页。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.slider_solver.captcha import run_yidun_cli

if __name__ == "__main__":
    run_yidun_cli()
