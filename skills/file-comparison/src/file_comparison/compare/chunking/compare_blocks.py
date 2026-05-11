
"""compare block 构建与块内条目过滤。

职责：`build_compare_blocks_for_llm`、块内去噪、送模展示文本。
不负责：LLM 批次切分（见 `batching`）、operation 后处理（见 `postprocess`）。
"""

from __future__ import annotations

from ..extractor import ordered_unique_section_numbers
from ..models import CompareBlock, CompareBlockItem, ComparisonRow, Section
from .constants import COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW
from .text_match import text_starts_with_subchapter
from .section_items import ordered_parent_groups, parent_path_match_key, section_items_for_blocks
from .text_match import strip_leading_numbering

def block_item_texts(items: tuple[CompareBlockItem, ...]) -> tuple[str, ...]:
    """返回 block 内条目文本序列，用于判断 block 是否完全未变。"""
    return tuple(item.text for item in items)

def _matched_indices_stripped_equal_within_window(
    old_block_items: tuple[CompareBlockItem, ...],
    new_block_items: tuple[CompareBlockItem, ...],
    *,
    neighbor_window: int,
) -> tuple[set[int], set[int]]:
    """返回去序号后正文相同、且 old 下标 i 与 new 下标 j 满足 |i-j|<=neighbor_window 的配对下标。"""
    old_n = len(old_block_items)
    new_n = len(new_block_items)
    candidates: list[tuple[int, int, int]] = []
    for i in range(old_n):
        key_old = strip_leading_numbering(old_block_items[i].text)
        j_lo = max(0, i - neighbor_window)
        j_hi = min(new_n, i + neighbor_window + 1)
        for j in range(j_lo, j_hi):
            if strip_leading_numbering(new_block_items[j].text) != key_old:
                continue
            candidates.append((abs(i - j), i, j))
    candidates.sort()
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for _dist, i, j in candidates:
        if i in matched_old or j in matched_new:
            continue
        matched_old.add(i)
        matched_new.add(j)
    return matched_old, matched_new

def filter_compare_block_items_by_stripped_numbering_identity(
    old_block_items: tuple[CompareBlockItem, ...],
    new_block_items: tuple[CompareBlockItem, ...],
    *,
    neighbor_window: int | None = None,
) -> tuple[tuple[CompareBlockItem, ...], tuple[CompareBlockItem, ...]]:
    """同块内减少送模噪音：先全文完全一致剔除，再在邻域内配对去行首序号后相同的条目。

    全文交集与原先一致。随后在所有满足 ``|i-j| <= neighbor_window``（默认
    ``COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW``）且
    ``strip_leading_numbering(old[i]) == strip_leading_numbering(new[j])`` 的 (i,j) 中，
    按 ``|i-j|`` 升序贪心配对，已配对的下标不再参与；配对成功的两侧条目一并丢弃。
    """
    if not old_block_items and not new_block_items:
        return old_block_items, new_block_items

    window = (
        neighbor_window
        if neighbor_window is not None
        else COMPARE_BLOCK_STRIP_MATCH_NEIGHBOR_WINDOW
    )

    old_texts = {item.text for item in old_block_items}
    new_texts = {item.text for item in new_block_items}
    identical_texts = old_texts & new_texts
    if identical_texts:
        old_block_items = tuple(item for item in old_block_items if item.text not in identical_texts)
        new_block_items = tuple(item for item in new_block_items if item.text not in identical_texts)

    if not old_block_items or not new_block_items:
        return old_block_items, new_block_items

    matched_old, matched_new = _matched_indices_stripped_equal_within_window(
        old_block_items, new_block_items, neighbor_window=window
    )
    kept_old = tuple(
        item for index, item in enumerate(old_block_items) if index not in matched_old
    )
    kept_new = tuple(
        item for index, item in enumerate(new_block_items) if index not in matched_new
    )
    return kept_old, kept_new

def build_compare_blocks_for_llm(
    old_sections: list[Section],
    new_sections: list[Section],
) -> tuple[list[CompareBlock], list[dict[str, object]]]:
    """把章节差异拆成父标题块级上下文。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers = ordered_unique_section_numbers(old_sections, new_sections)

    compare_blocks: list[CompareBlock] = []
    chapter_summaries: list[dict[str, object]] = []
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        chapter_title = old_section.title if old_section is not None else new_section.title
        old_items = section_items_for_blocks(old_section) if old_section is not None else []
        new_items = section_items_for_blocks(new_section) if new_section is not None else []
        parent_groups = ordered_parent_groups(old_items, new_items)
        chapter_blocks: list[CompareBlock] = []
        old_item_sequence = 1
        new_item_sequence = 1
        for block_index, parent_group in enumerate(parent_groups, start=1):
            old_block_items = tuple(
                CompareBlockItem(item_id=f"{number}-old-{old_item_sequence + index:03d}", text=text)
                for index, (_path, text) in enumerate(
                    item for item in old_items if parent_path_match_key(item[0]) == parent_group.key
                )
            )
            old_item_sequence += len(old_block_items)
            new_block_items = tuple(
                CompareBlockItem(item_id=f"{number}-new-{new_item_sequence + index:03d}", text=text)
                for index, (_path, text) in enumerate(
                    item for item in new_items if parent_path_match_key(item[0]) == parent_group.key
                )
            )
            new_item_sequence += len(new_block_items)
            if not old_block_items and not new_block_items:
                continue
            old_block_items, new_block_items = filter_compare_block_items_by_stripped_numbering_identity(
                old_block_items, new_block_items
            )
            if not old_block_items and not new_block_items:
                continue
            if block_item_texts(old_block_items) == block_item_texts(new_block_items):
                continue
            chapter_blocks.append(
                CompareBlock(
                    block_id=f"{number}-block-{block_index:03d}",
                    chapter_number=number,
                    chapter_title=chapter_title,
                    parent_path=parent_group.display_path,
                    old_items=old_block_items,
                    new_items=new_block_items,
                )
            )
        compare_blocks.extend(chapter_blocks)
        chapter_summaries.append(
            {
                "chapter_number": number,
                "chapter_title": chapter_title,
                "original_old_chars": len(old_section.body) if old_section is not None else 0,
                "original_new_chars": len(new_section.body) if new_section is not None else 0,
                "block_count": len(chapter_blocks),
                "sent_old_chars": sum(sum(len(item.text) for item in block.old_items) for block in chapter_blocks),
                "sent_new_chars": sum(sum(len(item.text) for item in block.new_items) for block in chapter_blocks),
                "kept_for_llm": bool(chapter_blocks),
                "reason": (
                    "added"
                    if old_section is None and new_section is not None
                    else "deleted"
                    if new_section is None and old_section is not None
                    else "changed"
                    if chapter_blocks
                    else "unchanged"
                ),
            }
        )
    return compare_blocks, chapter_summaries

def row_display_text(text: str, subchapter: str) -> str:
    """把对照行还原成适合再次发送给模型的展示文本。"""
    if not subchapter or text in {"新增", "删除"}:
        return text
    if text_starts_with_subchapter(text, subchapter):
        return text
    return f"{subchapter}\n{text}"

def build_candidate_section(section: Section, rows: list[ComparisonRow], *, side: str) -> Section | None:
    """把章节内真实变化的行重新压缩成精简版 section。"""
    display_chunks: list[str] = []
    for row in rows:
        text = row.old_text if side == "old" else row.new_text
        if not text.strip():
            continue
        display_chunks.append(row_display_text(text, row.subchapter))
    if not display_chunks:
        return None
    body = "\n".join([section.title, *display_chunks]).strip()
    return Section(number=section.number, title=section.title, body=body)
