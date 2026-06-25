"""把比较结果渲染成符合业务格式的 Word 对照表。"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

from ..runtime.dependencies import ensure_python_docx_on_path
from .chunking import OMITTED_EQUAL_MARKER, text_starts_with_subchapter
from .models import ComparisonRow

# 以下渲染常量是默认值，可被 config/runtime.local.json 的 render 段覆盖，
# 无需改代码（见 apply_render_config 与 runtime.config.RenderRuntimeConfig）。
# 颜色：右侧差异文本字色（修改这两处即可改变对照表的变更/删除字色）
CHANGE_BLUE = "0070C0"  # 右侧“新增/替换”文本字色（蓝）
DELETE_RED = "C00000"  # 左侧“删除”文本字色（红，配合删除线）
# 左侧差异文本是否加粗的总开关：
#   True  = 左侧“删除/替换”文本（红字删除线）加粗，与右侧蓝字一致；
#   False = 左侧维持常规字重，仅保留删除线 + 红色。
OLD_CHANGE_BOLD = True
# 右侧差异文本加下划线的字数上限。注意：按“单个连续变更片段(diff run)”判定，
# 不是按整行/整段——单个片段长度 <= 该值才给下划线（见 new_change_style）。
# 已知局限：因为按片段判定，一行里若有多个各自 <= 该值的零散变更，会各自带下划线；
#   只有当“整行/段变更字符占比 >= UNDERLINE_DENSITY_RATIO”时，密度兜底才统一去掉下划线
#   （见 diff_new_line_runs / _adjust_new_paragraph_underline）。
#   因此一行里实际带下划线的文字总量可能远超该值，并非“全文只给 <=20 字加下划线”。
MAX_UNDERLINE_CHARS = 20
EMBEDDED_EQUAL_MAX_CHARS = 8
EMBEDDED_EQUAL_CONTEXT_MIN_CHARS = 10
HEADING_REPLACE_THRESHOLD = 0.50
BODY_REPLACE_THRESHOLD = 0.90
HEADING_FULL_REPLACE_MAX_CHARS = 30
CHAPTER_HEADING_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百千万\d]+部分\s*")

# 颜色：表头底色（差异文本字色见文件顶部 CHANGE_BLUE / DELETE_RED）
HEADER_FILL = "E6EEF7"  # 表头单元格底色（浅蓝）

# 右侧某行/段“变更密度”阈值：变更字符 / 总字符 >= 该值时，去掉该段所有蓝字的下划线。
# 与 MAX_UNDERLINE_CHARS 共同生效（见 diff_new_line_runs / _adjust_new_paragraph_underline）：
#   仅当“段总字符 > MAX_UNDERLINE_CHARS 且 变更占比 >= UNDERLINE_DENSITY_RATIO”两个条件同时满足，
#   才统一取消下划线（用于“整段大面积改写”的场景，避免半行都是带下划线的蓝字）。
# 例：释义段总长 90、变更 41（占比 0.46）大于 0.45，且 90 大于 20，因此整段蓝字不加下划线。
# 调大该值会让更多段落保留下划线；调小会让更多段落被去掉下划线。
UNDERLINE_DENSITY_RATIO = 0.45

# 下划线智能控制总开关：
#   True  = 维持智能判定：按 MAX_UNDERLINE_CHARS（单段长度）+ UNDERLINE_DENSITY_RATIO（变更密度）
#           共同决定右侧蓝字是否加下划线（两个去线条件 AND 同时满足才去掉下划线）。
#   False = 关闭智能判定：右侧任何变更文本（蓝字）一律加下划线（“有变动就下划线”），
#           不再受 MAX_UNDERLINE_CHARS / UNDERLINE_DENSITY_RATIO 影响。
SMART_UNDERLINE = True

# 字体
FONT_LATIN = "Songti SC"
FONT_EAST_ASIA = "宋体"

# 字号（pt）
SIZE_BODY = 9.5
SIZE_CELL = 10.5
SIZE_TITLE = 15
SIZE_DELETE_MARK = 10

# 段落间距
SPACE_AFTER_PT = 0
LINE_SPACING_CELL = 1.15
LINE_SPACING_RUNS = 1.05

# 页面与表格布局
PAGE_WIDTH_CM = 21
PAGE_HEIGHT_CM = 29.7
PAGE_MARGIN_CM = 1.0
COL_WIDTHS_CM = [1.4, 8.8, 8.8]
TABLE_STYLE = "Table Grid"
TITLE_ALIGN_CENTER = 1
EMU_PER_TWIP = 635

# 标记 / 占位文本
MARK_INSERT = "新增"
MARK_DELETE = "删除"
PLACEHOLDER_NONE = "无"
HEADER_CHAPTER = "章节"
HEADER_CONTENT = "内容"


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


def apply_render_config(render) -> None:
    """把运行配置里的渲染参数写回本模块全局变量。

    在 `write_docx` 之前调用一次即可生效（见 `RenderRuntimeConfig`）；
    不调用时维持文件顶部的默认值，保证单元测试与历史行为不变。
    """
    global OLD_CHANGE_BOLD, SMART_UNDERLINE, MAX_UNDERLINE_CHARS, UNDERLINE_DENSITY_RATIO
    global CHANGE_BLUE, DELETE_RED, HEADER_FILL
    global FONT_LATIN, FONT_EAST_ASIA
    global SIZE_BODY, SIZE_CELL, SIZE_TITLE, SIZE_DELETE_MARK
    OLD_CHANGE_BOLD = render.old_change_bold
    SMART_UNDERLINE = render.smart_underline
    MAX_UNDERLINE_CHARS = render.max_underline_chars
    UNDERLINE_DENSITY_RATIO = render.underline_density_ratio
    CHANGE_BLUE = render.change_color
    DELETE_RED = render.delete_color
    HEADER_FILL = render.header_fill
    FONT_LATIN = render.font_latin
    FONT_EAST_ASIA = render.font_east_asia
    SIZE_BODY = render.size_body
    SIZE_CELL = render.size_cell
    SIZE_TITLE = render.size_title
    SIZE_DELETE_MARK = render.size_delete_mark


def new_change_style(text: str, *, size: float | None = None) -> dict:
    """返回右侧新增或替换文本默认使用的样式。"""
    size = SIZE_BODY if size is None else size
    return {"bold": True, "underline": len(text) <= MAX_UNDERLINE_CHARS, "color": CHANGE_BLUE, "size": size}


def full_replace_new_style(*, size: float | None = None) -> dict:
    """返回右侧整行替换文本的样式，整行生成时不再加下划线。"""
    size = SIZE_BODY if size is None else size
    return {"bold": True, "underline": False, "color": CHANGE_BLUE, "size": size}


def old_change_style(*, size: float | None = None) -> dict:
    """返回左侧删除或替换文本的样式，是否加粗由 OLD_CHANGE_BOLD 控制。"""
    size = SIZE_BODY if size is None else size
    return {"strike": True, "color": DELETE_RED, "bold": OLD_CHANGE_BOLD, "size": size}


def display_text_with_subchapter(text: str, subchapter: str) -> str:
    """在必要时把子标题补回展示文本首行。"""
    if not subchapter or text in {MARK_INSERT, MARK_DELETE}:
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
        run.font.name = FONT_LATIN
        run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_EAST_ASIA)
        run.font.size = Pt(SIZE_CELL)
        paragraph.paragraph_format.space_after = Pt(SPACE_AFTER_PT)
        paragraph.paragraph_format.line_spacing = LINE_SPACING_CELL
    if not paragraphs:
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run("")
        run.font.name = FONT_LATIN
        run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_EAST_ASIA)
        run.font.size = Pt(SIZE_CELL)


def set_cell_paragraph_runs(cell, paragraphs: list[list[tuple[str, dict]]]):
    """向单元格写入带局部样式的多段 runs。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    cell.text = ""
    for paragraph_index, runs in enumerate(paragraphs):
        paragraph = cell.paragraphs[0] if paragraph_index == 0 else cell.add_paragraph()
        if not runs:
            runs = [("", {"size": SIZE_BODY})]
        for text, style in runs:
            run = paragraph.add_run(text)
            run.font.name = FONT_LATIN
            run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_EAST_ASIA)
            run.font.size = Pt(style.get("size", SIZE_BODY))
            run.bold = style.get("bold", False)
            run.font.strike = style.get("strike", False)
            if SMART_UNDERLINE:
                run.underline = style.get("underline", False)
            else:
                # 关闭智能控制：右侧任何变更（蓝字）一律加下划线
                run.underline = style.get("color") == CHANGE_BLUE
            if style.get("color"):
                run.font.color.rgb = RGBColor.from_string(style["color"])
        paragraph.paragraph_format.space_after = Pt(SPACE_AFTER_PT)
        paragraph.paragraph_format.line_spacing = LINE_SPACING_RUNS


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
    if old_line in {MARK_INSERT, MARK_DELETE} or new_line in {MARK_INSERT, MARK_DELETE}:
        return False
    threshold = HEADING_REPLACE_THRESHOLD if is_heading_like_line(old_line, new_line) else BODY_REPLACE_THRESHOLD
    return line_change_ratio(old_line, new_line) > threshold


