from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List

from .models import Recommendation, StyleView


@dataclass
class ResearchRunRecord:
    run_at: datetime
    data_source: str
    universe_size: int
    dominant_style: str
    style_confidence: float
    report_path: str | None


@dataclass
class PersistedRun:
    run_id: int
    style_view: StyleView
    recommendations: List[Recommendation]
    data_source: str
    universe_size: int
    report_path: str | None
