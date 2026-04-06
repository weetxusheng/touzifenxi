CREATE TABLE IF NOT EXISTS research_runs (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL,
    data_source TEXT NOT NULL,
    universe_size INTEGER NOT NULL,
    dominant_style TEXT NOT NULL,
    style_confidence DOUBLE PRECISION NOT NULL,
    report_path TEXT,
    ready_pool_size INTEGER NOT NULL DEFAULT 0,
    fallback_pool_size INTEGER NOT NULL DEFAULT 0,
    coverage_ratio DOUBLE PRECISION NOT NULL DEFAULT 0,
    router_mode TEXT NOT NULL DEFAULT 'fallback',
    active_theme_count INTEGER NOT NULL DEFAULT 0,
    bypass_count INTEGER NOT NULL DEFAULT 0,
    rule_version_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS recommendations (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES research_runs(id),
    rank_no INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    sector TEXT NOT NULL,
    stage TEXT NOT NULL,
    total_score DOUBLE PRECISION NOT NULL,
    last_price DOUBLE PRECISION NOT NULL,
    fundamental_source TEXT NOT NULL,
    industry_ready INTEGER NOT NULL DEFAULT 0,
    financial_ready INTEGER NOT NULL DEFAULT 0,
    factor_ready INTEGER NOT NULL DEFAULT 0,
    ready_pool INTEGER NOT NULL DEFAULT 0,
    theme_name TEXT NOT NULL DEFAULT '',
    theme_bucket TEXT NOT NULL DEFAULT 'fallback',
    theme_source TEXT NOT NULL DEFAULT '',
    theme_strength DOUBLE PRECISION NOT NULL DEFAULT 0,
    router_mode TEXT NOT NULL DEFAULT 'fallback',
    prefilter_week TEXT NOT NULL DEFAULT '',
    prefilter_theme TEXT NOT NULL DEFAULT '',
    prefilter_bucket TEXT NOT NULL DEFAULT '',
    prefilter_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    policy_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    valuation_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    performance_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    prefilter_source TEXT NOT NULL DEFAULT '',
    reasons TEXT NOT NULL,
    risks TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_scores (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES recommendations(id),
    agent_name TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_stocks (
    ticker TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    board TEXT NOT NULL,
    latest_price DOUBLE PRECISION NOT NULL,
    change_percent DOUBLE PRECISION NOT NULL,
    turnover_ratio DOUBLE PRECISION NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    is_st INTEGER NOT NULL,
    is_suspended INTEGER NOT NULL,
    source TEXT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES research_runs(id),
    ticker TEXT NOT NULL,
    data_source TEXT NOT NULL,
    last_price DOUBLE PRECISION NOT NULL,
    momentum_20d DOUBLE PRECISION NOT NULL,
    momentum_60d DOUBLE PRECISION NOT NULL,
    relative_strength DOUBLE PRECISION NOT NULL,
    volume_trend DOUBLE PRECISION NOT NULL,
    turnover_trend DOUBLE PRECISION NOT NULL,
    drawdown_from_high DOUBLE PRECISION NOT NULL,
    volatility DOUBLE PRECISION NOT NULL,
    crowding DOUBLE PRECISION NOT NULL,
    valuation_percentile DOUBLE PRECISION NOT NULL,
    earnings_growth DOUBLE PRECISION NOT NULL,
    revenue_growth DOUBLE PRECISION NOT NULL,
    roe DOUBLE PRECISION NOT NULL,
    free_cashflow_margin DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_returns (
    recommendation_id BIGINT PRIMARY KEY REFERENCES recommendations(id),
    base_price DOUBLE PRECISION NOT NULL,
    horizon_1d DOUBLE PRECISION,
    horizon_5d DOUBLE PRECISION,
    horizon_20d DOUBLE PRECISION,
    horizon_60d DOUBLE PRECISION,
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS industry_dictionary (
    industry_name TEXT PRIMARY KEY,
    industry_code TEXT,
    source TEXT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_industries (
    ticker TEXT PRIMARY KEY,
    industry_standard TEXT NOT NULL,
    industry_code TEXT,
    sector_lv1 TEXT,
    sector_lv2 TEXT,
    sector_lv3 TEXT,
    sector_lv4 TEXT,
    source TEXT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_factors (
    snapshot_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    sector TEXT NOT NULL,
    style_tags TEXT NOT NULL,
    data_source TEXT NOT NULL,
    fundamental_source TEXT NOT NULL,
    last_price DOUBLE PRECISION NOT NULL,
    valuation_percentile DOUBLE PRECISION NOT NULL,
    earnings_growth DOUBLE PRECISION NOT NULL,
    revenue_growth DOUBLE PRECISION NOT NULL,
    roe DOUBLE PRECISION NOT NULL,
    free_cashflow_margin DOUBLE PRECISION NOT NULL,
    momentum_20d DOUBLE PRECISION NOT NULL,
    momentum_60d DOUBLE PRECISION NOT NULL,
    relative_strength DOUBLE PRECISION NOT NULL,
    volume_trend DOUBLE PRECISION NOT NULL,
    turnover_trend DOUBLE PRECISION NOT NULL,
    drawdown_from_high DOUBLE PRECISION NOT NULL,
    volatility DOUBLE PRECISION NOT NULL,
    crowding DOUBLE PRECISION NOT NULL,
    event_score DOUBLE PRECISION NOT NULL,
    ma20_gap DOUBLE PRECISION NOT NULL,
    ma60_gap DOUBLE PRECISION NOT NULL,
    price_above_ma20 INTEGER NOT NULL,
    price_above_ma60 INTEGER NOT NULL,
    ma20_slope DOUBLE PRECISION NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE TABLE IF NOT EXISTS financial_profiles (
    ticker TEXT PRIMARY KEY,
    earnings_growth DOUBLE PRECISION NOT NULL,
    revenue_growth DOUBLE PRECISION NOT NULL,
    roe DOUBLE PRECISION NOT NULL,
    free_cashflow_margin DOUBLE PRECISION NOT NULL,
    event_score DOUBLE PRECISION NOT NULL,
    valuation_percentile DOUBLE PRECISION NOT NULL,
    fundamental_source TEXT NOT NULL,
    report_period TEXT,
    synced_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_events (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES research_runs(id),
    event_date DATE NOT NULL,
    theme_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT,
    source_url TEXT,
    title TEXT NOT NULL,
    ticker TEXT,
    strength DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_assignments (
    run_id BIGINT NOT NULL REFERENCES research_runs(id),
    ticker TEXT NOT NULL,
    theme_name TEXT NOT NULL,
    theme_bucket TEXT NOT NULL,
    theme_source TEXT NOT NULL,
    theme_strength DOUBLE PRECISION NOT NULL,
    router_mode TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, ticker, theme_name, theme_bucket)
);

CREATE TABLE IF NOT EXISTS theme_prefilter_runs (
    id BIGSERIAL PRIMARY KEY,
    prefilter_week TEXT NOT NULL,
    built_at TIMESTAMPTZ NOT NULL,
    theme_count INTEGER NOT NULL,
    pool_size INTEGER NOT NULL,
    wildcard_count INTEGER NOT NULL,
    build_mode TEXT NOT NULL DEFAULT 'weekly',
    base_run_id BIGINT,
    refresh_added INTEGER NOT NULL DEFAULT 0,
    refresh_removed INTEGER NOT NULL DEFAULT 0,
    rule_version_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS theme_prefilter_scores (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES theme_prefilter_runs(id),
    theme_name TEXT NOT NULL,
    total_score DOUBLE PRECISION NOT NULL,
    policy_score DOUBLE PRECISION NOT NULL,
    valuation_score DOUBLE PRECISION NOT NULL,
    performance_score DOUBLE PRECISION NOT NULL,
    performance_source TEXT NOT NULL DEFAULT 'proxy',
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    selected INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_pool_members (
    run_id BIGINT NOT NULL REFERENCES theme_prefilter_runs(id),
    prefilter_week TEXT NOT NULL,
    ticker TEXT NOT NULL,
    prefilter_theme TEXT NOT NULL,
    prefilter_bucket TEXT NOT NULL,
    prefilter_score DOUBLE PRECISION NOT NULL,
    policy_score DOUBLE PRECISION NOT NULL,
    valuation_score DOUBLE PRECISION NOT NULL,
    performance_score DOUBLE PRECISION NOT NULL,
    prefilter_source TEXT NOT NULL,
    rank_no INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, ticker)
);

CREATE TABLE IF NOT EXISTS rule_versions (
    id BIGSERIAL PRIMARY KEY,
    scope TEXT NOT NULL,
    version TEXT NOT NULL,
    config_json JSONB NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_score_inputs (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES theme_prefilter_runs(id),
    theme_name TEXT NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    ready_ratio DOUBLE PRECISION,
    valuation_median DOUBLE PRECISION,
    policy_recent_strength DOUBLE PRECISION,
    history_count INTEGER NOT NULL DEFAULT 0,
    history_avg_return DOUBLE PRECISION,
    history_win_rate DOUBLE PRECISION,
    stage_ratio DOUBLE PRECISION,
    performance_source TEXT NOT NULL,
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_pool_changes (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES theme_prefilter_runs(id),
    previous_run_id BIGINT,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    change_type TEXT NOT NULL,
    from_theme TEXT,
    to_theme TEXT,
    from_bucket TEXT,
    to_bucket TEXT,
    reason_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_decisions (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES research_runs(id),
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    decision_stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_text TEXT NOT NULL,
    theme_name TEXT,
    theme_bucket TEXT,
    total_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    stage TEXT NOT NULL,
    rule_version_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_pool_lifecycle (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES theme_prefilter_runs(id),
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
    horizon_1d DOUBLE PRECISION,
    horizon_5d DOUBLE PRECISION,
    horizon_20d DOUBLE PRECISION,
    horizon_60d DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_lifecycle (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES theme_prefilter_runs(id),
    prefilter_week TEXT NOT NULL,
    theme_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    selected INTEGER NOT NULL DEFAULT 0,
    theme_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    member_count INTEGER NOT NULL DEFAULT 0,
    ready_ratio DOUBLE PRECISION,
    wildcard_count INTEGER NOT NULL DEFAULT 0,
    recommendation_count INTEGER NOT NULL DEFAULT 0,
    history_count INTEGER NOT NULL DEFAULT 0,
    avg_return_5d DOUBLE PRECISION,
    win_rate_5d DOUBLE PRECISION,
    consecutive_active_runs INTEGER NOT NULL DEFAULT 0,
    performance_source TEXT NOT NULL DEFAULT 'proxy',
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendations_run_rank ON recommendations(run_id, rank_no);
CREATE INDEX IF NOT EXISTS idx_research_runs_run_at ON research_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_theme_prefilter_runs_week_built ON theme_prefilter_runs(prefilter_week, built_at);
CREATE INDEX IF NOT EXISTS idx_weekly_pool_members_run_ticker ON weekly_pool_members(run_id, ticker);
CREATE INDEX IF NOT EXISTS idx_daily_factors_snapshot_ticker ON daily_factors(snapshot_date, ticker);
CREATE INDEX IF NOT EXISTS idx_theme_events_run_date_theme ON theme_events(run_id, event_date, theme_name);
CREATE INDEX IF NOT EXISTS idx_candidate_decisions_run_ticker_stage ON candidate_decisions(run_id, ticker, decision_stage);
CREATE INDEX IF NOT EXISTS idx_stock_pool_lifecycle_run_ticker ON stock_pool_lifecycle(run_id, ticker);
CREATE INDEX IF NOT EXISTS idx_theme_lifecycle_run_theme ON theme_lifecycle(run_id, theme_name);
