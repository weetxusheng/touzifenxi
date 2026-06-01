"""Combined database utilities: profile resolution, runtime connections, and schema models."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List

import psycopg
from psycopg.rows import tuple_row

POSTGRES_SCHEMA_SQL_PATH = Path(__file__).resolve().parents[3] / "docs" / "postgresql_schema.sql"

UPSERT_CONFLICT_COLUMNS: dict[str, tuple[str, ...]] = {
    "recommendation_returns": ("recommendation_id",),
    "universe_stocks": ("ticker",),
    "theme_assignments": ("run_id", "ticker", "theme_name", "theme_bucket"),
    "daily_factors": ("snapshot_date", "ticker"),
    "sync_state": ("state_key",),
    "industry_dictionary": ("industry_name",),
    "stock_industries": ("ticker",),
    "financial_profiles": ("ticker",),
}

INSERT_RETURNING_ID_TABLES = {
    "research_runs",
    "recommendations",
    "theme_prefilter_runs",
}


# ---------------------------------------------------------------------------
# Database profile resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DatabaseProfile:
    configured_url: str | None
    configured_backend: str
    active_backend: str
    sqlite_url: str

    @property
    def is_sqlite_runtime(self) -> bool:
        return self.active_backend == "sqlite"

    @property
    def is_postgres_configured(self) -> bool:
        return self.configured_backend == "postgresql"


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def detect_backend(database_url: str | None) -> str:
    value = str(database_url or "").strip().lower()
    if value.startswith("postgresql://") or value.startswith("postgres://"):
        return "postgresql"
    return "sqlite"


def resolve_database_profile(db_path: Path, database_url: str | None = None) -> DatabaseProfile:
    configured_url = database_url or os.getenv("TOUZIFENXI_DATABASE_URL")
    configured_backend = detect_backend(configured_url)
    active_backend = configured_backend if configured_url else "sqlite"
    return DatabaseProfile(
        configured_url=configured_url,
        configured_backend=configured_backend,
        active_backend=active_backend,
        sqlite_url=_sqlite_url(db_path),
    )


def create_sqlalchemy_engine(database_url: str):
    try:
        from sqlalchemy import create_engine
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime deps.
        raise RuntimeError("sqlalchemy 未安装，无法初始化 PostgreSQL schema。") from exc
    normalized_url = database_url
    if normalized_url.startswith("postgresql://"):
        normalized_url = normalized_url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif normalized_url.startswith("postgres://"):
        normalized_url = normalized_url.replace("postgres://", "postgresql+psycopg://", 1)
    return create_engine(normalized_url, future=True)


def init_postgres_schema(database_url: str) -> None:
    if not POSTGRES_SCHEMA_SQL_PATH.exists():
        raise RuntimeError(f"PostgreSQL schema file missing: {POSTGRES_SCHEMA_SQL_PATH}")
    engine = create_sqlalchemy_engine(database_url)
    schema_sql = POSTGRES_SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    statements = [item.strip() for item in schema_sql.split(";\n") if item.strip()]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


def serialize_rule_snapshot(rule_snapshot: dict[str, Any]) -> str:
    import json

    return json.dumps(rule_snapshot, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# PostgreSQL compat layer (db_runtime)
# ---------------------------------------------------------------------------

class CompatRow(Mapping[str, Any]):
    def __init__(self, values: Sequence[Any], column_order: Sequence[str]):
        self._values = list(values)
        self._column_order = list(column_order)
        self._data = {column: self._values[index] for index, column in enumerate(self._column_order)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._data[self._column_order[key]]
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class CompatCursor:
    def __init__(self, cursor, lastrowid: int | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        return _wrap_row(row, self._cursor.description)

    def fetchall(self):
        return [_wrap_row(row, self._cursor.description) for row in self._cursor.fetchall()]


def _wrap_row(row: Any, description: Any):
    if row is None:
        return None
    columns = [item.name if hasattr(item, "name") else item[0] for item in (description or [])]
    return CompatRow(row, columns)


def _rewrite_insert_or_replace(sql: str) -> str:
    match = re.match(
        r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*$",
        sql.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return sql
    table_name = match.group(1)
    columns = [item.strip() for item in match.group(2).split(",")]
    values_expr = match.group(3).strip()
    conflict_columns = UPSERT_CONFLICT_COLUMNS.get(table_name)
    if not conflict_columns:
        raise RuntimeError(f"PostgreSQL upsert key missing for table: {table_name}")
    update_columns = [column for column in columns if column not in conflict_columns]
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    return (
        f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values_expr}) "
        f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {updates}"
    )


def _normalize_sql(sql: str) -> tuple[str, str | None]:
    normalized = _rewrite_insert_or_replace(sql)
    match = re.match(r"^\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", normalized, re.IGNORECASE)
    table_name = match.group(1) if match else None
    normalized = normalized.replace("?", "%s")
    if table_name in INSERT_RETURNING_ID_TABLES and "RETURNING" not in normalized.upper():
        normalized = f"{normalized.rstrip()} RETURNING id"
    return normalized, table_name


class PostgresCompatConnection:
    def __init__(self, raw: psycopg.Connection[Any]):
        self._raw = raw

    def __enter__(self) -> "PostgresCompatConnection":
        self._raw.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._raw.__exit__(exc_type, exc, tb)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> CompatCursor:
        normalized, table_name = _normalize_sql(sql)
        cursor = self._raw.cursor(row_factory=tuple_row)
        cursor.execute(normalized, list(params or []))
        lastrowid = None
        if table_name in INSERT_RETURNING_ID_TABLES:
            row = cursor.fetchone()
            if row:
                lastrowid = int(row[0])
        return CompatCursor(cursor, lastrowid=lastrowid)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> CompatCursor:
        normalized, _ = _normalize_sql(sql)
        cursor = self._raw.cursor(row_factory=tuple_row)
        cursor.executemany(normalized, list(seq_of_params))
        return CompatCursor(cursor, lastrowid=None)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    @property
    def row_factory(self):
        return tuple_row

    @row_factory.setter
    def row_factory(self, value) -> None:
        return

    def __getattr__(self, item: str):
        return getattr(self._raw, item)


def connect_sqlite(db_path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def connect_postgres(database_url: str) -> PostgresCompatConnection:
    raw = psycopg.connect(database_url, row_factory=tuple_row)
    return PostgresCompatConnection(raw)


# ---------------------------------------------------------------------------
# Schema record models (schema.py)
# ---------------------------------------------------------------------------

from ..models import Recommendation, StyleView


@dataclass
class ResearchRunRecord:
    run_at: datetime
    data_source: str
    universe_size: int
    dominant_style: str
    style_confidence: float
    report_path: str | None


@dataclass
class PersistedRun:
    run_id: int
    style_view: StyleView
    recommendations: List[Recommendation]
    data_source: str
    universe_size: int
    report_path: str | None
