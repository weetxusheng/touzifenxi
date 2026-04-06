from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .models import AgentScore, StockIdea, StyleView


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


@dataclass
class StyleAgent:
    name: str = "style"

    def analyze(self, universe: List[StockIdea]) -> StyleView:
        growth_score = 0.0
        value_score = 0.0
        dividend_score = 0.0
        cyclical_score = 0.0

        for stock in universe:
            growth_bias = 0.5 * clamp(stock.earnings_growth) + 0.5 * clamp(stock.momentum_60d * 3)
            value_bias = 0.6 * (1 - stock.valuation_percentile) + 0.4 * clamp(stock.roe)
            dividend_bias = 0.5 * clamp(stock.free_cashflow_margin * 2) + 0.5 * (1 - stock.volatility)
            cyclical_bias = 0.5 * clamp(stock.revenue_growth) + 0.5 * clamp(stock.event_score)

            if "成长" in stock.style_tags or "科技" in stock.style_tags:
                growth_score += growth_bias
            if "价值" in stock.style_tags:
                value_score += value_bias
            if "红利" in stock.style_tags:
                dividend_score += dividend_bias
            if "周期" in stock.style_tags:
                cyclical_score += cyclical_bias

        scores: Dict[str, float] = {
            "成长": growth_score,
            "价值": value_score,
            "红利": dividend_score,
            "周期": cyclical_score,
        }
        dominant_style = max(scores, key=scores.get)
        total = sum(scores.values()) or 1.0
        confidence = scores[dominant_style] / total
        reason = f"当前样本池中{dominant_style}风格的景气与价格强度更占优。"
        return StyleView(dominant_style=dominant_style, confidence=confidence, reason=reason)


@dataclass
class FundamentalAgent:
    name: str = "fundamental"

    def score(self, stock: StockIdea, style_view: StyleView) -> AgentScore:
        quality = clamp(stock.roe * 2) * 0.35 + clamp(stock.free_cashflow_margin * 2) * 0.25
        growth = clamp(stock.earnings_growth * 2) * 0.25 + clamp(stock.revenue_growth * 2) * 0.15
        valuation = (1 - stock.valuation_percentile) * 0.25

        style_bonus = 0.08 if style_view.dominant_style in stock.style_tags else 0.0
        score = clamp(quality + growth + valuation + style_bonus)
        reason = "盈利质量、成长性和估值处于可接受区间。"
        return AgentScore(agent_name=self.name, score=score, reason=reason)


@dataclass
class TechnicalAgent:
    name: str = "technical"

    def score(self, stock: StockIdea, style_view: StyleView) -> AgentScore:
        trend = clamp(stock.momentum_20d * 4) * 0.25 + clamp(stock.momentum_60d * 3) * 0.35
        strength = clamp(stock.relative_strength) * 0.25
        volume = clamp((stock.volume_trend + stock.turnover_trend) / 2) * 0.15
        retracement_bonus = 0.05 if -0.18 <= stock.drawdown_from_high <= -0.05 else 0.0
        moving_average_bonus = 0.05 if stock.price_above_ma20 and stock.price_above_ma60 else 0.0
        slope_bonus = 0.05 if stock.ma20_slope > 0 else 0.0

        score = clamp(trend + strength + volume + retracement_bonus + moving_average_bonus + slope_bonus)
        reason = "趋势、相对强弱和量价结构支持继续观察或布局。"
        return AgentScore(agent_name=self.name, score=score, reason=reason)


@dataclass
class RiskAgent:
    name: str = "risk"

    def score(self, stock: StockIdea, style_view: StyleView) -> AgentScore:
        volatility_penalty = clamp(stock.volatility) * 0.45
        crowding_penalty = clamp(stock.crowding) * 0.35
        valuation_penalty = clamp(stock.valuation_percentile) * 0.20

        risk_penalty = clamp(volatility_penalty + crowding_penalty + valuation_penalty)
        score = clamp(1 - risk_penalty)
        reason = "波动、拥挤和估值风险尚可控。" if score >= 0.5 else "当前拥挤或波动偏高，需要降低仓位。"
        return AgentScore(agent_name=self.name, score=score, reason=reason)


@dataclass
class EventAgent:
    name: str = "event"

    def score(self, stock: StockIdea, style_view: StyleView) -> AgentScore:
        style_tailwind = 0.08 if style_view.dominant_style in stock.style_tags else 0.0
        score = clamp(stock.event_score + style_tailwind)
        reason = "存在景气、政策或产业催化预期。"
        return AgentScore(agent_name=self.name, score=score, reason=reason)
