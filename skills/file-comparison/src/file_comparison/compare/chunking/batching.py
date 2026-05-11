
"""compare block/unit 分批与超长拆分。

职责：`group_compare_blocks_into_batches`、按字数/块数/章数拆分与重编号。
不负责：块内条目语义（见 `compare_blocks`）。
"""

from __future__ import annotations

from ..extractor import ordered_unique_section_numbers
from ..models import ChapterBatch, CompareBlock, CompareBlockItem, CompareUnit, Section

def compare_block_chars(block: CompareBlock) -> int:
    """计算单个 compare block 的左右文本总字符数。"""
    return sum(len(item.text) for item in block.old_items) + sum(len(item.text) for item in block.new_items)

def split_compare_block_by_items(block: CompareBlock, *, max_compare_block_chars: int) -> list[CompareBlock]:
    """按 sibling 边界拆分单个过长 block。"""
    old_items = list(block.old_items)
    new_items = list(block.new_items)
    chunk_pairs: list[tuple[list[CompareBlockItem], list[CompareBlockItem]]] = []
    current_old: list[CompareBlockItem] = []
    current_new: list[CompareBlockItem] = []
    current_chars = 0
    item_count = max(len(old_items), len(new_items))
    for index in range(item_count):
        old_item = old_items[index] if index < len(old_items) else None
        new_item = new_items[index] if index < len(new_items) else None
        pair_chars = (len(old_item.text) if old_item else 0) + (len(new_item.text) if new_item else 0)
        if current_old or current_new:
            if current_chars + pair_chars > max_compare_block_chars:
                chunk_pairs.append((current_old, current_new))
                current_old = []
                current_new = []
                current_chars = 0
        if old_item is not None:
            current_old.append(old_item)
        if new_item is not None:
            current_new.append(new_item)
        current_chars += pair_chars
    if current_old or current_new:
        chunk_pairs.append((current_old, current_new))
    parts: list[CompareBlock] = []
    for index, (old_chunk, new_chunk) in enumerate(chunk_pairs):
        parts.append(
            CompareBlock(
                block_id=f"{block.block_id}-part-{index + 1:03d}",
                chapter_number=block.chapter_number,
                chapter_title=block.chapter_title,
                parent_path=block.parent_path,
                old_items=tuple(old_chunk),
                new_items=tuple(new_chunk),
            )
        )
    return parts

def split_oversized_compare_blocks(
    compare_blocks: list[CompareBlock],
    *,
    max_compare_block_chars: int,
) -> list[CompareBlock]:
    """把单个过长 compare block 按 sibling 边界拆成 part。"""
    if max_compare_block_chars <= 0:
        return compare_blocks
    refined: list[CompareBlock] = []
    for block in compare_blocks:
        if compare_block_chars(block) <= max_compare_block_chars:
            refined.append(block)
            continue
        refined.extend(split_compare_block_by_items(block, max_compare_block_chars=max_compare_block_chars))
    return refined

def _group_blocks_by_chapter_limit(compare_blocks: list[CompareBlock], batch_size: int) -> list[ChapterBatch]:
    """按章节数量上限把 compare blocks 分组。"""
    batches: list[ChapterBatch] = []
    current_blocks: list[CompareBlock] = []
    current_numbers: list[str] = []
    current_seen_numbers: set[str] = set()
    for block in compare_blocks:
        block_is_new_chapter = block.chapter_number not in current_seen_numbers
        if current_blocks and block_is_new_chapter and len(current_seen_numbers) >= batch_size:
            batches.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(current_numbers),
                    compare_blocks=tuple(current_blocks),
                )
            )
            current_blocks = []
            current_numbers = []
            current_seen_numbers = set()
        current_blocks.append(block)
        if block.chapter_number not in current_seen_numbers:
            current_seen_numbers.add(block.chapter_number)
            current_numbers.append(block.chapter_number)
    if current_blocks:
        batches.append(
            ChapterBatch(
                batch_id="",
                chapter_numbers=tuple(current_numbers),
                compare_blocks=tuple(current_blocks),
            )
        )
    return batches

def batch_compare_block_chars(batch: ChapterBatch) -> int:
    """计算 batch 中实际发送给模型的 block 总字符数。"""
    return sum(compare_block_chars(block) for block in batch.compare_blocks)

