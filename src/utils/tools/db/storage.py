from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import connect_postgres, connect_sqlite, init_postgres_schema, resolve_database_profile, serialize_rule_snapshot
from ..models import Recommendation, RunResult, StockIdea, ThemeEvent, UniverseFilter
from ..market.universe import UniverseSnapshot

RULE_VERSION_SNAPSHOT = {
    "theme_prefilter": {"version": "v2.0", "notes": "weekly_50_pool + daily_refresh + theme_score_detail"},
    "committee": {"version": "v2.0", "notes": "top3_themes_shortlist + theme_cap_2"},
    "dashboard": {"version": "v1.2", "notes": "tabbed_dashboard + eastmoney_links + theme_score_drilldown"},
}


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS research_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at TEXT NOT NULL,
        data_source TEXT NOT NULL,
        universe_size INTEGER NOT NULL,
        dominant_style TEXT NOT NULL,
        style_confidence REAL NOT NULL,
        report_path TEXT,
        ready_pool_size INTEGER NOT NULL DEFAULT 0,
        fallback_pool_size INTEGER NOT NULL DEFAULT 0,
        coverage_ratio REAL NOT NULL DEFAULT 0,
        router_mode TEXT NOT NULL DEFAULT 'fallback',
        active_theme_count INTEGER NOT NULL DEFAULT 0,
        bypass_count INTEGER NOT NULL DEFAULT 0,
        rule_version_snapshot TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        rank_no INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        name TEXT NOT NULL,
        sector TEXT NOT NULL,
        stage TEXT NOT NULL,
        total_score REAL NOT NULL,
        last_price REAL NOT NULL,
        fundamental_source TEXT NOT NULL,
        industry_ready INTEGER NOT NULL DEFAULT 0,
        financial_ready INTEGER NOT NULL DEFAULT 0,
        factor_ready INTEGER NOT NULL DEFAULT 0,
        ready_pool INTEGER NOT NULL DEFAULT 0,
        theme_name TEXT NOT NULL DEFAULT '',
        theme_bucket TEXT NOT NULL DEFAULT 'fallback',
        theme_source TEXT NOT NULL DEFAULT '',
        theme_strength REAL NOT NULL DEFAULT 0,
        router_mode TEXT NOT NULL DEFAULT 'fallback',
        prefilter_week TEXT NOT NULL DEFAULT '',
        prefilter_theme TEXT NOT NULL DEFAULT '',
        prefilter_bucket TEXT NOT NULL DEFAULT '',
        prefilter_score REAL NOT NULL DEFAULT 0,
        policy_score REAL NOT NULL DEFAULT 0,
        valuation_score REAL NOT NULL DEFAULT 0,
        performance_score REAL NOT NULL DEFAULT 0,
        prefilter_source TEXT NOT NULL DEFAULT '',
        reasons TEXT NOT NULL,
        risks TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES research_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recommendation_id INTEGER NOT NULL,
        agent_name TEXT NOT NULL,
        score REAL NOT NULL,
        reason TEXT NOT NULL,
        FOREIGN KEY(recommendation_id) REFERENCES recommendations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS universe_stocks (
        ticker TEXT PRIMARY KEY,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        exchange TEXT NOT NULL,
        board TEXT NOT NULL,
        latest_price REAL NOT NULL,
        change_percent REAL NOT NULL,
        turnover_ratio REAL NOT NULL,
        amount REAL NOT NULL,
        is_st INTEGER NOT NULL,
        is_suspended INTEGER NOT NULL,
        source TEXT NOT NULL,
        synced_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        data_source TEXT NOT NULL,
        last_price REAL NOT NULL,
        momentum_20d REAL NOT NULL,
        momentum_60d REAL NOT NULL,
        relative_strength REAL NOT NULL,
        volume_trend REAL NOT NULL,
        turnover_trend REAL NOT NULL,
        drawdown_from_high REAL NOT NULL,
        volatility REAL NOT NULL,
        crowding REAL NOT NULL,
        valuation_percentile REAL NOT NULL,
        earnings_growth REAL NOT NULL,
        revenue_growth REAL NOT NULL,
        roe REAL NOT NULL,
        free_cashflow_margin REAL NOT NULL,
        FOREIGN KEY(run_id) REFERENCES research_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recommendation_returns (
        recommendation_id INTEGER PRIMARY KEY,
        base_price REAL NOT NULL,
        horizon_1d REAL,
        horizon_5d REAL,
        horizon_20d REAL,
        horizon_60d REAL,
        updated_at TEXT,
        FOREIGN KEY(recommendation_id) REFERENCES recommendations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS industry_dictionary (
        industry_name TEXT PRIMARY KEY,
        industry_code TEXT,
        source TEXT NOT NULL,
        synced_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_industries (
        ticker TEXT PRIMARY KEY,
        industry_standard TEXT NOT NULL,
        industry_code TEXT,
        sector_lv1 TEXT,
        sector_lv2 TEXT,
        sector_lv3 TEXT,
        sector_lv4 TEXT,
        source TEXT NOT NULL,
        synced_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_factors (
        snapshot_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        name TEXT NOT NULL,
        sector TEXT NOT NULL,
        style_tags TEXT NOT NULL,
        data_source TEXT NOT NULL,
        fundamental_source TEXT NOT NULL,
        last_price REAL NOT NULL,
        valuation_percentile REAL NOT NULL,
        earnings_growth REAL NOT NULL,
        revenue_growth REAL NOT NULL,
        roe REAL NOT NULL,
        free_cashflow_margin REAL NOT NULL,
        momentum_20d REAL NOT NULL,
        momentum_60d REAL NOT NULL,
        relative_strength REAL NOT NULL,
        volume_trend REAL NOT NULL,
        turnover_trend REAL NOT NULL,
        drawdown_from_high REAL NOT NULL,
        volatility REAL NOT NULL,
        crowding REAL NOT NULL,
        event_score REAL NOT NULL,
        ma20_gap REAL NOT NULL,
        ma60_gap REAL NOT NULL,
        price_above_ma20 INTEGER NOT NULL,
        price_above_ma60 INTEGER NOT NULL,
        ma20_slope REAL NOT NULL,
        synced_at TEXT NOT NULL,
        PRIMARY KEY (snapshot_date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS financial_profiles (
        ticker TEXT PRIMARY KEY,
        earnings_growth REAL NOT NULL,
        revenue_growth REAL NOT NULL,
        roe REAL NOT NULL,
        free_cashflow_margin REAL NOT NULL,
        event_score REAL NOT NULL,
        valuation_percentile REAL NOT NULL,
        fundamental_source TEXT NOT NULL,
        report_period TEXT,
        synced_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_state (
        state_key TEXT PRIMARY KEY,
        state_value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        event_date TEXT NOT NULL,
        theme_name TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_name TEXT,
        source_url TEXT,
        title TEXT NOT NULL,
        ticker TEXT,
        strength REAL NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES research_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_assignments (
        run_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        theme_name TEXT NOT NULL,
        theme_bucket TEXT NOT NULL,
        theme_source TEXT NOT NULL,
        theme_strength REAL NOT NULL,
        router_mode TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, ticker, theme_name, theme_bucket),
        FOREIGN KEY(run_id) REFERENCES research_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_prefilter_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prefilter_week TEXT NOT NULL,
        built_at TEXT NOT NULL,
        theme_count INTEGER NOT NULL,
        pool_size INTEGER NOT NULL,
        wildcard_count INTEGER NOT NULL,
        build_mode TEXT NOT NULL DEFAULT 'weekly',
        base_run_id INTEGER,
        refresh_added INTEGER NOT NULL DEFAULT 0,
        refresh_removed INTEGER NOT NULL DEFAULT 0,
        rule_version_snapshot TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_prefilter_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        theme_name TEXT NOT NULL,
        total_score REAL NOT NULL,
        policy_score REAL NOT NULL,
        valuation_score REAL NOT NULL,
        performance_score REAL NOT NULL,
        performance_source TEXT NOT NULL DEFAULT 'proxy',
        detail_json TEXT NOT NULL DEFAULT '{}',
        selected INTEGER NOT NULL,
        FOREIGN KEY(run_id) REFERENCES theme_prefilter_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weekly_pool_members (
        run_id INTEGER NOT NULL,
        prefilter_week TEXT NOT NULL,
        ticker TEXT NOT NULL,
        prefilter_theme TEXT NOT NULL,
        prefilter_bucket TEXT NOT NULL,
        prefilter_score REAL NOT NULL,
        policy_score REAL NOT NULL,
        valuation_score REAL NOT NULL,
        performance_score REAL NOT NULL,
        prefilter_source TEXT NOT NULL,
        rank_no INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, ticker),
        FOREIGN KEY(run_id) REFERENCES theme_prefilter_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,
        version TEXT NOT NULL,
        config_json TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_score_inputs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        theme_name TEXT NOT NULL,
        member_count INTEGER NOT NULL DEFAULT 0,
        ready_ratio REAL,
        valuation_median REAL,
        policy_recent_strength REAL,
        history_count INTEGER NOT NULL DEFAULT 0,
        history_avg_return REAL,
        history_win_rate REAL,
        stage_ratio REAL,
        performance_source TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES theme_prefilter_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weekly_pool_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        previous_run_id INTEGER,
        ticker TEXT NOT NULL,
        name TEXT NOT NULL,
        change_type TEXT NOT NULL,
        from_theme TEXT,
        to_theme TEXT,
        from_bucket TEXT,
        to_bucket TEXT,
        reason_text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES theme_prefilter_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        name TEXT NOT NULL,
        decision_stage TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        reason_text TEXT NOT NULL,
        theme_name TEXT,
        theme_bucket TEXT,
        total_score REAL NOT NULL DEFAULT 0,
        stage TEXT NOT NULL,
        rule_version_snapshot TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES research_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_pool_lifecycle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        prefilter_week TEXT NOT NULL,
        ticker TEXT NOT NULL,
        name TEXT NOT NULL,
        theme_name TEXT,
        bucket TEXT,
        lifecycle_status TEXT NOT NULL,
        entry_count INTEGER NOT NULL DEFAULT 0,
        consecutive_runs INTEGER NOT NULL DEFAULT 0,
        in_pool INTEGER NOT NULL DEFAULT 1,
        exit_reason TEXT,
        horizon_1d REAL,
        horizon_5d REAL,
        horizon_20d REAL,
        horizon_60d REAL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES theme_prefilter_runs(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_lifecycle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        prefilter_week TEXT NOT NULL,
        theme_name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        selected INTEGER NOT NULL DEFAULT 0,
        theme_score REAL NOT NULL DEFAULT 0,
        member_count INTEGER NOT NULL DEFAULT 0,
        ready_ratio REAL,
        wildcard_count INTEGER NOT NULL DEFAULT 0,
        recommendation_count INTEGER NOT NULL DEFAULT 0,
        history_count INTEGER NOT NULL DEFAULT 0,
        avg_return_5d REAL,
        win_rate_5d REAL,
        consecutive_active_runs INTEGER NOT NULL DEFAULT 0,
        performance_source TEXT NOT NULL DEFAULT 'proxy',
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES theme_prefilter_runs(id)
    )
    """,
]


class ResearchStore:
    def __init__(self, db_path: Path, database_url: str | None = None):
        self.db_path = db_path
        self.profile = resolve_database_profile(db_path=db_path, database_url=database_url)

    def connect(self):
        if self.profile.active_backend == "postgresql":
            if not self.profile.configured_url:
                raise RuntimeError("PostgreSQL backend configured without database URL")
            return connect_postgres(self.profile.configured_url)
        return connect_sqlite(self.db_path)

    @staticmethod
    def default_universe_filter(limit: int = 300, offset: int = 0) -> UniverseFilter:
        return UniverseFilter(limit=limit, offset=offset)

    @staticmethod
    def _financial_profile_max_age_days() -> int:
        return 120

    @staticmethod
    def _financial_report_period_grace_days() -> int:
        return 7

    def _financial_ready_sql(self, alias: str = "fp") -> str:
        latest_period_sql = "(SELECT MAX(report_period) FROM financial_profiles WHERE report_period IS NOT NULL)"
        if self.profile.active_backend == "postgresql":
            return f"""
                CASE
                    WHEN {alias}.ticker IS NULL THEN 0
                    WHEN {alias}.synced_at < (NOW() - INTERVAL '{self._financial_profile_max_age_days()} day') THEN 0
                    WHEN {alias}.report_period IS NOT NULL
                     AND {latest_period_sql} IS NOT NULL
                     AND {alias}.report_period < {latest_period_sql}
                     AND {alias}.synced_at < (NOW() - INTERVAL '{self._financial_report_period_grace_days()} day')
                    THEN 0
                    ELSE 1
                END
            """
        return f"""
            CASE
                WHEN {alias}.ticker IS NULL THEN 0
                WHEN datetime({alias}.synced_at) < datetime('now', '-{self._financial_profile_max_age_days()} day') THEN 0
                WHEN {alias}.report_period IS NOT NULL
                 AND {latest_period_sql} IS NOT NULL
                 AND {alias}.report_period < {latest_period_sql}
                 AND datetime({alias}.synced_at) < datetime('now', '-{self._financial_report_period_grace_days()} day')
                THEN 0
                ELSE 1
            END
        """

    def _financial_missing_condition_sql(self, alias: str = "fp") -> str:
        latest_period_sql = "(SELECT MAX(report_period) FROM financial_profiles WHERE report_period IS NOT NULL)"
        if self.profile.active_backend == "postgresql":
            return (
                f"({alias}.ticker IS NULL "
                f"OR {alias}.synced_at < (NOW() - INTERVAL '{self._financial_profile_max_age_days()} day') "
                f"OR ({alias}.report_period IS NOT NULL "
                f"AND {latest_period_sql} IS NOT NULL "
                f"AND {alias}.report_period < {latest_period_sql} "
                f"AND {alias}.synced_at < (NOW() - INTERVAL '{self._financial_report_period_grace_days()} day')))"
            )
        return (
            f"({alias}.ticker IS NULL "
            f"OR datetime({alias}.synced_at) < datetime('now', '-{self._financial_profile_max_age_days()} day') "
            f"OR ({alias}.report_period IS NOT NULL "
            f"AND {latest_period_sql} IS NOT NULL "
            f"AND {alias}.report_period < {latest_period_sql} "
            f"AND datetime({alias}.synced_at) < datetime('now', '-{self._financial_report_period_grace_days()} day')))"
        )

    def init_db(self) -> None:
        if self.profile.active_backend == "postgresql":
            if not self.profile.configured_url:
                raise RuntimeError("TOUZIFENXI_DATABASE_URL 未配置")
            init_postgres_schema(self.profile.configured_url)
            self.save_rule_versions()
            return
        with self.connect() as conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            self._ensure_column(conn, "research_runs", "ready_pool_size", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "research_runs", "fallback_pool_size", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "research_runs", "coverage_ratio", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "research_runs", "router_mode", "TEXT NOT NULL DEFAULT 'fallback'")
            self._ensure_column(conn, "research_runs", "active_theme_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "research_runs", "bypass_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "industry_ready", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "financial_ready", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "factor_ready", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "ready_pool", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "theme_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "recommendations", "theme_bucket", "TEXT NOT NULL DEFAULT 'fallback'")
            self._ensure_column(conn, "recommendations", "theme_source", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "recommendations", "theme_strength", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "router_mode", "TEXT NOT NULL DEFAULT 'fallback'")
            self._ensure_column(conn, "recommendations", "prefilter_week", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "recommendations", "prefilter_theme", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "recommendations", "prefilter_bucket", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "recommendations", "prefilter_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "policy_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "valuation_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "performance_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "recommendations", "prefilter_source", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "financial_profiles", "report_period", "TEXT")
            self._ensure_column(conn, "theme_prefilter_scores", "performance_source", "TEXT NOT NULL DEFAULT 'proxy'")
            self._ensure_column(conn, "theme_prefilter_scores", "detail_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "theme_events", "source_url", "TEXT")
            self._ensure_column(conn, "theme_prefilter_runs", "build_mode", "TEXT NOT NULL DEFAULT 'weekly'")
            self._ensure_column(conn, "theme_prefilter_runs", "base_run_id", "INTEGER")
            self._ensure_column(conn, "theme_prefilter_runs", "refresh_added", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "theme_prefilter_runs", "refresh_removed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "research_runs", "rule_version_snapshot", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "theme_prefilter_runs", "rule_version_snapshot", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "candidate_decisions", "rule_version_snapshot", "TEXT NOT NULL DEFAULT '{}'")
            conn.commit()
        self.save_rule_versions()

    def save_weekly_prefilter(self, result) -> int:
        built_at = datetime.now().isoformat(timespec="seconds")
        theme_count = sum(1 for item in result.theme_scores if item.selected)
        previous_summary = self.get_latest_weekly_pool_summary()
        previous_rows = [dict(row) for row in self.get_latest_weekly_pool_rows(limit=50)] if previous_summary else []
        rule_snapshot = self.get_rule_version_snapshot_json()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO theme_prefilter_runs (
                    prefilter_week, built_at, theme_count, pool_size, wildcard_count, build_mode, base_run_id, refresh_added, refresh_removed, rule_version_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.prefilter_week,
                    built_at,
                    theme_count,
                    len(result.pool_members),
                    result.wildcard_count,
                    result.build_mode,
                    result.base_run_id,
                    result.refresh_added,
                    result.refresh_removed,
                    rule_snapshot,
                ),
            )
            run_id = int(cursor.lastrowid)
            if result.theme_scores:
                conn.executemany(
                    """
                    INSERT INTO theme_prefilter_scores (
                        run_id, theme_name, total_score, policy_score, valuation_score, performance_score, performance_source, detail_json, selected
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            item.theme_name,
                            item.total_score,
                            item.policy_score,
                            item.valuation_score,
                            item.performance_score,
                            item.performance_source,
                            item.detail_json,
                            int(item.selected),
                        )
                        for item in result.theme_scores
                    ],
                )
            if result.pool_members:
                conn.executemany(
                    """
                    INSERT INTO weekly_pool_members (
                        run_id, prefilter_week, ticker, prefilter_theme, prefilter_bucket, prefilter_score,
                        policy_score, valuation_score, performance_score, prefilter_source, rank_no, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            result.prefilter_week,
                            str(row["ticker"]),
                            str(row.get("prefilter_theme", "")),
                            str(row.get("prefilter_bucket", "")),
                            float(row.get("prefilter_score", 0.0)),
                            float(row.get("policy_score", 0.0)),
                            float(row.get("valuation_score", 0.0)),
                            float(row.get("performance_score", 0.0)),
                            str(row.get("prefilter_source", "")),
                            int(row.get("prefilter_rank", 0)),
                            built_at,
                        )
                        for row in result.pool_members
                    ],
                )
            self._save_theme_score_inputs(conn, run_id, result.theme_scores, built_at)
            self._save_weekly_pool_changes(conn, run_id, previous_summary, previous_rows, result.pool_members, built_at)
            self._save_stock_pool_lifecycle(conn, run_id, result.prefilter_week, previous_rows, result.pool_members, built_at)
            self._save_theme_lifecycle(conn, run_id, result.prefilter_week, result.theme_scores, result.pool_members, built_at)
            conn.commit()
        return run_id

    def save_run(self, result: RunResult, report_path: Path | None = None) -> int:
        rule_snapshot = self.get_rule_version_snapshot_json()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_runs (
                    run_at, data_source, universe_size, dominant_style, style_confidence, report_path,
                    ready_pool_size, fallback_pool_size, coverage_ratio, router_mode, active_theme_count, bypass_count, rule_version_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    result.data_source,
                    result.universe_size,
                    result.style_view.dominant_style,
                    result.style_view.confidence,
                    str(report_path) if report_path else None,
                    result.ready_pool_size,
                    result.fallback_pool_size,
                    result.coverage_ratio,
                    result.router_mode,
                    len(result.active_themes),
                    result.bypass_count,
                    rule_snapshot,
                ),
            )
            run_id = cursor.lastrowid
            for rank_no, recommendation in enumerate(result.recommendations, start=1):
                recommendation_id = self._insert_recommendation(conn, run_id, rank_no, recommendation)
                for agent_score in recommendation.agent_scores.values():
                    conn.execute(
                        """
                        INSERT INTO agent_scores (
                            recommendation_id, agent_name, score, reason
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            recommendation_id,
                            agent_score.agent_name,
                            agent_score.score,
                            agent_score.reason,
                        ),
                    )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO recommendation_returns (
                        recommendation_id, base_price
                    ) VALUES (?, ?)
                    """,
                    (
                        recommendation_id,
                        recommendation.stock.last_price,
                    ),
                )
            self._save_candidate_decisions(conn, run_id, result.candidate_decisions, rule_snapshot)
            self._update_theme_lifecycle_recommendations(conn, result.recommendations)
            conn.commit()
        return int(run_id)

    def get_rule_version_snapshot_json(self) -> str:
        return serialize_rule_snapshot(RULE_VERSION_SNAPSHOT)

    def get_database_status(self) -> dict[str, object]:
        table_count = 0
        with self.connect() as conn:
            if self.profile.active_backend == "postgresql":
                table_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM pg_tables
                        WHERE schemaname = 'public'
                        """
                    ).fetchone()[0]
                )
            else:
                table_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    ).fetchone()[0]
                )
        return {
            "db_path": str(self.db_path),
            "configured_backend": self.profile.configured_backend,
            "configured_url": self.profile.configured_url or "",
            "active_backend": self.profile.active_backend,
            "sqlite_url": self.profile.sqlite_url,
            "postgres_schema_sql": str(Path(__file__).resolve().parents[2] / "docs" / "postgresql_schema.sql"),
            "table_count": table_count,
        }

    def save_rule_versions(self) -> None:
        created_at = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            for scope, payload in RULE_VERSION_SNAPSHOT.items():
                current = conn.execute(
                    """
                    SELECT version, config_json
                    FROM rule_versions
                    WHERE scope = ? AND is_active = 1
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (scope,),
                ).fetchone()
                config_json = json.dumps(payload, ensure_ascii=False)
                if current and str(current[0]) == str(payload["version"]) and str(current[1]) == config_json:
                    continue
                conn.execute("UPDATE rule_versions SET is_active = 0 WHERE scope = ?", (scope,))
                conn.execute(
                    """
                    INSERT INTO rule_versions (scope, version, config_json, is_active, created_at)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (
                        scope,
                        str(payload["version"]),
                        config_json,
                        created_at,
                    ),
                )
            conn.commit()

    def save_universe_snapshot(self, snapshot: UniverseSnapshot) -> int:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO universe_stocks (
                    ticker, code, name, exchange, board, latest_price, change_percent,
                    turnover_ratio, amount, is_st, is_suspended, source, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        stock.ticker,
                        stock.code,
                        stock.name,
                        stock.exchange,
                        stock.board,
                        stock.latest_price,
                        stock.change_percent,
                        stock.turnover_ratio,
                        stock.amount,
                        int(stock.is_st),
                        int(stock.is_suspended),
                        snapshot.source,
                        snapshot.captured_at.isoformat(timespec="seconds"),
                    )
                    for stock in snapshot.stocks
                ],
            )
            conn.commit()
        return len(snapshot.stocks)

    def save_market_snapshots(self, run_id: int, universe: list[StockIdea], data_source: str) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO market_snapshots (
                    run_id, ticker, data_source, last_price, momentum_20d, momentum_60d,
                    relative_strength, volume_trend, turnover_trend, drawdown_from_high,
                    volatility, crowding, valuation_percentile, earnings_growth,
                    revenue_growth, roe, free_cashflow_margin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        stock.ticker,
                        data_source,
                        stock.last_price,
                        stock.momentum_20d,
                        stock.momentum_60d,
                        stock.relative_strength,
                        stock.volume_trend,
                        stock.turnover_trend,
                        stock.drawdown_from_high,
                        stock.volatility,
                        stock.crowding,
                        stock.valuation_percentile,
                        stock.earnings_growth,
                        stock.revenue_growth,
                        stock.roe,
                        stock.free_cashflow_margin,
                    )
                    for stock in universe
                ],
            )
            conn.commit()

    def save_theme_router_data(self, run_id: int, universe: list[StockIdea], events: list[ThemeEvent]) -> None:
        created_at = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            if events:
                conn.executemany(
                    """
                    INSERT INTO theme_events (
                        run_id, event_date, theme_name, source_type, source_name, source_url, title, ticker, strength, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            event.event_date,
                            event.theme_name,
                            event.source_type,
                            event.source_name,
                            event.source_url,
                            event.title,
                            event.ticker,
                            event.strength,
                            created_at,
                        )
                        for event in events
                    ],
                )
            assignment_rows = []
            for stock in universe:
                if not stock.theme_name and stock.theme_bucket == "fallback":
                    continue
                assignment_rows.append(
                    (
                        run_id,
                        stock.ticker,
                        stock.theme_name,
                        stock.theme_bucket,
                        stock.theme_source,
                        stock.theme_strength,
                        stock.router_mode,
                        created_at,
                    )
                )
            if assignment_rows:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO theme_assignments (
                        run_id, ticker, theme_name, theme_bucket, theme_source, theme_strength, router_mode, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    assignment_rows,
                )
            conn.commit()

    def save_daily_factors(self, snapshot_date: str, universe: list[StockIdea], data_source: str) -> int:
        synced_at = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_factors (
                    snapshot_date, ticker, name, sector, style_tags, data_source,
                    fundamental_source, last_price, valuation_percentile, earnings_growth,
                    revenue_growth, roe, free_cashflow_margin, momentum_20d, momentum_60d,
                    relative_strength, volume_trend, turnover_trend, drawdown_from_high,
                    volatility, crowding, event_score, ma20_gap, ma60_gap,
                    price_above_ma20, price_above_ma60, ma20_slope, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_date,
                        stock.ticker,
                        stock.name,
                        stock.sector,
                        ",".join(stock.style_tags),
                        data_source,
                        stock.fundamental_source,
                        stock.last_price,
                        stock.valuation_percentile,
                        stock.earnings_growth,
                        stock.revenue_growth,
                        stock.roe,
                        stock.free_cashflow_margin,
                        stock.momentum_20d,
                        stock.momentum_60d,
                        stock.relative_strength,
                        stock.volume_trend,
                        stock.turnover_trend,
                        stock.drawdown_from_high,
                        stock.volatility,
                        stock.crowding,
                        stock.event_score,
                        stock.ma20_gap,
                        stock.ma60_gap,
                        int(stock.price_above_ma20),
                        int(stock.price_above_ma60),
                        stock.ma20_slope,
                        synced_at,
                    )
                    for stock in universe
                ],
            )
            conn.commit()
        return len(universe)

    def get_pending_return_updates(self) -> list[tuple[int, str, str, float]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT rr.recommendation_id, rec.ticker, runs.run_at, rr.base_price
                FROM recommendation_returns rr
                JOIN recommendations rec ON rec.id = rr.recommendation_id
                JOIN research_runs runs ON runs.id = rec.run_id
                WHERE rr.horizon_1d IS NULL OR rr.horizon_5d IS NULL OR rr.horizon_20d IS NULL OR rr.horizon_60d IS NULL
                """
            ).fetchall()
        return [(int(row[0]), str(row[1]), str(row[2]), float(row[3])) for row in rows]

    @staticmethod
    def _return_status(run_at: str, updated_at: str | None, missing_horizons: list[str], today: datetime.date) -> str:
        if not missing_horizons:
            return "completed"
        run_date = datetime.fromisoformat(run_at).date()
        due_days = {"1d": 2, "5d": 8, "20d": 30, "60d": 90}
        next_missing = missing_horizons[0]
        due_date = run_date + timedelta(days=due_days[next_missing])
        if today < due_date:
            return "waiting"
        if updated_at:
            return "failed"
        return "updatable"

    def get_due_return_updates(self) -> list[tuple[int, str, str, float]]:
        today = datetime.now().date()
        due_rows: list[tuple[int, str, str, float]] = []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT rr.recommendation_id, rec.ticker, runs.run_at, rr.base_price, rr.updated_at,
                       rr.horizon_1d, rr.horizon_5d, rr.horizon_20d, rr.horizon_60d
                FROM recommendation_returns rr
                JOIN recommendations rec ON rec.id = rr.recommendation_id
                JOIN research_runs runs ON runs.id = rec.run_id
                """
            ).fetchall()
        for row in rows:
            missing_horizons = []
            if row[5] is None:
                missing_horizons.append("1d")
            if row[6] is None:
                missing_horizons.append("5d")
            if row[7] is None:
                missing_horizons.append("20d")
            if row[8] is None:
                missing_horizons.append("60d")
            status = self._return_status(
                str(row[2]),
                str(row[4]) if row[4] is not None else None,
                missing_horizons,
                today,
            )
            if status == "updatable":
                due_rows.append((int(row[0]), str(row[1]), str(row[2]), float(row[3])))
        return due_rows

    def get_performance_summary(self) -> dict[str, object]:
        today = datetime.now().date()

        with self.connect() as conn:
            runs = int(conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0])
            recommendations = int(conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0])
            pending_returns = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM recommendation_returns
                    WHERE horizon_1d IS NULL OR horizon_5d IS NULL OR horizon_20d IS NULL OR horizon_60d IS NULL
                    """
                ).fetchone()[0]
            )
            source_rows = conn.execute(
                """
                SELECT fundamental_source, COUNT(*)
                FROM recommendations
                GROUP BY fundamental_source
                ORDER BY COUNT(*) DESC, fundamental_source ASC
                """
            ).fetchall()
            horizon_map = {}
            for horizon in ["1d", "5d", "20d", "60d"]:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*),
                           AVG(horizon_{horizon}),
                           AVG(CASE WHEN horizon_{horizon} > 0 THEN 1.0 ELSE 0.0 END)
                    FROM recommendation_returns
                    WHERE horizon_{horizon} IS NOT NULL
                    """
                ).fetchone()
                horizon_map[horizon] = {
                    "count": int(row[0] or 0),
                    "avg_return": float(row[1]) if row[1] is not None else None,
                    "win_rate": float(row[2]) if row[2] is not None else None,
                }
            latest_run_row = conn.execute(
                """
                SELECT id, run_at, data_source, universe_size, dominant_style, style_confidence,
                       ready_pool_size, fallback_pool_size, coverage_ratio, router_mode, active_theme_count, bypass_count
                FROM research_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_run_sources = []
            latest_run_financial_coverage = None
            if latest_run_row:
                latest_run_sources = conn.execute(
                    """
                    SELECT fundamental_source, COUNT(*)
                    FROM recommendations
                    WHERE run_id = ?
                    GROUP BY fundamental_source
                    ORDER BY COUNT(*) DESC, fundamental_source ASC
                    """,
                    (int(latest_run_row[0]),),
                ).fetchall()
                latest_run_financial_coverage = conn.execute(
                    """
                    SELECT AVG(CASE WHEN fundamental_source != 'local_profile' THEN 1.0 ELSE 0.0 END)
                    FROM recommendations
                    WHERE run_id = ?
                    """,
                    (int(latest_run_row[0]),),
                ).fetchone()[0]
            recent_runs = conn.execute(
                """
                SELECT id, run_at, data_source, dominant_style, style_confidence, ready_pool_size, fallback_pool_size, coverage_ratio,
                       router_mode, active_theme_count, bypass_count
                FROM research_runs
                ORDER BY id DESC
                LIMIT 5
                """
            ).fetchall()
            router_rows = conn.execute(
                """
                SELECT router_mode, COUNT(*)
                FROM research_runs
                GROUP BY router_mode
                ORDER BY COUNT(*) DESC, router_mode ASC
                """
            ).fetchall()
            theme_bucket_rows = conn.execute(
                """
                SELECT theme_bucket, COUNT(*)
                FROM recommendations
                GROUP BY theme_bucket
                ORDER BY COUNT(*) DESC, theme_bucket ASC
                """
            ).fetchall()
            theme_bucket_returns = conn.execute(
                """
                SELECT rec.theme_bucket, COUNT(rr.horizon_1d),
                       AVG(rr.horizon_1d),
                       AVG(
                           CASE
                               WHEN rr.horizon_1d IS NULL THEN NULL
                               WHEN rr.horizon_1d > 0 THEN 1.0
                               ELSE 0.0
                           END
                       )
                FROM recommendations rec
                LEFT JOIN recommendation_returns rr ON rr.recommendation_id = rec.id
                GROUP BY rec.theme_bucket
                ORDER BY COUNT(*) DESC, rec.theme_bucket ASC
                """
            ).fetchall()
            latest_weekly_pool = conn.execute(
                """
                SELECT id, prefilter_week, built_at, theme_count, pool_size, wildcard_count, build_mode, base_run_id, refresh_added, refresh_removed
                FROM theme_prefilter_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_weekly_bucket_rows = []
            if latest_weekly_pool:
                latest_weekly_bucket_rows = conn.execute(
                    """
                    SELECT prefilter_bucket, COUNT(*)
                    FROM weekly_pool_members
                    WHERE run_id = ?
                    GROUP BY prefilter_bucket
                    ORDER BY COUNT(*) DESC, prefilter_bucket ASC
                    """,
                    (int(latest_weekly_pool[0]),),
                ).fetchall()
            latest_run_theme_rows = []
            latest_run_bucket_rows = []
            latest_run_bypass_count = 0
            if latest_run_row:
                latest_run_theme_rows = conn.execute(
                    """
                    SELECT theme_name, COUNT(*)
                    FROM recommendations
                    WHERE run_id = ? AND theme_name != ''
                    GROUP BY theme_name
                    ORDER BY COUNT(*) DESC, theme_name ASC
                    """,
                    (int(latest_run_row[0]),),
                ).fetchall()
                latest_run_bucket_rows = conn.execute(
                    """
                    SELECT theme_bucket, COUNT(*)
                    FROM recommendations
                    WHERE run_id = ?
                    GROUP BY theme_bucket
                    ORDER BY COUNT(*) DESC, theme_bucket ASC
                    """,
                    (int(latest_run_row[0]),),
                ).fetchall()
                latest_run_bypass_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM recommendations
                        WHERE run_id = ? AND theme_bucket = 'bypass'
                        """,
                        (int(latest_run_row[0]),),
                    ).fetchone()[0]
                )
            return_rows = conn.execute(
                """
                SELECT rec.run_id, runs.run_at, rr.updated_at,
                       rr.horizon_1d, rr.horizon_5d, rr.horizon_20d, rr.horizon_60d
                FROM recommendation_returns rr
                JOIN recommendations rec ON rec.id = rr.recommendation_id
                JOIN research_runs runs ON runs.id = rec.run_id
                """
            ).fetchall()
            return_status = {"updatable": 0, "waiting": 0, "failed": 0, "completed": 0}
            for row in return_rows:
                missing_horizons = []
                if row[3] is None:
                    missing_horizons.append("1d")
                if row[4] is None:
                    missing_horizons.append("5d")
                if row[5] is None:
                    missing_horizons.append("20d")
                if row[6] is None:
                    missing_horizons.append("60d")
                status = self._return_status(str(row[1]), str(row[2]) if row[2] is not None else None, missing_horizons, today)
                return_status[status] += 1
        return {
            "runs": runs,
            "recommendations": recommendations,
            "pending_returns": pending_returns,
            "return_status": return_status,
            "fundamental_sources": [(str(row[0]), int(row[1])) for row in source_rows],
            "router_modes": [(str(row[0]), int(row[1])) for row in router_rows],
            "theme_buckets": [(str(row[0]), int(row[1])) for row in theme_bucket_rows],
            "theme_bucket_returns": [
                {
                    "theme_bucket": str(row[0]),
                    "count": int(row[1] or 0),
                    "avg_return_1d": float(row[2]) if row[2] is not None else None,
                    "win_rate_1d": float(row[3]) if row[3] is not None else None,
                }
                for row in theme_bucket_returns
            ],
            "latest_weekly_pool": (
                {
                    "id": int(latest_weekly_pool[0]),
                    "prefilter_week": str(latest_weekly_pool[1]),
                    "built_at": str(latest_weekly_pool[2]),
                    "theme_count": int(latest_weekly_pool[3]),
                    "pool_size": int(latest_weekly_pool[4]),
                    "wildcard_count": int(latest_weekly_pool[5]),
                    "build_mode": str(latest_weekly_pool[6]),
                    "base_run_id": int(latest_weekly_pool[7]) if latest_weekly_pool[7] is not None else None,
                    "refresh_added": int(latest_weekly_pool[8]),
                    "refresh_removed": int(latest_weekly_pool[9]),
                    "bucket_counts": {str(row[0]): int(row[1]) for row in latest_weekly_bucket_rows},
                }
                if latest_weekly_pool
                else None
            ),
            "horizons": horizon_map,
            "latest_run": (
                {
                    "id": int(latest_run_row[0]),
                    "run_at": str(latest_run_row[1]),
                    "data_source": str(latest_run_row[2]),
                    "universe_size": int(latest_run_row[3]),
                    "dominant_style": str(latest_run_row[4]),
                    "style_confidence": float(latest_run_row[5]),
                    "ready_pool_size": int(latest_run_row[6]),
                    "fallback_pool_size": int(latest_run_row[7]),
                    "coverage_ratio": float(latest_run_row[8]),
                    "router_mode": str(latest_run_row[9]),
                    "active_theme_count": int(latest_run_row[10]),
                    "bypass_count": int(latest_run_row[11]),
                    "fundamental_sources": [(str(row[0]), int(row[1])) for row in latest_run_sources],
                    "theme_counts": [(str(row[0]), int(row[1])) for row in latest_run_theme_rows],
                    "theme_buckets": [(str(row[0]), int(row[1])) for row in latest_run_bucket_rows],
                    "latest_run_bypass_count": latest_run_bypass_count,
                    "recommendation_real_financial_ratio": (
                        float(latest_run_financial_coverage) if latest_run_financial_coverage is not None else 0.0
                    ),
                }
                if latest_run_row
                else None
            ),
            "recent_runs": [
                {
                    "id": int(row[0]),
                    "run_at": str(row[1]),
                    "data_source": str(row[2]),
                    "dominant_style": str(row[3]),
                    "style_confidence": float(row[4]),
                    "ready_pool_size": int(row[5]),
                    "fallback_pool_size": int(row[6]),
                    "coverage_ratio": float(row[7]),
                    "router_mode": str(row[8]),
                    "active_theme_count": int(row[9]),
                    "bypass_count": int(row[10]),
                }
                for row in recent_runs
            ],
        }

    def get_sync_state(self, state_key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state_value FROM sync_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
        if row and row[0] is not None:
            return str(row[0])
        return default

    def set_sync_state(self, state_key: str, state_value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sync_state (state_key, state_value, updated_at)
                VALUES (?, ?, ?)
                """,
                (state_key, state_value, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()

    def update_recommendation_returns(
        self,
        recommendation_id: int,
        horizon_1d: float | None,
        horizon_5d: float | None,
        horizon_20d: float | None,
        horizon_60d: float | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE recommendation_returns
                SET horizon_1d = COALESCE(?, horizon_1d),
                    horizon_5d = COALESCE(?, horizon_5d),
                    horizon_20d = COALESCE(?, horizon_20d),
                    horizon_60d = COALESCE(?, horizon_60d),
                    updated_at = ?
                WHERE recommendation_id = ?
                """,
                (
                    horizon_1d,
                    horizon_5d,
                    horizon_20d,
                    horizon_60d,
                    datetime.now().isoformat(timespec="seconds"),
                    recommendation_id,
                ),
            )
            conn.commit()

    def list_universe_candidates(self, universe_filter: UniverseFilter) -> list[sqlite3.Row]:
        conditions = []
        params: list[object] = []
        if universe_filter.exclude_st:
            conditions.append("is_st = 0")
        if universe_filter.exclude_suspended:
            conditions.append("is_suspended = 0")
        if universe_filter.min_amount > 0:
            conditions.append("amount >= ?")
            params.append(universe_filter.min_amount)
        if universe_filter.exclude_boards:
            placeholders = ", ".join("?" for _ in universe_filter.exclude_boards)
            conditions.append(f"board NOT IN ({placeholders})")
            params.extend(universe_filter.exclude_boards)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT u.ticker, u.code, u.name, u.exchange, u.board, u.latest_price, u.change_percent,
                   u.turnover_ratio, u.amount, u.is_st, u.is_suspended, u.source, u.synced_at,
                   COALESCE(si.sector_lv3, si.sector_lv2, u.board) AS sector,
                   COALESCE(fp.valuation_percentile, 0.5) AS valuation_percentile,
                   COALESCE(fp.fundamental_source, 'local_profile') AS fundamental_source,
                   CASE WHEN si.ticker IS NULL THEN 0 ELSE 1 END AS industry_ready,
                   {self._financial_ready_sql("fp")} AS financial_ready,
                   CASE
                       WHEN df.ticker IS NULL THEN 0
                       ELSE 1
                   END AS factor_ready,
                   CASE
                       WHEN si.ticker IS NOT NULL
                        AND ({self._financial_ready_sql("fp")}) = 1
                        AND df.ticker IS NOT NULL
                       THEN 1 ELSE 0
                   END AS ready_pool
            FROM universe_stocks u
            LEFT JOIN stock_industries si ON si.ticker = u.ticker
            LEFT JOIN financial_profiles fp ON fp.ticker = u.ticker
            LEFT JOIN daily_factors df
              ON df.ticker = u.ticker
             AND df.snapshot_date = (SELECT MAX(snapshot_date) FROM daily_factors)
            {where_clause}
            ORDER BY ready_pool DESC, financial_ready DESC, industry_ready DESC, factor_ready DESC, u.amount DESC, u.turnover_ratio DESC
            LIMIT ?
            OFFSET ?
        """
        params.extend([universe_filter.limit, universe_filter.offset])
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return rows

    def get_universe_rows_by_tickers(self, tickers: list[str]) -> list[sqlite3.Row]:
        if not tickers:
            return []
        placeholders = ", ".join("?" for _ in tickers)
        query = f"""
            SELECT u.ticker, u.code, u.name, u.exchange, u.board, u.latest_price, u.change_percent,
                   u.turnover_ratio, u.amount, u.is_st, u.is_suspended, u.source, u.synced_at,
                   COALESCE(si.sector_lv3, si.sector_lv2, u.board) AS sector,
                   COALESCE(fp.valuation_percentile, 0.5) AS valuation_percentile,
                   COALESCE(fp.fundamental_source, 'local_profile') AS fundamental_source,
                   CASE WHEN si.ticker IS NULL THEN 0 ELSE 1 END AS industry_ready,
                   {self._financial_ready_sql("fp")} AS financial_ready,
                   CASE
                       WHEN df.ticker IS NULL THEN 0
                       ELSE 1
                   END AS factor_ready,
                   CASE
                       WHEN si.ticker IS NOT NULL
                        AND ({self._financial_ready_sql("fp")}) = 1
                        AND df.ticker IS NOT NULL
                       THEN 1 ELSE 0
                   END AS ready_pool
            FROM universe_stocks u
            LEFT JOIN stock_industries si ON si.ticker = u.ticker
            LEFT JOIN financial_profiles fp ON fp.ticker = u.ticker
            LEFT JOIN daily_factors df
              ON df.ticker = u.ticker
             AND df.snapshot_date = (SELECT MAX(snapshot_date) FROM daily_factors)
            WHERE u.ticker IN ({placeholders})
        """
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, tickers).fetchall()
        return rows

    def save_industry_dictionary(self, rows: list[tuple[str, str, str]]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO industry_dictionary (
                    industry_name, industry_code, source, synced_at
                ) VALUES (?, ?, ?, ?)
                """,
                [(name, code, source, now) for name, code, source in rows],
            )
            conn.commit()
        return len(rows)

    def save_stock_industries(self, rows: list[tuple[str, str, str, str, str, str, str, str]]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO stock_industries (
                    ticker, industry_standard, industry_code, sector_lv1, sector_lv2,
                    sector_lv3, sector_lv4, source, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(ticker, std, code, lv1, lv2, lv3, lv4, source, now) for ticker, std, code, lv1, lv2, lv3, lv4, source in rows],
            )
            conn.commit()
        return len(rows)

    def get_stock_industry_map(self, tickers: list[str]) -> dict[str, sqlite3.Row]:
        if not tickers:
            return {}
        placeholders = ", ".join("?" for _ in tickers)
        query = f"""
            SELECT ticker, industry_standard, industry_code, sector_lv1, sector_lv2, sector_lv3, sector_lv4, source, synced_at
            FROM stock_industries
            WHERE ticker IN ({placeholders})
        """
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, tickers).fetchall()
        return {str(row["ticker"]): row for row in rows}

    def get_latest_factor_snapshot_date(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT MAX(snapshot_date) FROM daily_factors").fetchone()
        if row and row[0]:
            return str(row[0])
        return None

    def get_latest_factor_map(self, tickers: list[str]) -> dict[str, sqlite3.Row]:
        if not tickers:
            return {}
        snapshot_date = self.get_latest_factor_snapshot_date()
        if not snapshot_date:
            return {}
        placeholders = ", ".join("?" for _ in tickers)
        query = f"""
            SELECT *
            FROM daily_factors
            WHERE snapshot_date = ? AND ticker IN ({placeholders})
        """
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, [snapshot_date, *tickers]).fetchall()
        return {str(row["ticker"]): row for row in rows}

    def get_theme_history_metrics(self) -> dict[str, dict[str, float | int | None]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT rec.theme_name,
                       COUNT(rr.horizon_5d),
                       AVG(rr.horizon_5d),
                       AVG(CASE WHEN rr.horizon_5d IS NULL THEN NULL WHEN rr.horizon_5d > 0 THEN 1.0 ELSE 0.0 END)
                FROM recommendations rec
                LEFT JOIN recommendation_returns rr ON rr.recommendation_id = rec.id
                WHERE rec.theme_name != ''
                GROUP BY rec.theme_name
                """
            ).fetchall()
        return {
            str(row[0]): {
                "count": int(row[1] or 0),
                "avg_return": float(row[2]) if row[2] is not None else None,
                "win_rate": float(row[3]) if row[3] is not None else None,
            }
            for row in rows
        }

    def get_recent_theme_strength(self, days: int = 7) -> dict[str, float]:
        with self.connect() as conn:
            if self.profile.active_backend == "postgresql":
                rows = conn.execute(
                    """
                    SELECT theme_name, SUM(strength)
                    FROM theme_events
                    WHERE created_at >= (NOW() - (%s * INTERVAL '1 day'))
                    GROUP BY theme_name
                    """,
                    (days,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT theme_name, SUM(strength)
                    FROM theme_events
                    WHERE datetime(created_at) >= datetime('now', ?)
                    GROUP BY theme_name
                    """,
                    (f"-{days} day",),
                ).fetchall()
        return {str(row[0]): float(row[1] or 0.0) for row in rows}

    def save_financial_profiles(self, rows: list[tuple[str, float, float, float, float, float, float, str, str | None]]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO financial_profiles (
                    ticker, earnings_growth, revenue_growth, roe, free_cashflow_margin,
                    event_score, valuation_percentile, fundamental_source, report_period, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ticker,
                        earnings_growth,
                        revenue_growth,
                        roe,
                        free_cashflow_margin,
                        event_score,
                        valuation_percentile,
                        source,
                        report_period,
                        now,
                    )
                    for ticker, earnings_growth, revenue_growth, roe, free_cashflow_margin, event_score, valuation_percentile, source, report_period in rows
                ],
            )
            conn.commit()
        return len(rows)

    def get_financial_profile_map(self, tickers: list[str]) -> dict[str, sqlite3.Row]:
        if not tickers:
            return {}
        placeholders = ", ".join("?" for _ in tickers)
        query = f"""
            SELECT ticker, earnings_growth, revenue_growth, roe, free_cashflow_margin,
                   event_score, valuation_percentile, fundamental_source, report_period, synced_at
            FROM financial_profiles
            WHERE ticker IN ({placeholders})
        """
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, tickers).fetchall()
        return {str(row["ticker"]): row for row in rows}

    def list_candidates_missing_industry(self, universe_filter: UniverseFilter) -> list[sqlite3.Row]:
        conditions = []
        params: list[object] = []
        if universe_filter.exclude_st:
            conditions.append("u.is_st = 0")
        if universe_filter.exclude_suspended:
            conditions.append("u.is_suspended = 0")
        if universe_filter.min_amount > 0:
            conditions.append("u.amount >= ?")
            params.append(universe_filter.min_amount)
        if universe_filter.exclude_boards:
            placeholders = ", ".join("?" for _ in universe_filter.exclude_boards)
            conditions.append(f"u.board NOT IN ({placeholders})")
            params.extend(universe_filter.exclude_boards)
        conditions.append("si.ticker IS NULL")
        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"""
            SELECT u.ticker, u.code, u.name, u.exchange, u.board, u.latest_price, u.change_percent,
                   u.turnover_ratio, u.amount, u.is_st, u.is_suspended, u.source, u.synced_at
            FROM universe_stocks u
            LEFT JOIN stock_industries si ON si.ticker = u.ticker
            {where_clause}
            ORDER BY u.amount DESC, u.turnover_ratio DESC
            LIMIT ?
            OFFSET ?
        """
        params.extend([universe_filter.limit, universe_filter.offset])
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return rows

    def get_latest_weekly_pool_summary(self) -> dict[str, object] | None:
        with self.connect() as conn:
            run_row = conn.execute(
                """
                SELECT id, prefilter_week, built_at, theme_count, pool_size, wildcard_count, build_mode, base_run_id, refresh_added, refresh_removed
                FROM theme_prefilter_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if not run_row:
                return None
            theme_rows = conn.execute(
                """
                SELECT theme_name, total_score, policy_score, valuation_score, performance_score, performance_source, selected
                , detail_json
                FROM theme_prefilter_scores
                WHERE run_id = ?
                ORDER BY selected DESC, total_score DESC, theme_name ASC
                """,
                (int(run_row[0]),),
            ).fetchall()
            bucket_rows = conn.execute(
                """
                SELECT prefilter_bucket, COUNT(*)
                FROM weekly_pool_members
                WHERE run_id = ?
                GROUP BY prefilter_bucket
                ORDER BY COUNT(*) DESC, prefilter_bucket ASC
                """,
                (int(run_row[0]),),
            ).fetchall()
        return {
            "id": int(run_row[0]),
            "prefilter_week": str(run_row[1]),
            "built_at": str(run_row[2]),
            "theme_count": int(run_row[3]),
            "pool_size": int(run_row[4]),
            "wildcard_count": int(run_row[5]),
            "build_mode": str(run_row[6]),
            "base_run_id": int(run_row[7]) if run_row[7] is not None else None,
            "refresh_added": int(run_row[8]),
            "refresh_removed": int(run_row[9]),
            "themes": [
                {
                    "theme_name": str(row[0]),
                    "total_score": float(row[1]),
                    "policy_score": float(row[2]),
                    "valuation_score": float(row[3]),
                    "performance_score": float(row[4]),
                    "performance_source": str(row[5]),
                    "selected": bool(row[6]),
                    "detail_json": str(row[7] or "{}"),
                }
                for row in theme_rows
            ],
            "bucket_counts": {str(row[0]): int(row[1]) for row in bucket_rows},
        }

    def get_latest_weekly_pool_rows(self, limit: int | None = None) -> list[sqlite3.Row]:
        with self.connect() as conn:
            latest_run = conn.execute("SELECT MAX(id) FROM theme_prefilter_runs").fetchone()[0]
            if latest_run is None:
                return []
            query = """
                SELECT u.ticker, u.code, u.name, u.exchange, u.board, u.latest_price, u.change_percent,
                       u.turnover_ratio, u.amount, u.is_st, u.is_suspended, u.source, u.synced_at,
                       COALESCE(si.sector_lv3, si.sector_lv2, u.board) AS sector,
                       COALESCE(fp.valuation_percentile, 0.5) AS valuation_percentile,
                       COALESCE(fp.fundamental_source, 'local_profile') AS fundamental_source,
                       CASE WHEN si.ticker IS NULL THEN 0 ELSE 1 END AS industry_ready,
                       {financial_ready} AS financial_ready,
                       CASE WHEN df.ticker IS NULL THEN 0 ELSE 1 END AS factor_ready,
                       CASE
                           WHEN si.ticker IS NOT NULL AND ({financial_ready}) = 1 AND df.ticker IS NOT NULL
                           THEN 1 ELSE 0
                       END AS ready_pool,
                       wpm.prefilter_week, wpm.prefilter_theme, wpm.prefilter_bucket, wpm.prefilter_score,
                       wpm.policy_score, wpm.valuation_score, wpm.performance_score, wpm.prefilter_source, wpm.rank_no
                FROM weekly_pool_members wpm
                JOIN universe_stocks u ON u.ticker = wpm.ticker
                LEFT JOIN stock_industries si ON si.ticker = u.ticker
                LEFT JOIN financial_profiles fp ON fp.ticker = u.ticker
                LEFT JOIN daily_factors df
                  ON df.ticker = u.ticker
                 AND df.snapshot_date = (SELECT MAX(snapshot_date) FROM daily_factors)
                WHERE wpm.run_id = ?
                ORDER BY wpm.prefilter_bucket ASC, wpm.rank_no ASC
            """.format(financial_ready=self._financial_ready_sql("fp"))
            if limit is not None:
                query += " LIMIT ?"
                params = [int(latest_run), limit]
            else:
                params = [int(latest_run)]
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        # sqlite rows are immutable; return as fetched and use prefetched scores already embedded
        return rows

    def get_latest_recommendations(self, limit: int = 5) -> list[dict[str, object]]:
        with self.connect() as conn:
            latest_run = conn.execute("SELECT MAX(id) FROM research_runs").fetchone()[0]
            if latest_run is None:
                return []
            conn.row_factory = sqlite3.Row
            rec_rows = conn.execute(
                """
                SELECT id, run_id, rank_no, ticker, name, sector, stage, total_score, last_price,
                       fundamental_source, industry_ready, financial_ready, factor_ready, ready_pool,
                       theme_name, theme_bucket, theme_source, theme_strength, router_mode,
                       prefilter_week, prefilter_theme, prefilter_bucket, prefilter_score,
                       policy_score, valuation_score, performance_score, prefilter_source, reasons, risks
                FROM recommendations
                WHERE run_id = ?
                ORDER BY rank_no ASC
                LIMIT ?
                """,
                (int(latest_run), limit),
            ).fetchall()
            score_rows = conn.execute(
                """
                SELECT recommendation_id, agent_name, score
                FROM agent_scores
                WHERE recommendation_id IN (
                    SELECT id FROM recommendations WHERE run_id = ?
                )
                """,
                (int(latest_run),),
            ).fetchall()
        score_map: dict[int, dict[str, float]] = {}
        for row in score_rows:
            rec_id = int(row["recommendation_id"])
            score_map.setdefault(rec_id, {})
            score_map[rec_id][str(row["agent_name"])] = float(row["score"])
        recommendations: list[dict[str, object]] = []
        for row in rec_rows:
            rec_id = int(row["id"])
            recommendations.append(
                {
                    "id": rec_id,
                    "run_id": int(row["run_id"]),
                    "rank_no": int(row["rank_no"]),
                    "ticker": str(row["ticker"]),
                    "name": str(row["name"]),
                    "sector": str(row["sector"]),
                    "stage": str(row["stage"]),
                    "total_score": float(row["total_score"]),
                    "last_price": float(row["last_price"]),
                    "fundamental_source": str(row["fundamental_source"]),
                    "industry_ready": bool(row["industry_ready"]),
                    "financial_ready": bool(row["financial_ready"]),
                    "factor_ready": bool(row["factor_ready"]),
                    "ready_pool": bool(row["ready_pool"]),
                    "theme_name": str(row["theme_name"]),
                    "theme_bucket": str(row["theme_bucket"]),
                    "theme_source": str(row["theme_source"]),
                    "theme_strength": float(row["theme_strength"]),
                    "router_mode": str(row["router_mode"]),
                    "prefilter_week": str(row["prefilter_week"]),
                    "prefilter_theme": str(row["prefilter_theme"]),
                    "prefilter_bucket": str(row["prefilter_bucket"]),
                    "prefilter_score": float(row["prefilter_score"]),
                    "policy_score": float(row["policy_score"]),
                    "valuation_score": float(row["valuation_score"]),
                    "performance_score": float(row["performance_score"]),
                    "prefilter_source": str(row["prefilter_source"]),
                    "reasons": [item for item in str(row["reasons"]).split(" | ") if item],
                    "risks": [item for item in str(row["risks"]).split(" | ") if item],
                    "agent_scores": score_map.get(rec_id, {}),
                }
            )
        return recommendations

    def get_recent_theme_events(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id, event_date, theme_name, source_type, source_name, source_url, title, ticker, strength
                FROM theme_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": int(row["run_id"]),
                "event_date": str(row["event_date"]),
                "theme_name": str(row["theme_name"]),
                "source_type": str(row["source_type"]),
                "source_name": str(row["source_name"] or ""),
                "source_url": str(row["source_url"] or ""),
                "title": str(row["title"]),
                "ticker": str(row["ticker"] or ""),
                "strength": float(row["strength"]),
            }
            for row in rows
        ]

    def get_recent_weekly_pool_changes(self, limit: int = 30) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id, previous_run_id, ticker, name, change_type, from_theme, to_theme, from_bucket, to_bucket, reason_text, created_at
                FROM weekly_pool_changes
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_candidate_decisions(self, limit: int = 40) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id, ticker, name, decision_stage, decision, reason_code, reason_text, theme_name, theme_bucket, total_score, stage, created_at
                FROM candidate_decisions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stock_pool_lifecycle_summary(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT spl.*
                FROM stock_pool_lifecycle spl
                JOIN (
                    SELECT ticker, MAX(id) AS latest_id
                    FROM stock_pool_lifecycle
                    GROUP BY ticker
                ) latest ON latest.latest_id = spl.id
                ORDER BY spl.in_pool DESC, spl.consecutive_runs DESC, spl.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_universe_master_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM universe_stocks").fetchone()[0])

    def get_universe_master_rows(self, limit: int = 100) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT ticker, code, name, exchange, board, latest_price, change_percent,
                       turnover_ratio, amount, is_st, is_suspended, source, synced_at
                FROM universe_stocks
                ORDER BY amount DESC, turnover_ratio DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_theme_lifecycle_summary(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT tl.*
                FROM theme_lifecycle tl
                ORDER BY tl.run_id DESC, tl.selected DESC, tl.theme_score DESC, tl.theme_name ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_rule_versions(self) -> list[dict[str, object]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT scope, version, config_json, created_at
                FROM rule_versions
                WHERE is_active = 1
                ORDER BY scope ASC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_candidates_missing_factors(self, snapshot_date: str, universe_filter: UniverseFilter) -> list[sqlite3.Row]:
        conditions = []
        params: list[object] = []
        if universe_filter.exclude_st:
            conditions.append("u.is_st = 0")
        if universe_filter.exclude_suspended:
            conditions.append("u.is_suspended = 0")
        if universe_filter.min_amount > 0:
            conditions.append("u.amount >= ?")
            params.append(universe_filter.min_amount)
        if universe_filter.exclude_boards:
            placeholders = ", ".join("?" for _ in universe_filter.exclude_boards)
            conditions.append(f"u.board NOT IN ({placeholders})")
            params.extend(universe_filter.exclude_boards)
        conditions.append("df.ticker IS NULL")
        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"""
            SELECT u.ticker, u.code, u.name, u.exchange, u.board, u.latest_price, u.change_percent,
                   u.turnover_ratio, u.amount, u.is_st, u.is_suspended, u.source, u.synced_at
            FROM universe_stocks u
            LEFT JOIN daily_factors df
              ON df.ticker = u.ticker AND df.snapshot_date = ?
            {where_clause}
            ORDER BY u.amount DESC, u.turnover_ratio DESC
            LIMIT ?
            OFFSET ?
        """
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, [snapshot_date, *params, universe_filter.limit, universe_filter.offset]).fetchall()
        return rows

    def list_candidates_missing_financials(self, universe_filter: UniverseFilter) -> list[sqlite3.Row]:
        conditions = []
        params: list[object] = []
        if universe_filter.exclude_st:
            conditions.append("u.is_st = 0")
        if universe_filter.exclude_suspended:
            conditions.append("u.is_suspended = 0")
        if universe_filter.min_amount > 0:
            conditions.append("u.amount >= ?")
            params.append(universe_filter.min_amount)
        if universe_filter.exclude_boards:
            placeholders = ", ".join("?" for _ in universe_filter.exclude_boards)
            conditions.append(f"u.board NOT IN ({placeholders})")
            params.extend(universe_filter.exclude_boards)
        conditions.append(self._financial_missing_condition_sql("fp"))
        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"""
            SELECT u.ticker, u.code, u.name, u.exchange, u.board, u.latest_price, u.change_percent,
                   u.turnover_ratio, u.amount, u.is_st, u.is_suspended, u.source, u.synced_at
            FROM universe_stocks u
            LEFT JOIN financial_profiles fp ON fp.ticker = u.ticker
            {where_clause}
            ORDER BY u.amount DESC, u.turnover_ratio DESC
            LIMIT ?
            OFFSET ?
        """
        params.extend([universe_filter.limit, universe_filter.offset])
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        return rows

    def get_coverage_summary(self, snapshot_date: str, universe_filter: UniverseFilter) -> dict[str, int]:
        conditions = []
        params: list[object] = []
        if universe_filter.exclude_st:
            conditions.append("u.is_st = 0")
        if universe_filter.exclude_suspended:
            conditions.append("u.is_suspended = 0")
        if universe_filter.min_amount > 0:
            conditions.append("u.amount >= ?")
            params.append(universe_filter.min_amount)
        if universe_filter.exclude_boards:
            placeholders = ", ".join("?" for _ in universe_filter.exclude_boards)
            conditions.append(f"u.board NOT IN ({placeholders})")
            params.extend(universe_filter.exclude_boards)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM universe_stocks u {where_clause}",
                params,
            ).fetchone()[0]
            industries = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM universe_stocks u
                JOIN stock_industries si ON si.ticker = u.ticker
                {where_clause}
                """,
                params,
            ).fetchone()[0]
            factors = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM universe_stocks u
                JOIN daily_factors df ON df.ticker = u.ticker AND df.snapshot_date = ?
                {where_clause}
                """,
                [snapshot_date, *params],
            ).fetchone()[0]
            financials = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM universe_stocks u
                JOIN financial_profiles fp ON fp.ticker = u.ticker
                AND ({self._financial_ready_sql("fp")}) = 1
                {where_clause}
                """,
                params,
            ).fetchone()[0]
            ready = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM universe_stocks u
                JOIN stock_industries si ON si.ticker = u.ticker
                JOIN financial_profiles fp ON fp.ticker = u.ticker
                AND ({self._financial_ready_sql("fp")}) = 1
                JOIN daily_factors df ON df.ticker = u.ticker AND df.snapshot_date = ?
                {where_clause}
                """,
                [snapshot_date, *params],
            ).fetchone()[0]
        return {
            "total_candidates": int(total),
            "industry_covered": int(industries),
            "factor_covered": int(factors),
            "financial_covered": int(financials),
            "ready_candidates": int(ready),
            "fallback_candidates": int(total - ready),
        }

    @staticmethod
    def _save_theme_score_inputs(conn: sqlite3.Connection, run_id: int, theme_scores: list, created_at: str) -> None:
        rows = []
        for item in theme_scores:
            try:
                detail = json.loads(str(getattr(item, "detail_json", "{}")))
            except json.JSONDecodeError:
                detail = {}
            rows.append(
                (
                    run_id,
                    item.theme_name,
                    int(detail.get("member_count", 0) or 0),
                    detail.get("ready_ratio"),
                    detail.get("valuation_median"),
                    detail.get("policy_recent_strength"),
                    int(detail.get("history_count", 0) or 0),
                    detail.get("history_avg_return"),
                    detail.get("history_win_rate"),
                    detail.get("stage_ratio"),
                    item.performance_source,
                    json.dumps(detail, ensure_ascii=False),
                    created_at,
                )
            )
        if rows:
            conn.executemany(
                """
                INSERT INTO theme_score_inputs (
                    run_id, theme_name, member_count, ready_ratio, valuation_median, policy_recent_strength,
                    history_count, history_avg_return, history_win_rate, stage_ratio, performance_source,
                    detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    @staticmethod
    def _save_weekly_pool_changes(
        conn: sqlite3.Connection,
        run_id: int,
        previous_summary: dict[str, object] | None,
        previous_rows: list[dict],
        current_rows: list[dict],
        created_at: str,
    ) -> None:
        previous_map = {str(row["ticker"]): row for row in previous_rows}
        current_map = {str(row["ticker"]): row for row in current_rows}
        previous_run_id = int(previous_summary["id"]) if previous_summary else None
        changes = []
        for ticker, row in current_map.items():
            prev = previous_map.get(ticker)
            if prev is None:
                changes.append(
                    (
                        run_id,
                        previous_run_id,
                        ticker,
                        str(row.get("name", "")),
                        "added",
                        None,
                        str(row.get("prefilter_theme", "")),
                        None,
                        str(row.get("prefilter_bucket", "")),
                        f"新增入池: {row.get('prefilter_source', '') or row.get('theme_source', '')}",
                        created_at,
                    )
                )
            elif (
                str(prev.get("prefilter_theme", "")) != str(row.get("prefilter_theme", ""))
                or str(prev.get("prefilter_bucket", "")) != str(row.get("prefilter_bucket", ""))
            ):
                changes.append(
                    (
                        run_id,
                        previous_run_id,
                        ticker,
                        str(row.get("name", "")),
                        "updated",
                        str(prev.get("prefilter_theme", "")),
                        str(row.get("prefilter_theme", "")),
                        str(prev.get("prefilter_bucket", "")),
                        str(row.get("prefilter_bucket", "")),
                        "池内主题或分层发生变化",
                        created_at,
                    )
                )
        for ticker, row in previous_map.items():
            if ticker in current_map:
                continue
            changes.append(
                (
                    run_id,
                    previous_run_id,
                    ticker,
                    str(row.get("name", "")),
                    "removed",
                    str(row.get("prefilter_theme", "")),
                    None,
                    str(row.get("prefilter_bucket", "")),
                    None,
                    "从周度池移除",
                    created_at,
                )
            )
        if changes:
            conn.executemany(
                """
                INSERT INTO weekly_pool_changes (
                    run_id, previous_run_id, ticker, name, change_type, from_theme, to_theme, from_bucket, to_bucket, reason_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                changes,
            )

    @staticmethod
    def _save_candidate_decisions(conn: sqlite3.Connection, run_id: int, decisions: list[dict], rule_version_snapshot: str) -> None:
        if not decisions:
            return
        created_at = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            """
            INSERT INTO candidate_decisions (
                run_id, ticker, name, decision_stage, decision, reason_code, reason_text, theme_name, theme_bucket, total_score, stage, rule_version_snapshot, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    str(item.get("ticker", "")),
                    str(item.get("name", "")),
                    str(item.get("decision_stage", "")),
                    str(item.get("decision", "")),
                    str(item.get("reason_code", "")),
                    str(item.get("reason_text", "")),
                    str(item.get("theme_name", "")),
                    str(item.get("theme_bucket", "")),
                    float(item.get("total_score", 0.0)),
                    str(item.get("stage", "")),
                    str(item.get("rule_version_snapshot", rule_version_snapshot)),
                    created_at,
                )
                for item in decisions
            ],
        )

    @staticmethod
    def _save_stock_pool_lifecycle(
        conn: sqlite3.Connection,
        run_id: int,
        prefilter_week: str,
        previous_rows: list[dict],
        current_rows: list[dict],
        created_at: str,
    ) -> None:
        previous_map = {str(row["ticker"]): row for row in previous_rows}
        current_map = {str(row["ticker"]): row for row in current_rows}
        lifecycle_rows = conn.execute(
            """
            SELECT ticker, entry_count, consecutive_runs, in_pool
            FROM stock_pool_lifecycle
            WHERE id IN (
                SELECT MAX(id)
                FROM stock_pool_lifecycle
                GROUP BY ticker
            )
            """
        ).fetchall()
        lifecycle_map = {str(row[0]): {"entry_count": int(row[1]), "consecutive_runs": int(row[2]), "in_pool": int(row[3])} for row in lifecycle_rows}
        writes = []
        for ticker, row in current_map.items():
            previous = previous_map.get(ticker)
            lifecycle = lifecycle_map.get(ticker, {"entry_count": 0, "consecutive_runs": 0, "in_pool": 0})
            is_new_entry = previous is None or lifecycle["in_pool"] == 0
            writes.append(
                (
                    run_id,
                    prefilter_week,
                    ticker,
                    str(row.get("name", "")),
                    str(row.get("prefilter_theme", "")),
                    str(row.get("prefilter_bucket", "")),
                    "entered" if is_new_entry else "retained",
                    lifecycle["entry_count"] + 1 if is_new_entry else lifecycle["entry_count"],
                    1 if is_new_entry else lifecycle["consecutive_runs"] + 1,
                    1,
                    None,
                    created_at,
                )
            )
        for ticker, row in previous_map.items():
            if ticker in current_map:
                continue
            lifecycle = lifecycle_map.get(ticker, {"entry_count": 1, "consecutive_runs": 1})
            writes.append(
                (
                    run_id,
                    prefilter_week,
                    ticker,
                    str(row.get("name", "")),
                    str(row.get("prefilter_theme", "")),
                    str(row.get("prefilter_bucket", "")),
                    "removed",
                    lifecycle["entry_count"],
                    0,
                    0,
                    "从周度池移除",
                    created_at,
                )
            )
        if writes:
            conn.executemany(
                """
                INSERT INTO stock_pool_lifecycle (
                    run_id, prefilter_week, ticker, name, theme_name, bucket, lifecycle_status,
                    entry_count, consecutive_runs, in_pool, exit_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                writes,
            )

    @staticmethod
    def _save_theme_lifecycle(
        conn: sqlite3.Connection,
        run_id: int,
        prefilter_week: str,
        theme_scores: list,
        pool_members: list[dict],
        created_at: str,
    ) -> None:
        latest_rows = conn.execute(
            """
            SELECT theme_name, consecutive_active_runs
            FROM theme_lifecycle
            WHERE id IN (
                SELECT MAX(id)
                FROM theme_lifecycle
                GROUP BY theme_name
            )
            """
        ).fetchall()
        previous_map = {str(row[0]): int(row[1]) for row in latest_rows}
        member_count_map: dict[str, int] = {}
        wildcard_count_map: dict[str, int] = {}
        for row in pool_members:
            theme_name = str(row.get("prefilter_theme", "")).strip()
            if not theme_name:
                continue
            member_count_map[theme_name] = member_count_map.get(theme_name, 0) + 1
            if str(row.get("prefilter_bucket", "")) == "wildcard":
                wildcard_count_map[theme_name] = wildcard_count_map.get(theme_name, 0) + 1
        writes = []
        for item in theme_scores:
            try:
                detail = json.loads(str(getattr(item, "detail_json", "{}")))
            except json.JSONDecodeError:
                detail = {}
            previous_runs = previous_map.get(item.theme_name, 0)
            writes.append(
                (
                    run_id,
                    prefilter_week,
                    item.theme_name,
                    int(item.selected),
                    int(item.selected),
                    item.total_score,
                    member_count_map.get(item.theme_name, int(detail.get("member_count", 0) or 0)),
                    detail.get("ready_ratio"),
                    wildcard_count_map.get(item.theme_name, 0),
                    0,
                    int(detail.get("history_count", 0) or 0),
                    detail.get("history_avg_return"),
                    detail.get("history_win_rate"),
                    (previous_runs + 1) if item.selected else 0,
                    item.performance_source,
                    json.dumps(detail, ensure_ascii=False),
                    created_at,
                )
            )
        if writes:
            conn.executemany(
                """
                INSERT INTO theme_lifecycle (
                    run_id, prefilter_week, theme_name, is_active, selected, theme_score,
                    member_count, ready_ratio, wildcard_count, recommendation_count, history_count,
                    avg_return_5d, win_rate_5d, consecutive_active_runs, performance_source, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                writes,
            )

    @staticmethod
    def _update_theme_lifecycle_recommendations(conn: sqlite3.Connection, recommendations: list[Recommendation]) -> None:
        counts: dict[tuple[str, str], int] = {}
        for recommendation in recommendations:
            prefilter_week = str(recommendation.stock.prefilter_week or "").strip()
            theme_name = str(recommendation.stock.prefilter_theme or recommendation.stock.theme_name or "").strip()
            if not prefilter_week or not theme_name:
                continue
            key = (prefilter_week, theme_name)
            counts[key] = counts.get(key, 0) + 1
        for (prefilter_week, theme_name), count in counts.items():
            conn.execute(
                """
                UPDATE theme_lifecycle
                SET recommendation_count = COALESCE(recommendation_count, 0) + ?
                WHERE id = (
                    SELECT id
                    FROM theme_lifecycle
                    WHERE prefilter_week = ? AND theme_name = ?
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (count, prefilter_week, theme_name),
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        if column_name not in columns:
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    @staticmethod
    def _insert_recommendation(
        conn: sqlite3.Connection,
        run_id: int,
        rank_no: int,
        recommendation: Recommendation,
    ) -> int:
        cursor = conn.execute(
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
                rank_no,
                recommendation.stock.ticker,
                recommendation.stock.name,
                recommendation.stock.sector,
                recommendation.stage,
                recommendation.total_score,
                recommendation.stock.last_price,
                recommendation.stock.fundamental_source,
                int(recommendation.stock.industry_ready),
                int(recommendation.stock.financial_ready),
                int(recommendation.stock.factor_ready),
                int(recommendation.stock.ready_pool),
                recommendation.stock.theme_name,
                recommendation.stock.theme_bucket,
                recommendation.stock.theme_source,
                recommendation.stock.theme_strength,
                recommendation.stock.router_mode,
                recommendation.stock.prefilter_week,
                recommendation.stock.prefilter_theme,
                recommendation.stock.prefilter_bucket,
                recommendation.stock.prefilter_score,
                recommendation.stock.policy_score,
                recommendation.stock.valuation_score,
                recommendation.stock.performance_score,
                recommendation.stock.prefilter_source,
                " | ".join(recommendation.reasons),
                " | ".join(recommendation.risks),
            ),
        )
        return int(cursor.lastrowid)
