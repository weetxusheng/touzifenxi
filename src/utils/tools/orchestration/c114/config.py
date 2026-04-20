"""配置类子命令的处理逻辑。"""

from __future__ import annotations

import argparse
import json
from typing import Any


def handle_config_status_command(args: argparse.Namespace, *, facade: Any) -> None:
    """输出当前 runtime.local.json 的缺失配置项。"""

    current = facade.read_c114_local_config()
    missing = facade.collect_missing_c114_config()
    config_path = facade.runtime_local_path()
    if args.json:
        print(
            json.dumps(
                {"config_path": str(config_path), "current": current, "missing": missing},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print(f"C114 配置文件: {config_path}")
    if missing:
        print("缺失配置项:")
        for item in missing:
            print(f"- {item}")
        runtime_config = facade.load_c114_runtime_config()
        if runtime_config.execution_mode == "controller-agent":
            print("说明：当前是 controller-agent 模式，不强制要求内置 LLM key；请至少配置一个搜索 provider key，并补齐 search/content/brief 基础配置。")
            return
        print("说明：step 1 可先不配 API key；若要继续跑 step 2-7，需要先配置 llm.providers[0].api_key，再至少配置一个搜索 provider key（Tavily / Metaso / Baidu 三选一），并补齐其余 search/content/brief 基础配置。若启用 provider 链切换，还要补齐链路中其它 provider 的 api_key。")
        return
    print("配置已完整。")


def handle_config_apply_command(args: argparse.Namespace, *, facade: Any) -> None:
    """把 JSON payload 合并写入 runtime.local.json。"""

    payload = json.loads(args.payload_json)
    output_path = facade.write_c114_local_config(None, payload)
    missing = facade.collect_missing_c114_config()
    print(f"C114 配置已写入: {output_path}")
    if missing:
        print("仍缺少以下配置项:")
        for item in missing:
            print(f"- {item}")
        return
    print("配置已完整。")


def handle_config_init_command(args: argparse.Namespace, *, facade: Any) -> None:
    """用模板初始化 runtime.local.json。"""

    output_path = facade.initialize_c114_local_config(None, overwrite=bool(args.force))
    print(f"C114 配置模板已初始化: {output_path}")
    if facade.collect_missing_c114_config():
        print("请编辑 runtime.local.json，补齐 keys/search/content/brief/llm 配置后再运行。")
