#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [[ -z "${FEEDCORE_FETCHER_TOKEN:-}" ]]; then
  echo "[WARN] FEEDCORE_FETCHER_TOKEN is not set. Remote APIs will run without bearer-token protection."
fi

python -m pip install -r requirements.txt
python run_fetcher.py --host 0.0.0.0 --port 3000 --output-dir remote_output
