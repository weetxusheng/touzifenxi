"""负责从 Word 文档中抽取章节文本与编号层级。"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .models import NumberingLevel, Section

# 「第N部分」与标题之间可无空格（如「第一部分前言」）。第一分支只用行内空白（不含换行），
# 避免「第七部分\\n正文」被整段吃进标题；第二分支要求标题以汉字起笔，以区别于异常排版。
SECTION_RE = re.compile(
    r"(^|\n|\x0c)(第(?:[一二三四五六七八九十百零]+|\d+)部分(?:[ \t\x0c]+[^\n]+|(?=[\u4e00-\u9fff])[^\n]+))",
    re.M,
)
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
    match = re.match(r"^(第(?:[一二三四五六七八九十百零]+|\d+)部分)", title)
    if not match:
        raise ValueError(f"无法识别章节号: {title}")
    return match.group(1)


_SECTION_PART_ORDINAL_RE = re.compile(r"^第([一二三四五六七八九十百零]+|\d+)部分$")


def chinese_counting_string_to_int(text: str) -> int | None:
    """把「一」「十二」「二十三」「一百二十三」等中文计数串解析为正整数；无法解析时返回 None。"""
    if not text:
        return None
    digit = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if set(text) - set(digit) - {"十", "百", "千"}:
        return None
    if "千" in text:
        left, _, right = text.partition("千")
        hi = digit[left] if left else 1
        if hi is None:
            return None
        rest = right.lstrip("零") if right else ""
        if not rest:
            return hi * 1000
        low = chinese_counting_string_to_int(rest)
        return None if low is None else hi * 1000 + low
    if "百" in text:
        left, _, right = text.partition("百")
        hi = digit[left] if left else 1
        if left and hi is None:
            return None
        rest = right.lstrip("零") if right else ""
        if not rest:
            return hi * 100
        low = chinese_counting_string_to_int(rest)
        return None if low is None else hi * 100 + low
    if "十" in text:
        left, _, right = text.partition("十")
        tens = digit[left] if left else 1
        if left and tens is None:
            return None
        base = tens * 10
        rest = right.lstrip("零")
        if not rest:
            return base
        if len(rest) != 1 or rest not in digit:
            return None
        return base + digit[rest]
    if len(text) == 1:
        return digit.get(text)
    return None


def section_part_ordinal_sort_key(number: str) -> tuple[int, str]:
    """用于排序的键：匹配 `第N部分` 时按 N 升序；否则排在后面并保持字符串稳定次序。"""
    stripped = str(number).strip()
    match = _SECTION_PART_ORDINAL_RE.match(stripped)
    if not match:
        return (1 << 30, stripped)
    raw = match.group(1)
    ordinal = int(raw) if raw.isdigit() else chinese_counting_string_to_int(raw)
    if ordinal is None:
        return (1 << 30, stripped)
    return (ordinal, stripped)


def ordered_unique_section_numbers(old_sections: list[Section], new_sections: list[Section]) -> list[str]:
    """合并两侧出现的一级章节号，去重后按合同「第N部分」序号排序（与原文阅读顺序一致）。"""
    ordered: list[str] = []
    seen: set[str] = set()
    for section in old_sections + new_sections:
        num = section.number
        if num not in seen:
            seen.add(num)
            ordered.append(num)
    ordered.sort(key=section_part_ordinal_sort_key)
    return ordered


def clean_lines(text: str) -> list[str]:
    """按行清洗文本，移除空行与首尾空白。"""
    return [line.strip() for line in text.replace("\r", "").splitlines() if line.strip()]


def split_sections(text: str) -> list[Section]:
    """把整份文档文本按一级章节切成 `Section` 列表。"""
    # 用 SECTION_RE 找所有候选章节，先跳过文档开头的目录页（标题末尾紧跟页码的条目），
    # 从第一个真正的正文章节开始切；解决「目录用中文数字、正文用阿拉伯数字」时正文章节被误丢的回归。
    body = _select_body_after_toc(text)
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


_ZIP_MAGIC = b"PK\x03\x04"  # docx 实为 zip 包
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # 旧版二进制 .doc/.xls/.ppt(OLE2)


def _detect_office_format(path: Path) -> str:
    """按文件头字节判断真实格式：zip(docx) / ole2(旧 doc) / unknown。

    依据内容而非扩展名，可识别被改名成 .docx 的旧版二进制 .doc，避免误当 zip 解析。
    """
    with open(path, "rb") as handle:
        head = handle.read(8)
    if head[:4] == _ZIP_MAGIC:
        return "zip"
    if head == _OLE2_MAGIC:
        return "ole2"
    return "unknown"


def _extract_legacy_doc_text(path: Path, convert_dir: Path | None = None) -> str:
    """抽取旧版二进制 .doc。按运行环境二选一：

    - Windows：用 Word COM 转成 .docx 后复用富抽取（保留编号与修订标记）。
    - macOS：用系统自带 textutil 转纯文本（降级，无编号/修订还原）。

    convert_dir 非空时，转出的 .docx 会持久落盘到该目录（文件出现即表示转换完成，mtime 为完成时刻）；
    为空时退回临时目录、抽取后即删，保持旧行为。仅 Windows 转换分支会产出落盘 .docx。
    """
    if sys.platform == "win32":
        if convert_dir is not None:
            docx_path = convert_doc_to_docx(path, convert_dir)
            return extract_docx_text(docx_path)
        with tempfile.TemporaryDirectory() as tmp_dir:
            docx_path = convert_doc_to_docx(path, Path(tmp_dir))
            return extract_docx_text(docx_path)
    if sys.platform == "darwin":
        result = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)], check=True, capture_output=True)
        return result.stdout.decode("utf-8", errors="ignore")
    raise RuntimeError(f"当前平台不支持解析旧版 .doc（仅支持 Windows/macOS）: {path.name}")


def extract_text(path: Path, convert_dir: Path | None = None) -> str:
    """抽取文档文本：只有真正的 docx(zip 包) 才直接解析，其余一律先经 Office 引擎转 docx。

    关键不变量：**绝不把非 zip 文件喂给 zipfile**。按文件头字节判断真实格式而非扩展名，
    凡不是 zip 的输入（旧版二进制 .doc/OLE2、RTF/HTML 误存成 .docx、被改名的文件等）都走
    `_extract_legacy_doc_text` 交给 Word(Win)/textutil(mac) 处理，从根上消除「File is not a zip file」。
    convert_dir 传入时，转换产物 .docx 会落盘到该目录，作为「转换完成」的可观测信号。
    """
    if _detect_office_format(path) == "zip":
        return extract_docx_text(path)
    return _extract_legacy_doc_text(path, convert_dir)


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


def convert_doc_to_docx(src: Path, out_dir: Path) -> Path:
    """Windows 上调用 Word COM 把旧版 .doc 另存为 .docx；需本机已安装 Microsoft Word 与 pywin32。

    架构约定：仅 Windows 走此路径；macOS 走 textutil（见 `_extract_legacy_doc_text`），不在此处转换。
    """
    if sys.platform != "win32":
        raise RuntimeError(f"convert_doc_to_docx 仅支持 Windows(Word COM)，当前平台无法转换: {src.name}")
    if not src.exists():
        raise RuntimeError(f"待转换文件不存在: {src}")
    try:
        import win32com.client  # 仅 Windows 且装有 Word、pywin32 时可用
    except ImportError as exc:
        raise RuntimeError(
            f"无法解析 {src.name}：缺少 pywin32(win32com)。请执行 `uv add pywin32` 后再跑一次 "
            r"`uv run python .venv\Scripts\pywin32_postinstall.py -install`，并确保本机已安装 Microsoft Word。"
        ) from exc

    # Word/COM 常量（避免依赖 makepy，直接用数值）：
    # wdFormatXMLDocument=12（显式 .docx），wdDoNotSaveChanges=0，wdAlertsNone=0，msoAutomationSecurityForceDisable=3。
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{src.stem}.docx"

    word = win32com.client.DispatchEx("Word.Application")
    document = None
    try:
        word.Visible = False
        # P0：屏蔽所有交互弹窗，否则带「可信文档/启用编辑/宏」警告或密码保护的 .doc 会让 Word 在 COM 模式下卡死。
        try:
            word.DisplayAlerts = 0
        except Exception:  # noqa: BLE001 - 不同 Word 版本属性差异，失败不致命
            pass
        try:
            word.AutomationSecurity = 3
        except Exception:  # noqa: BLE001
            pass
        try:
            document = word.Documents.Open(
                FileName=str(src.resolve()),
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                PasswordDocument=" ",  # 给一个非空假密码：有真密码时 Word 直接报错而非弹框
                Visible=False,
            )
            # 必须用 wdFormatXMLDocument(12) 显式写 .docx；wdFormatDocumentDefault(16) 受机器默认格式影响，
            # 在默认为 .doc 的 Word 上会写出 OLE2 二进制（文件名带 .docx 但内容仍是旧 doc），后续按 zip 解析必然失败。
            document.SaveAs(str(target.resolve()), FileFormat=12)
        except Exception as exc:  # noqa: BLE001 - 把 COM hresult 包装成可读中文，原异常保留在 __cause__
            raise RuntimeError(
                f"Word 转换 .doc 失败: {src.name}（{type(exc).__name__}: {exc}）。"
                "请确认：本机 Word 可正常打开该文件、文件未被其他程序占用、未启用密码保护或受保护视图。"
            ) from exc
    finally:
        # P0：无论是否成功，都先关 document（不保存改动），再 Quit，避免 Word 在「是否保存」对话框上卡死、进程残留。
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:  # noqa: BLE001
                pass
        try:
            word.Quit()
        except Exception:  # noqa: BLE001
            pass

    if not target.exists():
        raise RuntimeError(f"Word 转换未产出 docx: {src.name}")
    # 校验真实写入的就是 docx(zip)，不是 OLE2——避免「文件名带 .docx 但内容是 .doc」导致后续 zipfile 报错。
    with open(target, "rb") as handle:
        head = handle.read(4)
    if head != b"PK\x03\x04":
        raise RuntimeError(
            f"Word 写出的文件不是真 docx(zip)，实际文件头 {head!r}: {target.name}。"
            "通常是 Word 默认保存格式被设成兼容模式，或文件被另存为模板/PDF；请检查源文件是否完好。"
        )
    return target


# === 进程级 Word COM 串行锁（追加在文件尾，遵守新业务放末尾规则） ===
# 同一 Python 进程内，确保任何时刻只有一个 convert_doc_to_docx 在调用 Word，
# 避免「上传期后台预转换」「生成期 fallback 现场转换」「用户连续多次上传」共存时
# 启动多个 winword.exe 互抢 normal.dot/正在打开的临时文件等单例资源。
import threading as _threading  # noqa: E402 - 末尾追加，避免动顶部导入块

_WORD_COM_LOCK = _threading.Lock()
_original_convert_doc_to_docx = convert_doc_to_docx


def convert_doc_to_docx(src, out_dir):  # type: ignore[no-redef]
    """串行化包装：所有 .doc -> .docx 调用全局共享一把锁。"""
    with _WORD_COM_LOCK:
        return _original_convert_doc_to_docx(src, out_dir)


# === 跳过文档开头的目录页（追加在文件尾，遵守新业务放末尾规则） ===
# 一些合同：目录用中文数字（第一部分\t前言\t1）+ 正文用阿拉伯数字（第4部分基金份额的发售），
# 如果直接从首个 SECTION_RE 匹配处切，会把目录页当成 24 个章节、正文全被塞进最后一个章节。
# 这里识别「标题末尾紧跟页码（Tab/空格 + 数字）」的目录条目，跳过整段连续目录页，
# 从第一个非目录条目的章节开始切。
_TOC_TAIL_RE = re.compile(r"[\t \xa0]+\d+\s*$")


def _looks_like_toc_entry(title: str) -> bool:
    """末尾紧跟 Tab/空格 + 纯数字 -> 视为目录条目（页码引用），不当正文章节。"""
    return bool(_TOC_TAIL_RE.search(title))


def _select_body_after_toc(text: str) -> str:
    """跳过开头的目录条目，返回从第一个真正正文章节开始的文本。

    向后兼容：没有 SECTION_RE 命中或全部是目录条目时，回退到完整文本（与旧实现等价）。
    """
    for match in SECTION_RE.finditer(text):
        if not _looks_like_toc_entry(match.group(2)):
            return text[match.start(2):]
    return text
