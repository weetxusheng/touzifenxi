"""处理 skill 运行时依赖检查。"""

from __future__ import annotations


def ensure_python_docx_on_path() -> None:
    """确保运行环境能够导入 `python-docx`。"""
    try:
        import docx  # noqa: F401
    except ImportError as exc:
        raise ImportError("缺少 python-docx，请使用项目 uv 环境启动 file-comparison 页面。") from exc
