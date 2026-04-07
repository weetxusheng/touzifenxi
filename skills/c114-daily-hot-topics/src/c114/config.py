"""C114 skill 运行配置的读写、校验与归一化工具。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class C114RuntimeConfig:
    """C114 全流程使用的强类型运行配置。"""

    tavily_api_key: str
    metaso_api_key: str
    baidu_api_key: str
    aliyun_iqs_api_key: str
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str
    llm_timeout_seconds: float
    llm_max_retries: int
    search_recent_days: int
    search_max_external_results: int
    content_fetch_keep_levels: tuple[str, ...]
    brief_role: str
    review_enable_step7: bool
    output_mode: str


def skill_root(base_path: Path | None = None) -> Path:
    """根据 skill 目录或项目目录推导出 C114 skill 根目录。"""

    if base_path is None:
        return Path(__file__).resolve().parents[2]
    candidate = base_path.resolve()
    if (candidate / "SKILL.md").exists():
        return candidate
    nested = candidate / "skills" / "c114-daily-hot-topics"
    if nested.exists():
        return nested.resolve()
    return candidate


def runtime_example_path(base_path: Path | None = None) -> Path:
    """返回 skill 内可分发的示例配置文件路径。"""
    return skill_root(base_path) / "config" / "runtime.example.json"


def runtime_local_path(base_path: Path | None = None) -> Path:
    """返回 skill 内本地运行配置文件路径。"""
    return skill_root(base_path) / "config" / "runtime.local.json"


def initialize_c114_local_config(base_path: Path | None = None, *, overwrite: bool = False) -> Path:
    """基于示例模板初始化 `runtime.local.json`。"""

    local_path = runtime_local_path(base_path)
    if local_path.exists() and not overwrite:
        return local_path
    example_path = runtime_example_path(base_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return local_path


def read_c114_local_config(base_path: Path | None = None) -> dict[str, Any]:
    """读取 skill 本地配置；文件不存在时返回空字典。"""

    config_path = runtime_local_path(base_path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def write_c114_local_config(base_path: Path | None, updates: dict[str, Any]) -> Path:
    """将更新内容合并进本地配置，并写回 skill 目录。"""

    config_path = runtime_local_path(base_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(read_c114_local_config(base_path), updates)
    config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def collect_missing_c114_config(base_path: Path | None = None) -> list[str]:
    """按点路径收集当前缺失的关键配置项。"""

    config = read_c114_local_config(base_path)
    required_paths = (
        "keys.aliyun_iqs_api_key",
        "llm.api_key",
        "llm.model",
        "llm.base_url",
        "search.recent_days",
        "search.max_external_results",
        "content.fetch_keep_levels",
        "brief.role",
    )
    missing: list[str] = []
    search_provider_keys = (
        "keys.tavily_api_key",
        "keys.metaso_api_key",
        "keys.baidu_api_key",
    )
    if all(_is_missing_value(_read_nested_value(config, dotted_path)) for dotted_path in search_provider_keys):
        missing.append("keys.search_provider_api_key")
    for dotted_path in required_paths:
        value = _read_nested_value(config, dotted_path)
        if _is_missing_value(value):
            missing.append(dotted_path)
    return missing


def load_c114_runtime_config(base_path: Path | None = None) -> C114RuntimeConfig:
    """加载运行配置，并在本地执行时允许环境变量兜底。"""

    config = read_c114_local_config(base_path)
    keep_levels = _read_nested_value(config, "content.fetch_keep_levels", default=["strong", "weak"])
    if not isinstance(keep_levels, list):
        keep_levels = ["strong", "weak"]
    normalized_keep_levels = tuple(str(level).strip() for level in keep_levels if str(level).strip())
    if not normalized_keep_levels:
        normalized_keep_levels = ("strong", "weak")
    return C114RuntimeConfig(
        tavily_api_key=str(_read_nested_value(config, "keys.tavily_api_key", default=os.getenv("TAVILY_API_KEY", ""))).strip(),
        metaso_api_key=str(_read_nested_value(config, "keys.metaso_api_key", default=os.getenv("METASO_API_KEY", ""))).strip(),
        baidu_api_key=str(_read_nested_value(config, "keys.baidu_api_key", default=os.getenv("BAIDU_API_KEY", ""))).strip(),
        aliyun_iqs_api_key=str(
            _read_nested_value(config, "keys.aliyun_iqs_api_key", default=os.getenv("ALIYUN_IQS_API_KEY", ""))
        ).strip(),
        llm_provider=str(_read_nested_value(config, "llm.provider", default="minimax")).strip() or "minimax",
        llm_model=str(_read_nested_value(config, "llm.model", default="MiniMax M2.7")).strip() or "MiniMax M2.7",
        llm_api_key=str(_read_nested_value(config, "llm.api_key", default=os.getenv("MINIMAX_API_KEY", ""))).strip(),
        llm_base_url=str(_read_nested_value(config, "llm.base_url", default="https://api.minimaxi.com/v1")).strip()
        or "https://api.minimaxi.com/v1",
        llm_timeout_seconds=float(_read_nested_value(config, "llm.timeout_seconds", default=60.0)),
        llm_max_retries=int(_read_nested_value(config, "llm.max_retries", default=2)),
        search_recent_days=int(_read_nested_value(config, "search.recent_days", default=30)),
        search_max_external_results=int(_read_nested_value(config, "search.max_external_results", default=5)),
        content_fetch_keep_levels=normalized_keep_levels,
        brief_role=str(_read_nested_value(config, "brief.role", default="senior_researcher")).strip()
        or "senior_researcher",
        review_enable_step7=bool(_read_nested_value(config, "review.enable_step7", default=True)),
        output_mode=_normalize_output_mode(_read_nested_value(config, "paths.output_mode", default="skill")),
    )


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置更新内容。"""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _read_nested_value(payload: dict[str, Any], dotted_path: str, default: Any = None) -> Any:
    """按点路径读取嵌套配置值。"""
    current: Any = payload
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _is_missing_value(value: Any) -> bool:
    """统一判断一个配置值是否可视为缺失。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not any(str(item).strip() for item in value)
    return False


def _normalize_output_mode(value: Any) -> str:
    """将输出模式别名归一化为当前支持的两种值。"""
    normalized = str(value).strip().lower()
    if normalized in {"project", "project_shared"}:
        return "project"
    return "skill"
