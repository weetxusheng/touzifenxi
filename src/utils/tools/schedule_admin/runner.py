"""Subprocess runner for Python-managed scheduled tasks."""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path

from .store import ScheduleStore, StoredTask


class TaskRunner:
    def __init__(self, *, store: ScheduleStore, project_root: Path):
        self.store = store
        self.project_root = project_root
        self.log_dir = project_root / "logs" / "scheduler"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._running

    def running_task_ids(self) -> set[str]:
        with self._lock:
            return set(self._running)

    def start_task(self, task: StoredTask) -> bool:
        with self._lock:
            if task.id in self._running:
                return False
            self._running.add(task.id)
        thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        thread.start()
        return True

    def _run_task(self, task: StoredTask) -> None:
        started_at = datetime.now()
        log_path = self.log_dir / f"{task.id}_{started_at.strftime('%Y%m%d_%H%M%S')}.log"
        run_id = self.store.create_run(task.id, started_at=started_at, log_path=log_path)
        exit_code: int | None = None
        error = ""
        try:
            Path(task.working_dir).mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
                log_file.write(f"[scheduler] task_id={task.id}\n")
                log_file.write(f"[scheduler] command={task.command}\n")
                log_file.write(f"[scheduler] cwd={task.working_dir}\n")
                log_file.write(f"[scheduler] started_at={started_at.isoformat(timespec='seconds')}\n")
                log_file.flush()
                completed = subprocess.run(
                    task.command,
                    cwd=task.working_dir,
                    shell=True,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                exit_code = completed.returncode
                log_file.write(f"\n[scheduler] finished_at={datetime.now().isoformat(timespec='seconds')}\n")
                log_file.write(f"[scheduler] exit_code={exit_code}\n")
        except Exception as exc:
            error = str(exc)
        finally:
            self.store.finish_run(
                run_id,
                task_id=task.id,
                finished_at=datetime.now(),
                exit_code=exit_code,
                error=error,
            )
            with self._lock:
                self._running.discard(task.id)
