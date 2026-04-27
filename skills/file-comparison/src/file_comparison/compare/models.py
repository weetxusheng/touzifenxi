"""定义文件对照流程共享的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Section:
    """表示从文档中切分出的一级章节。"""

    number: str
    title: str
    body: str


@dataclass(slots=True)
class ComparisonRow:
    """表示最终对照表中的一行内容。"""

    chapter: str
    old_text: str
    new_text: str
    subchapter: str = ""


@dataclass(slots=True)
class NumberingLevel:
    """表示 Word 自动编号的格式定义。"""

    num_format: str
    level_text: str


@dataclass(slots=True)
class PairMatch:
    """表示一组已配对的旧版文件与新版文件。"""

    pair_id: str
    key: str
    old_path: Path
    new_path: Path
    old_label: str
    new_label: str
    old_month: int | None = None
    new_month: int | None = None


@dataclass(slots=True)
class ChapterBatch:
    """表示送给模型的一批章节。"""

    batch_id: str
    chapter_numbers: tuple[str, ...]
    old_sections: tuple[Section, ...]
    new_sections: tuple[Section, ...]


@dataclass(slots=True)
class CompareResult:
    """表示单个文件对完成比较后的产物摘要。"""

    pair_id: str
    old_name: str
    new_name: str
    rows: list[ComparisonRow]
    docx_path: Path
    doc_path: Path | None = None
    metadata: dict[str, object] = field(default_factory=dict)
