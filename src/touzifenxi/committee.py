from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .agents import EventAgent, FundamentalAgent, RiskAgent, StyleAgent, TechnicalAgent
from .models import Recommendation, RunResult, StockIdea


def classify_stage(stock: StockIdea) -> str:
    if (
        stock.price_above_ma20
        and stock.price_above_ma60
        and stock.ma20_slope > 0
        and stock.momentum_60d >= 0.18
        and stock.relative_strength >= 0.65
        and stock.turnover_trend >= 0.55
    ):
        return "主升启动"
    if (
        stock.price_above_ma20
        and stock.ma20_slope > 0
        and stock.momentum_20d >= 0.03
        and stock.momentum_60d >= 0.10
        and stock.relative_strength >= 0.58
    ):
        return "启动前夜"
    if (
        stock.valuation_percentile <= 0.40
        and stock.drawdown_from_high <= -0.08
        and stock.momentum_60d < 0.10
        and stock.relative_strength <= 0.55
    ):
        return "未启动"
    if stock.drawdown_from_high <= -0.20 or not stock.price_above_ma20:
        return "左侧观察"
    return "中继观察"


@dataclass
class InvestmentCommittee:
    style_agent: StyleAgent
    fundamental_agent: FundamentalAgent
    technical_agent: TechnicalAgent
    risk_agent: RiskAgent
    event_agent: EventAgent
    max_per_sector: int = 1
    max_per_style: int = 2
    max_focus_themes: int = 3
    max_per_focus_theme: int = 3

    def _portfolio_construction(self, ranked: List[Recommendation], top_n: int) -> tuple[List[Recommendation], List[str]]:
        selected: List[Recommendation] = []
        sector_counts: Dict[str, int] = {}
        style_counts: Dict[str, int] = {}
        theme_counts: Dict[str, int] = {}
        skipped: List[str] = []
        decisions: List[dict] = []
        theme_scores: Dict[str, float] = {}
        for rec in ranked:
            theme_name = rec.stock.prefilter_theme or rec.stock.theme_name
            if not theme_name:
                continue
            theme_scores[theme_name] = max(theme_scores.get(theme_name, 0.0), rec.stock.prefilter_score or rec.stock.theme_strength)
        focus_themes = [
            theme_name
            for theme_name, _ in sorted(theme_scores.items(), key=lambda item: item[1], reverse=True)[: self.max_focus_themes]
        ]
        focus_ranked = [rec for rec in ranked if (rec.stock.prefilter_theme or rec.stock.theme_name) in focus_themes]
        focus_shortlist: List[Recommendation] = []
        focus_counts: Dict[str, int] = {}
        for rec in focus_ranked:
            theme_name = rec.stock.prefilter_theme or rec.stock.theme_name
            if not theme_name:
                continue
            if focus_counts.get(theme_name, 0) >= self.max_per_focus_theme:
                continue
            focus_shortlist.append(rec)
            focus_counts[theme_name] = focus_counts.get(theme_name, 0) + 1
        shortlist_ids = {id(rec) for rec in focus_shortlist}
        themed_primary = [
            rec
            for rec in focus_shortlist
            if rec.stock.theme_bucket in {"core", "expanded"} and rec.stock.fundamental_source != "local_profile"
        ]
        themed_secondary = [
            rec
            for rec in focus_shortlist
            if rec.stock.theme_bucket in {"core", "expanded"} and rec.stock.fundamental_source == "local_profile"
        ]
        bypass = [rec for rec in ranked if rec.stock.theme_bucket == "bypass" and id(rec) not in shortlist_ids]
        fallback = [rec for rec in ranked if rec.stock.theme_bucket == "fallback" and id(rec) not in shortlist_ids]
        theme_tail = [
            rec
            for rec in ranked
            if id(rec) not in shortlist_ids and rec.stock.theme_bucket in {"core", "expanded"}
        ]

        for rec in themed_primary + themed_secondary:
            sector = rec.stock.sector
            dominant_style = rec.stock.style_tags[0] if rec.stock.style_tags else "未知"
            theme_name = rec.stock.prefilter_theme or rec.stock.theme_name or "无主题"
            if sector_counts.get(sector, 0) >= self.max_per_sector:
                skipped.append(f"{rec.stock.name} 因行业集中度限制被跳过。")
                decisions.append(self._decision(rec, "committee_primary", "skipped", "sector_limit", "行业集中度限制"))
                continue
            if style_counts.get(dominant_style, 0) >= self.max_per_style:
                skipped.append(f"{rec.stock.name} 因风格暴露限制被跳过。")
                decisions.append(self._decision(rec, "committee_primary", "skipped", "style_limit", "风格暴露限制"))
                continue
            if theme_name != "无主题" and theme_counts.get(theme_name, 0) >= 2:
                skipped.append(f"{rec.stock.name} 因主题集中度限制被跳过。")
                decisions.append(self._decision(rec, "committee_primary", "skipped", "theme_limit", "主题集中度限制"))
                continue

            selected.append(rec)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            style_counts[dominant_style] = style_counts.get(dominant_style, 0) + 1
            if theme_name != "无主题":
                theme_counts[theme_name] = theme_counts.get(theme_name, 0) + 1
            decisions.append(self._decision(rec, "committee_primary", "selected", "theme_priority", "主题优先池入选"))
            if len(selected) >= top_n:
                break

        if len(selected) < top_n:
            for rec in theme_tail + bypass + fallback:
                if rec in selected:
                    continue
                theme_name = rec.stock.prefilter_theme or rec.stock.theme_name or ""
                if theme_name and theme_counts.get(theme_name, 0) >= 2:
                    decisions.append(self._decision(rec, "committee_secondary", "skipped", "theme_limit", "主题集中度限制"))
                    continue
                if rec.stock.theme_bucket == "bypass":
                    skipped.append(f"{rec.stock.name} 因主题白名单不足作为旁路候补补入。")
                    reason_code = "bypass_fill"
                    reason_text = "旁路候补补入"
                elif rec.stock.theme_bucket in {"core", "expanded"}:
                    skipped.append(f"{rec.stock.name} 作为焦点主题补位补入。")
                    reason_code = "focus_theme_fill"
                    reason_text = "焦点主题补位"
                else:
                    skipped.append(f"{rec.stock.name} 因主题白名单覆盖不足回退到常规候选。")
                    reason_code = "fallback_fill"
                    reason_text = "回退常规候选补位"
                selected.append(rec)
                if theme_name:
                    theme_counts[theme_name] = theme_counts.get(theme_name, 0) + 1
                decisions.append(self._decision(rec, "committee_secondary", "selected", reason_code, reason_text))
                if len(selected) >= top_n:
                    break

        if len(selected) < top_n:
            for rec in ranked:
                if rec in selected:
                    continue
                theme_name = rec.stock.prefilter_theme or rec.stock.theme_name or ""
                if theme_name and theme_counts.get(theme_name, 0) >= 2:
                    decisions.append(self._decision(rec, "committee_relaxed", "skipped", "theme_limit", "主题集中度限制"))
                    continue
                selected.append(rec)
                skipped.append(f"{rec.stock.name} 在放宽约束后补入组合。")
                if theme_name:
                    theme_counts[theme_name] = theme_counts.get(theme_name, 0) + 1
                decisions.append(self._decision(rec, "committee_relaxed", "selected", "relaxed_fill", "放宽约束补入"))
                if len(selected) >= top_n:
                    break

        notes = [
            f"主题优先池: 前 {self.max_focus_themes} 个主题，每个主题最多保留 {self.max_per_focus_theme} 只进入候选短名单。",
            f"行业集中度上限: 每个行业最多 {self.max_per_sector} 只。",
            f"风格暴露上限: 每个主风格最多 {self.max_per_style} 只。",
            "主题集中度上限: 最终推荐中每个主题最多 2 只。",
        ]
        notes.extend(skipped[:5])
        return selected, notes, decisions

    def _decision(self, rec: Recommendation, decision_stage: str, decision: str, reason_code: str, reason_text: str) -> dict:
        return {
            "ticker": rec.stock.ticker,
            "name": rec.stock.name,
            "decision_stage": decision_stage,
            "decision": decision,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "theme_name": rec.stock.prefilter_theme or rec.stock.theme_name,
            "theme_bucket": rec.stock.prefilter_bucket or rec.stock.theme_bucket,
            "total_score": rec.total_score,
            "stage": rec.stage,
        }

    def run(self, universe: List[StockIdea], top_n: int = 5, data_source: str = "sample") -> RunResult:
        style_view = self.style_agent.analyze(universe)
        recommendations: List[Recommendation] = []

        for stock in universe:
            fundamental = self.fundamental_agent.score(stock, style_view)
            technical = self.technical_agent.score(stock, style_view)
            risk = self.risk_agent.score(stock, style_view)
            event = self.event_agent.score(stock, style_view)

            total_score = (
                fundamental.score * 0.34
                + technical.score * 0.28
                + risk.score * 0.20
                + event.score * 0.18
            )

            stage = classify_stage(stock)
            reasons = [
                (
                    f"{stock.theme_name}主题({stock.theme_bucket})，{stock.sector}方向，处于{stage}阶段。"
                    if stock.theme_name
                    else f"{stock.sector}方向，处于{stage}阶段。"
                ),
                fundamental.reason,
                technical.reason,
                event.reason,
            ]
            risks = []
            if stock.crowding >= 0.65:
                risks.append("资金拥挤度偏高，注意追高风险。")
            if stock.volatility >= 0.28:
                risks.append("波动率较高，仓位控制应更严格。")
            if stock.valuation_percentile >= 0.60:
                risks.append("估值不低，更多依赖后续催化兑现。")
            if not risks:
                risks.append("短期未见明显硬伤，但仍需结合盘面确认。")

            recommendations.append(
                Recommendation(
                    stock=stock,
                    total_score=round(total_score, 4),
                    stage=stage,
                    agent_scores={
                        fundamental.agent_name: fundamental,
                        technical.agent_name: technical,
                        risk.agent_name: risk,
                        event.agent_name: event,
                    },
                    reasons=reasons,
                    risks=risks,
                )
            )

        ranked = sorted(recommendations, key=lambda item: item.total_score, reverse=True)
        selected, notes, decisions = self._portfolio_construction(ranked, top_n)
        return RunResult(
            style_view=style_view,
            recommendations=selected,
            universe_size=len(universe),
            data_source=data_source,
            portfolio_notes=notes,
            candidate_decisions=decisions,
        )


def build_committee(max_per_sector: int = 1, max_per_style: int = 2) -> InvestmentCommittee:
    return InvestmentCommittee(
        style_agent=StyleAgent(),
        fundamental_agent=FundamentalAgent(),
        technical_agent=TechnicalAgent(),
        risk_agent=RiskAgent(),
        event_agent=EventAgent(),
        max_per_sector=max_per_sector,
        max_per_style=max_per_style,
    )
