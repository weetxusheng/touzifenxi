"""提供 file-comparison skill 的本地批处理页面入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from file_comparison.runtime.config import load_file_comparison_runtime_config  # noqa: E402
from file_comparison.web.server import create_app  # noqa: E402


def main() -> None:
    """解析启动参数并运行本地页面服务。"""
    parser = argparse.ArgumentParser(description="启动文件对照批处理页面")
    parser.add_argument("--host", help="覆盖配置中的监听地址")
    parser.add_argument("--port", type=int, help="覆盖配置中的端口")
    args = parser.parse_args()

    config = load_file_comparison_runtime_config(ROOT)
    if args.host:
        config = config.__class__(
            llm_mode=config.llm_mode,
            llm=config.llm,
            execution=config.execution,
            paths=config.paths,
            pairing=config.pairing,
            ui=config.ui.__class__(
                poll_interval_seconds=config.ui.poll_interval_seconds,
                host=args.host,
                port=args.port or config.ui.port,
            ),
        )
    elif args.port:
        config = config.__class__(
            llm_mode=config.llm_mode,
            llm=config.llm,
            execution=config.execution,
            paths=config.paths,
            pairing=config.pairing,
            ui=config.ui.__class__(
                poll_interval_seconds=config.ui.poll_interval_seconds,
                host=config.ui.host,
                port=args.port,
            ),
        )

    server = create_app(config)
    print(f"http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