def full_replace_old_line_runs(old_line: str) -> list[tuple[str, dict]]:
    """生成左侧整行替换样式，章节编号前缀保持普通样式。"""
    prefix, body = split_chapter_heading_prefix(old_line)
    if prefix and body:
        return [(prefix, {"size": SIZE_BODY}), (body, old_change_style())]
    return [(old_line, old_change_style())]


def full_replace_new_line_runs(new_line: str) -> list[tuple[str, dict]]:
    """生成右侧整行替换样式，章节编号前缀保持普通样式。"""
    prefix, body = split_chapter_heading_prefix(new_line)
    if prefix and body:
        return [(prefix, {"size": SIZE_BODY}), (body, full_replace_new_style())]
    return [(new_line, full_replace_new_style())]


def line_render_opcodes(old_line: str, new_line: str) -> list[tuple[str, int, int, int, int]]:
    """根据行的尺寸决定是否启用 ``smooth_embedded_equal_opcodes``。

    - heading-like 行（章节前缀或 max ≤ HEADING_FULL_REPLACE_MAX_CHARS）：保留 smoothing，
      短的等号片段（标点、衔接词）会被吸进周边 replace，呈现"整段被改"的视觉。
    - body 行：使用 raw ``difflib`` opcodes。长正文里的短等号片段（如尾句句号、保留短语
      "不列入基金财产。"、"，而是从"）都是真实未改动内容，不该被错染成删除。
    """
    if is_heading_like_line(old_line, new_line):
        return semantic_line_opcodes(old_line, new_line)
    return list(difflib.SequenceMatcher(a=old_line, b=new_line).get_opcodes())


