from __future__ import annotations

import re

from ..chunking.constants import OMITTED_EQUAL_MARKER
from ..models import ComparisonRow


_HEADING_CN_RE = re.compile(r"^[一二三四五六七八九十]+、")
_HEADING_PAREN_CN_RE = re.compile(r"^（[一二三四五六七八九十]+）")
_HEADING_NUMERIC_RE = re.compile(r"^(\d+)、")


def insert_marker_between_heading_and_numbered_continuation(
    rows: list[ComparisonRow],
) -> list[ComparisonRow]:
    """row-level 兜底: cell 内若 `中文/(中文) heading` 紧接 `N、xxx` (N>1), 在二者间插入 `......`。

    覆盖路径:
    - `merge_pure_delete_and_add_runs` 把多条 delete row 拼成大 row 后, cell 内出现
      `三、估值方法\\n6、xxx\\n7、xxx`, 中间没有 marker, 用户看不出 `1-5、xxx` 被切;
    - `merge_same_subchapter_rows_with_ellipsis` 合并后同样问题;
    - `remove_fully_equal_lines` 处理失败 (例如 LLM 给出的 item 已经预合并多行) 的兜底。

    判定:
    - 前一行: `中文、` 或 `（中文）` heading (≤ 内部行长度无限制 — 用户的 heading 可能较长);
    - 后一行: `数字、` 编号且数字 > 1;
    - 二者之间不已经有 `......`(由前一行末尾或在它们之间已经隔了一行 marker)。
    """
    if not rows:
        return rows
    cleaned: list[ComparisonRow] = []
    for row in rows:
        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_insert_marker_after_heading_before_numbered(row.old_text),
                new_text=_insert_marker_after_heading_before_numbered(row.new_text),
            )
        )
    return cleaned


def _insert_marker_after_heading_before_numbered(text: str) -> str:
    """在文本里, `中文/(中文) heading` 紧邻 `N、xxx` (N>1) 时插入 `......` 行。"""
    if not text or text in ("新增", "删除"):
        return text
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        if i + 1 >= len(lines):
            continue
        nxt = lines[i + 1].strip()
        cur = line.strip()
        if not (_HEADING_CN_RE.match(cur) or _HEADING_PAREN_CN_RE.match(cur)):
            continue
        m = _HEADING_NUMERIC_RE.match(nxt)
        if not m:
            continue
        try:
            ordinal = int(m.group(1))
        except ValueError:
            continue
        if ordinal <= 1:
            continue
        out.append(OMITTED_EQUAL_MARKER)
    return "\n".join(out)


_MAX_NUMERIC_HEADING_LEN = 40
_SENTENCE_END_PUNCT = ("。", ".", "；", ";")


def drop_redundant_numeric_heading_after_marker(
    rows: list[ComparisonRow],
) -> list[ComparisonRow]:
    """row-level 后处理: cell 内 `......\\nN、heading-like\\n正文` 时, 删除中间的 `N、heading-like`。

    `......` 已经表达"前面有内容被省略", `N、xxx` 数字小标题(本身就暗示前面有 1..N-1 被省略)
    紧跟其后再出现, 与 marker 语义重复, 视觉冗余。用户原话: "上面已经有 ...... 了, 可以考虑
    不增加 2、巨额赎回的处理方式 标题"。本步骤抽象为通用规则覆盖所有 chapter 而非单 case。

    judging "heading-like":
    - `数字、xxx` 形式, 行长 ≤ 40, 不以句末标点(。.；;) 结尾;
    - 且 `数字、` 后至少 2 字符标题文本, 避免误删 LLM 切断的孤立 `4、` 这种短行。

    judging "正文" (heading 之后那一行必须是正文, 不是另一个 heading-like):
    - 非空 / 非 `......` / 非 numeric heading-like。
    """
    if not rows:
        return rows
    cleaned: list[ComparisonRow] = []
    for row in rows:
        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_drop_numeric_heading_after_marker(row.old_text),
                new_text=_drop_numeric_heading_after_marker(row.new_text),
            )
        )
    return cleaned


