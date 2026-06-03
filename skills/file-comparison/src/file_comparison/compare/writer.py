"""把比较结果渲染成符合业务格式的 Word 对照表。"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

from ..runtime.dependencies import ensure_python_docx_on_path
from .chunking import text_starts_with_subchapter
from .models import ComparisonRow

CHANGE_BLUE = "0070C0"
DELETE_RED = "C00000"
MAX_UNDERLINE_CHARS = 20
EMBEDDED_EQUAL_MAX_CHARS = 8
EMBEDDED_EQUAL_CONTEXT_MIN_CHARS = 10
HEADING_REPLACE_THRESHOLD = 0.50
BODY_REPLACE_THRESHOLD = 0.90
HEADING_FULL_REPLACE_MAX_CHARS = 30
CHAPTER_HEADING_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百千万\d]+部分\s*")


def ensure_docx_module() -> None:
    """确保运行环境可用 `python-docx`，否则给出明确退出信息。"""
    ensure_python_docx_on_path()
    try:
        import docx  # noqa: F401
        from docx.enum.section import WD_ORIENT  # noqa: F401
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT  # noqa: F401
        from docx.oxml import OxmlElement  # noqa: F401
        from docx.oxml.ns import qn  # noqa: F401
        from docx.shared import Cm, Pt  # noqa: F401
    except ImportError as exc:
        raise SystemExit("缺少 python-docx。请使用 Codex workspace dependencies 的 Python 运行本脚本。") from exc


def new_change_style(text: str, *, size: float = 9.5) -> dict:
    """返回右侧新增或替换文本默认使用的样式。"""
    return {"bold": True, "underline": len(text) <= MAX_UNDERLINE_CHARS, "color": CHANGE_BLUE, "size": size}


def full_replace_new_style(*, size: float = 9.5) -> dict:
    """返回右侧整行替换文本的样式，整行生成时不再加下划线。"""
    return {"bold": True, "underline": False, "color": CHANGE_BLUE, "size": size}


def display_text_with_subchapter(text: str, subchapter: str) -> str:
    """在必要时把子标题补回展示文本首行。"""
    if not subchapter or text in {"新增", "删除"}:
        return text
    if text_starts_with_subchapter(text, subchapter):
        return text
    return f"{subchapter}\n{text}"


def set_cell_text(cell, text: str, *, bold: bool = False):
    """向单元格写入普通文本，并统一字体与段落样式。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    paragraphs = [part for part in text.split("\n") if part != ""]
    cell.text = ""
    for index, paragraph_text in enumerate(paragraphs):
        paragraph = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
        run = paragraph.add_run(paragraph_text)
        run.bold = bold
        run.font.name = "Songti SC"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        run.font.size = Pt(10.5)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.15
    if not paragraphs:
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run("")
        run.font.name = "Songti SC"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        run.font.size = Pt(10.5)


