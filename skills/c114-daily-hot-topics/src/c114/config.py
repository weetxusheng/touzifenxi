"""C114 skill 运行配置的读写、校验与归一化工具。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LLMProviderRuntimeConfig:
    """单个大模型 provider 的运行配置。"""

    provider: str
    model: str
    api_key: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    max_concurrency: int = 2


@dataclass(frozen=True)
class LLMFailoverRuntimeConfig:
    """主备模型切换的运行配置。"""

    enabled: bool
    consecutive_failures: int
    reset_scope: str
    error_scope: str


@dataclass(frozen=True)
class LLMRetryRuntimeConfig:
    """统一的重试策略配置。"""

    honor_retry_after: bool
    jitter_seconds: float


@dataclass(frozen=True)
class LLMStreamingRuntimeConfig:
    """统一的流式接收配置。"""

    enabled: bool
    steps: tuple[str, ...]


@dataclass(frozen=True)
class C114RuntimeConfig:
    """C114 全流程使用的强类型运行配置。"""

    tavily_api_key: str
    metaso_api_key: str
    baidu_api_key: str
    aliyun_iqs_api_key: str
    llm_providers: tuple[LLMProviderRuntimeConfig, ...]
    llm_primary: LLMProviderRuntimeConfig
    llm_fallback: LLMProviderRuntimeConfig | None
    llm_failover: LLMFailoverRuntimeConfig
    llm_retry: LLMRetryRuntimeConfig
    llm_streaming: LLMStreamingRuntimeConfig
    request_timeout_seconds: float
    aliyun_timeout_seconds: float
    aliyun_max_retries: int
    aliyun_retry_backoff_seconds: float
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
    llm_missing = _collect_missing_llm_config(config)
    missing.extend(llm_missing)
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
    llm_config = _build_llm_runtime_config(config)
    return C114RuntimeConfig(
        tavily_api_key=str(_read_nested_value(config, "keys.tavily_api_key", default=os.getenv("TAVILY_API_KEY", ""))).strip(),
        metaso_api_key=str(_read_nested_value(config, "keys.metaso_api_key", default=os.getenv("METASO_API_KEY", ""))).strip(),
        baidu_api_key=str(_read_nested_value(config, "keys.baidu_api_key", default=os.getenv("BAIDU_API_KEY", ""))).strip(),
        aliyun_iqs_api_key=str(
            _read_nested_value(config, "keys.aliyun_iqs_api_key", default=os.getenv("ALIYUN_IQS_API_KEY", ""))
        ).strip(),
        llm_providers=llm_config["providers"],
        llm_primary=llm_config["primary"],
        llm_fallback=llm_config["fallback"],
        llm_failover=llm_config["failover"],
        llm_retry=llm_config["retry"],
        llm_streaming=llm_config["streaming"],
        request_timeout_seconds=float(_read_nested_value(config, "network.request_timeout_seconds", default=45.0)),
        aliyun_timeout_seconds=float(_read_nested_value(config, "network.aliyun_timeout_seconds", default=45.0)),
        aliyun_max_retries=int(_read_nested_value(config, "network.aliyun_max_retries", default=2)),
        aliyun_retry_backoff_seconds=float(
            _read_nested_value(config, "network.aliyun_retry_backoff_seconds", default=0.5)
        ),
        search_recent_days=int(_read_nested_value(config, "search.recent_days", default=30)),
        search_max_external_results=int(_read_nested_value(config, "search.max_external_results", default=5)),
        content_fetch_keep_levels=normalized_keep_levels,
        brief_role=str(_read_nested_value(config, "brief.role", default="senior_researcher")).strip()
        or "senior_researcher",
        review_enable_step7=bool(_read_nested_value(config, "review.enable_step7", default=True)),
        output_mode=_normalize_output_mode(_read_nested_value(config, "paths.output_mode", default="skill")),
    )


def _build_llm_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    """统一读取主模型、备用模型和切换配置，并兼容旧单组 llm 配置。"""

    llm_root = _read_nested_value(config, "llm", default={})
    if not isinstance(llm_root, dict):
        llm_root = {}

    providers_payload = llm_root.get("providers")
    concurrency_root = llm_root.get("concurrency") if isinstance(llm_root.get("concurrency"), dict) else {}
    providers: list[LLMProviderRuntimeConfig] = []
    if isinstance(providers_payload, list) and providers_payload:
        for provider_payload in providers_payload:
            if not isinstance(provider_payload, dict):
                continue
            payload_with_shared = dict(provider_payload)
            payload_with_shared["_shared_concurrency"] = concurrency_root
            providers.append(_read_provider_runtime_config(payload_with_shared))
    else:
        has_primary = isinstance(llm_root.get("primary"), dict)
        if has_primary:
            primary_payload = dict(llm_root["primary"])
            primary_payload["_shared_concurrency"] = concurrency_root
            providers.append(_read_provider_runtime_config(primary_payload))
            secondary_payload = llm_root.get("secondary")
            if isinstance(secondary_payload, dict):
                payload_with_shared = dict(secondary_payload)
                payload_with_shared["_shared_concurrency"] = concurrency_root
                providers.append(_read_provider_runtime_config(payload_with_shared))
            fallback_payload = llm_root.get("fallback")
            if isinstance(fallback_payload, dict):
                payload_with_shared = dict(fallback_payload)
                payload_with_shared["_shared_concurrency"] = concurrency_root
                providers.append(_read_provider_runtime_config(payload_with_shared))
        else:
            payload_with_shared = dict(llm_root)
            payload_with_shared["_shared_concurrency"] = concurrency_root
            providers.append(_read_provider_runtime_config(payload_with_shared))

    providers = [provider for provider in providers if provider.provider]
    if not providers:
        providers = [
            _read_provider_runtime_config(
                {
                    "provider": "minimax",
                    "model": "MiniMax M2.7",
                    "base_url": "https://api.minimaxi.com/v1",
                    "_shared_concurrency": concurrency_root,
                }
            )
        ]
    primary = providers[0]
    fallback = providers[-1] if len(providers) > 1 else None

    failover_root = llm_root.get("failover") if isinstance(llm_root.get("failover"), dict) else {}
    failover = LLMFailoverRuntimeConfig(
        enabled=bool(failover_root.get("enabled", False)) and fallback is not None,
        consecutive_failures=max(1, int(failover_root.get("consecutive_failures", 3))),
        reset_scope=str(failover_root.get("reset_scope", "step")).strip() or "step",
        error_scope=str(failover_root.get("error_scope", "infra_only")).strip() or "infra_only",
    )
    retry_root = llm_root.get("retry") if isinstance(llm_root.get("retry"), dict) else {}
    retry = LLMRetryRuntimeConfig(
        honor_retry_after=bool(retry_root.get("honor_retry_after", True)),
        jitter_seconds=max(0.0, float(retry_root.get("jitter_seconds", 0.5))),
    )
    streaming_root = llm_root.get("streaming") if isinstance(llm_root.get("streaming"), dict) else {}
    steps_payload = streaming_root.get("steps", ["step_5", "step_6", "step_7"])
    if not isinstance(steps_payload, list):
        steps_payload = ["step_5", "step_6", "step_7"]
    streaming = LLMStreamingRuntimeConfig(
        enabled=bool(streaming_root.get("enabled", True)),
        steps=tuple(str(step).strip() for step in steps_payload if str(step).strip()) or ("step_5", "step_6", "step_7"),
    )
    return {
        "providers": tuple(providers),
        "primary": primary,
        "fallback": fallback,
        "failover": failover,
        "retry": retry,
        "streaming": streaming,
    }


def _read_provider_runtime_config(payload: dict[str, Any]) -> LLMProviderRuntimeConfig:
    """读取单个 provider 配置，并补齐默认值。"""

    provider_name = str(payload.get("provider", "")).strip().lower() or "minimax"
    defaults = _provider_defaults(provider_name)
    concurrency_default = _provider_default_concurrency(payload, provider_name)

    return LLMProviderRuntimeConfig(
        provider=provider_name,
        model=str(payload.get("model", defaults["model"])).strip() or str(defaults["model"]),
        api_key=str(payload.get("api_key", os.getenv(str(defaults["env_var"]), ""))).strip(),
        base_url=str(payload.get("base_url", defaults["base_url"])).strip() or str(defaults["base_url"]),
        timeout_seconds=float(payload.get("timeout_seconds", 180.0)),
        max_retries=int(payload.get("max_retries", 2)),
        retry_backoff_seconds=float(payload.get("retry_backoff_seconds", 2.0)),
        max_concurrency=max(1, int(payload.get("max_concurrency", concurrency_default))),
    )


def _provider_defaults(provider_name: str) -> dict[str, str]:
    """返回不同 provider 的默认模型、地址与环境变量。"""

    normalized = provider_name.strip().lower()
    if normalized == "kimi-code":
        return {
            "env_var": "KIMI_CODE_API_KEY",
            "model": "kimi-for-coding",
            "base_url": "https://api.kimi.com/coding",
        }
    if normalized == "kimi":
        return {
            "env_var": "KIMI_API_KEY",
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
        }
    return {
        "env_var": "MINIMAX_API_KEY",
        "model": "MiniMax M2.7",
        "base_url": "https://api.minimaxi.com/v1",
    }


def _provider_default_concurrency(payload: dict[str, Any], provider_name: str) -> int:
    """读取 provider 默认并发上限。"""

    concurrency_root = payload.get("_shared_concurrency")
    provider_specific: Any = {}
    default_value = 2
    if isinstance(concurrency_root, dict):
        provider_specific = concurrency_root.get("providers", {})
        default_value = max(1, int(concurrency_root.get("default", 2)))
    if isinstance(provider_specific, dict) and provider_name in provider_specific:
        try:
            return max(1, int(provider_specific[provider_name]))
        except (TypeError, ValueError):
            return default_value
    if provider_name == "minimax":
        return 3
    return default_value


def _collect_missing_llm_config(config: dict[str, Any]) -> list[str]:
    """收集主模型与主备链缺失的关键配置。"""

    llm_root = _read_nested_value(config, "llm", default={})
    if not isinstance(llm_root, dict):
        llm_root = {}
    missing: list[str] = []
    providers_payload = llm_root.get("providers")
    failover_enabled = bool(_read_nested_value(config, "llm.failover.enabled", default=False))
    if isinstance(providers_payload, list) and providers_payload:
        required_count = len(providers_payload) if failover_enabled else 1
        for index, payload in enumerate(providers_payload[:required_count]):
            if not isinstance(payload, dict):
                missing.append(f"llm.providers[{index}]")
                continue
            for field_name in ("api_key", "model", "base_url"):
                value = payload.get(field_name)
                if _is_missing_value(value):
                    missing.append(f"llm.providers[{index}].{field_name}")
        return missing

    for dotted_path in ("llm.primary.api_key", "llm.primary.model", "llm.primary.base_url"):
        value = _read_nested_value(config, dotted_path)
        if _is_missing_value(value):
            missing.append(dotted_path)
    if failover_enabled:
        for dotted_path in ("llm.fallback.api_key", "llm.fallback.model", "llm.fallback.base_url"):
            value = _read_nested_value(config, dotted_path)
            if _is_missing_value(value):
                missing.append(dotted_path)
    if not missing and _is_missing_value(_read_nested_value(config, "llm.provider")):
        missing.extend(("llm.primary.api_key", "llm.primary.model", "llm.primary.base_url"))
    return missing


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
