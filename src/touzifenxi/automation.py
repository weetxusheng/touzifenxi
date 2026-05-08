from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_AUTOMATION_LABEL = "com.touzifenxi.daily-cycle"
DEFAULT_AUTOMATION_HOURS = [18, 19, 20, 21, 22, 23]
DEFAULT_AUTOMATION_MINUTE = 30


@dataclass(frozen=True)
class AutomationPaths:
    source_root: Path
    runtime_root: Path
    runner_path: Path
    plist_path: Path

    @property
    def stdout_log_path(self) -> Path:
        return self.runtime_root / "runtime" / "daily-cycle-launchd.log"

    @property
    def stderr_log_path(self) -> Path:
        return self.runtime_root / "runtime" / "daily-cycle-launchd.err.log"


@dataclass(frozen=True)
class AutomationInstallResult:
    paths: AutomationPaths
    copied_runtime: bool
    loaded: bool
    kickstarted: bool
    commands: list[str]


def default_automation_paths(source_root: Path, runtime_root: str | None = None) -> AutomationPaths:
    home = Path.home()
    resolved_runtime = Path(runtime_root).expanduser() if runtime_root else home / "Projects" / "touzifenxi-auto"
    return AutomationPaths(
        source_root=source_root.resolve(),
        runtime_root=resolved_runtime.resolve(),
        runner_path=home / "Library" / "Scripts" / "touzifenxi" / "run_daily_cycle.sh",
        plist_path=home / "Library" / "LaunchAgents" / f"{DEFAULT_AUTOMATION_LABEL}.plist",
    )


def build_calendar_intervals(
    weekdays: range = range(1, 6),
    hours: list[int] | None = None,
    minute: int = DEFAULT_AUTOMATION_MINUTE,
) -> list[dict[str, int]]:
    selected_hours = hours or DEFAULT_AUTOMATION_HOURS
    return [{"Weekday": weekday, "Hour": hour, "Minute": minute} for weekday in weekdays for hour in selected_hours]


def render_launchd_plist(paths: AutomationPaths) -> str:
    payload: dict[str, Any] = {
        "Label": DEFAULT_AUTOMATION_LABEL,
        "ProgramArguments": [
            "/bin/zsh",
            "-lc",
            f"/bin/zsh {paths.runner_path}",
        ],
        "WorkingDirectory": str(paths.runtime_root),
        "StartCalendarInterval": build_calendar_intervals(),
        "StandardOutPath": str(paths.stdout_log_path),
        "StandardErrorPath": str(paths.stderr_log_path),
        "RunAtLoad": True,
    }
    return plistlib.dumps(payload, sort_keys=False).decode("utf-8")


def render_runner_script(runtime_root: Path) -> str:
    runtime = str(runtime_root)
    return f"""#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${{TOUZIFENXI_PROJECT_ROOT:-{runtime}}}"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/src:${{PYTHONPATH:-}}"
export PYTHONUNBUFFERED=1
export TZ="${{TZ:-Asia/Shanghai}}"

mkdir -p runtime
LOCK_DIR="$PROJECT_ROOT/runtime/daily-cycle.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily-cycle skipped: another automation run is active"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

PYTHON_BIN="${{TOUZIFENXI_PYTHON:-$PROJECT_ROOT/.venv/bin/python}}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="/usr/bin/python3"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] automation start | project=$PROJECT_ROOT | python=$PYTHON_BIN"

"$PYTHON_BIN" -m touzifenxi.cli db-info
"$PYTHON_BIN" -m touzifenxi.cli update-returns --network-mode direct --limit 200

SHOULD_RUN="$("$PYTHON_BIN" - <<'PY'
from datetime import datetime

from touzifenxi.settings import resolve_paths
from touzifenxi.storage import ResearchStore

today = datetime.now().date().isoformat()
paths = resolve_paths()
store = ResearchStore(paths.db_path, database_url=paths.database_url)
store.init_db()
summary = store.get_performance_summary()
latest_run = summary.get("latest_run")
latest_run_date = str(latest_run.get("run_at", ""))[:10] if latest_run else ""
latest_factor_date = store.get_latest_factor_snapshot_date() or ""
if latest_run_date == today and latest_factor_date == today:
    print("skip")
else:
    print("run")
PY
)"

if [[ "$SHOULD_RUN" == "skip" ]]; then
  "$PYTHON_BIN" -m touzifenxi.cli data-quality
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily-cycle skipped: already current after returns/data-quality refresh"
  exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily-cycle start"
"$PYTHON_BIN" -m touzifenxi.cli daily-cycle --network-mode direct --top-n 5
"$PYTHON_BIN" -m touzifenxi.cli data-quality
echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily-cycle done"
"""