def diff_old_line_runs(old_line: str, new_line: str) -> list[tuple[str, dict]]:
    """为左侧旧文本生成精确到字符的删除线样式。"""
    if should_mark_full_line_replace(old_line, new_line):
        return full_replace_old_line_runs(old_line)
    runs: list[tuple[str, dict]] = []
    for tag, i1, i2, _j1, _j2 in line_render_opcodes(old_line, new_line):
        if tag == "insert":
            continue
        text = old_line[i1:i2]
        if not text:
            continue
        style = old_change_style() if tag in {"delete", "replace"} else {"size": SIZE_BODY}
        runs.append((text, style))
    return runs


def diff_new_line_runs(old_line: str, new_line: str) -> list[tuple[str, dict]]:
    """为右侧新文本生成精确到字符的蓝色变更样式。"""
    if should_mark_full_line_replace(old_line, new_line):
        return full_replace_new_line_runs(new_line)
    runs: list[tuple[str, dict]] = []
    for tag, _i1, _i2, j1, j2 in line_render_opcodes(old_line, new_line):
        if tag == "delete":
            continue
        text = new_line[j1:j2]
        if not text:
            continue
        style = new_change_style(text) if tag in {"insert", "replace"} else {"bold": False, "underline": False, "color": None, "size": SIZE_BODY}
        runs.append((text, style))
    changed_chars = sum(len(text) for text, style in runs if style.get("color") == CHANGE_BLUE)
    total_chars = sum(len(text) for text, _style in runs)
    if total_chars > MAX_UNDERLINE_CHARS and changed_chars / total_chars >= UNDERLINE_DENSITY_RATIO:
        runs = [(text, {**style, "underline": False} if style.get("color") == CHANGE_BLUE else style) for text, style in runs]
    return runs


