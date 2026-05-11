"""非 blocks 形态的 unit 判定 payload。

职责：将 `units` 结构转为 `ComparisonRow`，并对照源 compare_units 做一致性校验。
与 `blocks` 流水线并行分支，由 `rows_from_llm_payload` 按 payload 形态分发。
"""

from __future__ import annotations

from typing import Any

from ..chunking import is_only_numbering_changed, remove_fully_equal_lines
from ..models import ComparisonRow
from .shift_lines import normalize_line_for_shift_compare


def rows_from_unit_decision_payload(payload: dict[str, Any], compare_units: tuple[Any, ...]) -> list[ComparisonRow]:
    """把新版 unit 判定结果回查原始 compare unit 后转成最终展示行。"""
    units_by_id = {str(unit.unit_id): unit for unit in compare_units}
    rows: list[ComparisonRow] = []
    for decision in payload.get("units", []):
        if decision.get("numbering_only") or decision.get("change_type") in {"numbering_only", "equal"}:
            continue
        if decision.get("display_strategy") == "skip":
            continue
        source_unit = units_by_id.get(str(decision.get("unit_id", "")).strip())
        if source_unit is None:
            continue
        validate_unit_decision_against_source(decision, source_unit)
        old_text, new_text = texts_from_unit_decision(decision, source_unit)
        if old_text and new_text and is_only_numbering_changed(old_text, new_text):
            continue
        old_text, new_text = remove_fully_equal_lines(old_text, new_text)
        if not old_text and not new_text:
            continue
        rows.append(
            ComparisonRow(
                chapter=str(source_unit.chapter_title or decision.get("chapter")).strip(),
                subchapter=str(source_unit.subchapter or decision.get("subchapter")).strip(),
                old_text=old_text or "新增",
                new_text=new_text or "删除",
            )
        )
    return rows


def validate_unit_decision_against_source(decision: dict[str, Any], source_unit: Any) -> None:
    """校验模型判定是否与本地 compare unit 的基本事实冲突。"""
    strategy = str(decision.get("display_strategy", "")).strip()
    change_type = str(decision.get("change_type", "")).strip()
    old_text = str(source_unit.old_text).strip()
    new_text = str(source_unit.new_text).strip()
    unit_id = str(decision.get("unit_id", "")).strip()
    if (
        strategy == "delete_old_only" or change_type in {"delete", "delete_item"}
    ) and not is_deletion_marker(new_text) and not is_shift_delete_decision(new_text, decision.get("unchanged_lines", []), old_text):
        raise ValueError(f"模型误判为整项删除: {unit_id or getattr(source_unit, 'unit_id', '')}")
    if (
        strategy == "add_new_only" or change_type in {"add", "add_item"}
    ) and not is_addition_marker(old_text) and not is_shift_add_decision(old_text, decision.get("unchanged_lines", []), new_text):
        raise ValueError(f"模型误判为整项新增: {unit_id or getattr(source_unit, 'unit_id', '')}")


def texts_from_unit_decision(decision: dict[str, Any], source_unit: Any) -> tuple[str, str]:
    """根据模型判定和原始 compare unit 生成左右展示文本。"""
    strategy = str(decision.get("display_strategy", "")).strip()
    old_text = str(source_unit.old_text).strip()
    new_text = str(source_unit.new_text).strip()
    if strategy == "delete_old_only" or decision.get("change_type") in {"delete", "delete_item"}:
        old_text = remove_decision_unchanged_lines(old_text, decision.get("unchanged_lines", []))
        if not old_text:
            old_text = str(source_unit.old_text).strip()
        return old_text.strip(), ""
    if strategy == "add_new_only" or decision.get("change_type") in {"add", "add_item"}:
        new_text = remove_decision_unchanged_lines(new_text, decision.get("unchanged_lines", []))
        if not new_text:
            new_text = str(source_unit.new_text).strip()
        return "", new_text.strip()
    return old_text.strip(), new_text.strip()


def is_deletion_marker(text: str) -> bool:
    """判断 compare unit 右侧是否确实表示整项删除。"""
    return not text.strip() or text.strip() == "删除"


def is_addition_marker(text: str) -> bool:
    """判断 compare unit 左侧是否确实表示整项新增。"""
    return not text.strip() or text.strip() == "新增"


def is_shift_delete_decision(new_text: str, unchanged_lines: Any, old_text: str) -> bool:
    """判断 delete_item 是否属于删除整行后编号上移的合法场景。"""
    old_lines = [line for line in old_text.splitlines() if line.strip()]
    new_lines = [line for line in new_text.splitlines() if line.strip()]
    return len(old_lines) > len(new_lines) and lines_are_declared_unchanged(new_lines, unchanged_lines)


def is_shift_add_decision(old_text: str, unchanged_lines: Any, new_text: str) -> bool:
    """判断 add_item 是否属于新增整行后编号上移的合法场景。"""
    old_lines = [line for line in old_text.splitlines() if line.strip()]
    new_lines = [line for line in new_text.splitlines() if line.strip()]
    return len(new_lines) > len(old_lines) and lines_are_declared_unchanged(old_lines, unchanged_lines)


def lines_are_declared_unchanged(lines: list[str], unchanged_lines: Any) -> bool:
    """判断一组正文行是否都被模型声明为后续未变行。"""
    normalized_unchanged = {
        normalize_line_for_shift_compare(str(line))
        for line in unchanged_lines
        if str(line).strip()
    }
    normalized_lines = [normalize_line_for_shift_compare(line) for line in lines if line.strip()]
    return bool(normalized_lines) and all(line in normalized_unchanged for line in normalized_lines)


def remove_decision_unchanged_lines(text: str, unchanged_lines: Any) -> str:
    """按模型判定剔除真实未变小行，比较时忽略行首编号变化。"""
    normalized_unchanged = {
        normalize_line_for_shift_compare(str(line))
        for line in unchanged_lines
        if str(line).strip()
    }
    if not normalized_unchanged:
        return text.strip()
    kept_lines = [
        line
        for line in text.splitlines()
        if normalize_line_for_shift_compare(line) not in normalized_unchanged
    ]
    return "\n".join(kept_lines).strip()
