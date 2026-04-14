#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="/Users/xusheng/Documents/project/touzifenxi"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

cd "$PROJECT_ROOT"
export PYTHONPATH="src"

exec "$PYTHON_BIN" -m touzifenxi.cli run-c114-daily-brief --to zx944532395@sina.com