def _adjust_new_paragraph_underline(runs: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """与 diff_new_line_runs 一致：变化密度过大时去掉下划线，避免整段下划。"""
    changed_chars = sum(len(text) for text, style in runs if style.get("color") == CHANGE_BLUE)
    total_chars = sum(len(text) for text, _style in runs)
    if total_chars > MAX_UNDERLINE_CHARS and changed_chars / total_chars >= UNDERLINE_DENSITY_RATIO:
        return [
            (text, {**style, "underline": False} if style.get("color") == CHANGE_BLUE else style)
            for text, style in runs
        ]
    return runs


def cross_newline_old_paragraphs(old_text: str, new_text: str) -> list[list[tuple[str, dict]]]:
    """旧侧跨段字符级 diff：把 ``\\n`` 视为普通字符参与对齐，遇到换行就切段。

    用于 replace opcode 中两侧行数不一致的场景，例如旧侧一段被新侧拆成两段、
    或新侧把两段合并成一段；此时按行配对会把"段内插入换行 + 句内细微删除"放大成整段标红。

    刻意使用 ``difflib.SequenceMatcher.get_opcodes`` 而非 ``semantic_line_opcodes``：
    跨段长文本里，``smooth_embedded_equal_opcodes`` 会把真正未改动的短片段（如 8 字符以内的常用短句）
    错误地吸收进周边变更而打上删除线，这是为单行短文本设计的策略，长文本不适用。
    """
    paragraphs: list[list[tuple[str, dict]]] = []
    current: list[tuple[str, dict]] = []
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(a=old_text, b=new_text).get_opcodes():
        if tag == "insert":
            continue
        segment = old_text[i1:i2]
        if not segment:
            continue
        is_strike = tag in {"delete", "replace"}
        parts = segment.split("\n")
        for index, part in enumerate(parts):
            if part:
                style: dict = old_change_style() if is_strike else {"size": SIZE_BODY}
                current.append((part, style))
            if index < len(parts) - 1:
                paragraphs.append(current)
                current = []
    paragraphs.append(current)
    return paragraphs


def cross_newline_new_paragraphs(old_text: str, new_text: str) -> list[list[tuple[str, dict]]]:
    """新侧跨段字符级 diff：与 ``cross_newline_old_paragraphs`` 镜像，遇到换行切段。

    同样刻意使用 raw ``difflib`` opcodes，避免 smoothing 把未改动的短片段染色。
    """
    paragraphs: list[list[tuple[str, dict]]] = []
    current: list[tuple[str, dict]] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=old_text, b=new_text).get_opcodes():
        if tag == "delete":
            continue
        segment = new_text[j1:j2]
        if not segment:
            continue
        is_change = tag in {"insert", "replace"}
        parts = segment.split("\n")
        for index, part in enumerate(parts):
            if part:
                if is_change:
                    current.append((part, new_change_style(part)))
                else:
                    current.append((part, {"bold": False, "underline": False, "color": None, "size": SIZE_BODY}))
            if index < len(parts) - 1:
                paragraphs.append(_adjust_new_paragraph_underline(current))
                current = []
    paragraphs.append(_adjust_new_paragraph_underline(current))
    return paragraphs


