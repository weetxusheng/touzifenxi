"""文件配对和输出文件名生成工具。"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .models import PairMatch

MONTH_RE = re.compile(r"(?P<month>\d{1,2})月")
FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\r\n]+')


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
