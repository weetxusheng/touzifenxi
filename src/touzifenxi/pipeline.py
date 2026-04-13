from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .committee import build_committee
from .market_data import (
    build_research_universe_from_rows,
    load_akshare_watchlist,
    load_market_universe,
    load_snapshot_universe_from_rows,
)
from .models import RunResult, ThemeRouteResult, UniverseFilter, WeeklyPoolResult
from .report import render_report, write_report
from .settings import AppPaths
from .storage import ResearchStore
from .theme_router import build_theme_route, load_theme_definitions
from .weekly_prefilter import build_weekly_pool, refresh_weekly_pool


@dataclass
class PipelineConfig:
    data_source: str
    top_n: int
    network_mode: str
    max_per_sector: int
    max_per_style: int
    write_report_file: bool
    use_synced_universe: bool = False
    candidate_limit: int = 300
    candidate_offset: int = 0
    only_missing_factors: bool = False
    use_theme_router: bool = True
    use_weekly_pool: bool = True


class DailyResearchPipeline:
    def __init__(self, paths: AppPaths, store: ResearchStore):
        self.paths = paths
        self.store = store

    def run(self, config: PipelineConfig) -> tuple[RunResult, Path | None, int]:
        theme_route_result = ThemeRouteResult(router_mode="fallback")
        if self._should_use_synced_universe(config):
            data_source, universe, ready_pool_size, fallback_pool_size, theme_route_result = self._load_synced_universe(config)
        else:
            data_source, universe = load_market_universe(
                data_source=config.data_source,
                sample_universe_path=self.paths.sample_universe_path,
                watchlist_path=self.paths.watchlist_path,
                direct_connection=config.network_mode == "direct",
            )
            ready_pool_size = 0
            fallback_pool_size = len(universe)
        committee = build_committee(
            max_per_sector=config.max_per_sector,
            max_per_style=config.max_per_style,
        )
        result = committee.run(universe=universe, top_n=config.top_n, data_source=data_source)
        result.ready_pool_size = ready_pool_size
        result.fallback_pool_size = fallback_pool_size
        result.coverage_ratio = (ready_pool_size / len(universe)) if universe else 0.0
        result.router_mode = theme_route_result.router_mode
        result.active_themes = theme_route_result.active_themes
        result.bypass_count = sum(1 for rec in result.recommendations if rec.stock.theme_bucket == "bypass")
        if result.router_mode == "theme":
            route_note = f"主题路由模式: theme，激活主题 {len(result.active_themes)} 个。"
        elif result.router_mode == "weekly_pool":
            route_note = f"研究入口: 周度预筛池，入选主题 {len(result.active_themes)} 个。"
        else:
            route_note = "主题路由未命中，回退到常规候选池。"
        result.portfolio_notes.insert(0, route_note)
        if result.bypass_count:
            result.portfolio_notes.insert(1, f"本轮旁路候选入选 {result.bypass_count} 只。")
        report_path = None
        if config.write_report_file:
            report_path = write_report(self.paths.reports_dir, render_report(result))
        run_id = self.store.save_run(result=result, report_path=report_path)
        self.store.save_theme_router_data(run_id=run_id, universe=universe, events=theme_route_result.events)
        self.store.save_market_snapshots(run_id=run_id, universe=universe, data_source=data_source)
        self.store.save_daily_factors(
            snapshot_date=datetime.now().date().isoformat(),
            universe=universe,
            data_source=data_source,
        )
        return result, report_path, run_id

    def sync_factors(self, config: PipelineConfig) -> tuple[str, int]:
        try:
            data_source, universe, _, _, _ = self._load_synced_universe(
                config,
                only_missing_factors=config.only_missing_factors,
            )
        except RuntimeError as exc:
            if "missing daily factors" in str(exc):
                return "akshare", 0
            raise
        count = self.store.save_daily_factors(
            snapshot_date=datetime.now().date().isoformat(),
            universe=universe,
            data_source=data_source,
        )
        return data_source, count

    def build_weekly_pool(self, config: PipelineConfig) -> tuple[int, WeeklyPoolResult]:
        result = build_weekly_pool(
            store=self.store,
            config_path=self.paths.theme_config_path,
            trade_date=datetime.now().date(),
            direct_connection=config.network_mode == "direct",
            max_pool_size=50,
            theme_target_count=6,
            wildcard_limit=10,
        )
        run_id = self.store.save_weekly_prefilter(result)
        return run_id, result

    def refresh_weekly_pool(self, config: PipelineConfig, refresh_limit: int = 5, candidate_limit: int = 200) -> tuple[int, WeeklyPoolResult]:
        result = refresh_weekly_pool(
            store=self.store,
            config_path=self.paths.theme_config_path,
            trade_date=datetime.now().date(),
            direct_connection=config.network_mode == "direct",
            refresh_limit=refresh_limit,
            candidate_limit=candidate_limit,
        )
        run_id = self.store.save_weekly_prefilter(result)
        return run_id, result

    def _should_use_synced_universe(self, config: PipelineConfig) -> bool:
        if config.data_source not in {"akshare", "auto"}:
            return False
        if config.use_synced_universe:
            return True
        candidates = self.store.list_universe_candidates(UniverseFilter(limit=1))
        return bool(candidates)

    def _load_synced_universe(self, config: PipelineConfig, only_missing_factors: bool = False):
        if not only_missing_factors and config.use_weekly_pool:
            candidate_rows = self.store.get_latest_weekly_pool_rows(limit=50)
            if not candidate_rows:
                raise RuntimeError("weekly pool missing; run build-weekly-pool first")
            normalized_rows = [dict(row) for row in candidate_rows]
            normalized_rows = normalized_rows[: min(config.candidate_limit, 50)]
            active_themes = []
            seen_themes = set()
            for row in normalized_rows:
                theme_name = str(row.get("prefilter_theme", "")).strip()
                if theme_name and theme_name not in seen_themes:
                    seen_themes.add(theme_name)
                    active_themes.append(theme_name)
            theme_route_result = ThemeRouteResult(
                router_mode="weekly_pool",
                candidate_rows=normalized_rows,
                active_themes=active_themes,
                bypass_count=sum(1 for row in normalized_rows if str(row.get("prefilter_bucket", "")) == "wildcard"),
            )
            ready_rows = [row for row in normalized_rows if bool(row.get("ready_pool", False))]
            fallback_rows = [row for row in normalized_rows if not bool(row.get("ready_pool", False))]
            ordered_rows = ready_rows + fallback_rows
            routed_tickers = [str(row["ticker"]) for row in ordered_rows]
            factor_map = self.store.get_latest_factor_map(routed_tickers)
            data_source, universe = load_snapshot_universe_from_rows(ordered_rows, factor_map)
            loaded_tickers = {stock.ticker for stock in universe}
            loaded_ready_rows = [row for row in ready_rows if str(row["ticker"]) in loaded_tickers]
            loaded_fallback_rows = [row for row in fallback_rows if str(row["ticker"]) in loaded_tickers]
            return data_source, universe, len(loaded_ready_rows), len(loaded_fallback_rows), theme_route_result

        router_limit = max(config.candidate_limit * 4, config.candidate_limit, 80)
        universe_filter = UniverseFilter(limit=router_limit)
        universe_filter.offset = config.candidate_offset
        if only_missing_factors:
            snapshot_date = datetime.now().date().isoformat()
            candidate_rows = self.store.list_candidates_missing_factors(snapshot_date, universe_filter)
        else:
            candidate_rows = self.store.list_universe_candidates(universe_filter)
        if not candidate_rows:
            if only_missing_factors:
                raise RuntimeError("no eligible candidates are missing daily factors")
            raise RuntimeError("universe_stocks has no eligible candidates; run sync-universe first")
        normalized_rows = [dict(row) for row in candidate_rows]
        theme_route_result = ThemeRouteResult(router_mode="fallback", candidate_rows=normalized_rows[: config.candidate_limit])
        if not only_missing_factors and config.use_theme_router:
            core_tickers: list[str] = []
            if self.paths.theme_config_path.exists():
                for definition in load_theme_definitions(self.paths.theme_config_path):
                    core_tickers.extend(definition.core_tickers)
            extra_rows = [dict(row) for row in self.store.get_universe_rows_by_tickers(core_tickers)]
            theme_route_result = build_theme_route(
                trade_date=datetime.now().date(),
                candidate_rows=normalized_rows,
                config_path=self.paths.theme_config_path,
                top_n=max(config.top_n, 1),
                direct_connection=config.network_mode == "direct",
                candidate_limit=config.candidate_limit,
                extra_rows=extra_rows,
            )
            normalized_rows = theme_route_result.candidate_rows
        else:
            normalized_rows = normalized_rows[: config.candidate_limit]
        ready_rows = [row for row in normalized_rows if bool(row.get("ready_pool", False))]
        fallback_rows = [row for row in normalized_rows if not bool(row.get("ready_pool", False))]
        ordered_rows = ready_rows + fallback_rows
        routed_tickers = [str(row["ticker"]) for row in ordered_rows]
        industry_map = self.store.get_stock_industry_map(routed_tickers)
        financial_map = self.store.get_financial_profile_map(routed_tickers)
        watchlist_payload = build_research_universe_from_rows(
            ordered_rows,
            industry_map=industry_map,
            financial_map=financial_map,
        )
        self._persist_candidate_watchlist(watchlist_payload)
        data_source, universe = load_akshare_watchlist(
            watchlist=watchlist_payload,
            direct_connection=config.network_mode == "direct",
        )
        return data_source, universe, len(ready_rows), len(fallback_rows), theme_route_result

    def _persist_candidate_watchlist(self, watchlist_payload: list[dict]) -> None:
        import json

        self.paths.processed_dir.mkdir(parents=True, exist_ok=True)
        temp_watchlist = self.paths.processed_dir / "candidate_watchlist.json"
        temp_watchlist.write_text(json.dumps(watchlist_payload, ensure_ascii=False), encoding="utf-8")
