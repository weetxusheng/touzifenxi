"""已迁移至 ``scripts/risk_slider_solver.py``；保留本路径以免旧 ``risk_verification_command`` 中断。"""

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
