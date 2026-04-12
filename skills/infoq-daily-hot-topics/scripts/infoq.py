from __future__ import annotations

import sys
from pathlib import Path


def bootstrap() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    project_root = skill_root.parents[1]
    scripts_dir = Path(__file__).resolve().parent
    c114_src = project_root / "skills" / "c114-daily-hot-topics" / "src"
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != scripts_dir]
    for path in (project_root / "src", c114_src, skill_root / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def main() -> None:
    bootstrap()
    from infoq.cli import main as skill_main

    skill_main()


if __name__ == "__main__":
    main()
