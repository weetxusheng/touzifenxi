from __future__ import annotations

import json
from datetime import date, timedelta
from statistics import median

from .committee import classify_stage
from .models import StockIdea, WeeklyPoolResult, WeeklyThemeScore
from .theme_router import assign_theme_candidates, collect_theme_signals, load_theme_definitions


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _week_label(trade_date: date) -> str:
    monday = trade_date - timedelta(days=trade_date.weekday())
    friday = monday + timedelta(days=4)
    return f"{monday.isoformat()}_{friday.isoformat()}"


def _build_stock_for_stage(row: dict, factor_row: dict | None) -> StockIdea | None:
    if not factor_row:
        return None
    factor_data = dict(factor_row)
    style_tags = str(factor_data.get("style_tags", "")).split(",") if factor_data.get("style_tags") else []
    return StockIdea(
        ticker=str(row["ticker"]),
        name=str(row["name"]),
        sector=str(factor_data.get("sector", row.get("sector", "综合"))),
        style_tags=[tag for tag in style_tags if tag],
        valuation_percentile=float(factor_data.get("valuation_percentile", 0.5)),
        earnings_growth=float(factor_data.get("earnings_growth", 0.1)),
        revenue_growth=float(factor_data.get("revenue_growth", 0.1)),
        roe=float(factor_data.get("roe", 0.12)),
        free_cashflow_margin=float(factor_data.get("free_cashflow_margin", 0.08)),
        momentum_20d=float(factor_data.get("momentum_20d", 0.0)),
        momentum_60d=float(factor_data.get("momentum_60d", 0.0)),
        relative_strength=float(factor_data.get("relative_strength", 0.5)),
        volume_trend=float(factor_data.get("volume_trend", 0.5)),
        drawdown_from_high=float(factor_data.get("drawdown_from_high", 0.0)),
        volatility=float(factor_data.get("volatility", 0.5)),
        crowding=float(factor_data.get("crowding", 0.5)),
        event_score=float(factor_data.get("event_score", 0.3)),
        last_price=float(factor_data.get("last_price", row.get("latest_price", 0.0))),
        ma20_gap=float(factor_data.get("ma20_gap", 0.0)),
        ma60_gap=float(factor_data.get("ma60_gap", 0.0)),
        price_above_ma20=bool(factor_data.get("price_above_ma20", 0)),
        price_above_ma60=bool(factor_data.get("price_above_ma60", 0)),
        ma20_slope=float(factor_data.get("ma20_slope", 0.0)),
        turnover_trend=float(factor_data.get("turnover_trend", 0.5)),
        data_source=str(factor_data.get("data_source", "weekly_pool")),
        fundamental_source=str(factor_data.get("fundamental_source", row.get("fundamental_source", "local_profile"))),
        industry_ready=bool(row.get("industry_ready", False)),
        financial_ready=bool(row.get("financial_ready", False)),
        factor_ready=bool(row.get("factor_ready", False)),
        ready_pool=bool(row.get("ready_pool", False)),
    )


