"""SQLite storage for the local Python scheduler."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .tasks import ScheduleTask

DAY_MAP = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}


@dataclass(frozen=True)
class StoredTask:
    id: str
    group_id: str
    label: str
    command: str
    working_dir: str
    schedule_type: str
    time_of_day: str
    days_of_week: str
    enabled: bool
    next_run_at: str
    last_run_at: str
    last_exit_code: int | None
    last_error: str


@dataclass(frozen=True)
class StoredRun:
    id: int
    task_id: str
    started_at: str
    finished_at: str
    exit_code: int | None
    log_path: str
    error: str


class ScheduleStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schedule_tasks (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    command TEXT NOT NULL,
                    working_dir TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    days_of_week TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT NOT NULL DEFAULT '',
                    last_run_at TEXT NOT NULL DEFAULT '',
                    last_exit_code INTEGER,
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS schedule_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    exit_code INTEGER,
                    log_path TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def sync_defaults(self, tasks: tuple[ScheduleTask, ...], *, project_root: Path) -> None:
        now = datetime.now()
        with self.connect() as conn:
            for task in tasks:
                command = str((project_root / task.script).resolve())
                next_run_at = compute_next_run_at(
                    schedule_type=task.schedule_kind,
                    time_of_day=task.time,
                    days_of_week=",".join(task.days),
                    after=now,
                )
                existing = conn.execute("SELECT id FROM schedule_tasks WHERE id = ?", (task.id,)).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE schedule_tasks
                        SET group_id = ?, label = ?, command = ?, working_dir = ?,
                            schedule_type = ?, days_of_week = ?
                        WHERE id = ?
                        """,
                        (
                            task.group,
                            task.label,
                            command,
                            str(project_root.resolve()),
                            task.schedule_kind,
                            ",".join(task.days),
                            task.id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO schedule_tasks (
                            id, group_id, label, command, working_dir, schedule_type,
                            time_of_day, days_of_week, enabled, next_run_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            task.id,
                            task.group,
                            task.label,
                            command,
                            str(project_root.resolve()),
                            task.schedule_kind,
                            task.time,
                            ",".join(task.days),
                            next_run_at.isoformat(timespec="seconds"),
                        ),
                    )

    def list_tasks(self) -> list[StoredTask]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM schedule_tasks ORDER BY group_id, time_of_day, id").fetchall()
        return [_task_from_row(row) for row in rows]

    def get_task(self, task_id: str) -> StoredTask | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM schedule_tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None

    def due_tasks(self, *, now: datetime) -> list[StoredTask]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM schedule_tasks
                WHERE enabled = 1 AND next_run_at != '' AND next_run_at <= ?
                ORDER BY next_run_at, id
                """,
                (now.isoformat(timespec="seconds"),),
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def update_task(self, task_id: str, *, time_of_day: str, enabled: bool) -> None:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Unknown task: {task_id}")
        next_run_at = compute_next_run_at(
            schedule_type=task.schedule_type,
            time_of_day=time_of_day,
            days_of_week=task.days_of_week,
            after=datetime.now(),
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE schedule_tasks
                SET time_of_day = ?, enabled = ?, next_run_at = ?
                WHERE id = ?
                """,
                (time_of_day, 1 if enabled else 0, next_run_at.isoformat(timespec="seconds"), task_id),
            )

    def set_enabled(self, task_id: str, enabled: bool) -> None:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"Unknown task: {task_id}")
        next_run_at = compute_next_run_at(
            schedule_type=task.schedule_type,
            time_of_day=task.time_of_day,
            days_of_week=task.days_of_week,
            after=datetime.now(),
        )
        with self.connect() as conn:
            conn.execute(
                "UPDATE schedule_tasks SET enabled = ?, next_run_at = ? WHERE id = ?",
                (1 if enabled else 0, next_run_at.isoformat(timespec="seconds"), task_id),
            )

    def create_run(self, task_id: str, *, started_at: datetime, log_path: Path) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO schedule_runs (task_id, started_at, log_path) VALUES (?, ?, ?)",
                (task_id, started_at.isoformat(timespec="seconds"), str(log_path)),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        task_id: str,
        finished_at: datetime,
        exit_code: int | None,
        error: str = "",
    ) -> None:
        next_run_at = None
        task = self.get_task(task_id)
        if task:
            next_run_at = compute_next_run_at(
                schedule_type=task.schedule_type,
                time_of_day=task.time_of_day,
                days_of_week=task.days_of_week,
                after=finished_at + timedelta(seconds=1),
            )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE schedule_runs
                SET finished_at = ?, exit_code = ?, error = ?
                WHERE id = ?
                """,
                (finished_at.isoformat(timespec="seconds"), exit_code, error, run_id),
            )
            conn.execute(
                """
                UPDATE schedule_tasks
                SET last_run_at = ?, last_exit_code = ?, last_error = ?, next_run_at = ?
                WHERE id = ?
                """,
                (
                    finished_at.isoformat(timespec="seconds"),
                    exit_code,
                    error,
                    next_run_at.isoformat(timespec="seconds") if next_run_at else "",
                    task_id,
                ),
            )

    def list_runs(self, *, limit: int = 20) -> list[StoredRun]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]


def compute_next_run_at(
    *,
    schedule_type: str,
    time_of_day: str,
    days_of_week: str,
    after: datetime,
) -> datetime:
    hour_raw, minute_raw = time_of_day.split(":", 1)
    hour = int(hour_raw)
    minute = int(minute_raw)
    if schedule_type == "weekly":
        allowed = [DAY_MAP[day.strip().upper()] for day in days_of_week.split(",") if day.strip()]
        if not allowed:
            allowed = [after.weekday()]
        for offset in range(0, 8):
            candidate_date = (after + timedelta(days=offset)).date()
            candidate = datetime.combine(candidate_date, datetime.min.time()).replace(hour=hour, minute=minute)
            if candidate.weekday() in allowed and candidate > after:
                return candidate
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate


def _task_from_row(row: sqlite3.Row) -> StoredTask:
    return StoredTask(
        id=str(row["id"]),
        group_id=str(row["group_id"]),
        label=str(row["label"]),
        command=str(row["command"]),
        working_dir=str(row["working_dir"]),
        schedule_type=str(row["schedule_type"]),
        time_of_day=str(row["time_of_day"]),
        days_of_week=str(row["days_of_week"]),
        enabled=bool(row["enabled"]),
        next_run_at=str(row["next_run_at"]),
        last_run_at=str(row["last_run_at"]),
        last_exit_code=row["last_exit_code"],
        last_error=str(row["last_error"]),
    )


def _run_from_row(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        id=int(row["id"]),
        task_id=str(row["task_id"]),
        started_at=str(row["started_at"]),
        finished_at=str(row["finished_at"]),
        exit_code=row["exit_code"],
        log_path=str(row["log_path"]),
        error=str(row["error"]),
    )