def build_old_revision_paragraphs(old_text: str, new_text: str) -> list[list[tuple[str, dict]]]:
    """把旧版文本转换成左侧单元格段落 runs。"""
    if old_text == MARK_INSERT:
        return [[(PLACEHOLDER_NONE, {"size": SIZE_BODY})]]
    if new_text == MARK_DELETE:
        return [[(line, old_change_style())] for line in old_text.split("\n")]
    return _build_old_paragraphs_with_segment_alignment(
        old_text.split("\n"), new_text.split("\n")
    )


def build_new_revision_paragraphs(old_text: str, new_text: str) -> list[list[tuple[str, dict]]]:
    """把新版文本转换成右侧单元格段落 runs。"""
    if new_text == MARK_DELETE:
        return [[(MARK_DELETE, {"bold": True, "color": CHANGE_BLUE, "size": SIZE_DELETE_MARK})]]
    if old_text == MARK_INSERT:
        return [[(line, new_change_style(line))] for line in new_text.split("\n")]
    return _build_new_paragraphs_with_segment_alignment(
        old_text.split("\n"), new_text.split("\n")
    )


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
    return int(Cm(width_cm).emu / EMU_PER_TWIP)


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
    section.page_width = Cm(PAGE_WIDTH_CM)
    section.page_height = Cm(PAGE_HEIGHT_CM)
    section.top_margin = Cm(PAGE_MARGIN_CM)
    section.bottom_margin = Cm(PAGE_MARGIN_CM)
    section.left_margin = Cm(PAGE_MARGIN_CM)
    section.right_margin = Cm(PAGE_MARGIN_CM)
    title = document.add_paragraph()
    title.alignment = TITLE_ALIGN_CENTER
    title_run = title.add_run(f"《{new_name}》与《{old_name}》对照表")
    title_run.bold = True
    title_run.font.name = FONT_LATIN
    title_run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_EAST_ASIA)
    title_run.font.size = Pt(SIZE_TITLE)
    table = document.add_table(rows=2, cols=3)
    table.style = TABLE_STYLE
    set_fixed_table_layout(table)
    widths = COL_WIDTHS_CM
    set_table_widths(table, widths)
    first_header = table.rows[0].cells
    for cell, width in zip(first_header, widths):
        set_cell_width(cell, width)
    set_cell_text(first_header[0], HEADER_CHAPTER, bold=True)
    set_cell_text(first_header[1], f"原《{old_name}》版本", bold=True)
    set_cell_text(first_header[2], f"修订后《{new_name}》版本", bold=True)
    second_header = table.rows[1].cells
    for cell, width in zip(second_header, widths):
        set_cell_width(cell, width)
    set_cell_text(second_header[0], "", bold=True)
    set_cell_text(second_header[1], HEADER_CONTENT, bold=True)
    set_cell_text(second_header[2], HEADER_CONTENT, bold=True)
    for row_cells in (first_header, second_header):
        for cell in row_cells:
            shade_cell(cell, HEADER_FILL)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    chapter_groups: list[tuple[int, int, str]] = []
    group_start_index: int | None = None
    current_chapter: str | None = None
    # 跨行 subchapter 去重状态：同一章节内，只在首次出现的小节标题前画一次，后续 row 不再 prepend。
    last_subchapter: str | None = None
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
            last_subchapter = None  # 进入新章节，subchapter 也重置
            set_cell_text(cells[0], row.chapter, bold=True)
        else:
            set_cell_text(cells[0], "", bold=True)
        show_subchapter_prefix, next_last_subchapter = decide_subchapter_prefix(row, last_subchapter)
        effective_subchapter = row.subchapter if show_subchapter_prefix else ""
        old_display_text = display_text_with_subchapter(row.old_text, effective_subchapter)
        new_display_text = display_text_with_subchapter(row.new_text, effective_subchapter)
        last_subchapter = next_last_subchapter
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


# === subchapter 跨行去重判定（追加在文件尾，遵守新业务放末尾规则） ===
# 解决任务 20260615-190107 截图问题：同一 chapter+subchapter 下的多个 ComparisonRow
# 在 cell 文本里被每条 prepend 一遍 subchapter（writer 旧逻辑），视觉上小节标题重复。
# 现在改成：chapter 不变 + subchapter 相同 ⇒ 后续行不再 prepend，subchapter 只在首行显示一次。