def _drop_numeric_heading_after_marker(text: str) -> str:
    """扫描文本, 把 `......` 后的冗余短 heading 一行剥掉。

    两类判定:
    - paren-chinese `（X）xxx` 短标题: 二元 window — `......\\n（X）xxx` 直接删 `（X）`。
      理由: 用户原话 "...... 已经有了, （二）基金管理人的权利与义务 多了"; 带括号中文编号
      本身就暗示"前面有省略", 跟 marker 完全重复, 无论后面跟啥都该剥。
    - numeric `N、xxx` 短标题: 三元 window — 后面必须是 body (非 heading-like) 才删。
      理由: 数字 heading 可能是 list lead (例如 `1、根据《基金法》...权利包括但不限于：`
      虽然长 < 40 但用 `：` 结尾, 表明下面有列表), 应保留作 (12)(16) 的语义 context。

    numeric 分支追加"平行小节"守卫: 若 cell 已输出行里已经出现过任意 `M、xxx` 短 numeric
    heading, 说明当前 cell 是"1、xxx / 2、xxx / 3、xxx ..."的平行小节结构, 此时紧跟 marker
    的 `N、xxx` 是与前置 heading 对齐的下一节标题(如 `1、管理费 → 2、托管费`), 语义并非
    "marker 重复的 list lead", 应保留。qus15 场景 (cell 里唯一的 N、xxx 孤立紧贴 ......)
    仍走剥逻辑, 不回归。
    """
    if not text or text in ("新增", "删除"):
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and lines[i].strip() == OMITTED_EQUAL_MARKER:
            if _is_paren_chinese_heading_like_line(lines[i + 1]):
                out.append(lines[i])
                i += 2
                continue
            if (
                i + 2 < len(lines)
                and _is_numeric_heading_like_line(lines[i + 1])
                and _is_body_line_after_heading(lines[i + 2])
                and not _has_prior_numeric_heading(out)
            ):
                out.append(lines[i])
                out.append(lines[i + 2])
                i += 3
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _has_prior_numeric_heading(prev_lines: list[str]) -> bool:
    """已输出行里是否已出现过 numeric `N、xxx` 短标题 — 有则说明 cell 是平行小节结构, 后续
    `N、xxx` 应保留。"""
    return any(_is_numeric_heading_like_line(line) for line in prev_lines)


def _is_numeric_heading_like_line(line: str) -> bool:
    """是否短数字小标题 (`N、标题`, ≤40 字, 无句末标点, 标题文本 ≥ 2 字)。"""
    stripped = line.strip()
    match = _HEADING_NUMERIC_RE.match(stripped)
    if not match or len(stripped) > _MAX_NUMERIC_HEADING_LEN:
        return False
    if stripped and stripped[-1] in _SENTENCE_END_PUNCT:
        return False
    if len(stripped) - len(match.group(0)) < 2:
        return False
    return True


def _is_body_line_after_heading(line: str) -> bool:
    """行是否真正的正文 (非空、非 marker、非另一个 short heading-like)。"""
    stripped = line.strip()
    if not stripped or stripped == OMITTED_EQUAL_MARKER:
        return False
    return not _is_short_heading_after_marker_line(stripped)


_PAREN_CHINESE_HEAD_RE = re.compile(r"^（[一二三四五六七八九十]+）")


def _is_paren_chinese_heading_like_line(line: str) -> bool:
    """是否短带括号中文小标题 (`（X）xxx`, ≤40 字, 无句末标点, `（X）` 后至少 2 字)。

    判定与 `_is_numeric_heading_like_line` 对称, 同样排除孤立的 `（一）` (无后续标题文本)
    避免 LLM 切断的短行被误删。
    """
    stripped = line.strip()
    match = _PAREN_CHINESE_HEAD_RE.match(stripped)
    if not match or len(stripped) > _MAX_NUMERIC_HEADING_LEN:
        return False
    if stripped and stripped[-1] in _SENTENCE_END_PUNCT:
        return False
    if len(stripped) - len(match.group(0)) < 2:
        return False
    return True


def _is_short_heading_after_marker_line(line: str) -> bool:
    """统一 heading-like 判定: 覆盖 numeric (`N、xxx`) 与 paren-chinese (`（X）xxx`)。

    用于 `_drop_numeric_heading_after_marker`: cell 内 `......` 紧后这两类短 heading 都跟
    marker 重复表达"前面省略", 都应剥掉。中文 (`X、xxx`) 不在范围: 它通常是 subchapter
    级别 heading, 在 row.subchapter 列单独显示, 不会跟 `......` 同 cell 出现冗余。
    """
    return _is_numeric_heading_like_line(line) or _is_paren_chinese_heading_like_line(line)


_LIST_LEAD_END_PUNCT = ("：", ":")


