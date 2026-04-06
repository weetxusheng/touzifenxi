"""C114 skill runtime configuration helpers.

This module owns the skill-local config layout under `skills/.../config/` and
is the only place that should know where `runtime.example.json` and
`runtime.local.json` live.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class C114RuntimeConfig:
    """Typed runtime config consumed by step 3-7 workflows."""

    tavily_api_key: str
    metaso_api_key: str
    baidu_api_key: str
    aliyun_iqs_api_key: str
    search_recent_days: int
    search_max_external_results: int
    content_fetch_keep_levels: tuple[str, ...]
    brief_role: str
    output_mode: str


def skill_root(base_path: Path | None = None) -> Path:
    """Resolve the physical skill root from either a skill path or project path."""

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
    return skill_root(base_path) / "config" / "runtime.example.json"


def runtime_local_path(base_path: Path | None = None) -> Path:
    return skill_root(base_path) / "config" / "runtime.local.json"


def read_c114_local_config(base_path: Path | None = None) -> dict[str, Any]:
    """Read the skill-local runtime config, returning an empty payload when absent."""

    config_path = runtime_local_path(base_path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def write_c114_local_config(base_path: Path | None, updates: dict[str, Any]) -> Path:
    """Merge updates into the local runtime config and persist them under the skill."""

    config_path = runtime_local_path(base_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(read_c114_local_config(base_path), updates)
    config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def collect_missing_c114_config(base_path: Path | None = None) -> list[str]:
    """Return missing required config keys using dotted-path notation."""

    config = read_c114_local_config(base_path)
    required_paths = (
        "keys.tavily_api_key",
        "keys.metaso_api_key",
        "keys.baidu_api_key",
        "keys.aliyun_iqs_api_key",
        "search.recent_days",
        "search.max_external_results",
        "content.fetch_keep_levels",
        "brief.role",
    )
    missing: list[str] = []
    for dotted_path in required_paths:
        value = _read_nested_value(config, dotted_path)
        if _is_missing_value(value):
            missing.append(dotted_path)
    return missing


def load_c114_runtime_config(base_path: Path | None = None) -> C114RuntimeConfig:
    """Load runtime config with environment-variable fallback for local execution."""

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
        search_recent_days=int(_read_nested_value(config, "search.recent_days", default=30)),
        search_max_external_results=int(_read_nested_value(config, "search.max_external_results", default=5)),
        content_fetch_keep_levels=normalized_keep_levels,
        brief_role=str(_read_nested_value(config, "brief.role", default="senior_researcher")).strip()
        or "senior_researcher",
        output_mode=_normalize_output_mode(_read_nested_value(config, "paths.output_mode", default="skill")),
    )


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _read_nested_value(payload: dict[str, Any], dotted_path: str, default: Any = None) -> Any:
    current: Any = payload
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not any(str(item).strip() for item in value)
    return False


def _normalize_output_mode(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"project", "project_shared"}:
        return "project"
    return "skill"