def decide_subchapter_prefix(
    row: "ComparisonRow", last_subchapter: str | None
) -> tuple[bool, str | None]:
    """判定当前 row 是否需要把 subchapter prepend 到 cell 文本里。

    返回:
        (show_prefix, next_last_subchapter)
        - show_prefix: True 表示需要把 subchapter 拼进 cell 文本，False 表示直接用 row 原文本
        - next_last_subchapter: 写完本行后用于下一行判定的「上一次显示过的 subchapter」状态

    判定规则:
        - subchapter 与 last_subchapter 不同：show_prefix=True，更新 last_subchapter=本行 subchapter。
        - subchapter 与 last_subchapter 相同：show_prefix=False，状态保持。

    注意：chapter 切换由主循环处理，进入新章节前会把 last_subchapter 重置为 None；
    本函数只关心同一章节内的 subchapter 关系，不接收 chapter 字段。
    """
    if row.subchapter != (last_subchapter or ""):
        return True, row.subchapter
    return False, last_subchapter


# === 整段删除/新增行的 subchapter 标题渲染保护（追加在文件尾，遵守新业务放末尾规则） ===
# 解决 qus11 现象：当 row.new_text == MARK_DELETE 或 row.old_text == MARK_INSERT 时，
# 主循环里 build_*_revision_paragraphs 会触发"整段标红/整段标蓝"分支，把已经被
# display_text_with_subchapter prepend 上去的 subchapter 标题行也一并染色。
# 本函数把 subchapter 拆成独立的普通样式段落 + 真实正文，规避误染。
#
# 注意：当前未被主循环调用，先以独立函数形式留在这里供后续集成审定使用。


def build_marker_row_paragraphs_with_clean_subchapter(
    row: "ComparisonRow",
    effective_subchapter: str,
) -> tuple[list[list[tuple[str, dict]]], list[list[tuple[str, dict]]]]:
    """对"整段删除/整段新增"行,把 subchapter 抽成独立的普通样式段落,避免被整段染色。

    返回 (old_paragraphs, new_paragraphs);调用方负责用 set_cell_paragraph_runs 写入对应 cell。
    仅当 row 是 marker 行 (new=MARK_DELETE 或 old=MARK_INSERT) 且 effective_subchapter 非空时,
    这种处理才有意义；其它场景应继续走原 display_text_with_subchapter + build_*_revision_paragraphs 路径。
    """
    old_paragraphs = build_old_revision_paragraphs(row.old_text, row.new_text)
    new_paragraphs = build_new_revision_paragraphs(row.old_text, row.new_text)
    if not effective_subchapter:
        return old_paragraphs, new_paragraphs
    subchapter_header = [(effective_subchapter, {"size": SIZE_BODY})]
    return [subchapter_header, *old_paragraphs], [subchapter_header, *new_paragraphs]


# === 按 `......` marker 切段后按段内容相似度对齐的左/右侧渲染 ===
# 解决 qus17 现象: `merge_same_subchapter_rows_with_ellipsis` 把同 subchapter 多条 row 合成一格后,
# 两侧 `......` marker 数量常不等。原 build_*_revision_paragraphs 把每一行 `......` 当成 line-equal
# 锚点交给 difflib LCS, 锚点错配后, 后续不相关的两段被打进同一个 replace opcode, 经 cross_newline_*
# 字符级 diff 产生纯巧合的"部分文字匹配"。本组函数先按 `......` 切段, 再按"段-段内容相似度"做单调 DP
# 对齐, 已配对段在更短范围内复用原行级 diff (`_segment_*_paragraphs_from_lines`), 未配对段整段渲染成
# pure delete (左侧) / pure insert (右侧), 从根本上消除锚点错配。
#
# 与原行为的兼容性: 0 marker 行 segments=1, 必然 1↔1 配对, 完全等价于原行级 diff; 两侧 marker 数相等
# 且语义对齐时 DP 也按位配对, 段内渲染等价。仅当 marker 数不等或两侧段内容差异极大时, 段相似度门槛会
# 把"巧合相似"挡掉, 把它当 pure delete + pure insert 渲染——这正是 qus17 想要的修复。