def split_oversized_compare_block_batch(
    batch: ChapterBatch,
    *,
    max_batch_chars: int,
    oversized_batch_size: int,
) -> list[ChapterBatch]:
    """递归拆分超长 block batch，优先按章节数收缩。"""
    if max_batch_chars <= 0 or batch_compare_block_chars(batch) <= max_batch_chars or len(batch.chapter_numbers) <= 1:
        return [batch]
    next_batch_size = oversized_batch_size if len(batch.chapter_numbers) > oversized_batch_size else 1
    split_batches = _group_blocks_by_chapter_limit(list(batch.compare_blocks), next_batch_size)
    refined: list[ChapterBatch] = []
    for split_batch in split_batches:
        refined.extend(
            split_oversized_compare_block_batch(
                split_batch,
                max_batch_chars=max_batch_chars,
                oversized_batch_size=oversized_batch_size,
            )
        )
    return refined

def split_batches_by_compare_block_limit(
    batches: list[ChapterBatch],
    *,
    max_compare_blocks_per_batch: int,
) -> list[ChapterBatch]:
    """按 compare block 数拆分 batch，但不拆开同一章节。"""
    if max_compare_blocks_per_batch <= 0:
        return batches
    refined: list[ChapterBatch] = []
    for batch in batches:
        blocks = list(batch.compare_blocks)
        if len(blocks) <= max_compare_blocks_per_batch:
            refined.append(batch)
            continue
        chapter_groups: list[list[CompareBlock]] = []
        for block in blocks:
            if not chapter_groups or chapter_groups[-1][0].chapter_number != block.chapter_number:
                chapter_groups.append([block])
            else:
                chapter_groups[-1].append(block)
        current: list[CompareBlock] = []
        for chapter_group in chapter_groups:
            if current and len(current) + len(chapter_group) > max_compare_blocks_per_batch:
                refined.append(
                    ChapterBatch(
                        batch_id="",
                        chapter_numbers=tuple(dict.fromkeys(block.chapter_number for block in current)),
                        compare_blocks=tuple(current),
                    )
                )
                current = []
            current.extend(chapter_group)
        if current:
            refined.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(dict.fromkeys(block.chapter_number for block in current)),
                    compare_blocks=tuple(current),
                )
            )
    return refined

def _renumber_block_batches(batches: list[ChapterBatch]) -> list[ChapterBatch]:
    """为 block 批次重新生成稳定 batch 编号。"""
    return [
        ChapterBatch(
            batch_id=f"batch-{index:03d}",
            chapter_numbers=batch.chapter_numbers,
            old_sections=batch.old_sections,
            new_sections=batch.new_sections,
            compare_blocks=batch.compare_blocks,
        )
        for index, batch in enumerate(batches, start=1)
    ]

def group_compare_blocks_into_batches(
    compare_blocks: list[CompareBlock],
    batch_size: int,
    *,
    max_batch_chars: int = 0,
    oversized_batch_size: int = 2,
    max_compare_blocks_per_batch: int = 0,
    max_compare_block_chars: int = 0,
) -> list[ChapterBatch]:
    """按章节数、字符数和 block 数把 compare blocks 分批。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    if oversized_batch_size <= 0:
        raise ValueError("oversized_batch_size 必须大于 0")
    # 业务对照要求同一章节内的上下文不能因字数被拆散；max_compare_block_chars 仅保留为兼容配置。
    initial_batches = _group_blocks_by_chapter_limit(compare_blocks, batch_size)
    if max_batch_chars <= 0:
        refined_batches = initial_batches
    else:
        refined_batches = []
        for batch in initial_batches:
            refined_batches.extend(
                split_oversized_compare_block_batch(
                    batch,
                    max_batch_chars=max_batch_chars,
                    oversized_batch_size=oversized_batch_size,
                )
            )
    block_limited_batches = split_batches_by_compare_block_limit(
        refined_batches,
        max_compare_blocks_per_batch=max_compare_blocks_per_batch,
    )
    return _renumber_block_batches(block_limited_batches)

def group_compare_units_into_batches(
    compare_units: list[CompareUnit],
    batch_size: int,
    *,
    max_batch_chars: int = 0,
    oversized_batch_size: int = 2,
    max_compare_units_per_batch: int = 0,
    max_compare_unit_chars: int = 0,
) -> list[ChapterBatch]:
    """按章节数分组；超大 batch 自动降为更小章节组或更小 unit 组。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    if oversized_batch_size <= 0:
        raise ValueError("oversized_batch_size 必须大于 0")
    compare_units = split_oversized_compare_units(
        compare_units,
        max_compare_unit_chars=max_compare_unit_chars,
    )
    initial_batches = _group_units_by_chapter_limit(compare_units, batch_size)
    if max_batch_chars <= 0:
        refined_batches = initial_batches
    else:
        refined_batches = []
        for batch in initial_batches:
            refined_batches.extend(
                split_oversized_compare_batch(
                    batch,
                    max_batch_chars=max_batch_chars,
                    oversized_batch_size=oversized_batch_size,
                )
            )
    unit_limited_batches = split_batches_by_compare_unit_limit(
        refined_batches,
        max_compare_units_per_batch=max_compare_units_per_batch,
    )
    return _renumber_batches(unit_limited_batches)