def build_weekly_pool(
    store,
    config_path,
    trade_date: date,
    direct_connection: bool = True,
    max_pool_size: int = 50,
    theme_target_count: int = 6,
    wildcard_limit: int = 10,
) -> WeeklyPoolResult:
    candidate_rows = [dict(row) for row in store.list_universe_candidates(store.default_universe_filter(limit=800))]
    if not candidate_rows:
        raise RuntimeError("no eligible candidates for weekly prefilter")

    theme_defs = load_theme_definitions(config_path)
    recent_theme_strength = store.get_recent_theme_strength(days=7)
    theme_strength = {theme.name: float(recent_theme_strength.get(theme.name, 0.6)) for theme in theme_defs}
    all_assigned_rows = assign_theme_candidates(
        candidate_rows=candidate_rows,
        theme_defs=theme_defs,
        active_themes=[theme.name for theme in theme_defs],
        theme_strength=theme_strength,
        matched_notice_tickers={},
        extra_rows=None,
    )
    factor_map = store.get_latest_factor_map([str(row["ticker"]) for row in candidate_rows])
    theme_history = store.get_theme_history_metrics()

    theme_member_map: dict[str, list[dict]] = {theme.name: [] for theme in theme_defs}
    for row in all_assigned_rows:
        theme_name = str(row.get("theme_name", ""))
        if theme_name in theme_member_map:
            theme_member_map[theme_name].append(row)

    theme_scores: list[WeeklyThemeScore] = []
    for theme in theme_defs:
        members = theme_member_map.get(theme.name, [])
        if not members:
            theme_scores.append(
                WeeklyThemeScore(
                    theme_name=theme.name,
                    total_score=0.0,
                    policy_score=0.0,
                    valuation_score=0.0,
                    performance_score=0.0,
                    performance_source="empty",
                    selected=False,
                )
            )
            continue
        ready_ratio = sum(1 for row in members if int(row.get("ready_pool", 0)) == 1) / len(members)
        valuations = [float(row.get("valuation_percentile", 0.5)) for row in members]
        valuation_score = _clamp((1 - float(median(valuations))) * 0.7 + ready_ratio * 0.3)
        policy_score = _clamp(float(theme_strength.get(theme.name, 0.0)) / 2.5)

        history = theme_history.get(theme.name)
        if history and int(history["count"]) >= 3:
            avg_return = float(history["avg_return"] or 0.0)
            win_rate = float(history["win_rate"] or 0.0)
            performance_score = _clamp(win_rate * 0.7 + _clamp(0.5 + avg_return * 10) * 0.3)
            performance_source = "history"
            stage_ratio = None
            history_count = int(history["count"])
        else:
            stage_hits = 0
            for row in members:
                stock = _build_stock_for_stage(row, factor_map.get(str(row["ticker"])))
                if stock and classify_stage(stock) in {"启动前夜", "主升启动", "中继观察"}:
                    stage_hits += 1
            stage_ratio = (stage_hits / len(members)) if members else 0.0
            performance_score = _clamp(ready_ratio * 0.6 + stage_ratio * 0.4)
            performance_source = "proxy"
            avg_return = None
            win_rate = None
            history_count = int(history["count"]) if history else 0

        total_score = round(policy_score * 0.40 + performance_score * 0.35 + valuation_score * 0.25, 4)
        detail_json = json.dumps(
            {
                "member_count": len(members),
                "ready_ratio": round(ready_ratio, 4),
                "valuation_median": round(float(median(valuations)), 4),
                "policy_recent_strength": round(float(theme_strength.get(theme.name, 0.0)), 4),
                "history_count": history_count,
                "history_avg_return": round(avg_return, 4) if avg_return is not None else None,
                "history_win_rate": round(win_rate, 4) if win_rate is not None else None,
                "stage_ratio": round(stage_ratio, 4) if stage_ratio is not None else None,
                "policy_formula": "clamp(recent_theme_strength / 2.5)",
                "valuation_formula": "clamp((1 - valuation_median) * 0.7 + ready_ratio * 0.3)",
                "performance_formula": (
                    "clamp(win_rate * 0.7 + clamp(0.5 + avg_return * 10) * 0.3)"
                    if performance_source == "history"
                    else "clamp(ready_ratio * 0.6 + stage_ratio * 0.4)"
                ),
                "total_formula": "policy * 0.40 + performance * 0.35 + valuation * 0.25",
            },
            ensure_ascii=False,
        )
        theme_scores.append(
            WeeklyThemeScore(
                theme_name=theme.name,
                total_score=total_score,
                policy_score=round(policy_score, 4),
                valuation_score=round(valuation_score, 4),
                performance_score=round(performance_score, 4),
                performance_source=performance_source,
                detail_json=detail_json,
                selected=False,
            )
        )

    theme_scores.sort(key=lambda item: item.total_score, reverse=True)
    selected_theme_names = {item.theme_name for item in theme_scores[: min(theme_target_count, len(theme_scores))]}
    theme_scores = [
        WeeklyThemeScore(
            theme_name=item.theme_name,
            total_score=item.total_score,
            policy_score=item.policy_score,
            valuation_score=item.valuation_score,
            performance_score=item.performance_score,
            performance_source=item.performance_source,
            detail_json=item.detail_json,
            selected=item.theme_name in selected_theme_names,
        )
        for item in theme_scores
    ]

    pool_members: list[dict] = []
    selected_tickers: set[str] = set()
    per_theme_limit = min(8, max(6, max_pool_size // max(1, len(selected_theme_names))))
    for theme_name in [item.theme_name for item in theme_scores if item.selected]:
        members = [row for row in theme_member_map.get(theme_name, []) if str(row["ticker"]) not in selected_tickers]
        members.sort(
            key=lambda row: (
                {"core": 0, "expanded": 1}.get(str(row.get("theme_bucket", "")), 2),
                -int(row.get("ready_pool", 0)),
                -int(row.get("financial_ready", 0)),
                float(row.get("valuation_percentile", 0.5)),
                -float(row.get("amount", 0.0)),
            )
        )
        score_row = next(item for item in theme_scores if item.theme_name == theme_name)
        for rank_no, row in enumerate(members[:per_theme_limit], start=1):
            row["prefilter_week"] = _week_label(trade_date)
            row["prefilter_theme"] = theme_name
            row["prefilter_bucket"] = "theme"
            row["prefilter_score"] = score_row.total_score
            row["policy_score"] = score_row.policy_score
            row["valuation_score"] = score_row.valuation_score
            row["performance_score"] = score_row.performance_score
            row["prefilter_source"] = str(row.get("theme_bucket", "expanded"))
            row["prefilter_rank"] = rank_no
            pool_members.append(row)
            selected_tickers.add(str(row["ticker"]))

    wildcard_count = 0
    wildcard_candidates = []
    for row in candidate_rows:
        ticker = str(row["ticker"])
        if ticker in selected_tickers:
            continue
        if int(row.get("ready_pool", 0)) != 1:
            continue
        if str(row.get("fundamental_source", "")) == "local_profile":
            continue
        stock = _build_stock_for_stage(row, factor_map.get(ticker))
        if not stock:
            continue
        if classify_stage(stock) not in {"启动前夜", "主升启动", "中继观察"}:
            continue
        wildcard_candidates.append(row)
    wildcard_candidates.sort(
        key=lambda row: (
            -int(row.get("ready_pool", 0)),
            float(row.get("valuation_percentile", 0.5)),
            -float(row.get("amount", 0.0)),
        )
    )
    for row in wildcard_candidates:
        if len(pool_members) >= max_pool_size or wildcard_count >= wildcard_limit:
            break
        row["prefilter_week"] = _week_label(trade_date)
        row["prefilter_theme"] = ""
        row["prefilter_bucket"] = "wildcard"
        row["prefilter_score"] = 0.0
        row["policy_score"] = 0.0
        row["valuation_score"] = round(1 - float(row.get("valuation_percentile", 0.5)), 4)
        row["performance_score"] = 0.6
        row["prefilter_source"] = "wildcard_stage"
        row["prefilter_rank"] = wildcard_count + 1
        pool_members.append(row)
        wildcard_count += 1
        selected_tickers.add(str(row["ticker"]))

    pool_members = pool_members[:max_pool_size]
    return WeeklyPoolResult(
        prefilter_week=_week_label(trade_date),
        theme_scores=theme_scores,
        pool_members=pool_members,
        wildcard_count=wildcard_count,
        build_mode="weekly",
    )


def refresh_weekly_pool(
    store,
    config_path,
    trade_date: date,
    direct_connection: bool = True,
    refresh_limit: int = 5,
    candidate_limit: int = 200,
) -> WeeklyPoolResult:
    latest_summary = store.get_latest_weekly_pool_summary()
    if not latest_summary:
        raise RuntimeError("weekly pool missing; run build-weekly-pool first")

    current_rows = [dict(row) for row in store.get_latest_weekly_pool_rows(limit=50)]
    if not current_rows:
        raise RuntimeError("weekly pool missing; run build-weekly-pool first")

    selected_themes = [item["theme_name"] for item in latest_summary["themes"] if item["selected"]]
    if not selected_themes:
        raise RuntimeError("latest weekly pool has no selected themes")

    candidate_rows = [dict(row) for row in store.list_universe_candidates(store.default_universe_filter(limit=candidate_limit))]
    if not candidate_rows:
        raise RuntimeError("no eligible candidates for weekly pool refresh")

    theme_defs, theme_strength, _, matched_notice_tickers = collect_theme_signals(
        trade_date=trade_date,
        config_path=config_path,
        direct_connection=direct_connection,
    )
    active_themes = [theme_name for theme_name in selected_themes if float(theme_strength.get(theme_name, 0.0)) >= 1.0]
    if not active_themes:
        return WeeklyPoolResult(
            prefilter_week=str(latest_summary["prefilter_week"]),
            theme_scores=[
                WeeklyThemeScore(
                    theme_name=str(item["theme_name"]),
                    total_score=float(item["total_score"]),
                    policy_score=float(item["policy_score"]),
                    valuation_score=float(item["valuation_score"]),
                    performance_score=float(item["performance_score"]),
                    performance_source=str(item["performance_source"]),
                    detail_json=str(item.get("detail_json", "{}")),
                    selected=bool(item["selected"]),
                )
                for item in latest_summary["themes"]
            ],
            pool_members=current_rows,
            wildcard_count=sum(1 for row in current_rows if str(row.get("prefilter_bucket", "")) == "wildcard"),
            build_mode="daily_refresh",
            base_run_id=int(latest_summary["id"]),
            refresh_added=0,
            refresh_removed=0,
        )

    core_tickers: list[str] = []
    for definition in theme_defs:
        if definition.name in active_themes:
            core_tickers.extend(definition.core_tickers)
    extra_rows = [dict(row) for row in store.get_universe_rows_by_tickers(core_tickers)] if core_tickers else None
    themed_rows = assign_theme_candidates(
        candidate_rows=candidate_rows,
        theme_defs=theme_defs,
        active_themes=active_themes,
        theme_strength=theme_strength,
        matched_notice_tickers=matched_notice_tickers,
        extra_rows=extra_rows,
    )
    factor_map = store.get_latest_factor_map([str(row["ticker"]) for row in candidate_rows])
    existing_tickers = {str(row["ticker"]) for row in current_rows}
    theme_score_map = {str(item["theme_name"]): item for item in latest_summary["themes"]}

    additions: list[dict] = []
    themed_rows.sort(
        key=lambda row: (
            {"core": 0, "expanded": 1}.get(str(row.get("theme_bucket", "")), 2),
            -float(row.get("theme_strength", 0.0)),
            -int(row.get("ready_pool", 0)),
            -int(row.get("financial_ready", 0)),
            -int(row.get("factor_ready", 0)),
            float(row.get("valuation_percentile", 0.5)),
            -float(row.get("amount", 0.0)),
        )
    )
    for row in themed_rows:
        ticker = str(row["ticker"])
        if ticker in existing_tickers:
            continue
        if str(row.get("theme_bucket", "")) not in {"core", "expanded"}:
            continue
        if int(row.get("ready_pool", 0)) != 1:
            continue
        if int(row.get("factor_ready", 0)) != 1:
            continue
        if str(row.get("fundamental_source", "")) == "local_profile":
            continue
        stock = _build_stock_for_stage(row, factor_map.get(ticker))
        if not stock:
            continue
        if classify_stage(stock) not in {"启动前夜", "主升启动", "中继观察"}:
            continue
        theme_name = str(row.get("theme_name", ""))
        score_item = theme_score_map.get(theme_name)
        row["prefilter_week"] = str(latest_summary["prefilter_week"])
        row["prefilter_theme"] = theme_name
        row["prefilter_bucket"] = "theme"
        row["prefilter_score"] = float(score_item["total_score"]) if score_item else round(float(row.get("theme_strength", 0.0)), 4)
        row["policy_score"] = float(score_item["policy_score"]) if score_item else round(_clamp(float(row.get("theme_strength", 0.0)) / 2.5), 4)
        row["valuation_score"] = float(score_item["valuation_score"]) if score_item else round(1 - float(row.get("valuation_percentile", 0.5)), 4)
        row["performance_score"] = float(score_item["performance_score"]) if score_item else 0.6
        row["prefilter_source"] = f"daily_refresh_{row.get('theme_bucket', 'expanded')}"
        additions.append(row)
        if len(additions) >= refresh_limit:
            break

    if not additions:
        return WeeklyPoolResult(
            prefilter_week=str(latest_summary["prefilter_week"]),
            theme_scores=[
                WeeklyThemeScore(
                    theme_name=str(item["theme_name"]),
                    total_score=float(item["total_score"]),
                    policy_score=float(item["policy_score"]),
                    valuation_score=float(item["valuation_score"]),
                    performance_score=float(item["performance_score"]),
                    performance_source=str(item["performance_source"]),
                    detail_json=str(item.get("detail_json", "{}")),
                    selected=bool(item["selected"]),
                )
                for item in latest_summary["themes"]
            ],
            pool_members=current_rows,
            wildcard_count=sum(1 for row in current_rows if str(row.get("prefilter_bucket", "")) == "wildcard"),
            build_mode="daily_refresh",
            base_run_id=int(latest_summary["id"]),
            refresh_added=0,
            refresh_removed=0,
        )

    removable_rows = sorted(
        current_rows,
        key=lambda row: (
            {"wildcard": 0, "theme": 1}.get(str(row.get("prefilter_bucket", "")), 2),
            {"wildcard_stage": 0, "expanded": 1, "daily_refresh_expanded": 1, "core": 2, "daily_refresh_core": 2}.get(
                str(row.get("prefilter_source", "")),
                3,
            ),
            int(row.get("ready_pool", 0)),
            float(row.get("prefilter_score", 0.0)),
            float(row.get("amount", 0.0)),
        ),
    )
    removed_tickers = {str(row["ticker"]) for row in removable_rows[: len(additions)]}
    kept_rows = [dict(row) for row in current_rows if str(row["ticker"]) not in removed_tickers]
    final_rows = kept_rows + additions
    final_rows.sort(
        key=lambda row: (
            {"theme": 0, "wildcard": 1}.get(str(row.get("prefilter_bucket", "")), 2),
            {"core": 0, "daily_refresh_core": 0, "expanded": 1, "daily_refresh_expanded": 1}.get(str(row.get("prefilter_source", "")), 2),
            -int(row.get("ready_pool", 0)),
            -float(row.get("prefilter_score", 0.0)),
            -float(row.get("amount", 0.0)),
        )
    )
    for index, row in enumerate(final_rows[:50], start=1):
        row["prefilter_rank"] = index

    return WeeklyPoolResult(
        prefilter_week=str(latest_summary["prefilter_week"]),
        theme_scores=[
            WeeklyThemeScore(
                theme_name=str(item["theme_name"]),
                total_score=float(item["total_score"]),
                policy_score=float(item["policy_score"]),
                valuation_score=float(item["valuation_score"]),
                performance_score=float(item["performance_score"]),
                performance_source=str(item["performance_source"]),
                detail_json=str(item.get("detail_json", "{}")),
                selected=bool(item["selected"]),
            )
            for item in latest_summary["themes"]
        ],
        pool_members=final_rows[:50],
        wildcard_count=sum(1 for row in final_rows[:50] if str(row.get("prefilter_bucket", "")) == "wildcard"),
        build_mode="daily_refresh",
        base_run_id=int(latest_summary["id"]),
        refresh_added=len(additions),
        refresh_removed=len(removed_tickers),
    )
