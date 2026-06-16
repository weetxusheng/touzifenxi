"""In-process scheduler loop for schedule-admin."""

from __future__ import annotations

import threading
from datetime import datetime

from .runner import TaskRunner
from .store import ScheduleStore


class PythonScheduler:
    def __init__(self, *, store: ScheduleStore, runner: TaskRunner, poll_seconds: int = 30):
        self.store = store
        self.runner = runner
        self.poll_seconds = max(5, int(poll_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(target=self._loop, name="schedule-admin-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def tick(self) -> int:
        started = 0
        for task in self.store.due_tasks(now=datetime.now()):
            if self.runner.start_task(task):
                started += 1
        return started

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"[schedule-admin] scheduler tick failed: {exc}")
            self._stop.wait(self.poll_seconds)