def split_oversized_compare_units(
    compare_units: list[CompareUnit],
    *,
    max_compare_unit_chars: int,
) -> list[CompareUnit]:
    """把单个过长 compare unit 按行拆成 part，避免单次返回过长。"""
    if max_compare_unit_chars <= 0:
        return compare_units
    refined: list[CompareUnit] = []
    for unit in compare_units:
        if compare_unit_chars(unit) <= max_compare_unit_chars:
            refined.append(unit)
            continue
        refined.extend(split_compare_unit_by_lines(unit, max_compare_unit_chars=max_compare_unit_chars))
    return refined

def split_compare_unit_by_lines(unit: CompareUnit, *, max_compare_unit_chars: int) -> list[CompareUnit]:
    """按左右文本的行边界拆分单个 compare unit。"""
    old_lines = unit.old_text.splitlines() or [unit.old_text]
    new_lines = unit.new_text.splitlines() or [unit.new_text]
    old_chunks = chunk_lines_by_char_limit(old_lines, max_compare_unit_chars=max_compare_unit_chars)
    new_chunks = chunk_lines_by_char_limit(new_lines, max_compare_unit_chars=max_compare_unit_chars)
    chunk_count = max(len(old_chunks), len(new_chunks))
    parts: list[CompareUnit] = []
    for index in range(chunk_count):
        old_text = "\n".join(old_chunks[index]) if index < len(old_chunks) else ""
        new_text = "\n".join(new_chunks[index]) if index < len(new_chunks) else ""
        parts.append(
            CompareUnit(
                unit_id=f"{unit.unit_id}-part-{index + 1:03d}",
                chapter_number=unit.chapter_number,
                chapter_title=unit.chapter_title,
                subchapter=unit.subchapter,
                old_text=old_text,
                new_text=new_text,
            )
        )
    return parts

def chunk_lines_by_char_limit(lines: list[str], *, max_compare_unit_chars: int) -> list[list[str]]:
    """把文本行按字符上限分块；单行超过上限时保留该整行。"""
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    side_limit = max(1, max_compare_unit_chars // 2)
    for line in lines:
        line_chars = len(line)
        separator_chars = 1 if current else 0
        if current and current_chars + separator_chars + line_chars > side_limit:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(line)
        current_chars += (1 if current_chars else 0) + line_chars
    if current:
        chunks.append(current)
    return chunks

def compare_unit_chars(unit: CompareUnit) -> int:
    """计算单个 compare unit 的左右文本总字符数。"""
    return len(unit.old_text) + len(unit.new_text)

def split_batches_by_compare_unit_limit(
    batches: list[ChapterBatch],
    *,
    max_compare_units_per_batch: int,
) -> list[ChapterBatch]:
    """按 compare unit 数继续拆分，避免模型输出过长被截断。"""
    if max_compare_units_per_batch <= 0:
        return batches
    refined: list[ChapterBatch] = []
    for batch in batches:
        units = list(batch.compare_units)
        if len(units) <= max_compare_units_per_batch:
            refined.append(batch)
            continue
        for start in range(0, len(units), max_compare_units_per_batch):
            chunk = units[start : start + max_compare_units_per_batch]
            refined.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(dict.fromkeys(unit.chapter_number for unit in chunk)),
                    compare_units=tuple(chunk),
                )
            )
    return refined

