from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def make_run_id(prefix: str = "feedcore") -> str:
    cn = timezone(timedelta(hours=8))
    return f"{prefix}_{datetime.now(cn).strftime('%Y%m%d%H%M%S')}"


@dataclass
class StepLog:
    name: str
    input_count: int = 0
    output_count: int = 0
    skipped_count: int = 0
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RunContext:
    output_dir: Path
    run_id: str
    run_dir: Path
    logs: list[StepLog] = field(default_factory=list)

    @classmethod
    def create(cls, output_dir: Path, run_id: str | None = None) -> "RunContext":
        rid = run_id or make_run_id()
        run_dir = output_dir / rid
        for child in (run_dir, run_dir / "checkpoints", run_dir / "logs"):
            child.mkdir(parents=True, exist_ok=True)
        return cls(output_dir=output_dir, run_id=rid, run_dir=run_dir)

    def write_json(self, filename: str, payload: Any) -> Path:
        path = self.run_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def log_step(
        self,
        name: str,
        *,
        input_count: int = 0,
        output_count: int = 0,
        skipped_count: int = 0,
        message: str = "",
    ) -> None:
        entry = StepLog(
            name=name,
            input_count=input_count,
            output_count=output_count,
            skipped_count=skipped_count,
            message=message,
        )
        self.logs.append(entry)
        self._append_workflow_log(entry)

    def write_execution_log(self) -> Path:
        path = self.run_dir / "execution_log.md"
        lines = [f"# Execution Log", "", f"- run_id={self.run_id}", ""]
        for item in self.logs:
            lines.extend(
                [
                    f"## {item.name}",
                    "",
                    (
                        f"- time={item.timestamp} | input={item.input_count} | "
                        f"output={item.output_count} | skipped={item.skipped_count}"
                    ),
                    f"- message={item.message}" if item.message else "- message=",
                    "",
                ]
            )
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path

    def _append_workflow_log(self, entry: StepLog) -> None:
        path = self.run_dir / "logs" / "workflow.log"
        line = (
            f"{entry.timestamp} | {entry.name} | input={entry.input_count} | "
            f"output={entry.output_count} | skipped={entry.skipped_count} | {entry.message}\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