# 段相似度阈值: 低于该值不允许配对, 避免 difflib.ratio 在无关长文本上偶然给出非零相似度 (常见
# 0.1~0.2) 被 DP 误并。同时高于一般"换字/拆段"的真实修订相似度门槛 (实测真实配对 0.50~1.00, 不相关
# 段 ≤ 0.16, 见 qus17 row 61 相似度矩阵)。
_SEGMENT_ALIGNMENT_SIMILARITY_THRESHOLD = 0.30


def _split_lines_by_omitted_marker(lines: list[str]) -> list[list[str]]:
    """以 `......` 行为分隔符把行列表切成段; 首/尾或连续 marker 会产生空段, 用于在渲染时还原 marker 位置。"""
    segments: list[list[str]] = [[]]
    for line in lines:
        if line.strip() == OMITTED_EQUAL_MARKER:
            segments.append([])
        else:
            segments[-1].append(line)
    return segments


def _segment_pair_similarity(old_segment: list[str], new_segment: list[str]) -> float:
    """两段非空行拼起来的字符级相似度比 0~1; 空段对空段 1.0, 单空段 0.0。"""
    old_text = "\n".join(line for line in old_segment if line.strip())
    new_text = "\n".join(line for line in new_segment if line.strip())
    if not old_text and not new_text:
        return 1.0
    if not old_text or not new_text:
        return 0.0
    return difflib.SequenceMatcher(a=old_text, b=new_text).ratio()


def _align_segments_monotonic(
    old_segments: list[list[str]],
    new_segments: list[list[str]],
    threshold: float,
) -> tuple[dict[int, int], dict[int, int]]:
    """单调 DP 在保序约束下最大化已配对段的相似度总和。

    返回 (old_idx → new_idx, new_idx → old_idx) 两个反向 dict; 未配对段不出现在 dict 里。
    仅相似度 >= ``threshold`` 的段对才允许配对; 低于阈值的段在渲染时分别走 pure delete /
    pure insert。
    """
    m = len(old_segments)
    n = len(new_segments)
    sim_cache: dict[tuple[int, int], float] = {}

    def sim(i: int, j: int) -> float:
        key = (i, j)
        cached = sim_cache.get(key)
        if cached is None:
            cached = _segment_pair_similarity(old_segments[i], new_segments[j])
            sim_cache[key] = cached
        return cached

    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        for j in range(n + 1):
            if i == 0 and j == 0:
                continue
            best_score = -1.0
            best_choice: tuple | None = None
            if i > 0 and dp[i - 1][j] > best_score:
                best_score = dp[i - 1][j]
                best_choice = ("skip_old",)
            if j > 0 and dp[i][j - 1] > best_score:
                best_score = dp[i][j - 1]
                best_choice = ("skip_new",)
            if i > 0 and j > 0:
                pair_score = sim(i - 1, j - 1)
                if pair_score >= threshold:
                    candidate = dp[i - 1][j - 1] + pair_score
                    if candidate > best_score:
                        best_score = candidate
                        best_choice = ("pair", i - 1, j - 1)
            dp[i][j] = best_score if best_score > 0 else 0.0
            back[i][j] = best_choice

    pairing_old: dict[int, int] = {}
    pairing_new: dict[int, int] = {}
    i, j = m, n
    while i > 0 or j > 0:
        choice = back[i][j]
        if choice is None:
            break
        if choice[0] == "pair":
            _, oi, nj = choice
            pairing_old[oi] = nj
            pairing_new[nj] = oi
            i, j = oi, nj
        elif choice[0] == "skip_old":
            i -= 1
        else:
            j -= 1
    return pairing_old, pairing_new


