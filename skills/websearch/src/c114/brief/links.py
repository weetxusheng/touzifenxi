"""简报链接和标题推导工具。

本模块只处理 step 6 Markdown 中的标题、关联文件路径和链接行格式。
它不读取业务文件，也不参与 LLM 章节生成。
"""

from __future__ import annotations

from pathlib import Path


def infer_related_step3_path(step4_or_step5_input_path: Path) -> Path:
    """从 step 4 输入路径推断同目录下的 step 3 搜索结果路径。"""

    name = step4_or_step5_input_path.name
    if "_step_4_content_" in name:
        return step4_or_step5_input_path.with_name(name.replace("_step_4_content_", "_step_3_search_results_"))
    return step4_or_step5_input_path


def infer_brief_title(input_path: Path) -> str:
    """根据输入文件名前缀推断站点简报标题。"""

    name = input_path.name.lower()
    if name.startswith("infoq_"):
        return "InfoQ 主题简报"
    return "C114 主题简报"


def format_brief_link_line(title: str, published_at: str, url: str) -> str:
    """把 step 6 链接行统一格式化为标题、日期、链接。"""

    normalized_date = (published_at or "").strip() or "日期未知"
    return f"- {title} | {normalized_date} | {url}"
