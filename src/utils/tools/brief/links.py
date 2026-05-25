"""简报链接和标题推导工具。

本模块只处理 step 6 Markdown 中的标题、关联文件路径和链接行格式。
它不读取业务文件，也不参与 LLM 章节生成。
"""

from __future__ import annotations

import re
from pathlib import Path

_TITLE_SITE_SUFFIX_RE = re.compile(r"_[^_\s]+$")
_YMD_IN_TEXT = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_TRAILING_PAREN_DATE = re.compile(r"\s*[（(]\s*\d{4}-\d{1,2}-\d{1,2}\s*[)）]\s*$")


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
    if name.startswith("kr36_") or name.startswith("36kr_"):
        return "36Kr 主题简报"
    if name.startswith("chip_"):
        return "半导体简报"
    return "C114 主题简报"


def format_brief_link_line(title: str, published_at: str, url: str, *, include_date: bool = True) -> str:
    """把 step 6 链接行统一格式化为标题、日期、链接。专题（/topics/）默认不含日期。"""

    if not include_date:
        return f"- {title} | {url}"
    normalized_date = (published_at or "").strip() or "日期未知"
    return f"- {title} | {normalized_date} | {url}"


def brief_link_dedupe_key(title: str, published_at: str) -> str:
    """与「源地址」行对比用的键：标题去掉末尾 _转载/站点 名，日期规范为 YYYY-MM-DD。"""

    t = (title or "").strip()
    p = (published_at or "").strip()
    while True:
        n = _TITLE_SITE_SUFFIX_RE.sub("", t).strip()
        if n == t:
            break
        t = n
    m = _YMD_IN_TEXT.search(p) or _YMD_IN_TEXT.search(t)
    d = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else p
    t = _TRAILING_PAREN_DATE.sub("", t).strip()
    return f"{t}|{d}"


def bare_title_for_link_dedupe(title: str) -> str:
    """去 _转载 后缀与尾部（日期）后的标题，供补充链接「日期未知」时与源对比。"""

    t = (title or "").strip()
    while True:
        n = _TITLE_SITE_SUFFIX_RE.sub("", t).strip()
        if n == t:
            break
        t = n
    return _TRAILING_PAREN_DATE.sub("", t).strip()


def has_ymd_in_title_or_pub(title: str, published_at: str) -> bool:
    s = f"{published_at or ''} {title or ''}"
    return bool(_YMD_IN_TEXT.search(s))