def drop_repeated_short_heading_after_marker_or_heading(
    rows: list[ComparisonRow],
) -> list[ComparisonRow]:
    """row-level 后处理: 任何 short heading (numeric / paren_cn) 紧贴 `......` 或紧贴另一个
    short heading 都视为冗余 list lead, 删掉。

    qus15 用户红框场景: row 23 顶部 `1、根据..权利..：` 与上方 `......` 重复表达"前面省略";
    row 24 内 `1、根据..权利..：` + `2、根据..义务..：` 连续两条 list lead 之间无差异内容夹隔。

    qus16 用户场景 (对称扩展): row 60 `第二十四部分/四、与基金财产管理..` cell 内
    `text-A \n 2、基金托管人的托管费 \n ...... \n text-B` — 等值 numeric 短标题夹在差异段
    之后、`......` 之前, marker 已表达"前面省略", 标题再现等同复述, 视觉冗余。
    判定补充: short heading 后紧跟的非空行是 `......` 时也删。

    判定为 short heading:
    - 行长 ≤ 40, 不以句末标点 (。.；;) 结尾;
    - `N、` 或 `（X）` 前缀 + 至少 2 字标题文本;
    - **排除释义条目**: numeric 中间含 `：/:` 但末尾不是 `：/:` 视为定义(如 `1、基金或本基金：
      指xxx基金`), 不算 heading, 不删。

    判定为"前后行是冗余 trigger":
    - 上一个非空输出行是 `......`;
    - 或上一个非空输出行也是 short heading-like (连续 list lead 堆叠);
    - 或下一个非空源行是 `......` (对称: marker 在 short heading 后)。

    单 pass 即可处理"删 line[i] 后 line[i+1] 自动跟新 prev (out[-1]) 比较"的递归效果。
    """
    if not rows:
        return rows
    cleaned: list[ComparisonRow] = []
    for row in rows:
        cleaned.append(
            ComparisonRow(
                chapter=row.chapter,
                subchapter=row.subchapter,
                old_text=_drop_repeated_short_heading_lines(row.old_text),
                new_text=_drop_repeated_short_heading_lines(row.new_text),
            )
        )
    return cleaned


def _drop_repeated_short_heading_lines(text: str) -> str:
    """单 pass 扫描: 每条短 heading 看 out 最后非空行 + 下一非空源行,
    任一侧是 marker (或 prev 也是 short heading 时) 就跳过。

    qus20 例外: 若当前 short heading 是 numeric `N、xxx` 且 out 里已经出现过其它 numeric
    `M、xxx` heading, 说明 cell 是平行小节结构, 后续 `N、xxx` 应保留, 不再判定为冗余
    list lead。paren_chinese `（X）xxx` 分支不放宽, 维持原剥法。
    """
    if not text or text in ("新增", "删除"):
        return text
    lines = text.split("\n")
    out: list[str] = []
    for idx, line in enumerate(lines):
        if _is_short_heading_or_list_lead(line):
            if _is_numeric_heading_like_line(line) and any(
                _is_numeric_heading_like_line(prior) for prior in out
            ):
                out.append(line)
                continue
            prev = None
            for prior in reversed(out):
                if prior.strip():
                    prev = prior
                    break
            if prev is not None and (
                prev.strip() == OMITTED_EQUAL_MARKER
                or _is_short_heading_or_list_lead(prev)
            ):
                continue
            nxt = None
            for j in range(idx + 1, len(lines)):
                if lines[j].strip():
                    nxt = lines[j]
                    break
            if nxt is not None and nxt.strip() == OMITTED_EQUAL_MARKER:
                continue
        out.append(line)
    return "\n".join(out)


def _is_short_heading_or_list_lead(line: str) -> bool:
    """short heading 判定: numeric/paren_cn 短行, 排除"中间含 `：`末尾非`：`"的释义条目。"""
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_NUMERIC_HEADING_LEN:
        return False
    if stripped[-1] in _SENTENCE_END_PUNCT:
        return False
    if _has_inline_colon_but_not_list_lead(stripped):
        return False
    numeric_match = _HEADING_NUMERIC_RE.match(stripped)
    if numeric_match and len(stripped) - len(numeric_match.group(0)) >= 2:
        return True
    paren_match = _PAREN_CHINESE_HEAD_RE.match(stripped)
    if paren_match and len(stripped) - len(paren_match.group(0)) >= 2:
        return True
    return False


def _has_inline_colon_but_not_list_lead(stripped: str) -> bool:
    """中间含 `：/:` 但末尾不是 `：/:` 视为释义条目 (`N、术语：解释`), 不算 heading。"""
    if not any(c in stripped for c in _LIST_LEAD_END_PUNCT):
        return False
    return stripped[-1] not in _LIST_LEAD_END_PUNCT