def parse_launchctl_print(output: str) -> dict[str, str]:
    key_map = {
        "state": "state",
        "program": "program",
        "working directory": "working_directory",
        "stdout path": "stdout_path",
        "stderr path": "stderr_path",
        "runs": "runs",
        "last exit code": "last_exit_code",
    }
    parsed: dict[str, str] = {}
    pattern = re.compile(r"^\s*([A-Za-z ]+)\s*=\s*(.*?)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        raw_key, raw_value = match.groups()
        normalized_key = raw_key.strip()
        if normalized_key in key_map:
            parsed.setdefault(key_map[normalized_key], raw_value.strip())
    return parsed


def tail_file(path: Path, lines: int = 30) -> list[str]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-lines:]


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def copy_runtime(source_root: Path, runtime_root: Path) -> None:
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    exclude_args = [
        "--exclude",
        ".git",
        "--exclude",
        ".pytest_cache",
        "--exclude",
        ".ruff_cache",
        "--exclude",
        "__pycache__",
        "--exclude",
        "runtime/daily-cycle.lock",
    ]
    if shutil.which("rsync"):
        run_command(["rsync", "-a", "--delete", *exclude_args, f"{source_root}/", f"{runtime_root}/"])
        return
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    shutil.copytree(source_root, runtime_root, ignore=shutil.ignore_patterns(".git", ".pytest_cache", ".ruff_cache"))


def install_daily_cycle_automation(
    source_root: Path,
    *,
    runtime_root: str | None = None,
    load: bool = True,
    kickstart: bool = True,
) -> AutomationInstallResult:
    paths = default_automation_paths(source_root, runtime_root=runtime_root)
    if paths.source_root == paths.runtime_root:
        raise ValueError("automation runtime_root must be outside the source project root")

    copy_runtime(paths.source_root, paths.runtime_root)
    paths.runtime_root.joinpath("runtime").mkdir(parents=True, exist_ok=True)
    paths.runner_path.parent.mkdir(parents=True, exist_ok=True)
    paths.plist_path.parent.mkdir(parents=True, exist_ok=True)
    paths.runner_path.write_text(render_runner_script(paths.runtime_root), encoding="utf-8")
    paths.runner_path.chmod(0o755)
    paths.plist_path.write_text(render_launchd_plist(paths), encoding="utf-8")
    paths.stdout_log_path.write_text("", encoding="utf-8")
    paths.stderr_log_path.write_text("", encoding="utf-8")

    commands: list[str] = []
    loaded = False
    kicked = False
    if load and os.uname().sysname == "Darwin":
        gui_target = f"gui/{os.getuid()}"
        bootout_command = ["launchctl", "bootout", gui_target, str(paths.plist_path)]
        bootstrap_command = ["launchctl", "bootstrap", gui_target, str(paths.plist_path)]
        enable_command = ["launchctl", "enable", f"{gui_target}/{DEFAULT_AUTOMATION_LABEL}"]
        commands.extend([" ".join(bootout_command), " ".join(bootstrap_command), " ".join(enable_command)])
        run_command(bootout_command, check=False)
        run_command(bootstrap_command)
        run_command(enable_command, check=False)
        loaded = True
        if kickstart:
            kickstart_command = ["launchctl", "kickstart", "-k", f"{gui_target}/{DEFAULT_AUTOMATION_LABEL}"]
            commands.append(" ".join(kickstart_command))
            run_command(kickstart_command, check=False)
            kicked = True

    return AutomationInstallResult(
        paths=paths,
        copied_runtime=True,
        loaded=loaded,
        kickstarted=kicked,
        commands=commands,
    )


def get_launchd_status() -> dict[str, str]:
    if os.uname().sysname != "Darwin":
        return {"state": "unsupported", "error": "launchd is only available on macOS"}
    command = ["launchctl", "print", f"gui/{os.getuid()}/{DEFAULT_AUTOMATION_LABEL}"]
    result = run_command(command, check=False)
    if result.returncode != 0:
        return {"state": "not_loaded", "error": result.stderr.strip() or result.stdout.strip()}
    return parse_launchctl_print(result.stdout)


def get_automation_status(paths: AutomationPaths, tail_lines: int = 30) -> dict[str, Any]:
    launchd = get_launchd_status()
    return {
        "label": DEFAULT_AUTOMATION_LABEL,
        "source_root": str(paths.source_root),
        "runtime_root": str(paths.runtime_root),
        "runner_path": str(paths.runner_path),
        "plist_path": str(paths.plist_path),
        "stdout_log_path": str(paths.stdout_log_path),
        "stderr_log_path": str(paths.stderr_log_path),
        "launchd": launchd,
        "stdout_tail": tail_file(paths.stdout_log_path, lines=tail_lines),
        "stderr_tail": tail_file(paths.stderr_log_path, lines=tail_lines),
    }
