"""基于已有 file-comparison run 数据重新生成 Word 对照表，不调用模型。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from file_comparison.compare.models import PairMatch  # noqa: E402
from file_comparison.compare.rerender import (  # noqa: E402
    output_paths,
    pair_dirs_for_run,
    rerender_pair,
    resolve_run_dir,
)
from file_comparison.runtime.python_env import maybe_reexec_into_project_python  # noqa: E402

__all__ = ("PairMatch", "output_paths", "pair_dirs_for_run", "rerender_pair", "resolve_run_dir")


def main() -> None:
    """解析命令行参数并执行已有 run 的离线重渲染。"""
    parser = argparse.ArgumentParser(description="基于已有 file-comparison run 数据重新生成 Word，不调用模型")
    parser.add_argument("run", help="run 目录绝对路径，或 task_id，例如 20260428-180153")
    parser.add_argument("--pair-id", default="", help="只重渲染指定 pair，例如 pair-001；默认重渲染全部 pair")
    parser.add_argument("--mode", choices=["stored", "local"], default="stored", help="stored 复用已有模型结果；local 使用当前本地规则")
    parser.add_argument("--overwrite", action="store_true", help="覆盖 outputs/comparison.docx；默认写 timestamp 文件")
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.run)
    results = [
        rerender_pair(pair_dir, mode=args.mode, overwrite=args.overwrite)
        for pair_dir in pair_dirs_for_run(run_dir, args.pair_id)
    ]
    print(json.dumps({"run_dir": str(run_dir), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    maybe_reexec_into_project_python(
        skill_root=ROOT,
        script_path=Path(__file__).resolve(),
        argv=sys.argv[1:],
        current_executable=sys.executable,
        execv=os.execv,
    )
    main()