def set_cell_paragraph_runs(cell, paragraphs: list[list[tuple[str, dict]]]):
    """向单元格写入带局部样式的多段 runs。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    cell.text = ""
    for paragraph_index, runs in enumerate(paragraphs):
        paragraph = cell.paragraphs[0] if paragraph_index == 0 else cell.add_paragraph()
        if not runs:
            runs = [("", {"size": 9.5})]
        for text, style in runs:
            run = paragraph.add_run(text)
            run.font.name = "Songti SC"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
            run.font.size = Pt(style.get("size", 9.5))
            run.bold = style.get("bold", False)
            run.font.strike = style.get("strike", False)
            run.underline = style.get("underline", False)
            if style.get("color"):
                run.font.color.rgb = RGBColor.from_string(style["color"])
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.05


def semantic_line_opcodes(old_line: str, new_line: str) -> list[tuple[str, int, int, int, int]]:
    """生成行内 diff，并把被新增长文本包住的短 equal 合并成整体替换。"""
    raw_opcodes = difflib.SequenceMatcher(a=old_line, b=new_line).get_opcodes()
    opcodes: list[tuple[str, int, int, int, int]] = []
    index = 0
    while index < len(raw_opcodes):
        tag, i1, i2, j1, j2 = raw_opcodes[index]
        if (
            tag == "insert"
            and index + 2 < len(raw_opcodes)
            and raw_opcodes[index + 1][0] == "equal"
            and raw_opcodes[index + 2][0] == "insert"
        ):
            equal_tag, equal_i1, equal_i2, equal_j1, equal_j2 = raw_opcodes[index + 1]
            next_tag, next_i1, next_i2, next_j1, next_j2 = raw_opcodes[index + 2]
            equal_text = old_line[equal_i1:equal_i2]
            if equal_tag == "equal" and next_tag == "insert" and 0 < len(equal_text) <= EMBEDDED_EQUAL_MAX_CHARS:
                opcodes.append(("replace", equal_i1, equal_i2, j1, next_j2))
                index += 3
                continue
        opcodes.append((tag, i1, i2, j1, j2))
        index += 1
    return smooth_embedded_equal_opcodes(opcodes)


def opcode_change_size(opcode: tuple[str, int, int, int, int]) -> int:
    """计算一个 diff opcode 涉及的变更上下文长度。"""
    tag, i1, i2, j1, j2 = opcode
    if tag == "equal":
        return 0
    return max(i2 - i1, j2 - j1)


def smooth_embedded_equal_opcodes(
    opcodes: list[tuple[str, int, int, int, int]],
) -> list[tuple[str, int, int, int, int]]:
    """把大段变更中夹杂的短相同片段也纳入替换范围。"""
    smoothed: list[tuple[str, int, int, int, int]] = []
    for index, opcode in enumerate(opcodes):
        tag, i1, i2, j1, j2 = opcode
        equal_length = i2 - i1
        if tag != "equal" or equal_length == 0 or equal_length > EMBEDDED_EQUAL_MAX_CHARS:
            smoothed.append(opcode)
            continue
        previous_change_size = nearby_change_cluster_size(opcodes, index, direction=-1)
        next_change_size = nearby_change_cluster_size(opcodes, index, direction=1)
        has_previous_change = previous_change_size > 0
        has_next_change = next_change_size > 0
        enough_context = previous_change_size + next_change_size >= EMBEDDED_EQUAL_CONTEXT_MIN_CHARS
        terminal_changed_tail = previous_change_size >= EMBEDDED_EQUAL_CONTEXT_MIN_CHARS and not has_next_change
        if has_previous_change and enough_context and (has_next_change or terminal_changed_tail):
            smoothed.append(("replace", i1, i2, j1, j2))
            continue
        smoothed.append(opcode)
    return smoothed


def nearby_change_cluster_size(opcodes: list[tuple[str, int, int, int, int]], index: int, *, direction: int) -> int:
    """统计短 equal 附近连续变更簇的规模，遇到长 equal 才停止。"""
    total = 0
    cursor = index + direction
    while 0 <= cursor < len(opcodes):
        opcode = opcodes[cursor]
        tag, i1, i2, _j1, _j2 = opcode
        if tag == "equal":
            if i2 - i1 > EMBEDDED_EQUAL_MAX_CHARS:
                break
        else:
            total += opcode_change_size(opcode)
        cursor += direction
    return total


def replace_ratio_basis(line: str) -> str:
    """返回用于高比例替换判断的文本，章节标题会剥掉稳定编号前缀。"""
    normalized = CHAPTER_HEADING_PREFIX_RE.sub("", line).strip()
    return normalized or line.strip()


def split_chapter_heading_prefix(line: str) -> tuple[str, str]:
    """把章节标题拆成稳定编号前缀和真实标题正文。"""
    match = CHAPTER_HEADING_PREFIX_RE.match(line)
    if not match:
        return "", line
    return line[: match.end()], line[match.end() :]


def is_heading_like_line(old_line: str, new_line: str) -> bool:
    """判断一组文本是否像章节标题，标题使用更敏感的整行替换阈值。"""
    return (
        bool(CHAPTER_HEADING_PREFIX_RE.match(old_line))
        or bool(CHAPTER_HEADING_PREFIX_RE.match(new_line))
        or max(len(old_line), len(new_line)) <= HEADING_FULL_REPLACE_MAX_CHARS
    )


def line_change_ratio(old_line: str, new_line: str) -> float:
    """计算一行内容的替换比例，用于判断是否应整行标记。"""
    old_basis = replace_ratio_basis(old_line)
    new_basis = replace_ratio_basis(new_line)
    if not old_basis and not new_basis:
        return 0.0
    old_changed = 0
    new_changed = 0
    opcodes = semantic_line_opcodes(old_basis, new_basis)
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag in {"delete", "replace"}:
            old_changed += i2 - i1
        if tag in {"insert", "replace"}:
            new_changed += j2 - j1
        if tag == "equal" and i2 > i1:
            has_change_before = any(previous[0] != "equal" for previous in opcodes[:index])
            has_change_after = any(next_opcode[0] != "equal" for next_opcode in opcodes[index + 1 :])
            if has_change_before and has_change_after and (i2 - i1) <= EMBEDDED_EQUAL_MAX_CHARS:
                old_changed += i2 - i1
                new_changed += j2 - j1
    old_ratio = old_changed / max(len(old_basis), 1)
    new_ratio = new_changed / max(len(new_basis), 1)
    return max(old_ratio, new_ratio)


def should_mark_full_line_replace(old_line: str, new_line: str) -> bool:
    """判断是否应该放弃碎片 diff，改为整行删除/新增标记。"""
    if old_line in {"新增", "删除"} or new_line in {"新增", "删除"}:
        return False
    threshold = HEADING_REPLACE_THRESHOLD if is_heading_like_line(old_line, new_line) else BODY_REPLACE_THRESHOLD
    return line_change_ratio(old_line, new_line) > threshold


def full_replace_old_line_runs(old_line: str) -> list[tuple[str, dict]]:
    """生成左侧整行替换样式，章节编号前缀保持普通样式。"""
    prefix, body = split_chapter_heading_prefix(old_line)
    if prefix and body:
        return [(prefix, {"size": 9.5}), (body, {"strike": True, "color": DELETE_RED, "size": 9.5})]
    return [(old_line, {"strike": True, "color": DELETE_RED, "size": 9.5})]


def full_replace_new_line_runs(new_line: str) -> list[tuple[str, dict]]:
    """生成右侧整行替换样式，章节编号前缀保持普通样式。"""
    prefix, body = split_chapter_heading_prefix(new_line)
    if prefix and body:
        return [(prefix, {"size": 9.5}), (body, full_replace_new_style())]
    return [(new_line, full_replace_new_style())]


def diff_old_line_runs(old_line: str, new_line: str) -> list[tuple[str, dict]]:
    """为左侧旧文本生成精确到字符的删除线样式。"""
    if should_mark_full_line_replace(old_line, new_line):
        return full_replace_old_line_runs(old_line)
    runs: list[tuple[str, dict]] = []
    for tag, i1, i2, _j1, _j2 in semantic_line_opcodes(old_line, new_line):
        if tag == "insert":
            continue
        text = old_line[i1:i2]
        if not text:
            continue
        style = {"strike": tag in {"delete", "replace"}, "size": 9.5}
        if style["strike"]:
            style["color"] = DELETE_RED
        runs.append((text, style))
    return runs


def diff_new_line_runs(old_line: str, new_line: str) -> list[tuple[str, dict]]:
    """为右侧新文本生成精确到字符的蓝色变更样式。"""
    if should_mark_full_line_replace(old_line, new_line):
        return full_replace_new_line_runs(new_line)
    runs: list[tuple[str, dict]] = []
    for tag, _i1, _i2, j1, j2 in semantic_line_opcodes(old_line, new_line):
        if tag == "delete":
            continue
        text = new_line[j1:j2]
        if not text:
            continue
        style = new_change_style(text) if tag in {"insert", "replace"} else {"bold": False, "underline": False, "color": None, "size": 9.5}
        runs.append((text, style))
    changed_chars = sum(len(text) for text, style in runs if style.get("color") == CHANGE_BLUE)
    total_chars = sum(len(text) for text, _style in runs)
    if total_chars > MAX_UNDERLINE_CHARS and changed_chars / total_chars >= 0.45:
        runs = [(text, {**style, "underline": False} if style.get("color") == CHANGE_BLUE else style) for text, style in runs]
    return runs


def build_old_revision_paragraphs(old_text: str, new_text: str) -> list[list[tuple[str, dict]]]:
    """把旧版文本转换成左侧单元格段落 runs。"""
    if old_text == "新增":
        return [[("无", {"size": 9.5})]]
    if new_text == "删除":
        return [[(line, {"strike": True, "color": DELETE_RED, "size": 9.5})] for line in old_text.split("\n")]
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    paragraphs: list[list[tuple[str, dict]]] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            paragraphs.extend([[(line, {"size": 9.5})] for line in old_lines[i1:i2]])
            continue
        if tag == "delete":
            paragraphs.extend([[(line, {"strike": True, "color": DELETE_RED, "size": 9.5})] for line in old_lines[i1:i2]])
            continue
        if tag == "insert":
            continue
        old_group = old_lines[i1:i2]
        new_group = new_lines[j1:j2]
        paired_count = min(len(old_group), len(new_group))
        for index in range(paired_count):
            paragraphs.append(diff_old_line_runs(old_group[index], new_group[index]))
        for line in old_group[paired_count:]:
            paragraphs.append([(line, {"strike": True, "color": DELETE_RED, "size": 9.5})])
    return paragraphs


def build_new_revision_paragraphs(old_text: str, new_text: str) -> list[list[tuple[str, dict]]]:
    """把新版文本转换成右侧单元格段落 runs。"""
    if new_text == "删除":
        return [[("删除", {"bold": True, "color": CHANGE_BLUE, "size": 10})]]
    if old_text == "新增":
        return [[(line, new_change_style(line))] for line in new_text.split("\n")]
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    paragraphs: list[list[tuple[str, dict]]] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            paragraphs.extend([[(line, {"size": 9.5})] for line in new_lines[j1:j2]])
            continue
        if tag == "delete":
            continue
        if tag == "insert":
            paragraphs.extend([[(line, new_change_style(line))] for line in new_lines[j1:j2]])
            continue
        old_group = old_lines[i1:i2]
        new_group = new_lines[j1:j2]
        paired_count = min(len(old_group), len(new_group))
        for index in range(paired_count):
            paragraphs.append(diff_new_line_runs(old_group[index], new_group[index]))
        for line in new_group[paired_count:]:
            paragraphs.append([(line, new_change_style(line))])
    return paragraphs


def shade_cell(cell, fill: str):
    """给表格单元格设置底色。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def cm_to_twips(width_cm: float) -> int:
    """把厘米单位转换成 Word 使用的 twips。"""
    from docx.shared import Cm
    return int(Cm(width_cm).emu / 635)


