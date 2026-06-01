from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg

from .db import init_postgres_schema

TABLE_MIGRATION_ORDER = [
    "research_runs",
    "industry_dictionary",
    "universe_stocks",
    "financial_profiles",
    "stock_industries",
    "daily_factors",
    "sync_state",
    "rule_versions",
    "theme_prefilter_runs",
    "recommendations",
    "market_snapshots",
    "theme_events",
    "theme_assignments",
    "theme_prefilter_scores",
    "weekly_pool_members",
    "theme_score_inputs",
    "weekly_pool_changes",
    "candidate_decisions",
    "agent_scores",
    "recommendation_returns",
    "stock_pool_lifecycle",
    "theme_lifecycle",
]


SEQUENCE_TABLES = {
    "research_runs": "id",
    "recommendations": "id",
    "agent_scores": "id",
    "market_snapshots": "id",
    "industry_dictionary": None,
    "stock_industries": None,
    "daily_factors": None,
    "financial_profiles": None,
    "sync_state": None,
    "theme_events": "id",
    "theme_assignments": None,
    "theme_prefilter_runs": "id",
    "theme_prefilter_scores": "id",
    "weekly_pool_members": None,
    "rule_versions": "id",
    "theme_score_inputs": "id",
    "weekly_pool_changes": "id",
    "candidate_decisions": "id",
    "recommendation_returns": None,
    "universe_stocks": None,
    "stock_pool_lifecycle": "id",
    "theme_lifecycle": "id",
}


def _postgres_table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        return cur.fetchone() is not None


def _sqlite_table_columns(sqlite_conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = sqlite_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row[1]) for row in rows]


def _postgres_truncate_all(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
            """
        )
        tables = [row[0] for row in cur.fetchall()]
        if not tables:
            return
        joined = ", ".join(f'public."{name}"' for name in tables)
        cur.execute(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")


def _copy_table(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection, table_name: str) -> int:
    columns = _sqlite_table_columns(sqlite_conn, table_name)
    if not columns:
        return 0
    select_sql = f'SELECT {", ".join(columns)} FROM {table_name}'
    sqlite_rows = sqlite_conn.execute(select_sql).fetchall()
    if not sqlite_rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    insert_sql = f'INSERT INTO public."{table_name}" ({quoted_columns}) VALUES ({placeholders})'
    with pg_conn.cursor() as cur:
        cur.executemany(insert_sql, sqlite_rows)
    return len(sqlite_rows)


def _reset_sequences(pg_conn: psycopg.Connection) -> None:
    with pg_conn.cursor() as cur:
        for table_name, pk_column in SEQUENCE_TABLES.items():
            if not pk_column:
                continue
            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f'public.{table_name}', pk_column))
            sequence_row = cur.fetchone()
            if not sequence_row or not sequence_row[0]:
                continue
            sequence_name = str(sequence_row[0])
            cur.execute(f'SELECT COALESCE(MAX("{pk_column}"), 0) FROM public."{table_name}"')
            max_id = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT setval(%s, %s, %s)", (sequence_name, max_id if max_id > 0 else 1, max_id > 0))


def migrate_sqlite_to_postgres(sqlite_path: Path, postgres_url: str, reset_target: bool = True) -> dict[str, int]:
    init_postgres_schema(postgres_url)
    sqlite_conn = sqlite3.connect(sqlite_path)
    try:
        pg_conn = psycopg.connect(postgres_url)
        try:
            if reset_target:
                _postgres_truncate_all(pg_conn)
            counts: dict[str, int] = {}
            for table_name in TABLE_MIGRATION_ORDER:
                if not _postgres_table_exists(pg_conn, table_name):
                    continue
                counts[table_name] = _copy_table(sqlite_conn, pg_conn, table_name)
            _reset_sequences(pg_conn)
            pg_conn.commit()
            return counts
        finally:
            pg_conn.close()
    finally:
        sqlite_conn.close()
