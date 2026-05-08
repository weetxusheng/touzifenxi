from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from touzifenxi.committee import build_committee
from touzifenxi.dashboard import _render_dashboard, _render_stock_evidence_page
from touzifenxi.data_quality import expected_factor_date, run_data_quality_check
from touzifenxi.models import StockIdea
from touzifenxi.pipeline import DailyResearchPipeline
from touzifenxi.storage import ResearchStore


def _store(tmp_path: Path) -> ResearchStore:
    store = ResearchStore(tmp_path / "test.db")
    store.init_db()
    return store


def _insert_universe(conn, ticker: str, name: str = "测试股") -> None:
    code, exchange = ticker.split(".")
    conn.execute(
        """
        INSERT OR REPLACE INTO universe_stocks (
            ticker, code, name, exchange, board, latest_price, change_percent,
            turnover_ratio, amount, is_st, is_suspended, source, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            code,
            name,
            exchange,
            "主板",
            10.0,
            0.0,
            1.0,
            200_000_000,
            0,
            0,
            "test_source",
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _insert_daily_factor(conn, ticker: str, snapshot_date: str, valuation: float = 0.4) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_factors (
            snapshot_date, ticker, name, sector, style_tags, data_source, fundamental_source,
            last_price, valuation_percentile, earnings_growth, revenue_growth, roe,
            free_cashflow_margin, momentum_20d, momentum_60d, relative_strength,
            volume_trend, turnover_trend, drawdown_from_high, volatility, crowding,
            event_score, ma20_gap, ma60_gap, price_above_ma20, price_above_ma60,
            ma20_slope, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date,
            ticker,
            "测试股",
            "测试行业",
            "成长",
            "akshare_sina",
            "real_financial",
            10.0,
            valuation,
            0.2,
            0.2,
            0.12,
            0.08,
            0.12,
            0.18,
            0.7,
            0.3,
            0.2,
            -0.1,
            0.2,
            0.3,
            0.5,
            0.02,
            0.03,
            1,
            1,
            0.01,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _insert_financial(conn, ticker: str, source: str = "akshare_financial") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO financial_profiles (
            ticker, earnings_growth, revenue_growth, roe, free_cashflow_margin,
            event_score, valuation_percentile, fundamental_source, report_period, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            0.2,
            0.2,
            0.12,
            0.08,
            0.5,
            0.4,
            source,
            "2025Q4",
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _insert_industry(conn, ticker: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO stock_industries (
            ticker, industry_standard, industry_code, sector_lv1, sector_lv2,
            sector_lv3, sector_lv4, source, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            "cninfo",
            "T001",
            "制造",
            "设备",
            "测试行业",
            "",
            "akshare_cninfo",
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def test_data_quality_flags_weekly_pool_factor_coverage_red(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot_date = date.today().isoformat()
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO theme_prefilter_runs (
                prefilter_week, built_at, theme_count, pool_size, wildcard_count, build_mode,
                base_run_id, refresh_added, refresh_removed, rule_version_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-W16", datetime.now().isoformat(timespec="seconds"), 6, 50, 0, "weekly", None, 0, 0, "{}"),
        )
        run_id = int(cursor.lastrowid)
        for index in range(50):
            ticker = f"{600000 + index:06d}.SH"
            _insert_universe(conn, ticker, f"测试股{index}")
            _insert_financial(conn, ticker, "akshare_financial" if index < 30 else "local_profile")
            _insert_industry(conn, ticker)
            if index < 44:
                _insert_daily_factor(conn, ticker, snapshot_date)
            conn.execute(
                """
                INSERT INTO weekly_pool_members (
                    run_id, prefilter_week, ticker, prefilter_theme, prefilter_bucket,
                    prefilter_score, policy_score, valuation_score, performance_score,
                    prefilter_source, rank_no, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "2026-W16",
                    ticker,
                    "测试主题",
                    "theme",
                    0.8,
                    0.8,
                    0.7,
                    0.6,
                    "core",
                    index + 1,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        conn.commit()

    quality_run_id, result = run_data_quality_check(store, context="test")
    summary = store.get_latest_data_quality_summary()
    factor_item = next(item for item in summary["items"] if item["name"] == "周度池因子覆盖")
    financial_item = next(item for item in summary["items"] if item["name"] == "周度池真实财务")

    assert quality_run_id > 0
    assert result.summary["weekly_pool_factor_covered"] == 44
    assert factor_item["status"] == "red"
    assert financial_item["status"] == "yellow"


def test_stock_evidence_marks_proxy_valuation_and_weak_event(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ticker = "600001.SH"
    snapshot_date = date.today().isoformat()
    with store.connect() as conn:
        _insert_universe(conn, ticker)
        _insert_daily_factor(conn, ticker, snapshot_date, valuation=0.5)
        _insert_financial(conn, ticker, source="local_profile")
        conn.execute(
            """
            INSERT INTO research_runs (
                run_at, data_source, universe_size, dominant_style, style_confidence, report_path,
                ready_pool_size, fallback_pool_size, coverage_ratio, router_mode,
                active_theme_count, bypass_count, rule_version_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                "test",
                1,
                "balanced",
                0.5,
                None,
                1,
                0,
                1.0,
                "weekly_pool",
                1,
                0,
                "{}",
            ),
        )
        run_id = int(conn.execute("SELECT MAX(id) FROM research_runs").fetchone()[0])
        conn.execute(
            """
            INSERT INTO recommendations (
                run_id, rank_no, ticker, name, sector, stage, total_score, last_price,
                fundamental_source, industry_ready, financial_ready, factor_ready, ready_pool,
                theme_name, theme_bucket, theme_source, theme_strength, router_mode,
                prefilter_week, prefilter_theme, prefilter_bucket, prefilter_score,
                policy_score, valuation_score, performance_score, prefilter_source, reasons, risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                1,
                ticker,
                "测试股",
                "测试行业",
                "启动前夜",
                0.8,
                10.0,
                "local_profile",
                0,
                0,
                1,
                0,
                "测试主题",
                "core",
                "manual",
                0.8,
                "weekly_pool",
                "2026-W16",
                "测试主题",
                "theme",
                0.8,
                0.8,
                0.5,
                0.6,
                "core",
                "理由",
                "风险",
            ),
        )
        conn.execute(
            """
            INSERT INTO theme_events (
                run_id, event_date, theme_name, source_type, source_name, source_url,
                title, ticker, strength, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_date,
                "测试主题",
                "notice",
                "交易所公告",
                "",
                "测试公告标题",
                ticker,
                0.8,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()

    evidence = store.get_stock_evidence(ticker)

    assert evidence["status"]["financial"] == "proxy"
    assert evidence["status"]["valuation"] == "proxy"
    assert evidence["status"]["event"] == "weak_evidence"
    assert evidence["factor"]["data_source"] == "akshare_sina"


def _idea(ticker: str, source: str, factor_ready: bool = True, financial_ready: bool | None = None) -> StockIdea:
    if financial_ready is None:
        financial_ready = source != "local_profile"
    return StockIdea(
        ticker=ticker,
        name=f"股票{ticker[:6]}",
        sector="测试行业",
        style_tags=["成长"],
        valuation_percentile=0.35,
        earnings_growth=0.3,
        revenue_growth=0.25,
        roe=0.15,
        free_cashflow_margin=0.1,
        momentum_20d=0.2,
        momentum_60d=0.25,
        relative_strength=0.8,
        volume_trend=0.35,
        drawdown_from_high=-0.08,
        volatility=0.2,
        crowding=0.35,
        event_score=0.7,
        last_price=10.0,
        data_source="test",
        fundamental_source=source,
        industry_ready=True,
        financial_ready=financial_ready,
        factor_ready=factor_ready,
        ready_pool=source != "local_profile" and factor_ready and financial_ready,
        prefilter_theme="测试主题",
        prefilter_bucket="theme",
    )


def test_committee_blocks_proxy_financial_when_real_candidates_are_sufficient() -> None:
    universe = [_idea(f"{600010 + i:06d}.SH", "akshare_financial") for i in range(5)]
    universe.append(_idea("600099.SH", "local_profile"))
    universe.append(_idea("600100.SH", "akshare_financial", factor_ready=False))
    universe.append(_idea("600101.SH", "akshare_financial", financial_ready=False))

    result = build_committee(max_per_sector=10, max_per_style=10).run(universe, top_n=5, data_source="test")
    tickers = {rec.stock.ticker for rec in result.recommendations}
    reason_codes = {decision["reason_code"] for decision in result.candidate_decisions}
    decision_by_ticker = {decision["ticker"]: decision["reason_code"] for decision in result.candidate_decisions}

    assert "600099.SH" not in tickers
    assert "600100.SH" not in tickers
    assert "600101.SH" not in tickers
    assert "proxy_financial" in reason_codes
    assert "stale_factor" in reason_codes
    assert decision_by_ticker["600101.SH"] == "proxy_financial"


def test_dashboard_exposes_quality_tab_and_stock_evidence_page(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ticker = "600002.SH"
    snapshot_date = date.today().isoformat()
    with store.connect() as conn:
        _insert_universe(conn, ticker, "证据股")
        _insert_daily_factor(conn, ticker, snapshot_date, valuation=0.5)
        _insert_financial(conn, ticker, source="local_profile")
        conn.commit()
    run_data_quality_check(store, context="dashboard_test")

    snapshot = {
        "paths": {"db_path": str(tmp_path / "test.db"), "report_path": None},
        "latest_snapshot": snapshot_date,
        "master_count": 1,
        "master_rows": [{"ticker": ticker, "name": "证据股", "board": "主板", "amount": 200_000_000}],
        "coverage": {"total_candidates": 1, "ready_candidates": 0},
        "candidate_rows": [{"ticker": ticker, "name": "证据股", "board": "主板", "amount": 200_000_000}],
        "ready_rows": [],
        "performance": {"latest_run": None, "latest_weekly_pool": None, "recent_runs": []},
        "weekly_summary": None,
        "weekly_pool_rows": [],
        "latest_recommendations": [],
        "recent_events": [],
        "recent_pool_changes": [],
        "recent_candidate_decisions": [],
        "active_rule_versions": [],
        "stock_lifecycle": [],
        "theme_lifecycle": [],
        "db_status": store.get_database_status(),
        "data_quality": store.get_latest_data_quality_summary(),
        "recommendation_evidence": {},
    }

    dashboard_html = _render_dashboard(snapshot, active_tab="quality")
    foundation_html = _render_dashboard(snapshot, active_tab="foundation", foundation_view="master")
    evidence_html = _render_stock_evidence_page(store, ticker)

    assert "数据健康" in dashboard_html
    assert "/stock?ticker=" in foundation_html
    assert "证据状态" in evidence_html
    assert "打开东方财富" in evidence_html
    assert "代理" in evidence_html


def test_storage_normalizes_postgres_jsonb_dict_strings() -> None:
    raw = "{'member_count': 3, 'ready_ratio': 0.75}"

    normalized = ResearchStore._normalize_json_text(raw)
    parsed = ResearchStore._parse_json_detail(raw)

    assert '"member_count": 3' in normalized
    assert parsed["ready_ratio"] == 0.75


def test_pipeline_does_not_write_snapshot_cache_as_non_trading_today() -> None:
    class FakeStore:
        def get_latest_factor_snapshot_date(self) -> str:
            return date.today().isoformat()

    pipeline = DailyResearchPipeline.__new__(DailyResearchPipeline)
    pipeline.store = FakeStore()

    assert pipeline._factor_write_snapshot_date("snapshot_cache") == expected_factor_date()[0]


def test_sync_weekly_pool_factors_fetches_only_missing_weekly_members(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    target_date = expected_factor_date()[0]
    existing = "600201.SH"
    missing = "600202.SH"
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO theme_prefilter_runs (
                prefilter_week, built_at, theme_count, pool_size, wildcard_count, build_mode,
                base_run_id, refresh_added, refresh_removed, rule_version_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-W16", datetime.now().isoformat(timespec="seconds"), 1, 2, 0, "weekly", None, 0, 0, "{}"),
        )
        run_id = int(cursor.lastrowid)
        for rank, ticker in enumerate([existing, missing], start=1):
            _insert_universe(conn, ticker, f"池内股{rank}")
            _insert_financial(conn, ticker)
            _insert_industry(conn, ticker)
            conn.execute(
                """
                INSERT INTO weekly_pool_members (
                    run_id, prefilter_week, ticker, prefilter_theme, prefilter_bucket,
                    prefilter_score, policy_score, valuation_score, performance_score,
                    prefilter_source, rank_no, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "2026-W16",
                    ticker,
                    "测试主题",
                    "theme",
                    0.8,
                    0.8,
                    0.7,
                    0.6,
                    "core",
                    rank,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        _insert_daily_factor(conn, existing, target_date)
        conn.commit()

    fetched_watchlists: list[list[dict]] = []

    def fake_load_akshare_watchlist(watchlist, **kwargs):
        fetched_watchlists.append(watchlist)
        return "akshare_test", [
            _idea(
                f"{item['ticker']}.SH" if "." not in item["ticker"] else item["ticker"],
                "akshare_financial",
            )
            for item in watchlist
        ]

    monkeypatch.setattr("touzifenxi.pipeline.load_akshare_watchlist", fake_load_akshare_watchlist)
    pipeline = DailyResearchPipeline(paths=type("Paths", (), {"theme_config_path": tmp_path / "themes.json"})(), store=store)

    result = pipeline.sync_weekly_pool_factors(network_mode="direct", min_coverage=2)

    assert result["target_snapshot_date"] == target_date
    assert result["before_covered"] == 1
    assert result["after_covered"] == 2
    assert result["synced_count"] == 1
    assert [item["ticker"] for item in fetched_watchlists[0]] == ["600202"]


def test_next_day_review_records_price_date_and_note(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO research_runs (
                run_at, data_source, universe_size, dominant_style, style_confidence, report_path,
                ready_pool_size, fallback_pool_size, coverage_ratio, router_mode,
                active_theme_count, bypass_count, rule_version_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-04-18T20:00:00",
                "snapshot_cache",
                1,
                "价值",
                0.6,
                None,
                1,
                0,
                1.0,
                "weekly_pool",
                1,
                0,
                "{}",
            ),
        )
        run_id = int(conn.execute("SELECT MAX(id) FROM research_runs").fetchone()[0])
        conn.execute(
            """
            INSERT INTO recommendations (
                run_id, rank_no, ticker, name, sector, stage, total_score, last_price,
                fundamental_source, industry_ready, financial_ready, factor_ready, ready_pool,
                theme_name, theme_bucket, theme_source, theme_strength, router_mode,
                prefilter_week, prefilter_theme, prefilter_bucket, prefilter_score,
                policy_score, valuation_score, performance_score, prefilter_source, reasons, risks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                1,
                "600001.SH",
                "复盘股",
                "测试行业",
                "主升启动",
                0.8,
                10.0,
                "akshare_financial",
                1,
                1,
                1,
                1,
                "测试主题",
                "theme",
                "core",
                0.8,
                "weekly_pool",
                "2026-W16",
                "测试主题",
                "theme",
                0.8,
                0.8,
                0.7,
                0.6,
                "core",
                "理由",
                "风险",
            ),
        )
        recommendation_id = int(conn.execute("SELECT MAX(id) FROM recommendations").fetchone()[0])
        conn.execute(
            """
            INSERT OR REPLACE INTO recommendation_returns (recommendation_id, base_price)
            VALUES (?, ?)
            """,
            (recommendation_id, 10.0),
        )
        conn.commit()

    store.update_recommendation_returns(
        recommendation_id=recommendation_id,
        horizon_1d=0.05,
        horizon_5d=None,
        horizon_20d=None,
        horizon_60d=None,
        horizon_1d_date="2026-04-20",
        horizon_1d_price=10.5,
    )
    reviews = store.get_recent_next_day_reviews(limit=5)

    assert reviews[0]["ticker"] == "600001.SH"
    assert reviews[0]["horizon_1d_date"] == "2026-04-20"
    assert reviews[0]["horizon_1d_price"] == 10.5
    assert reviews[0]["horizon_1d"] == 0.05
    assert reviews[0]["review_note"] == "次日上涨"
