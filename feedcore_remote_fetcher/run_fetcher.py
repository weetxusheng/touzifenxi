from __future__ import annotations

import sys
from pathlib import Path


def _add_local_dir_to_path() -> None:
    local_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(local_dir))


def main() -> None:
    _add_local_dir_to_path()
    try:
        from app import main as server_main
    except ModuleNotFoundError as exc:
        if exc.name != "app":
            raise
        raise SystemExit(
            "Cannot import remote_fetcher/app.py. Please keep run_fetcher.py and app.py in the same remote_fetcher folder."
        ) from exc
    server_main()


if __name__ == "__main__":
    main()
