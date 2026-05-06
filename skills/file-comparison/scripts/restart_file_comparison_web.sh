#!/bin/zsh

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
skill_root="$(cd "$script_dir/.." && pwd)"
project_root="$(cd "$skill_root/../.." && pwd)"

python_bin="$project_root/.venv/bin/python3"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$project_root/.venv/bin/python"
fi
if [[ ! -x "$python_bin" ]]; then
  python_bin="python3"
fi

cd "$skill_root"
"$python_bin" scripts/restart_file_comparison_web.py "$@"
