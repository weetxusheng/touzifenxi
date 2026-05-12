from __future__ import annotations

import sys
from pathlib import Path


def bootstrap() -> None:
    project_root = Path(__file__).resolve().parents[1]
    scripts_dir = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != scripts_dir]
    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def main() -> None:
    bootstrap()
    from utils.tools.runtime.win_stdio import ensure_utf8_stdio

    ensure_utf8_stdio()
    from c114.cli import main as skill_main

    skill_main()


if __name__ == "__main__":
    main()
