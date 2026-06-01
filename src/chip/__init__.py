"""Chip 产业新闻聚合源（SEMI 中国 + 爱集微/集微网）。"""

__all__ = ["ChipSourceAdapter"]


def __getattr__(name):
    if name == "ChipSourceAdapter":
        from chip.source_adapter import ChipSourceAdapter
        return ChipSourceAdapter
    raise AttributeError(name)
