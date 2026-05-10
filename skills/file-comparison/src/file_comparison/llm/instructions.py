"""结构化比较提示词：默认从 skill 的 prompts 目录加载 Markdown 文件。"""

from __future__ import annotations

from pathlib import Path

_COMPARE_INSTRUCTIONS_FILENAME = "compare-structured-instructions.md"


def default_compare_instructions_path() -> Path:
    """返回 skill 根目录下默认提示词文件路径（与 src 并列的 prompts/）。"""
    # client.py 位于 src/file_comparison/llm/，向上四级到 skills/file-comparison/
    return Path(__file__).resolve().parent.parent.parent.parent / "prompts" / _COMPARE_INSTRUCTIONS_FILENAME


def load_compare_instructions(*, path: Path | None = None) -> str:
    """读取 UTF-8 文本；path 缺省时使用 default_compare_instructions_path()。"""
    resolved = path or default_compare_instructions_path()
    if not resolved.is_file():
        raise RuntimeError(f"missing compare instructions file: {resolved}")
    return resolved.read_text(encoding="utf-8")
