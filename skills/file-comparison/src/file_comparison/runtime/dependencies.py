"""处理 skill 运行时依赖的按需加载。"""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_SITE_PACKAGES = Path(
    "/Users/xusheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages"
)


def ensure_python_docx_on_path() -> None:
    """确保运行环境能够导入 `python-docx`。"""
    try:
        import docx  # noqa: F401
        return
    except ImportError:
        if WORKSPACE_SITE_PACKAGES.exists():
            sys.path.append(str(WORKSPACE_SITE_PACKAGES))
