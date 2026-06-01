from __future__ import annotations

import argparse
import sys
from importlib import import_module
from pathlib import Path

_SOURCES = {
    "c114": "c114.cli",
    "infoq": "infoq.cli",
    "chip": "chip.cli",
}


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

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", choices=list(_SOURCES.keys()), default="c114")
    args, remainder = parser.parse_known_args()
    target_module = import_module(_SOURCES[args.source])
    sys.argv = [sys.argv[0]] + remainder
    target_module.main()


if __name__ == "__main__":
    main()