def _segment_old_paragraphs_from_lines(
    old_lines: list[str], new_lines: list[str]
) -> list[list[tuple[str, dict]]]:
    """单段范围内按行级 difflib 渲染左侧 — 即原 build_old_revision_paragraphs 的内循环, 抽出复用。"""
    paragraphs: list[list[tuple[str, dict]]] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            paragraphs.extend([[(line, {"size": SIZE_BODY})] for line in old_lines[i1:i2]])
            continue
        if tag == "delete":
            paragraphs.extend([[(line, old_change_style())] for line in old_lines[i1:i2]])
            continue
        if tag == "insert":
            continue
        old_group = old_lines[i1:i2]
        new_group = new_lines[j1:j2]
        if len(old_group) != len(new_group):
            paragraphs.extend(cross_newline_old_paragraphs("\n".join(old_group), "\n".join(new_group)))
            continue
        for old_line, new_line in zip(old_group, new_group):
            paragraphs.append(diff_old_line_runs(old_line, new_line))
    return paragraphs


def _segment_new_paragraphs_from_lines(
    old_lines: list[str], new_lines: list[str]
) -> list[list[tuple[str, dict]]]:
    """单段范围内按行级 difflib 渲染右侧 — 即原 build_new_revision_paragraphs 的内循环, 抽出复用。"""
    paragraphs: list[list[tuple[str, dict]]] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            paragraphs.extend([[(line, {"size": SIZE_BODY})] for line in new_lines[j1:j2]])
            continue
        if tag == "delete":
            continue
        if tag == "insert":
            paragraphs.extend([[(line, new_change_style(line))] for line in new_lines[j1:j2]])
            continue
        old_group = old_lines[i1:i2]
        new_group = new_lines[j1:j2]
        if len(old_group) != len(new_group):
            paragraphs.extend(cross_newline_new_paragraphs("\n".join(old_group), "\n".join(new_group)))
            continue
        for old_line, new_line in zip(old_group, new_group):
            paragraphs.append(diff_new_line_runs(old_line, new_line))
    return paragraphs


def _build_old_paragraphs_with_segment_alignment(
    old_lines: list[str], new_lines: list[str]
) -> list[list[tuple[str, dict]]]:
    """按 `......` 切段、按段相似度配对后逐段渲染左侧 cell paragraphs。"""
    old_segments = _split_lines_by_omitted_marker(old_lines)
    new_segments = _split_lines_by_omitted_marker(new_lines)
    pairing_old, _pairing_new = _align_segments_monotonic(
        old_segments, new_segments, _SEGMENT_ALIGNMENT_SIMILARITY_THRESHOLD
    )
    paragraphs: list[list[tuple[str, dict]]] = []
    for old_idx, old_segment in enumerate(old_segments):
        if old_idx > 0:
            paragraphs.append([(OMITTED_EQUAL_MARKER, {"size": SIZE_BODY})])
        new_idx = pairing_old.get(old_idx)
        if new_idx is None:
            paragraphs.extend([[(line, old_change_style())] for line in old_segment])
        else:
            paragraphs.extend(_segment_old_paragraphs_from_lines(old_segment, new_segments[new_idx]))
    return paragraphs


def _build_new_paragraphs_with_segment_alignment(
    old_lines: list[str], new_lines: list[str]
) -> list[list[tuple[str, dict]]]:
    """按 `......` 切段、按段相似度配对后逐段渲染右侧 cell paragraphs。"""
    old_segments = _split_lines_by_omitted_marker(old_lines)
    new_segments = _split_lines_by_omitted_marker(new_lines)
    _pairing_old, pairing_new = _align_segments_monotonic(
        old_segments, new_segments, _SEGMENT_ALIGNMENT_SIMILARITY_THRESHOLD
    )
    paragraphs: list[list[tuple[str, dict]]] = []
    for new_idx, new_segment in enumerate(new_segments):
        if new_idx > 0:
            paragraphs.append([(OMITTED_EQUAL_MARKER, {"size": SIZE_BODY})])
        old_idx = pairing_new.get(new_idx)
        if old_idx is None:
            paragraphs.extend([[(line, new_change_style(line))] for line in new_segment])
        else:
            paragraphs.extend(_segment_new_paragraphs_from_lines(old_segments[old_idx], new_segment))
    return paragraphs
