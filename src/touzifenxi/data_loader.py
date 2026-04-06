from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .models import StockIdea


def load_universe(path: Path) -> List[StockIdea]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    universe = []
    for item in payload:
        normalized = dict(item)
        momentum_20d = float(normalized.get("momentum_20d", 0.0))
        momentum_60d = float(normalized.get("momentum_60d", 0.0))
        relative_strength = float(normalized.get("relative_strength", 0.5))
        volume_trend = float(normalized.get("volume_trend", 0.5))
        normalized.setdefault("last_price", 0.0)
        normalized.setdefault("ma20_gap", momentum_20d / 2)
        normalized.setdefault("ma60_gap", momentum_60d / 2)
        normalized.setdefault("price_above_ma20", momentum_20d > 0)
        normalized.setdefault("price_above_ma60", momentum_60d > 0)
        normalized.setdefault("ma20_slope", momentum_20d / 4)
        normalized.setdefault("turnover_trend", volume_trend)
        normalized.setdefault("data_source", "sample")
        normalized.setdefault("crowding", max(0.0, min(1.0, relative_strength)))
        universe.append(
            StockIdea(**normalized)
        )
    return universe


def load_watchlist(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
