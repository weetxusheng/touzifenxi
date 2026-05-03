"""编排文件扫描、模型比较、回退策略和任务状态落盘。"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..llm.client import OpenAIResponsesClient, ProviderRequestError
from ..llm.parser import ResponseParseError, coerce_json_object_payload, parse_response_payload
from ..llm.repair import build_repair_record, repair_response_payload
from ..runtime.call_governance import CallTimeline, classify_exception_status
from ..runtime.call_policy import CallPolicy
from ..runtime.checkpoint import PairCheckpointStore, TaskCheckpointStore, atomic_write_json
from ..runtime.config import FileComparisonRuntimeConfig
from ..runtime.execution import BatchManifest, PairManifest, TaskManifest, task_status_from_pairs
from ..runtime.fallbacks import run_batch_fallback
from ..runtime.recovery import decide_batch_recovery
from ..runtime.settings import AppPaths, prepare_run_dir, resolve_paths
from .chunking import (
    build_compare_units_for_llm,
    build_rows,
    group_compare_units_into_batches,
    is_only_numbering_changed,
    remove_fully_equal_lines,
)
from .extractor import extract_fund_name_or_empty, extract_text, split_sections
from .models import ChapterBatch, CompareResult, ComparisonRow, PairMatch
from .writer import convert_docx_to_doc, write_docx

MONTH_RE = re.compile(r"(?P<month>\d{1,2})月")
FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\r\n]+')
PRODUCT_NAME_CHAPTER = "基金名称"
USABLE_BATCH_FINAL_STATUSES = {"succeeded"}
ARK_REQUEST_SHAPE_ERROR_RE = re.compile(
    r"unknown field|unknown parameter|unsupported field|unsupported parameter|unrecognized field|unrecognized parameter",
    re.I,
)


def sanitize_output_filename_part(value: str) -> str:
    """清理文件名片段，保留中文业务语义并移除系统非法字符。"""
    cleaned = FILENAME_UNSAFE_RE.sub(" ", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "未命名文件"


def build_output_document_stem(pair: PairMatch, *, timestamp: str | None = None) -> str:
    """按“前文件名 与 后文件名 对照表 时间戳”生成不含扩展名的产物名。"""
    current_timestamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
    old_part = sanitize_output_filename_part(Path(pair.old_label or pair.old_path.name).stem)
    new_part = sanitize_output_filename_part(Path(pair.new_label or pair.new_path.name).stem)
    return f"{old_part} 与 {new_part} 对照表 {current_timestamp}"


def build_output_document_paths(pair: PairMatch, output_dir: Path, *, timestamp: str | None = None) -> tuple[Path, Path]:
    """生成 docx/doc 两个正式产物路径，避免继续使用英文固定文件名。"""
    stem = build_output_document_stem(pair, timestamp=timestamp)
    return output_dir / f"{stem}.docx", output_dir / f"{stem}.doc"


def section_matches_pattern(section: Any, patterns: tuple[str, ...]) -> str:
    """返回章节标题命中的跳过规则；未命中时返回空字符串。"""
    title = str(getattr(section, "title", "")).strip()
    for pattern in patterns:
        if pattern and pattern in title:
            return pattern
    return ""


def signature_tail_match_index(lines: list[str], patterns: tuple[str, ...]) -> tuple[int | None, str]:
    """定位正文中签署页尾巴的起始行。"""
    for index, line in enumerate(lines):
        stripped = line.strip()
        for pattern in patterns:
            if pattern and pattern in stripped:
                return index, pattern
    return None, ""


def apply_section_skip_rules(sections: list[Any], patterns: tuple[str, ...]) -> tuple[list[Any], list[dict[str, object]]]:
    """按配置剔除签署页等非正文内容，并返回可落盘的跳过记录。"""
    if not patterns:
        return sections, []
    filtered_sections = []
    records: list[dict[str, object]] = []
    for section in sections:
        title_pattern = section_matches_pattern(section, patterns)
        if title_pattern:
            records.append(
                {
                    "section_number": section.number,
                    "section_title": section.title,
                    "action": "skip_section",
                    "reason": "signature_page",
                    "matched_pattern": title_pattern,
                    "removed_line_count": len(section.body.splitlines()),
                }
            )
            continue
        lines = section.body.splitlines()
        tail_index, body_pattern = signature_tail_match_index(lines, patterns)
        if tail_index is None:
            filtered_sections.append(section)
            continue
        kept_lines = lines[:tail_index]
        records.append(
            {
                "section_number": section.number,
                "section_title": section.title,
                "action": "trim_tail",
                "reason": "signature_page",
                "matched_pattern": body_pattern,
                "removed_line_count": len(lines) - tail_index,
            }
        )
        if kept_lines:
            filtered_sections.append(type(section)(number=section.number, title=section.title, body="\n".join(kept_lines).strip()))
    return filtered_sections, records


def build_pair_key(path: Path, month_pattern: str) -> tuple[str, int | None]:
    """根据文件名生成配对主键，并提取月份信息。"""
    pattern = re.compile(month_pattern)
    stem = path.stem
    match = pattern.search(stem)
    month = int(match.group("month")) if match and match.groupdict().get("month") else None
    key = pattern.sub("", stem)
    key = re.sub(r"[_\-\s]+", "", key)
    return key, month


def scan_folder_for_pairs(folder_path: Path, month_pattern: str) -> list[PairMatch]:
    """扫描目录中的文档并按“去月份后文件名”自动配对。"""
    groups: dict[str, list[tuple[Path, int | None]]] = {}
    for path in sorted(folder_path.iterdir()):
        if path.suffix.lower() not in {".doc", ".docx"} or path.name.startswith(".~"):
            continue
        key, month = build_pair_key(path, month_pattern)
        groups.setdefault(key, []).append((path, month))
    pairs: list[PairMatch] = []
    for index, (key, items) in enumerate(sorted(groups.items()), start=1):
        if len(items) < 2:
            continue
        sorted_items = sorted(items, key=lambda item: ((item[1] is None), item[1] if item[1] is not None else 999, item[0].name))
        old_path, old_month = sorted_items[0]
        new_path, new_month = sorted_items[-1]
        pairs.append(
            PairMatch(
                pair_id=f"pair-{index:03d}",
                key=key,
                old_path=old_path,
                new_path=new_path,
                old_label=old_path.name,
                new_label=new_path.name,
                old_month=old_month,
                new_month=new_month,
            )
        )
    return pairs


def rows_from_llm_payload(payload: dict[str, Any], compare_units: tuple[Any, ...] = ()) -> list[ComparisonRow]:
    """把模型返回的结构化 payload 转成最终展示行。"""
    if isinstance(payload.get("units"), list):
        return rows_from_unit_decision_payload(payload, compare_units)
    rows: list[ComparisonRow] = []
    for chapter in payload.get("chapters", []):
        chapter_name = str(chapter.get("chapter", "")).strip()
        for subsection in chapter.get("subsections", []):
            old_text = str(subsection.get("old_text", "")).strip()
            new_text = str(subsection.get("new_text", "")).strip()
            if subsection.get("numbering_only"):
                continue
            if old_text and new_text and is_only_numbering_changed(old_text, new_text):
                continue
            equal_lines = {line.strip() for line in subsection.get("fully_equal_lines", []) if str(line).strip()}
            if equal_lines:
                old_text = "\n".join(line for line in old_text.split("\n") if line.strip() not in equal_lines).strip()
                new_text = "\n".join(line for line in new_text.split("\n") if line.strip() not in equal_lines).strip()
            old_text, new_text = remove_fully_equal_lines(old_text, new_text)
            if not old_text and not new_text:
                continue
            rows.append(
                ComparisonRow(
                    chapter=chapter_name,
                    subchapter=str(subsection.get("subchapter", "")).strip(),
                    old_text=old_text or "新增",
                    new_text=new_text or "删除",
                )
            )
    return rows


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


def normalize_line_for_shift_compare(line: str) -> str:
    """返回忽略常见编号前缀后的行文本，用于识别编号上移但正文未变。"""
    stripped = str(line).strip()
    stripped = re.sub(r"^（\d+）\s*", "", stripped)
    stripped = re.sub(r"^\(\d+\)\s*", "", stripped)
    stripped = re.sub(r"^\d+[、.．]\s*", "", stripped)
    stripped = re.sub(r"^[一二三四五六七八九十百]+、\s*", "", stripped)
    return stripped.strip()


def normalize_product_name_rows(rows: list[ComparisonRow], old_name: str, new_name: str) -> list[ComparisonRow]:
    """把基金名称对照固定提升到第一行，并移除原章节中的重复基金名称行。"""
    old_clean = old_name.strip()
    new_clean = new_name.strip()
    if not old_clean or not new_clean or old_clean == new_clean:
        return rows
    filtered_rows = [
        row
        for row in rows
        if not _is_product_name_row(row, old_clean=old_clean, new_clean=new_clean)
    ]
    product_row = ComparisonRow(
        chapter=PRODUCT_NAME_CHAPTER,
        old_text=old_clean,
        new_text=new_clean,
    )
    return [product_row, *filtered_rows]


def _is_product_name_row(row: ComparisonRow, *, old_clean: str, new_clean: str) -> bool:
    """判断某行是否是原章节里的基金名称重复行。"""
    marker_hit = "基金名称" in row.subchapter or "基金名称" in row.old_text or "基金名称" in row.new_text
    if not marker_hit:
        return False
    return old_clean in row.old_text and new_clean in row.new_text


def write_preprocess_artifacts(
    *,
    process_dir: Path,
    pair: PairMatch,
    old_sections,
    new_sections,
    compare_units,
    chapter_summaries: list[dict[str, object]],
    batches: list[ChapterBatch],
    skipped_sections: list[dict[str, object]] | None = None,
) -> None:
    """把前置 diff 的统计、单元列表和 batch 计划落盘，便于排查。"""
    process_dir.mkdir(parents=True, exist_ok=True)
    original_old_chars = sum(len(section.body) for section in old_sections)
    original_new_chars = sum(len(section.body) for section in new_sections)
    sent_old_chars = sum(len(unit.old_text) for unit in compare_units)
    sent_new_chars = sum(len(unit.new_text) for unit in compare_units)
    atomic_write_json(
        process_dir / "preprocess_summary.json",
        {
            "pair_id": pair.pair_id,
            "old_label": pair.old_label,
            "new_label": pair.new_label,
            "original_section_count_old": len(old_sections),
            "original_section_count_new": len(new_sections),
            "changed_chapter_count": sum(1 for item in chapter_summaries if item["kept_for_llm"]),
            "compare_unit_count": len(compare_units),
            "batch_count": len(batches),
            "original_old_chars": original_old_chars,
            "original_new_chars": original_new_chars,
            "sent_old_chars": sent_old_chars,
            "sent_new_chars": sent_new_chars,
            "compression_ratio_old": 0 if original_old_chars == 0 else round(sent_old_chars / original_old_chars, 4),
            "compression_ratio_new": 0 if original_new_chars == 0 else round(sent_new_chars / original_new_chars, 4),
            "skipped_section_count": len(skipped_sections or []),
            "skipped_sections": skipped_sections or [],
            "chapters": chapter_summaries,
        },
    )
    atomic_write_json(
        process_dir / "compare_units.json",
        {
            "pair_id": pair.pair_id,
            "items": [
                {
                    "unit_id": unit.unit_id,
                    "chapter_number": unit.chapter_number,
                    "chapter_title": unit.chapter_title,
                    "subchapter": unit.subchapter,
                    "old_text": unit.old_text,
                    "new_text": unit.new_text,
                }
                for unit in compare_units
            ],
        },
    )
    atomic_write_json(
        process_dir / "batch_plan.json",
        {
            "pair_id": pair.pair_id,
            "batches": [
                {
                    "batch_id": batch.batch_id,
                    "chapter_numbers": list(batch.chapter_numbers),
                    "unit_count": len(batch.compare_units),
                    "sent_old_chars": sum(len(unit.old_text) for unit in batch.compare_units),
                    "sent_new_chars": sum(len(unit.new_text) for unit in batch.compare_units),
                    "unit_ids": [unit.unit_id for unit in batch.compare_units],
                }
                for batch in batches
            ],
        },
    )


def write_rule_preprocess_artifacts(
    *,
    process_dir: Path,
    pair: PairMatch,
    old_sections,
    new_sections,
    skipped_sections: list[dict[str, object]] | None = None,
) -> None:
    """为纯规则模式写出轻量预处理摘要，便于排查过滤行为。"""
    process_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        process_dir / "preprocess_summary.json",
        {
            "pair_id": pair.pair_id,
            "old_label": pair.old_label,
            "new_label": pair.new_label,
            "original_section_count_old": len(old_sections),
            "original_section_count_new": len(new_sections),
            "llm_enabled": False,
            "skipped_section_count": len(skipped_sections or []),
            "skipped_sections": skipped_sections or [],
        },
    )


def compare_pair(
    *,
    pair: PairMatch,
    pair_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None = None,
    client: OpenAIResponsesClient | None = None,
) -> CompareResult:
    """完成单个文件对的全文抽取、比较、写表和导出。"""
    old_text = extract_text(pair.old_path)
    new_text = extract_text(pair.new_path)
    old_sections = split_sections(old_text)
    new_sections = split_sections(new_text)
    old_sections, old_skipped_sections = apply_section_skip_rules(old_sections, runtime_config.compare.skip_section_patterns)
    new_sections, new_skipped_sections = apply_section_skip_rules(new_sections, runtime_config.compare.skip_section_patterns)
    skipped_sections = [
        {**record, "side": "old"}
        for record in old_skipped_sections
    ] + [
        {**record, "side": "new"}
        for record in new_skipped_sections
    ]
    old_name = extract_fund_name_or_empty(old_text)
    new_name = extract_fund_name_or_empty(new_text)
    pair_dir.mkdir(parents=True, exist_ok=True)
    source_dir = pair_dir / "source"
    extracted_dir = pair_dir / "extracted"
    llm_dir = pair_dir / "llm"
    output_dir = pair_dir / "outputs"
    for subdir in (source_dir, extracted_dir, llm_dir, output_dir):
        subdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pair.old_path, source_dir / pair.old_path.name)
    shutil.copy2(pair.new_path, source_dir / pair.new_path.name)
    atomic_write_json(
        extracted_dir / "old_sections.json",
        {
            "skipped_sections": [record for record in skipped_sections if record["side"] == "old"],
            "sections": [{"number": section.number, "title": section.title, "body": section.body} for section in old_sections],
        },
    )
    atomic_write_json(
        extracted_dir / "new_sections.json",
        {
            "skipped_sections": [record for record in skipped_sections if record["side"] == "new"],
            "sections": [{"number": section.number, "title": section.title, "body": section.body} for section in new_sections],
        },
    )
    if runtime_config.llm_mode == "responses":
        rows = compare_pair_with_llm(
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            llm_dir=llm_dir,
            runtime_config=runtime_config,
            pair_store=pair_store,
            client=client or OpenAIResponsesClient(runtime_config),
            process_dir=extracted_dir,
            skipped_sections=skipped_sections,
        )
    else:
        rows = build_rows(old_sections, new_sections)
        write_rule_preprocess_artifacts(
            process_dir=extracted_dir,
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            skipped_sections=skipped_sections,
        )
    rows = normalize_product_name_rows(rows, old_name, new_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_path, doc_path = build_output_document_paths(pair, output_dir)
    old_display_name = old_name or pair.old_path.stem
    new_display_name = new_name or pair.new_path.stem
    write_docx(rows, docx_path, old_display_name, new_display_name)
    convert_docx_to_doc(docx_path, doc_path)
    return CompareResult(
        pair_id=pair.pair_id,
        old_name=old_display_name,
        new_name=new_display_name,
        rows=rows,
        docx_path=docx_path,
        doc_path=doc_path,
        metadata={
            "old_fund_name_detected": bool(old_name),
            "new_fund_name_detected": bool(new_name),
        },
    )


def compare_pair_with_llm(
    *,
    pair: PairMatch,
    old_sections,
    new_sections,
    llm_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None,
    client: OpenAIResponsesClient,
    process_dir: Path | None = None,
    skipped_sections: list[dict[str, object]] | None = None,
) -> list[ComparisonRow]:
    """对单个文件对执行本地预处理、批次并行和多模型比较。"""
    compare_units, chapter_summaries = build_compare_units_for_llm(old_sections, new_sections)
    batches = group_compare_units_into_batches(
        compare_units,
        runtime_config.llm.chapter_batch_size,
        max_batch_chars=runtime_config.llm.chapter_batch_char_limit,
        oversized_batch_size=runtime_config.llm.oversized_chapter_batch_size,
        max_compare_units_per_batch=runtime_config.llm.max_compare_units_per_batch,
        max_compare_unit_chars=runtime_config.llm.max_compare_unit_chars,
    )
    if process_dir is not None:
        write_preprocess_artifacts(
            process_dir=process_dir,
            pair=pair,
            old_sections=old_sections,
            new_sections=new_sections,
            compare_units=compare_units,
            chapter_summaries=chapter_summaries,
            batches=batches,
            skipped_sections=skipped_sections,
        )
    provider_chain = (
        client.provider_chain_for_attempts()
        if hasattr(client, "provider_chain_for_attempts")
        else runtime_config.llm.providers
    )
    batch_results: dict[str, list[ComparisonRow]] = {}
    pending_batches: list[tuple[int, ChapterBatch]] = []
    for batch_index, batch in enumerate(batches):
        batch_dir = llm_dir / batch.batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        recovery = decide_batch_recovery(batch_dir)
        if recovery.action in {"reuse_parsed", "reuse_repair"} and recovery.payload is not None:
            batch_results[batch.batch_id] = rows_from_llm_payload(recovery.payload, compare_units=batch.compare_units)
            continue
        pending_batches.append((batch_index, batch))
    if pending_batches:
        max_workers = min(runtime_config.execution.per_pair_max_workers, len(pending_batches))
        batch_errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _process_llm_batch,
                    pair=pair,
                    batch=batch,
                    batch_index=batch_index,
                    llm_dir=llm_dir,
                    runtime_config=runtime_config,
                    pair_store=pair_store,
                    client=client,
                    provider_chain=provider_chain,
                ): batch.batch_id
                for batch_index, batch in pending_batches
            }
            for future in as_completed(futures):
                try:
                    batch_id, rows = future.result()
                    batch_results[batch_id] = rows
                except Exception as exc:  # noqa: BLE001
                    batch_errors.append(f"{futures[future]}: {type(exc).__name__}: {exc}")
        if batch_errors:
            raise RuntimeError("存在未完成或无可用结果的 batch，已停止生成文档: " + "；".join(batch_errors))
    validate_complete_batch_results(batches=batches, batch_results=batch_results, llm_dir=llm_dir)
    ordered_rows: list[ComparisonRow] = []
    for batch in batches:
        ordered_rows.extend(batch_results[batch.batch_id])
    return ordered_rows


def validate_complete_batch_results(
    *,
    batches: list[ChapterBatch],
    batch_results: dict[str, list[ComparisonRow]],
    llm_dir: Path,
) -> None:
    """确认每个 batch 都有可复用结果；否则禁止生成不完整对照文档。"""
    invalid_batches: list[str] = []
    for batch in batches:
        batch_dir = llm_dir / batch.batch_id
        rows = batch_results.get(batch.batch_id)
        final_status = _read_json_object(batch_dir / "final_status.json")
        status = str(final_status.get("status", "")).strip()
        legacy_parsed_exists = not final_status and (batch_dir / "parsed.json").exists()
        has_usable_status = status in USABLE_BATCH_FINAL_STATUSES or legacy_parsed_exists
        if rows is None:
            invalid_batches.append(f"{batch.batch_id}(缺少结果)")
            continue
        if batch.compare_units and not rows:
            invalid_batches.append(f"{batch.batch_id}(结果为空)")
            continue
        if not has_usable_status:
            invalid_batches.append(f"{batch.batch_id}(状态不可用:{status or 'missing'})")
    if invalid_batches:
        raise RuntimeError("存在未完成或无可用结果的 batch，已停止生成文档: " + "、".join(invalid_batches))


def _process_llm_batch(
    *,
    pair: PairMatch,
    batch: ChapterBatch,
    batch_index: int,
    llm_dir: Path,
    runtime_config: FileComparisonRuntimeConfig,
    pair_store: PairCheckpointStore | None,
    client: OpenAIResponsesClient,
    provider_chain,
) -> tuple[str, list[ComparisonRow]]:
    """执行单个章节批次的模型比较、重试和规则回退。"""
    batch_dir = llm_dir / batch.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    timeline = CallTimeline(batch_dir, batch_id=batch.batch_id)
    atomic_write_json(
        batch_dir / "batch_input.json",
        {
            "batch_id": batch.batch_id,
            "chapter_numbers": list(batch.chapter_numbers),
            "compare_units": [
                {
                    "unit_id": unit.unit_id,
                    "chapter_number": unit.chapter_number,
                    "chapter_title": unit.chapter_title,
                    "subchapter": unit.subchapter,
                    "old_text": unit.old_text,
                    "new_text": unit.new_text,
                }
                for unit in batch.compare_units
            ],
            "old_sections": [
                {"number": section.number, "title": section.title, "body": section.body}
                for section in batch.old_sections
            ],
            "new_sections": [
                {"number": section.number, "title": section.title, "body": section.body}
                for section in batch.new_sections
            ],
        },
    )
    timeline.record(
        "prepared",
        details={
            "chapter_numbers": list(batch.chapter_numbers),
            "compare_unit_count": len(batch.compare_units),
        },
    )
    routed_provider_chain = _provider_chain_for_batch(
        provider_chain=provider_chain,
        batch_index=batch_index,
        runtime_config=runtime_config,
    )
    policy = CallPolicy.from_runtime(runtime_config, tuple(routed_provider_chain))
    rows: list[ComparisonRow] = []
    last_error = ""
    attempt_index = 0
    plain_json_providers: set[str] = set()
    while True:
        attempt_index += 1
        provider_config = policy.provider_for_attempt(attempt_index)
        prompt_mode = policy.prompt_mode_for_attempt(attempt_index)
        if provider_config.provider in plain_json_providers:
            prompt_mode = "plain_json"
        base_request_payload = client.build_request_payload(
            pair_id=pair.pair_id,
            batch=batch,
            provider_config=provider_config,
        )
        request_payload = _apply_prompt_mode(base_request_payload, prompt_mode=prompt_mode)
        request_context = _request_context(pair, batch, runtime_config, provider_config, attempt_index)
        request_context["prompt_mode"] = prompt_mode
        client.write_request(
            batch_dir / f"request.attempt-{attempt_index:02d}.{provider_config.provider}.json",
            request_payload,
        )
        timeline.record(
            "requesting",
            provider=provider_config.provider,
            attempt_index=attempt_index,
            details=request_context,
        )
        attempt_started_at = time.perf_counter()
        try:
            raw_response, normalized_response = _post_with_raw(client, request_payload, provider_config)
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            _write_json_file(batch_dir / f"response.raw.attempt-{attempt_index:02d}.{provider_config.provider}.json", raw_response)
            _write_json_file(batch_dir / f"response.normalized.attempt-{attempt_index:02d}.{provider_config.provider}.json", normalized_response)
            timeline.record(
                "responded",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
            )
            timeline.record(
                "normalized",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
            )
            parsed = _parse_or_repair_payload(
                normalized_response=normalized_response,
                batch_dir=batch_dir,
                provider_name=provider_config.provider,
                attempt_index=attempt_index,
                default_chapter=_batch_title(batch),
                timeline=timeline,
            )
            parsed_filename = f"parsed.attempt-{attempt_index:02d}.{provider_config.provider}.json"
            _write_json_file(batch_dir / parsed_filename, parsed)
            batch_rows = rows_from_llm_payload(parsed, compare_units=batch.compare_units)
            rows.extend(batch_rows)
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="success",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=True,
                result={"row_count": len(batch_rows)},
                call_status="succeeded",
                repair_used=(batch_dir / "repair.json").exists(),
                resume_from="reuse_parsed",
            )
            timeline.record(
                "succeeded",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                details={"row_count": len(batch_rows)},
            )
            timeline.finalize(
                "succeeded",
                attempt_count=attempt_index,
                provider=provider_config.provider,
                recoverable=False,
                next_resume_step="reuse_parsed",
                parsed_file=parsed_filename,
            )
            return batch.batch_id, rows
        except (json.JSONDecodeError, ResponseParseError) as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            policy.record_failure("parse")
            _record_provider_unavailable(client, provider_config, reason="parse_error")
            timeline.record(
                "parse_error",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                error=last_error,
                retry_class="parse",
            )
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="parse_error",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=False,
                error=last_error,
                call_status="parse_error",
            )
        except ValueError as exc:
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"ValueError: {exc}"
            policy.record_failure("postprocess")
            _record_provider_unavailable(client, provider_config, reason="postprocess_error")
            timeline.record(
                "postprocess_error",
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                error=last_error,
                retry_class="postprocess",
            )
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="postprocess_error",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=False,
                error=last_error,
                call_status="postprocess_error",
            )
        except Exception as exc:  # noqa: BLE001
            attempt_duration_ms = int((time.perf_counter() - attempt_started_at) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            if should_downgrade_to_plain_json(provider_config.provider, exc):
                plain_json_providers.add(provider_config.provider)
            call_status, retry_class = classify_exception_status(exc)
            policy.record_failure(retry_class)
            _record_provider_unavailable(client, provider_config, reason=call_status)
            timeline.record(
                call_status,
                provider=provider_config.provider,
                attempt_index=attempt_index,
                duration_ms=attempt_duration_ms,
                error=last_error,
                retry_class=retry_class,
            )
            _record_checkpoint(
                pair_store,
                batch_id=batch.batch_id,
                status="error",
                provider=provider_config.provider,
                request_context=request_context,
                duration_ms=attempt_duration_ms,
                provider_available=False,
                error=last_error,
                call_status=call_status,
            )
        if policy.should_fallback(attempt_index):
            break
    fallback_result = run_batch_fallback(batch)
    _write_fallback_diagnostic(batch_dir, fallback_result)
    if fallback_result.success:
        _record_checkpoint(
            pair_store,
            batch_id=batch.batch_id,
            status="error",
            provider="fallback-rule",
            request_context={
                "pair_id": pair.pair_id,
                "chapter_range": list(batch.chapter_numbers),
                "old_section_titles": [section.title for section in batch.old_sections]
                or [unit.chapter_title for unit in batch.compare_units],
                "new_section_titles": [section.title for section in batch.new_sections]
                or [unit.chapter_title for unit in batch.compare_units],
                "compare_unit_ids": [unit.unit_id for unit in batch.compare_units],
                "compare_unit_count": len(batch.compare_units),
                "provider": "fallback-rule",
                "model": runtime_config.llm.model,
                "batch_size": runtime_config.llm.chapter_batch_size,
                "attempt_index": attempt_index,
            },
            duration_ms=0,
            provider_available=False,
            result={"row_count": len(fallback_result.rows), "fallback": True},
            error="本地 fallback 仅用于诊断，不能作为正式对照内容",
            call_status="failed",
            fallback_name=fallback_result.name,
            resume_from="continue",
        )
        timeline.record(
            "failed",
            provider="fallback-rule",
            attempt_index=attempt_index,
            error="本地 fallback 仅用于诊断，不能作为正式对照内容",
            details={
                "row_count": len(fallback_result.rows),
                "fallback": fallback_result.name,
                "diagnostic_path": "fallback.diagnostic.json",
            },
        )
        timeline.finalize(
            "failed",
            attempt_count=attempt_index,
            provider="fallback-rule",
            fallback=fallback_result.name,
            error=(last_error + "\n" if last_error else "") + "本地 fallback 仅用于诊断，不能作为正式对照内容",
            recoverable=True,
            next_resume_step="continue",
        )
        raise RuntimeError("本地 fallback 仅用于诊断，不能作为正式对照内容")
    timeline.record("failed", attempt_index=attempt_index, error=last_error)
    timeline.finalize(
        "failed",
        attempt_count=attempt_index,
        error=last_error,
        recoverable=True,
        next_resume_step="continue",
    )
    raise RuntimeError(last_error or "batch 未获得可用结果")


def _write_fallback_diagnostic(batch_dir: Path, fallback_result) -> None:
    """把本地 fallback 结果写成诊断文件，但不允许进入正式 rows。"""
    _write_json_file(
        batch_dir / "fallback.diagnostic.json",
        {
            "fallback": fallback_result.name,
            "success": fallback_result.success,
            "row_count": len(fallback_result.rows),
            "error": fallback_result.error,
            "formal_result_allowed": False,
            "rows": [
                {
                    "chapter": row.chapter,
                    "subchapter": row.subchapter,
                    "old_text": row.old_text,
                    "new_text": row.new_text,
                }
                for row in fallback_result.rows
            ],
        },
    )


def _provider_chain_for_batch(*, provider_chain, batch_index: int, runtime_config: FileComparisonRuntimeConfig):
    """按批次序号和路由配置生成本批首发 provider 顺序。"""
    if not runtime_config.llm.task_routing.enabled or not runtime_config.llm.task_routing.batch_compare:
        return tuple(provider_chain)
    providers_by_name = {provider.provider: provider for provider in provider_chain}
    configured_names = [name for name in runtime_config.llm.task_routing.batch_compare if name in providers_by_name]
    if not configured_names:
        return tuple(provider_chain)
    start_index = batch_index % len(configured_names)
    rotated_names = configured_names[start_index:] + configured_names[:start_index]
    ordered_chain = [providers_by_name[name] for name in rotated_names]
    ordered_names = set(rotated_names)
    ordered_chain.extend(provider for provider in provider_chain if provider.provider not in ordered_names)
    return tuple(ordered_chain)


def _batch_title(batch: ChapterBatch) -> str:
    """返回 batch 中最适合作为修复默认章节名的标题。"""
    if batch.compare_units:
        return batch.compare_units[0].chapter_title
    if batch.old_sections:
        return batch.old_sections[0].title
    if batch.new_sections:
        return batch.new_sections[0].title
    return ""


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    """用统一格式写 JSON 文件。"""
    atomic_write_json(path, payload)


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件缺失、损坏或不是对象时返回空字典。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _request_context(pair: PairMatch, batch: ChapterBatch, runtime_config: FileComparisonRuntimeConfig, provider_config, attempt_index: int) -> dict[str, Any]:
    """构造 checkpoint、timeline 和日志共享的请求上下文。"""
    return {
        "pair_id": pair.pair_id,
        "chapter_range": list(batch.chapter_numbers),
        "old_section_titles": [section.title for section in batch.old_sections]
        or [unit.chapter_title for unit in batch.compare_units],
        "new_section_titles": [section.title for section in batch.new_sections]
        or [unit.chapter_title for unit in batch.compare_units],
        "compare_unit_ids": [unit.unit_id for unit in batch.compare_units],
        "compare_unit_count": len(batch.compare_units),
        "provider": provider_config.provider,
        "model": provider_config.model,
        "batch_size": runtime_config.llm.chapter_batch_size,
        "attempt_index": attempt_index,
    }


def _apply_prompt_mode(payload: dict[str, Any], *, prompt_mode: str) -> dict[str, Any]:
    """根据治理层 prompt 模式调整请求文本，不向 provider 发送调试字段。"""
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    if prompt_mode == "plain_json":
        instructions = str(payload.pop("instructions", "")).strip()
        input_payload = str(payload.get("input", "")).strip()
        schema_payload = payload.pop("text", None)
        schema_hint = json.dumps(schema_payload, ensure_ascii=False) if schema_payload else ""
        prompt_parts = [
            instructions,
            "请严格输出一个 JSON 对象，不要输出 Markdown，不要解释。",
            f"JSON 输出格式要求：{schema_hint}" if schema_hint else "",
            "输入数据如下：",
            input_payload,
        ]
        payload["input"] = "\n\n".join(part for part in prompt_parts if part)
        return payload
    if prompt_mode != "minimal":
        return payload
    if isinstance(payload.get("messages"), list) and payload["messages"]:
        payload["messages"][-1]["content"] = (
            str(payload["messages"][-1].get("content", ""))
            + "\n\n请使用最小 JSON 输出：只保留 units、unit_id、chapter、subchapter、change_type、display_strategy、numbering_only、unchanged_lines、old_focus_text、new_focus_text、confidence。"
        )
    elif isinstance(payload.get("input"), str):
        payload["input"] = payload["input"] + "\n\n请使用最小 JSON 输出，只保留必要字段。"
    return payload


def should_downgrade_to_plain_json(provider_name: str, exc: Exception) -> bool:
    """判断 provider 是否因为请求字段兼容问题需要降级为普通 JSON prompt。"""
    if provider_name.strip().lower() != "deepseek-ark":
        return False
    if not isinstance(exc, ProviderRequestError):
        return False
    message = str(exc)
    return bool(ARK_REQUEST_SHAPE_ERROR_RE.search(message))


def _post_with_raw(client: OpenAIResponsesClient, request_payload: dict[str, Any], provider_config) -> tuple[dict[str, Any], dict[str, Any]]:
    """兼容新旧 client，返回 raw 与 normalized 两份响应。"""
    if hasattr(client, "post_with_raw"):
        return client.post_with_raw(request_payload, provider_config=provider_config)
    normalized = client.post(request_payload, provider_config=provider_config)
    return normalized, normalized


def _record_provider_unavailable(client: OpenAIResponsesClient, provider_config, *, reason: str) -> None:
    """通知 client 某 provider 返回不可用，用于触发 provider 级失败冷却。"""
    if hasattr(client, "record_provider_unavailable"):
        client.record_provider_unavailable(provider_config, reason=reason)


def _parse_or_repair_payload(
    *,
    normalized_response: dict[str, Any],
    batch_dir: Path,
    provider_name: str,
    attempt_index: int,
    default_chapter: str,
    timeline: CallTimeline,
) -> dict[str, Any]:
    """解析模型响应；失败时尝试结构修复并落 repair 文件。"""
    try:
        return parse_response_payload(normalized_response)
    except ResponseParseError as parse_error:
        try:
            output_text = normalized_response.get("output_text", "")
            raw_payload = coerce_json_object_payload(output_text) if isinstance(output_text, str) else normalized_response
            repaired = repair_response_payload(raw_payload, default_chapter=default_chapter)
        except Exception as repair_error:  # noqa: BLE001
            repair_record = build_repair_record(
                repair_type="none",
                before_payload=normalized_response,
                success=False,
                error=f"{type(repair_error).__name__}: {repair_error}",
            )
            _write_json_file(batch_dir / f"repair.attempt-{attempt_index:02d}.{provider_name}.json", repair_record)
            _write_json_file(batch_dir / "repair.json", repair_record)
            raise parse_error from repair_error
        repair_record = build_repair_record(
            repair_type="wrap_subsections",
            before_payload=raw_payload,
            success=True,
            payload=repaired,
        )
        _write_json_file(batch_dir / f"repair.attempt-{attempt_index:02d}.{provider_name}.json", repair_record)
        _write_json_file(batch_dir / "repair.json", repair_record)
        timeline.record(
            "repaired",
            provider=provider_name,
            attempt_index=attempt_index,
            details={"repair_type": repair_record["repair_type"]},
        )
        return repaired


def _record_checkpoint(
    pair_store: PairCheckpointStore | None,
    *,
    batch_id: str,
    status: str,
    provider: str,
    request_context: dict[str, Any],
    duration_ms: int,
    provider_available: bool,
    result: dict[str, Any] | None = None,
    error: str = "",
    call_status: str = "",
    repair_used: bool = False,
    fallback_name: str = "",
    resume_from: str = "",
) -> None:
    """向 pair checkpoint 写入一次尝试，并附加治理字段。"""
    if pair_store is None:
        return
    enriched_context = dict(request_context)
    if call_status:
        enriched_context["call_status"] = call_status
    if resume_from:
        enriched_context["resume_from"] = resume_from
    enriched_result = dict(result or {})
    if repair_used:
        enriched_result["repair_used"] = True
    if fallback_name:
        enriched_result["fallback_name"] = fallback_name
    pair_store.record_entry(
        entry_id=batch_id,
        status=status,
        result=enriched_result or None,
        error={"message": error} if error else None,
        provider=provider,
        request_context=enriched_context,
        duration_ms=duration_ms,
        provider_available=provider_available,
        call_status=call_status,
        repair_used=repair_used,
        fallback_name=fallback_name,
        resume_from=resume_from,
    )


def write_status_json(run_dir: Path, manifest: TaskManifest) -> None:
    """把任务当前状态写成页面轮询使用的 `status.json`。"""
    all_batches = [batch for pair in manifest.pairs for batch in pair.batches]
    failed_provider_count = sum(
        1
        for batch in all_batches
        if batch.provider and not batch.provider_available and batch.provider != "fallback-rule"
    )
    repair_count = sum(1 for batch in all_batches if batch.repair_used)
    fallback_count = sum(1 for batch in all_batches if batch.fallback_name)
    current_batch = next((batch for batch in all_batches if batch.status not in {"success", "aborted"}), None)
    if current_batch is None and all_batches:
        current_batch = all_batches[-1]
    payload = {
        "task_id": manifest.task_id,
        "run_dir": str(manifest.run_dir),
        "status": manifest.status,
        "duration_ms": manifest.duration_ms,
        "poll_interval_seconds": manifest.poll_interval_seconds,
        "pair_count": manifest.pair_count,
        "success_count": manifest.success_count,
        "failed_count": manifest.failed_count,
        "completed_pair_count": manifest.completed_pair_count,
        "failed_pair_count": manifest.failed_pair_count,
        "governance_summary": {
            "current_batch_id": current_batch.batch_id if current_batch else "",
            "current_provider": current_batch.provider if current_batch else "",
            "current_call_status": current_batch.call_status if current_batch else "",
            "completed_batch_count": sum(1 for batch in all_batches if batch.status == "success"),
            "failed_batch_count": sum(1 for batch in all_batches if batch.status != "success"),
            "provider_failure_count": failed_provider_count,
            "repair_count": repair_count,
            "fallback_count": fallback_count,
            "total_batch_count": len(all_batches),
        },
        "pairs": [
            {
                "pair_id": pair.pair_id,
                "key": pair.key,
                "status": pair.status,
                "old_path": pair.old_path,
                "new_path": pair.new_path,
                "docx_path": pair.docx_path,
                "doc_path": pair.doc_path,
                "error": pair.error,
                "duration_ms": pair.duration_ms,
                "completed_batch_count": pair.completed_batch_count,
                "failed_batch_count": pair.failed_batch_count,
                "batches": [
                    {
                        "batch_id": batch.batch_id,
                        "status": batch.status,
                        "chapter_range": list(batch.chapter_range),
                        "attempt_count": batch.attempt_count,
                        "provider": batch.provider,
                        "error": batch.error,
                        "duration_ms": batch.duration_ms,
                        "total_duration_ms": batch.total_duration_ms,
                        "provider_available": batch.provider_available,
                        "call_status": batch.call_status,
                        "repair_used": batch.repair_used,
                        "fallback_name": batch.fallback_name,
                        "resume_from": batch.resume_from,
                    }
                    for batch in pair.batches
                ],
            }
            for pair in manifest.pairs
        ],
        "notes": list(manifest.notes),
    }
    atomic_write_json(run_dir / "status.json", payload)


def run_task(
    *,
    folder_path: Path,
    runtime_config: FileComparisonRuntimeConfig,
    run_dir: Path | None = None,
    client: OpenAIResponsesClient | None = None,
    pairs: list[PairMatch] | None = None,
) -> TaskManifest:
    """执行一次完整批处理任务，并持续落盘任务状态。"""
    paths = resolve_paths()
    run_dir = run_dir or prepare_run_dir(paths.runs_root)
    task_started_at = time.perf_counter()
    pairs = pairs or scan_folder_for_pairs(folder_path, runtime_config.pairing.month_pattern)
    task_id = run_dir.name
    task_store = TaskCheckpointStore.load_or_create(run_dir / "checkpoints" / "task_checkpoint.json", task_id=task_id)
    atomic_write_json(
        run_dir / "task.json",
        {
            "task_id": task_id,
            "folder_path": str(folder_path),
            "pair_count": len(pairs),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )
    write_status_json(
        run_dir,
        TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status="running",
            poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=0,
            failed_count=0,
            completed_pair_count=0,
            failed_pair_count=0,
            pairs=tuple(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="pending",
                )
                for pair in pairs
            ),
            duration_ms=0,
        ),
    )
    pair_manifests: list[PairManifest] = []
    for pair in pairs:
        pair_dir = run_dir / "pairs" / pair.pair_id
        for subdir in ("source", "extracted", "llm", "outputs"):
            (pair_dir / subdir).mkdir(parents=True, exist_ok=True)
        pair_store = PairCheckpointStore.load_or_create(run_dir / "checkpoints" / f"pair_{pair.pair_id}_checkpoint.json", pair_id=pair.pair_id, task_id=task_id)
        task_store.record_pair(pair_id=pair.pair_id, status="extracting")
        atomic_write_json(
            pair_dir / "pair.json",
            {
                "pair_id": pair.pair_id,
                "key": pair.key,
                "old_path": str(pair.old_path),
                "new_path": str(pair.new_path),
                "old_label": pair.old_label,
                "new_label": pair.new_label,
                "status": "extracting",
            },
        )
        write_status_json(
            run_dir,
            TaskManifest(
                task_id=task_id,
                run_dir=run_dir,
                status="running",
                poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
                pair_count=len(pairs),
                success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                pairs=(
                    *pair_manifests,
                    PairManifest(
                        pair_id=pair.pair_id,
                        key=pair.key,
                        old_path=str(pair.old_path),
                        new_path=str(pair.new_path),
                        status="extracting",
                    ),
                ),
                duration_ms=int((time.perf_counter() - task_started_at) * 1000),
            ),
        )
        pair_started_at = time.perf_counter()
        try:
            task_store.record_pair(pair_id=pair.pair_id, status="llm_running")
            write_status_json(
                run_dir,
                TaskManifest(
                    task_id=task_id,
                    run_dir=run_dir,
                    status="running",
                    poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
                    pair_count=len(pairs),
                    success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                    failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                    completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
                    failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
                    pairs=(
                        *pair_manifests,
                        PairManifest(
                            pair_id=pair.pair_id,
                            key=pair.key,
                            old_path=str(pair.old_path),
                            new_path=str(pair.new_path),
                            status="llm_running" if runtime_config.llm_mode == "responses" else "postprocessing",
                        ),
                    ),
                    duration_ms=int((time.perf_counter() - task_started_at) * 1000),
                ),
            )
            result = compare_pair(pair=pair, pair_dir=pair_dir, runtime_config=runtime_config, pair_store=pair_store, client=client)
            pair_payload = pair_store.payload()
            summary = pair_payload["status_summary"]
            atomic_write_json(
                pair_dir / "pair.json",
                {
                    "pair_id": pair.pair_id,
                    "key": pair.key,
                    "old_path": str(pair.old_path),
                    "new_path": str(pair.new_path),
                    "old_label": pair.old_label,
                    "new_label": pair.new_label,
                    "status": "completed",
                    "docx_path": str(result.docx_path),
                    "doc_path": str(result.doc_path) if result.doc_path else "",
                    "row_count": len(result.rows),
                    "duration_ms": int((time.perf_counter() - pair_started_at) * 1000),
                    "completed_batch_count": int(summary.get("success", 0)),
                    "failed_batch_count": int(summary.get("error", 0))
                    + int(summary.get("parse_error", 0))
                    + int(summary.get("postprocess_error", 0))
                    + int(summary.get("aborted", 0)),
                },
            )
            pair_duration_ms = int((time.perf_counter() - pair_started_at) * 1000)
            task_store.record_pair(pair_id=pair.pair_id, status="completed", summary=summary, duration_ms=pair_duration_ms)
            pair_manifests.append(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="completed",
                    docx_path=str(result.docx_path),
                    doc_path=str(result.doc_path) if result.doc_path else "",
                    duration_ms=pair_duration_ms,
                    completed_batch_count=int(summary.get("success", 0)),
                    failed_batch_count=int(summary.get("error", 0))
                    + int(summary.get("parse_error", 0))
                    + int(summary.get("postprocess_error", 0))
                    + int(summary.get("aborted", 0)),
                    batches=tuple(
                        BatchManifest(
                            batch_id=entry["entry_id"],
                            status=entry["status"],
                            chapter_range=tuple(entry.get("request_context", {}).get("chapter_range", [])),
                            attempt_count=int(entry.get("attempt_count", 0)),
                            provider=str(entry.get("provider", "")),
                            error=str(entry.get("error", {}).get("message", "")),
                            duration_ms=int(entry.get("duration_ms", 0) or 0),
                            total_duration_ms=int(entry.get("total_duration_ms", 0) or 0),
                            provider_available=bool(entry.get("provider_available", True)),
                            call_status=str(entry.get("call_status", "")),
                            repair_used=bool(entry.get("repair_used", False)),
                            fallback_name=str(entry.get("fallback_name", "")),
                            resume_from=str(entry.get("resume_from", "")),
                        )
                        for entry in pair_payload["entries"]
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            pair_duration_ms = int((time.perf_counter() - pair_started_at) * 1000)
            task_store.record_pair(pair_id=pair.pair_id, status="failed", error=str(exc), duration_ms=pair_duration_ms)
            pair_manifests.append(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="failed",
                    error=str(exc),
                    duration_ms=pair_duration_ms,
                )
            )
            atomic_write_json(
                pair_dir / "pair.json",
                {
                    "pair_id": pair.pair_id,
                    "key": pair.key,
                    "old_path": str(pair.old_path),
                    "new_path": str(pair.new_path),
                    "old_label": pair.old_label,
                    "new_label": pair.new_label,
                    "status": "failed",
                    "error": str(exc),
                    "duration_ms": pair_duration_ms,
                },
            )
        manifest = TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status=task_status_from_pairs(pair_manifests),
            poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
            failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
            pairs=tuple(pair_manifests),
            duration_ms=int((time.perf_counter() - task_started_at) * 1000),
            completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
            failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
        )
        write_status_json(run_dir, manifest)
    final_manifest = TaskManifest(
        task_id=task_id,
        run_dir=run_dir,
        status=task_status_from_pairs(pair_manifests),
        poll_interval_seconds=runtime_config.ui.poll_interval_seconds,
        pair_count=len(pairs),
        success_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
        failed_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
        pairs=tuple(pair_manifests),
        duration_ms=int((time.perf_counter() - task_started_at) * 1000),
        completed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "completed"),
        failed_pair_count=sum(1 for pair_manifest in pair_manifests if pair_manifest.status == "failed"),
    )
    write_status_json(run_dir, final_manifest)
    return final_manifest


class TaskManager:
    """为本地页面提供后台任务创建与线程托管能力。"""

    def __init__(self, runtime_config: FileComparisonRuntimeConfig, paths: AppPaths | None = None) -> None:
        """保存运行配置、输出路径和后台线程表。"""
        self.runtime_config = runtime_config
        self.paths = paths or resolve_paths()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def create_task(self, folder_path: Path, pairs: list[PairMatch] | None = None) -> TaskManifest:
        """创建一个后台运行任务，并立即返回初始 manifest。"""
        run_dir = prepare_run_dir(self.paths.runs_root)
        task_id = run_dir.name
        pairs = pairs or scan_folder_for_pairs(folder_path, self.runtime_config.pairing.month_pattern)
        initial_manifest = TaskManifest(
            task_id=task_id,
            run_dir=run_dir,
            status="pending",
            poll_interval_seconds=self.runtime_config.ui.poll_interval_seconds,
            pair_count=len(pairs),
            success_count=0,
            failed_count=0,
            completed_pair_count=0,
            failed_pair_count=0,
            pairs=tuple(
                PairManifest(
                    pair_id=pair.pair_id,
                    key=pair.key,
                    old_path=str(pair.old_path),
                    new_path=str(pair.new_path),
                    status="pending",
                )
                for pair in pairs
            ),
        )
        atomic_write_json(
            run_dir / "task.json",
            {
                "task_id": task_id,
                "folder_path": str(folder_path),
                "pair_count": len(pairs),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        write_status_json(run_dir, initial_manifest)
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir, pairs), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return initial_manifest

    def rerun_batch(self, task_id: str, pair_id: str, batch_id: str) -> dict[str, str]:
        """清理单个 batch 的恢复现场，并启动同任务恢复执行。"""
        task_id = str(task_id).strip()
        pair_id = str(pair_id).strip()
        batch_id = str(batch_id).strip()
        if not task_id or not pair_id or not batch_id:
            raise ValueError("task_id、pair_id、batch_id 不能为空")
        with self._lock:
            current_thread = self._threads.get(task_id)
            if current_thread and current_thread.is_alive():
                raise RuntimeError("任务仍在运行，当前请求结束后再重跑该批次")
        run_dir = self.paths.runs_root / task_id
        if not run_dir.exists():
            raise FileNotFoundError(f"task not found: {task_id}")
        task_payload = _read_json_object(run_dir / "task.json")
        status_payload = _read_json_object(run_dir / "status.json")
        pairs = _pairs_from_status_payload(status_payload)
        if not pairs:
            raise ValueError("任务状态中没有可恢复的文件配对")
        target_pair = next((pair for pair in pairs if pair.pair_id == pair_id), None)
        if target_pair is None:
            raise ValueError(f"pair not found: {pair_id}")
        self._clear_batch_for_rerun(run_dir=run_dir, pair_id=pair_id, batch_id=batch_id)
        folder_path = Path(str(task_payload.get("folder_path") or target_pair.old_path.parent)).expanduser().resolve()
        thread = threading.Thread(target=self._run_background, args=(folder_path, run_dir, pairs), daemon=True)
        with self._lock:
            self._threads[task_id] = thread
        thread.start()
        return {"task_id": task_id, "pair_id": pair_id, "batch_id": batch_id, "status": "running"}

    def _clear_batch_for_rerun(self, *, run_dir: Path, pair_id: str, batch_id: str) -> None:
        """删除单个 batch 的过程文件、checkpoint entry 和旧产物。"""
        batch_dir = run_dir / "pairs" / pair_id / "llm" / batch_id
        if batch_dir.exists():
            shutil.rmtree(batch_dir)
        pair_checkpoint_path = run_dir / "checkpoints" / f"pair_{pair_id}_checkpoint.json"
        if pair_checkpoint_path.exists():
            pair_store = PairCheckpointStore.load_or_create(pair_checkpoint_path, pair_id=pair_id, task_id=run_dir.name)
            pair_store.remove_entry(batch_id)
        outputs_dir = run_dir / "pairs" / pair_id / "outputs"
        for target in tuple(outputs_dir.glob("*.docx")) + tuple(outputs_dir.glob("*.doc")):
            if target.exists():
                target.unlink()

    def _run_background(self, folder_path: Path, run_dir: Path, pairs: list[PairMatch]) -> None:
        """在线程中执行真实任务，并在结束后清理线程登记。"""
        try:
            run_task(folder_path=folder_path, runtime_config=self.runtime_config, run_dir=run_dir, pairs=pairs)
        finally:
            with self._lock:
                self._threads.pop(run_dir.name, None)


def _pairs_from_status_payload(payload: dict[str, Any]) -> list[PairMatch]:
    """从 status.json 中恢复任务的文件配对列表。"""
    raw_pairs = payload.get("pairs", [])
    if not isinstance(raw_pairs, list):
        return []
    pairs: list[PairMatch] = []
    for index, item in enumerate(raw_pairs, start=1):
        if not isinstance(item, dict):
            continue
        old_path = Path(str(item.get("old_path", ""))).expanduser().resolve()
        new_path = Path(str(item.get("new_path", ""))).expanduser().resolve()
        if not old_path.exists() or not new_path.exists():
            continue
        pairs.append(
            PairMatch(
                pair_id=str(item.get("pair_id") or f"pair-{index:03d}"),
                key=str(item.get("key") or old_path.stem),
                old_path=old_path,
                new_path=new_path,
                old_label=old_path.name,
                new_label=new_path.name,
            )
        )
    return pairs
