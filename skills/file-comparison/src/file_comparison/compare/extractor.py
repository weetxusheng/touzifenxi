"""负责从 Word 文档中抽取章节文本与编号层级。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .models import NumberingLevel, Section

SECTION_RE = re.compile(r"(^|\n|\x0c)(第[一二三四五六七八九十百零]+部分\s+[^\n]+)", re.M)
WORDPROCESSING_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_NAMESPACES = {"w": WORDPROCESSING_NS}


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


def int_to_chinese_counting(value: int) -> str:
    """把正整数转换成常见中文计数写法。"""
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    if value <= 0:
        return str(value)
    if value < 10:
        return digits[value]
    if value < 10000:
        parts: list[str] = []
        text = str(value)
        length = len(text)
        zero_pending = False
        for index, char in enumerate(text):
            digit = int(char)
            unit = units[length - index - 1]
            if digit == 0:
                zero_pending = bool(parts)
                continue
            if zero_pending:
                parts.append(digits[0])
                zero_pending = False
            if digit == 1 and unit == "十" and not parts:
                parts.append(unit)
            else:
                parts.append(digits[digit] + unit)
        return "".join(parts)
    return str(value)


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
    if num_format == "chineseCounting":
        return int_to_chinese_counting(value)
    return str(value)


def format_number_label(num_format: str, level_text: str, counters: list[int]) -> str:
    """使用计数器值把 Word 编号模板渲染成最终标签。"""
    label = level_text
    for index, value in enumerate(counters, start=1):
        label = label.replace(f"%{index}", format_counter(num_format, value))
    return label


def read_numbering_levels(path: Path) -> dict[tuple[str, int], NumberingLevel]:
    """从 docx 的 numbering.xml 中读取编号层级定义。"""
    try:
        with ZipFile(path) as archive:
            numbering_xml = archive.read("word/numbering.xml")
    except KeyError:
        return {}
    root = ET.fromstring(numbering_xml)
    abstract_by_id = {
        abstract.get(f"{{{WORD_NAMESPACES['w']}}}abstractNumId"): abstract
        for abstract in root.findall("w:abstractNum", WORD_NAMESPACES)
    }
    num_to_abstract: dict[str, str] = {}
    for num in root.findall("w:num", WORD_NAMESPACES):
        num_id = num.get(f"{{{WORD_NAMESPACES['w']}}}numId")
        abstract = num.find("w:abstractNumId", WORD_NAMESPACES)
        if num_id is not None and abstract is not None:
            num_to_abstract[num_id] = abstract.get(f"{{{WORD_NAMESPACES['w']}}}val")
    levels: dict[tuple[str, int], NumberingLevel] = {}
    for num_id, abstract_id in num_to_abstract.items():
        abstract = abstract_by_id.get(abstract_id)
        if abstract is None:
            continue
        for level in abstract.findall("w:lvl", WORD_NAMESPACES):
            ilvl = level.get(f"{{{WORD_NAMESPACES['w']}}}ilvl")
            num_fmt = level.find("w:numFmt", WORD_NAMESPACES)
            lvl_text = level.find("w:lvlText", WORD_NAMESPACES)
            start = level.find("w:start", WORD_NAMESPACES)
            if ilvl is None or num_fmt is None or lvl_text is None:
                continue
            levels[(num_id, int(ilvl))] = NumberingLevel(
                num_format=num_fmt.get(f"{{{WORD_NAMESPACES['w']}}}val", "decimal"),
                level_text=lvl_text.get(f"{{{WORD_NAMESPACES['w']}}}val", f"%{int(ilvl) + 1}."),
                start_value=int(start.get(f"{{{WORD_NAMESPACES['w']}}}val", "1")) if start is not None else 1,
            )
    return levels


def paragraph_numbering_from_xml(paragraph_element: ET.Element) -> tuple[str, int] | None:
    """从段落 XML 读取编号实例和层级。"""
    paragraph_properties = paragraph_element.find("w:pPr", WORD_NAMESPACES)
    if paragraph_properties is None:
        return None
    numbering_properties = paragraph_properties.find("w:numPr", WORD_NAMESPACES)
    if numbering_properties is None:
        return None
    num_id_element = numbering_properties.find("w:numId", WORD_NAMESPACES)
    if num_id_element is None:
        return None
    num_id = str(num_id_element.get(f"{{{WORDPROCESSING_NS}}}val", "")).strip()
    if not num_id:
        return None
    level_element = numbering_properties.find("w:ilvl", WORD_NAMESPACES)
    ilvl = int(level_element.get(f"{{{WORDPROCESSING_NS}}}val", "0")) if level_element is not None else 0
    return num_id, ilvl


def paragraph_text_from_xml(node: ET.Element, *, inside_deleted: bool = False) -> str:
    """按 Word XML 顺序读取段落可见文本，保留 `<w:ins>`，忽略 `<w:del>`。"""
    tag = node.tag
    if tag == f"{{{WORDPROCESSING_NS}}}del":
        return ""

    next_inside_deleted = inside_deleted or tag == f"{{{WORDPROCESSING_NS}}}del"
    parts: list[str] = []
    if not next_inside_deleted:
        if tag == f"{{{WORDPROCESSING_NS}}}t" and node.text:
            parts.append(node.text)
        elif tag == f"{{{WORDPROCESSING_NS}}}tab":
            parts.append("\t")
        elif tag in {f"{{{WORDPROCESSING_NS}}}br", f"{{{WORDPROCESSING_NS}}}cr"}:
            parts.append("\n")
    for child in node:
        parts.append(paragraph_text_from_xml(child, inside_deleted=next_inside_deleted))
    return "".join(parts)


def iter_document_paragraph_elements(path: Path) -> list[ET.Element]:
    """按文档主正文顺序返回 `.docx` 里的段落 XML。"""
    with ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    body = root.find("w:body", WORD_NAMESPACES)
    if body is None:
        return []
    return body.findall("w:p", WORD_NAMESPACES)


def extract_docx_text(path: Path) -> str:
    """提取 docx 文本，并尽量补回 Word 自动编号与修订插入文本。"""
    numbering_levels = read_numbering_levels(path)
    counters: dict[str, list[int]] = {}
    lines: list[str] = []
    for paragraph_element in iter_document_paragraph_elements(path):
        text = paragraph_text_from_xml(paragraph_element).strip()
        if not text:
            continue
        numbering = paragraph_numbering_from_xml(paragraph_element)
        if numbering is not None:
            num_id, ilvl = numbering
            num_counters = counters.setdefault(num_id, [0] * 9)
            level = numbering_levels.get((num_id, ilvl))
            if level is not None and num_counters[ilvl] == 0:
                num_counters[ilvl] = max(level.start_value - 1, 0)
            num_counters[ilvl] += 1
            for child_level in range(ilvl + 1, len(num_counters)):
                num_counters[child_level] = 0
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


def extract_fund_name_or_empty(text: str) -> str:
    """尽力识别基金名称；识别不到时返回空字符串，不中断对照流程。"""
    try:
        return extract_fund_name(text)
    except ValueError:
        return ""
