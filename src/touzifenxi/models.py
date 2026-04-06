from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class StockIdea:
    ticker: str
    name: str
    sector: str
    style_tags: List[str]
    valuation_percentile: float
    earnings_growth: float
    revenue_growth: float
    roe: float
    free_cashflow_margin: float
    momentum_20d: float
    momentum_60d: float
    relative_strength: float
    volume_trend: float
    drawdown_from_high: float
    volatility: float
    crowding: float
    event_score: float
    last_price: float = 0.0
    ma20_gap: float = 0.0
    ma60_gap: float = 0.0
    price_above_ma20: bool = False
    price_above_ma60: bool = False
    ma20_slope: float = 0.0
    turnover_trend: float = 0.0
    data_source: str = "sample"
    fundamental_source: str = "local_profile"
    industry_ready: bool = False
    financial_ready: bool = False
    factor_ready: bool = False
    ready_pool: bool = False
    theme_name: str = ""
    theme_bucket: str = "fallback"
    theme_source: str = ""
    theme_strength: float = 0.0
    router_mode: str = "fallback"
    prefilter_week: str = ""
    prefilter_theme: str = ""
    prefilter_bucket: str = ""
    prefilter_score: float = 0.0
    policy_score: float = 0.0
    valuation_score: float = 0.0
    performance_score: float = 0.0
    prefilter_source: str = ""


@dataclass
class AgentScore:
    agent_name: str
    score: float
    reason: str


@dataclass
class Recommendation:
    stock: StockIdea
    total_score: float
    stage: str
    agent_scores: Dict[str, AgentScore] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)


@dataclass
class StyleView:
    dominant_style: str
    confidence: float
    reason: str


@dataclass
class RunResult:
    style_view: StyleView
    recommendations: List[Recommendation]
    universe_size: int
    data_source: str
    portfolio_notes: List[str] = field(default_factory=list)
    ready_pool_size: int = 0
    fallback_pool_size: int = 0
    coverage_ratio: float = 0.0
    router_mode: str = "fallback"
    active_themes: List[str] = field(default_factory=list)
    bypass_count: int = 0
    candidate_decisions: List[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ThemeEvent:
    theme_name: str
    source_type: str
    title: str
    event_date: str
    ticker: str = ""
    source_name: str = ""
    source_url: str = ""
    strength: float = 0.0


@dataclass
class ThemeRouteResult:
    router_mode: str
    active_themes: List[str] = field(default_factory=list)
    candidate_rows: List[dict] = field(default_factory=list)
    events: List[ThemeEvent] = field(default_factory=list)
    bypass_count: int = 0


@dataclass(frozen=True)
class WeeklyThemeScore:
    theme_name: str
    total_score: float
    policy_score: float
    valuation_score: float
    performance_score: float
    performance_source: str
    detail_json: str = "{}"
    selected: bool = False


@dataclass
class WeeklyPoolResult:
    prefilter_week: str
    theme_scores: List[WeeklyThemeScore] = field(default_factory=list)
    pool_members: List[dict] = field(default_factory=list)
    wildcard_count: int = 0
    build_mode: str = "weekly"
    base_run_id: int | None = None
    refresh_added: int = 0
    refresh_removed: int = 0


@dataclass
class UniverseStock:
    ticker: str
    code: str
    name: str
    exchange: str
    board: str
    latest_price: float
    change_percent: float
    turnover_ratio: float
    amount: float
    is_st: bool
    is_suspended: bool


@dataclass
class UniverseFilter:
    exclude_st: bool = True
    exclude_suspended: bool = True
    min_amount: float = 100_000_000
    exclude_boards: List[str] = field(default_factory=lambda: ["BSE"])
    limit: int = 300
    offset: int = 0
