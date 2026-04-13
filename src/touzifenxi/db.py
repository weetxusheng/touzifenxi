from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POSTGRES_SCHEMA_SQL_PATH = Path(__file__).resolve().parents[2] / "docs" / "postgresql_schema.sql"


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
