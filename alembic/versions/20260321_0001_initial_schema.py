"""initial postgresql schema"""

from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "20260321_0001"
down_revision = None
branch_labels = None
depends_on = None


def _schema_statements() -> list[str]:
    schema_path = Path(__file__).resolve().parents[2] / "docs" / "postgresql_schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    return [item.strip() for item in schema_sql.split(";\n") if item.strip()]


def upgrade() -> None:
    for statement in _schema_statements():
        op.execute(statement)


def downgrade() -> None:
    tables = [
        "theme_lifecycle",
        "stock_pool_lifecycle",
        "candidate_decisions",
        "weekly_pool_changes",
        "theme_score_inputs",
        "rule_versions",
        "weekly_pool_members",
        "theme_prefilter_scores",
        "theme_prefilter_runs",
        "theme_assignments",
        "theme_events",
        "sync_state",
        "financial_profiles",
        "daily_factors",
        "stock_industries",
        "industry_dictionary",
        "recommendation_returns",
        "market_snapshots",
        "universe_stocks",
        "agent_scores",
        "recommendations",
        "research_runs",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
