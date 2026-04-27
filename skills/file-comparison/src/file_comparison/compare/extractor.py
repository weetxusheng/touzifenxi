"""负责从 Word 文档中抽取章节文本与编号层级。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from .models import NumberingLevel, Section
from ..runtime.dependencies import ensure_python_docx_on_path


SECTION_RE = re.compile(r"(^|\n|\x0c)(第[一二三四五六七八九十百零]+部分\s+[^\n]+)", re.M)


def normalize_title(title: str) -> str:
    """清理章节标题中的分页符、页码和多余空白。"""
    normalized = title.replace("\x0c", "").strip()
    normalized = re.sub(r"\t\d+$", "", normalized)
    normalized = re.sub(r"\s+\d+$", "", normalized)
    return normalized


def section_number(title: str) -> str:
    """从完整章节标题中提取 `第X部分` 形式的章节号。"""
    match = re.match(r"^(第[一二三四五六七八九十百零]+部分)", title)
    if not match:
        raise ValueError(f"无法识别章节号: {title}")
    return match.group(1)


def clean_lines(text: str) -> list[str]:
    """按行清洗文本，移除空行与首尾空白。"""
    return [line.strip() for line in text.replace("\r", "").splitlines() if line.strip()]


def split_sections(text: str) -> list[Section]:
    """把整份文档文本按一级章节切成 `Section` 列表。"""
    start = None
    for marker in ("第一部分  前言\n", "第一部分 前言\n"):
        idx = text.find(marker)
        if idx != -1:
            start = idx
            break
    body = text[start:] if start is not None else text
    matches = list(SECTION_RE.finditer(body))
    sections: list[Section] = []
    for index, match in enumerate(matches):
        title = normalize_title(match.group(2))
        section_start = match.start(2)
        section_end = matches[index + 1].start(2) if index + 1 < len(matches) else len(body)
        content = body[section_start:section_end].strip()
        sections.append(Section(number=section_number(title), title=title, body=content))
    return sections


def int_to_letters(value: int) -> str:
    """把正整数转换成字母序号。"""
    letters = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters


def int_to_roman(value: int) -> str:
    """把正整数转换成罗马数字。"""
    numerals = [
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ]
    result = ""
    for number, numeral in numerals:
        while value >= number:
            result += numeral
            value -= number
    return result


def format_counter(num_format: str, value: int) -> str:
    """按 Word 编号格式把计数器值渲染成对应文本。"""
    if num_format == "lowerLetter":
        return int_to_letters(value)
    if num_format == "upperLetter":
        return int_to_letters(value).upper()
    if num_format == "lowerRoman":
        return int_to_roman(value)
    if num_format == "upperRoman":
        return int_to_roman(value).upper()
    return str(value)


def format_number_label(num_format: str, level_text: str, counters: list[int]) -> str:
    """使用计数器值把 Word 编号模板渲染成最终标签。"""
    label = level_text
    for index, value in enumerate(counters, start=1):
        label = label.replace(f"%{index}", format_counter(num_format, value))
    return label


def read_numbering_levels(path: Path) -> dict[tuple[str, int], NumberingLevel]:
    """从 docx 的 numbering.xml 中读取编号层级定义。"""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with ZipFile(path) as archive:
            numbering_xml = archive.read("word/numbering.xml")
    except KeyError:
        return {}
    root = ET.fromstring(numbering_xml)
    abstract_by_id = {
        abstract.get(f"{{{ns['w']}}}abstractNumId"): abstract
        for abstract in root.findall("w:abstractNum", ns)
    }
    num_to_abstract: dict[str, str] = {}
    for num in root.findall("w:num", ns):
        num_id = num.get(f"{{{ns['w']}}}numId")
        abstract = num.find("w:abstractNumId", ns)
        if num_id is not None and abstract is not None:
            num_to_abstract[num_id] = abstract.get(f"{{{ns['w']}}}val")
    levels: dict[tuple[str, int], NumberingLevel] = {}
    for num_id, abstract_id in num_to_abstract.items():
        abstract = abstract_by_id.get(abstract_id)
        if abstract is None:
            continue
        for level in abstract.findall("w:lvl", ns):
            ilvl = level.get(f"{{{ns['w']}}}ilvl")
            num_fmt = level.find("w:numFmt", ns)
            lvl_text = level.find("w:lvlText", ns)
            if ilvl is None or num_fmt is None or lvl_text is None:
                continue
            levels[(num_id, int(ilvl))] = NumberingLevel(
                num_format=num_fmt.get(f"{{{ns['w']}}}val", "decimal"),
                level_text=lvl_text.get(f"{{{ns['w']}}}val", f"%{int(ilvl) + 1}."),
            )
    return levels


def paragraph_numbering(paragraph) -> tuple[str, int] | None:
    """读取单个段落的编号实例和层级。"""
    p_pr = paragraph._p.pPr
    if p_pr is None or p_pr.numPr is None or p_pr.numPr.numId is None:
        return None
    num_id = str(p_pr.numPr.numId.val)
    ilvl = int(p_pr.numPr.ilvl.val) if p_pr.numPr.ilvl is not None else 0
    return num_id, ilvl


def extract_docx_text(path: Path) -> str:
    """提取 docx 文本，并尽量补回 Word 自动编号。"""
    ensure_python_docx_on_path()
    from docx import Document

    document = Document(path)
    numbering_levels = read_numbering_levels(path)
    counters: dict[str, list[int]] = {}
    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        numbering = paragraph_numbering(paragraph)
        if numbering is not None:
            num_id, ilvl = numbering
            num_counters = counters.setdefault(num_id, [0] * 9)
            num_counters[ilvl] += 1
            for level in range(ilvl + 1, len(num_counters)):
                num_counters[level] = 0
            level = numbering_levels.get((num_id, ilvl))
            if level is not None:
                label = format_number_label(level.num_format, level.level_text, num_counters[: ilvl + 1])
                if not text.startswith(label):
                    text = f"{label}{text}"
        lines.append(text)
    return "\n".join(lines)


def extract_text(path: Path) -> str:
    """根据扩展名选择 docx 提取或 textutil 转文本。"""
    if path.suffix.lower() == ".docx":
        return extract_docx_text(path)
    result = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)], check=True, capture_output=True)
    return result.stdout.decode("utf-8", errors="ignore")


def full_body_without_title(section: Section) -> str:
    """返回去掉首行章节标题后的正文内容。"""
    lines = clean_lines(section.body)
    if lines and lines[0] == section.title:
        lines = lines[1:]
    return "\n".join(lines).strip()


def extract_fund_name(text: str) -> str:
    """从全文里识别基金名称行。"""
    for line in clean_lines(text):
        if "持有期混合型发起式基金中基金（FOF）" in line:
            return line
    raise ValueError("未识别到基金名称")