def split_oversized_compare_batch(
    batch: ChapterBatch,
    *,
    max_batch_chars: int,
    oversized_batch_size: int,
) -> list[ChapterBatch]:
    """递归拆分超长 batch，优先按配置的 2 章粒度，必要时降到单章。"""
    if max_batch_chars <= 0 or batch_compare_chars(batch) <= max_batch_chars or len(batch.chapter_numbers) <= 1:
        return [batch]
    next_batch_size = oversized_batch_size if len(batch.chapter_numbers) > oversized_batch_size else 1
    split_batches = _group_units_by_chapter_limit(list(batch.compare_units), next_batch_size)
    refined: list[ChapterBatch] = []
    for split_batch in split_batches:
        refined.extend(
            split_oversized_compare_batch(
                split_batch,
                max_batch_chars=max_batch_chars,
                oversized_batch_size=oversized_batch_size,
            )
        )
    return refined

def _group_units_by_chapter_limit(compare_units: list[CompareUnit], batch_size: int) -> list[ChapterBatch]:
    """按章节数量上限把 compare units 分组，临时 batch id 稍后统一重排。"""
    batches: list[ChapterBatch] = []
    current_units: list[CompareUnit] = []
    current_numbers: list[str] = []
    current_seen_numbers: set[str] = set()
    for unit in compare_units:
        unit_is_new_chapter = unit.chapter_number not in current_seen_numbers
        if current_units and unit_is_new_chapter and len(current_seen_numbers) >= batch_size:
            batches.append(
                ChapterBatch(
                    batch_id="",
                    chapter_numbers=tuple(current_numbers),
                    compare_units=tuple(current_units),
                )
            )
            current_units = []
            current_numbers = []
            current_seen_numbers = set()
        current_units.append(unit)
        if unit.chapter_number not in current_seen_numbers:
            current_seen_numbers.add(unit.chapter_number)
            current_numbers.append(unit.chapter_number)
    if current_units:
        batches.append(
            ChapterBatch(
                batch_id="",
                chapter_numbers=tuple(current_numbers),
                compare_units=tuple(current_units),
            )
        )
    return batches

def _renumber_batches(batches: list[ChapterBatch]) -> list[ChapterBatch]:
    """为最终 batch 列表重新生成稳定的 batch-XXX 编号。"""
    return [
        ChapterBatch(
            batch_id=f"batch-{index:03d}",
            chapter_numbers=batch.chapter_numbers,
            old_sections=batch.old_sections,
            new_sections=batch.new_sections,
            compare_units=batch.compare_units,
        )
        for index, batch in enumerate(batches, start=1)
    ]

def batch_compare_chars(batch: ChapterBatch) -> int:
    """计算 batch 中实际发送给模型的左右文本总字符数。"""
    return sum(len(unit.old_text) + len(unit.new_text) for unit in batch.compare_units)

def group_sections_into_batches(old_sections: list[Section], new_sections: list[Section], batch_size: int) -> list[tuple[tuple[Section, ...], tuple[Section, ...]]]:
    """按章节顺序把左右 section 分组成固定大小的 batch。"""
    old_by_number = {section.number: section for section in old_sections}
    new_by_number = {section.number: section for section in new_sections}
    ordered_numbers = ordered_unique_section_numbers(old_sections, new_sections)
    batches: list[tuple[tuple[Section, ...], tuple[Section, ...]]] = []
    current_old: list[Section] = []
    current_new: list[Section] = []
    for number in ordered_numbers:
        old_section = old_by_number.get(number)
        new_section = new_by_number.get(number)
        if old_section is not None:
            current_old.append(old_section)
        if new_section is not None:
            current_new.append(new_section)
        if len(current_old) >= batch_size or len(current_new) >= batch_size:
            batches.append((tuple(current_old), tuple(current_new)))
            current_old = []
            current_new = []
    if current_old or current_new:
        batches.append((tuple(current_old), tuple(current_new)))
    return batches