def set_cell_width(cell, width_cm: float):
    """显式设置单元格宽度，减少渲染器自动拉伸。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm
    cell.width = Cm(width_cm)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.first_child_found_in("w:tcW")
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(cm_to_twips(width_cm)))
    tc_w.set(qn("w:type"), "dxa")


def set_fixed_table_layout(table):
    """把表格布局切成 fixed，避免列宽被内容挤压。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tbl_pr = table._tbl.tblPr
    tbl_layout = tbl_pr.find(qn("w:tblLayout"))
    if tbl_layout is None:
        tbl_layout = OxmlElement("w:tblLayout")
        tbl_pr.append(tbl_layout)
    tbl_layout.set(qn("w:type"), "fixed")


def set_table_widths(table, widths_cm: list[float]):
    """按列宽数组统一设置表格宽度与 grid。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm
    table.autofit = False
    table.allow_autofit = False
    for column, width_cm in zip(table.columns, widths_cm):
        column.width = Cm(width_cm)
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(cm_to_twips(width) for width in widths_cm)))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_grid = table._tbl.tblGrid
    if tbl_grid is None:
        tbl_grid = OxmlElement("w:tblGrid")
        table._tbl.insert(0, tbl_grid)
    for grid_col in list(tbl_grid):
        tbl_grid.remove(grid_col)
    for width_cm in widths_cm:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(cm_to_twips(width_cm)))
        tbl_grid.append(grid_col)


def merge_cells_vertically(table, column_index: int, start_row: int, end_row: int, text: str) -> None:
    """纵向合并章节列的相邻单元格。"""
    if end_row <= start_row:
        return
    merged = table.cell(start_row, column_index).merge(table.cell(end_row, column_index))
    set_cell_text(merged, text, bold=True)


def write_docx(rows: list[ComparisonRow], output_path: Path, old_name: str, new_name: str) -> None:
    """把对照行渲染成最终 docx 文件。"""
    ensure_docx_module()
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.0)
    section.bottom_margin = Cm(1.0)
    section.left_margin = Cm(1.0)
    section.right_margin = Cm(1.0)
    title = document.add_paragraph()
    title.alignment = 1
    title_run = title.add_run(f"《{new_name}》与《{old_name}》对照表")
    title_run.bold = True
    title_run.font.name = "Songti SC"
    title_run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    title_run.font.size = Pt(15)
    table = document.add_table(rows=2, cols=3)
    table.style = "Table Grid"
    set_fixed_table_layout(table)
    widths = [1.4, 8.8, 8.8]
    set_table_widths(table, widths)
    first_header = table.rows[0].cells
    for cell, width in zip(first_header, widths):
        set_cell_width(cell, width)
    set_cell_text(first_header[0], "章节", bold=True)
    set_cell_text(first_header[1], f"原《{old_name}》版本", bold=True)
    set_cell_text(first_header[2], f"修订后《{new_name}》版本", bold=True)
    second_header = table.rows[1].cells
    for cell, width in zip(second_header, widths):
        set_cell_width(cell, width)
    set_cell_text(second_header[0], "", bold=True)
    set_cell_text(second_header[1], "内容", bold=True)
    set_cell_text(second_header[2], "内容", bold=True)
    for row_cells in (first_header, second_header):
        for cell in row_cells:
            shade_cell(cell, "E6EEF7")
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    chapter_groups: list[tuple[int, int, str]] = []
    group_start_index: int | None = None
    current_chapter: str | None = None
    for row in rows:
        cells = table.add_row().cells
        row_index = len(table.rows) - 1
        for cell, width in zip(cells, widths):
            set_cell_width(cell, width)
        if current_chapter != row.chapter:
            if group_start_index is not None and current_chapter is not None:
                chapter_groups.append((group_start_index, row_index - 1, current_chapter))
            group_start_index = row_index
            current_chapter = row.chapter
            set_cell_text(cells[0], row.chapter, bold=True)
        else:
            set_cell_text(cells[0], "", bold=True)
        old_display_text = display_text_with_subchapter(row.old_text, row.subchapter)
        new_display_text = display_text_with_subchapter(row.new_text, row.subchapter)
        set_cell_paragraph_runs(cells[1], build_old_revision_paragraphs(old_display_text, new_display_text))
        set_cell_paragraph_runs(cells[2], build_new_revision_paragraphs(old_display_text, new_display_text))
        for cell in cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    if group_start_index is not None and current_chapter is not None:
        chapter_groups.append((group_start_index, len(table.rows) - 1, current_chapter))
    for start_row, end_row, chapter in chapter_groups:
        merge_cells_vertically(table, 0, start_row, end_row, chapter)
    document.save(output_path)


def convert_docx_to_doc(docx_path: Path, doc_path: Path) -> None:
    """调用系统 textutil 把 docx 另存为旧版 doc。仅 macOS 支持，其他平台跳过。"""
    if sys.platform != "darwin":
        return
    subprocess.run(["textutil", "-convert", "doc", "-output", str(doc_path), str(docx_path)], check=True)
