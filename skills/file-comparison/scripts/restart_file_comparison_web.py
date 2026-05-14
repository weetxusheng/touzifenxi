"""快速重启 file-comparison 本地页面服务。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if sys.platform == "win32":
    from file_comparison.web.dev_server_win import restart_web_server  # noqa: E402
else:
    from file_comparison.web.dev_server import restart_web_server  # noqa: E402


def main() -> None:
    """解析参数并执行页面服务快速重启。"""
    parser = argparse.ArgumentParser(description="快速重启 file-comparison 本地页面服务")
    parser.add_argument("--host", help="覆盖配置中的监听地址")
    parser.add_argument("--port", type=int, help="覆盖配置中的监听端口")
    args = parser.parse_args()

    result = restart_web_server(
        skill_root=ROOT,
        host=args.host,
        port=args.port,
        python_executable=None,
    )
    stopped_summary = ", ".join(str(pid) for pid in result.stopped_pids) or "无旧进程"
    print(f"已重启 file-comparison 页面服务: {result.url}")
    print(f"新进程 PID: {result.pid}")
    print(f"已停止旧进程: {stopped_summary}")
    print(f"PID 文件: {result.pid_file}")
    print(f"日志文件: {result.log_file}")


if __name__ == "__main__":
    main()
