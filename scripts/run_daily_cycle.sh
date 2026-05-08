#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${TOUZIFENXI_PROJECT_ROOT:-/Users/xusheng/Projects/touzifenxi-auto}"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1
export TZ="${TZ:-Asia/Shanghai}"

mkdir -p runtime
LOCK_DIR="$PROJECT_ROOT/runtime/daily-cycle.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily-cycle skipped: another automation run is active"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

PYTHON_BIN="${TOUZIFENXI_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
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
