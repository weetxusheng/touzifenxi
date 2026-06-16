"""Task definitions for the local schedule administration service."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

ScheduleKind = Literal["daily", "weekly"]


@dataclass(frozen=True)
class ScheduleTask:
    id: str
    group: str
    label: str
    task_name: str
    script: Path
    schedule_kind: ScheduleKind
    time: str
    days: tuple[str, ...] = ()
    log_glob: str = ""

    @property
    def relative_script(self) -> str:
        return self.script.as_posix()


@dataclass(frozen=True)
class TaskGroup:
    id: str
    label: str
    task_ids: tuple[str, ...]


DEFAULT_GROUPS: tuple[TaskGroup, ...] = (
    TaskGroup("c114", "C114 日报", ("c114_run", "c114_mail")),
    TaskGroup("chip", "Chip 半导体日报", ("chip_run", "chip_mail")),
    TaskGroup("kr36_daily", "36Kr 日报", ("kr36_daily_run", "kr36_daily_mail")),
    TaskGroup("kr36_weekly_wed", "36Kr 周报（周三）", ("kr36_wed_run", "kr36_wed_mail")),
    TaskGroup("kr36_weekly_sat", "36Kr 周报（周六）", ("kr36_sat_run", "kr36_sat_mail")),
    TaskGroup("feedcore", "FeedCore 美国国际新闻", ("feedcore_run", "feedcore_mail")),
)


DEFAULT_TASKS: tuple[ScheduleTask, ...] = (
    ScheduleTask(
        id="c114_run",
        group="c114",
        label="C114 运行",
        task_name="touzifenxi-c114-daily-run",
        script=Path("scripts/c114/run_c114_daily_brief_no_email_windows.bat"),
        schedule_kind="daily",
        time="17:40",
        log_glob="logs/c114/*.log",
    ),
    ScheduleTask(
        id="c114_mail",
        group="c114",
        label="C114 发信",
        task_name="touzifenxi-c114-daily-mail",
        script=Path("scripts/c114/send_c114_daily_brief_email_t1_gate_windows.bat"),
        schedule_kind="daily",
        time="19:20",
        log_glob="logs/c114/*.log",
    ),
    ScheduleTask(
        id="chip_run",
        group="chip",
        label="Chip 运行",
        task_name="touzifenxi-chip-daily-run",
        script=Path("scripts/chip/run_chip_daily_brief_no_email_windows.bat"),
        schedule_kind="daily",
        time="17:40",
        log_glob="logs/chip/*.log",
    ),
    ScheduleTask(
        id="chip_mail",
        group="chip",
        label="Chip 发信",
        task_name="touzifenxi-chip-daily-mail",
        script=Path("scripts/chip/send_chip_daily_brief_email_t1_gate_windows.bat"),
        schedule_kind="daily",
        time="19:20",
        log_glob="logs/chip/*.log",
    ),
    ScheduleTask(
        id="kr36_daily_run",
        group="kr36_daily",
        label="36Kr 日报运行",
        task_name="touzifenxi-kr36-daily-run",
        script=Path("scripts/kr36/run_kr36_daily_brief_no_email_windows.bat"),
        schedule_kind="daily",
        time="17:40",
        log_glob="logs/kr36/*.log",
    ),
    ScheduleTask(
        id="kr36_daily_mail",
        group="kr36_daily",
        label="36Kr 日报发信",
        task_name="touzifenxi-kr36-daily-mail",
        script=Path("scripts/kr36/send_kr36_daily_brief_email_t1_gate_windows.bat"),
        schedule_kind="daily",
        time="19:20",
        log_glob="logs/kr36/*.log",
    ),
    ScheduleTask(
        id="kr36_wed_run",
        group="kr36_weekly_wed",
        label="36Kr 周三运行",
        task_name="touzifenxi-kr36-wed-run",
        script=Path("scripts/kr36/run_kr36_daily_brief_no_email_windows.bat"),
        schedule_kind="weekly",
        days=("WED",),
        time="13:30",
        log_glob="logs/kr36/*.log",
    ),
    ScheduleTask(
        id="kr36_wed_mail",
        group="kr36_weekly_wed",
        label="36Kr 周三发信",
        task_name="touzifenxi-kr36-wed-mail",
        script=Path("scripts/kr36/send_kr36_daily_brief_email_t1_gate_windows.bat"),
        schedule_kind="weekly",
        days=("WED",),
        time="15:00",
        log_glob="logs/kr36/*.log",
    ),
    ScheduleTask(
        id="kr36_sat_run",
        group="kr36_weekly_sat",
        label="36Kr 周六运行",
        task_name="touzifenxi-kr36-sat-run",
        script=Path("scripts/kr36/run_kr36_daily_brief_no_email_windows.bat"),
        schedule_kind="weekly",
        days=("SAT",),
        time="10:00",
        log_glob="logs/kr36/*.log",
    ),
    ScheduleTask(
        id="kr36_sat_mail",
        group="kr36_weekly_sat",
        label="36Kr 周六发信",
        task_name="touzifenxi-kr36-sat-mail",
        script=Path("scripts/kr36/send_kr36_daily_brief_email_t1_gate_windows.bat"),
        schedule_kind="weekly",
        days=("SAT",),
        time="11:30",
        log_glob="logs/kr36/*.log",
    ),
    ScheduleTask(
        id="feedcore_run",
        group="feedcore",
        label="FeedCore 运行",
        task_name="touzifenxi-feedcore-us-news",
        script=Path("scripts/feedcore/run_feedcore_us_news_windows.bat"),
        schedule_kind="daily",
        time="17:40",
        log_glob="logs/feedcore/*.log",
    ),
    ScheduleTask(
        id="feedcore_mail",
        group="feedcore",
        label="FeedCore 发信",
        task_name="touzifenxi-feedcore-daily-mail",
        script=Path("scripts/feedcore/send_feedcore_latest_brief_email_windows.bat"),
        schedule_kind="daily",
        time="19:20",
        log_glob="logs/feedcore/*.log",
    ),
)


def load_tasks(project_root: Path) -> tuple[ScheduleTask, ...]:
    """Load task definitions with optional local overrides."""

    config_path = project_root / "config" / "schedule.local.json"
    overrides: dict[str, dict[str, Any]] = {}
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        tasks_raw = raw.get("tasks", {}) if isinstance(raw, dict) else {}
        overrides = {str(key): value for key, value in tasks_raw.items() if isinstance(value, dict)}

    tasks: list[ScheduleTask] = []
    for task in DEFAULT_TASKS:
        override = overrides.get(task.id, {})
        fields: dict[str, Any] = {}
        if "time" in override:
            fields["time"] = str(override["time"])
        if "task_name" in override:
            fields["task_name"] = str(override["task_name"])
        if "script" in override:
            fields["script"] = Path(str(override["script"]))
        if "days" in override:
            days = override["days"]
            if isinstance(days, list):
                fields["days"] = tuple(str(day).upper() for day in days)
        tasks.append(replace(task, **fields))
    return tuple(tasks)


def task_map(tasks: tuple[ScheduleTask, ...]) -> dict[str, ScheduleTask]:
    return {task.id: task for task in tasks}


def group_map(groups: tuple[TaskGroup, ...] = DEFAULT_GROUPS) -> dict[str, TaskGroup]:
    return {group.id: group for group in groups}
