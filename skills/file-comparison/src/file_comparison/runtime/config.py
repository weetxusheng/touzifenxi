"""读取、归一化并写回 file-comparison skill 的运行配置。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class LLMProviderConfig:
    """描述单个大模型 provider 的请求参数与限流参数。"""

    provider: str
    model: str
    api_key: str
    api_key_env: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    max_concurrency: int
    honor_retry_after: bool
    jitter_seconds: float
    min_interval_seconds: float
    failure_cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class LLMRuntimeConfig:
    """聚合文件对照流程所需的大模型链路与重试配置。"""

    providers: tuple[LLMProviderConfig, ...]
    chapter_batch_size: int
    chapter_batch_char_limit: int
    oversized_chapter_batch_size: int
    max_compare_blocks_per_batch: int
    max_compare_block_chars: int
    parse_max_attempts: int
    infra_max_attempts: int
    postprocess_max_attempts: int
    fatal_max_attempts: int
    task_routing: LLMTaskRoutingRuntimeConfig

    @property
    def primary(self) -> LLMProviderConfig:
        """返回当前 provider 链中的首选 provider。"""
        return self.providers[0]

    @property
    def provider(self) -> str:
        """兼容旧代码读取首选 provider 名称。"""
        return self.primary.provider

    @property
    def model(self) -> str:
        """兼容旧代码读取首选模型名。"""
        return self.primary.model

    @property
    def api_key(self) -> str:
        """兼容旧代码读取首选 provider 明文 key。"""
        return self.primary.api_key

    @property
    def api_key_env(self) -> str:
        """兼容旧代码读取首选 provider 的环境变量名。"""
        return self.primary.api_key_env

    @property
    def base_url(self) -> str:
        """兼容旧代码读取首选 provider 的基础地址。"""
        return self.primary.base_url

    @property
    def timeout_seconds(self) -> float:
        """兼容旧代码读取首选 provider 的超时时间。"""
        return self.primary.timeout_seconds

    @property
    def max_retries(self) -> int:
        """兼容旧代码读取首选 provider 的内部重试次数。"""
        return self.primary.max_retries

    @property
    def retry_backoff_seconds(self) -> float:
        """兼容旧代码读取首选 provider 的退避基线秒数。"""
        return self.primary.retry_backoff_seconds

    @property
    def max_concurrency(self) -> int:
        """兼容旧代码读取首选 provider 的并发上限。"""
        return self.primary.max_concurrency

    @property
    def honor_retry_after(self) -> bool:
        """兼容旧代码读取是否尊重 Retry-After。"""
        return self.primary.honor_retry_after

    @property
    def jitter_seconds(self) -> float:
        """兼容旧代码读取退避抖动秒数。"""
        return self.primary.jitter_seconds

    @property
    def min_interval_seconds(self) -> float:
        """兼容旧代码读取最小请求间隔。"""
        return self.primary.min_interval_seconds

    @property
    def failure_cooldown_seconds(self) -> float:
        """兼容旧代码读取失败后的 provider 冷却秒数。"""
        return self.primary.failure_cooldown_seconds

    @property
    def max_compare_units_per_batch(self) -> int:
        """兼容旧代码读取旧 unit 批次上限字段。"""
        return self.max_compare_blocks_per_batch

    @property
    def max_compare_unit_chars(self) -> int:
        """兼容旧代码读取旧 unit 字符上限字段。"""
        return self.max_compare_block_chars


@dataclass(frozen=True, slots=True)
class PathsRuntimeConfig:
    """描述输出目录模式与根目录。"""
    output_mode: str
    output_root: str


@dataclass(frozen=True, slots=True)
class ExecutionRuntimeConfig:
    """描述文件对照任务内部的并行执行参数。"""
    per_pair_max_workers: int


@dataclass(frozen=True, slots=True)
class CompareRuntimeConfig:
    """描述对比前的业务过滤与清洗规则。"""

    skip_section_patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairingRuntimeConfig:
    """描述文件自动配对所需的命名规则。"""
    month_pattern: str


@dataclass(frozen=True, slots=True)
class UIRuntimeConfig:
    """描述本地页面服务的轮询与监听配置。"""
    poll_interval_seconds: float
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class LLMTaskRoutingRuntimeConfig:
    """描述 batch 在多 provider 间轮转首发的顺序。"""
    enabled: bool
    batch_compare: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileComparisonRuntimeConfig:
    """聚合整个 skill 的强类型运行配置。"""
    llm_mode: str
    llm: LLMRuntimeConfig
    execution: ExecutionRuntimeConfig
    compare: CompareRuntimeConfig
    paths: PathsRuntimeConfig
    pairing: PairingRuntimeConfig
    ui: UIRuntimeConfig


def project_root(base_path: Path | None = None) -> Path:
    """根据 skill 根目录或调用方路径推导 skill 根路径。"""
    if base_path is None:
        return Path(__file__).resolve().parents[3]
    return base_path.resolve()


def runtime_local_path(base_path: Path | None = None) -> Path:
    """返回本地运行配置 `runtime.local.json` 的路径。"""
    return project_root(base_path) / "config" / "runtime.local.json"


def runtime_example_path(base_path: Path | None = None) -> Path:
    """返回可分发的示例配置模板路径。"""
    return project_root(base_path) / "config" / "runtime.example.json"


def read_runtime_config(base_path: Path | None = None) -> dict[str, Any]:
    """读取本地运行配置；若文件不存在则返回空字典。"""
    path = runtime_local_path(base_path)
    if not path.exists():
        return {}
    return json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))


def write_runtime_config(base_path: Path | None, updates: dict[str, Any]) -> Path:
    """将更新内容合并进本地配置并写回 skill 目录。"""
    path = runtime_local_path(base_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(read_runtime_config(base_path), updates)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def initialize_runtime_config(base_path: Path | None = None, *, overwrite: bool = False) -> Path:
    """基于示例模板初始化 `runtime.local.json`。"""
    target = runtime_local_path(base_path)
    if target.exists() and not overwrite:
        return target
    source = runtime_example_path(base_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        target.write_text(json.dumps(default_runtime_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def default_runtime_payload() -> dict[str, Any]:
    """返回 file-comparison skill 的默认多模型运行模板。"""
    primary_provider = {
        "provider": "minimax",
        "model": "MiniMax-M2.7",
        "api_key": "",
        "api_key_env": "MINIMAX_API_KEY",
        "base_url": "https://api.minimaxi.com/v1",
        "timeout_seconds": 180.0,
        "max_retries": 2,
        "retry_backoff_seconds": 2.0,
        "min_interval_seconds": 2.0,
        "failure_cooldown_seconds": 40.0,
    }
    providers = [
        dict(primary_provider),
        {
            "provider": "kimi-code",
            "model": "kimi-for-coding",
            "api_key": "",
            "api_key_env": "KIMI_CODE_API_KEY",
            "base_url": "https://api.kimi.com/coding",
            "timeout_seconds": 180.0,
            "max_retries": 2,
            "retry_backoff_seconds": 2.0,
            "min_interval_seconds": 2.0,
            "failure_cooldown_seconds": 40.0,
        },
        {
            "provider": "deepseek-ark",
            "model": "ep-20260415114137-55jp7",
            "api_key": "",
            "api_key_env": "ARK_API_KEY",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "timeout_seconds": 180.0,
            "max_retries": 2,
            "retry_backoff_seconds": 2.0,
            "min_interval_seconds": 2.0,
            "failure_cooldown_seconds": 40.0,
        },
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com",
            "timeout_seconds": 180.0,
            "max_retries": 2,
            "retry_backoff_seconds": 2.0,
            "min_interval_seconds": 2.0,
            "failure_cooldown_seconds": 40.0,
        },
    ]
    return {
        "llm_mode": "rule",
        "llm": {
            **primary_provider,
            "providers": providers,
            "chapter_batch_size": 4,
            "chapter_batch_char_limit": 10000,
            "oversized_chapter_batch_size": 2,
            "max_compare_blocks_per_batch": 4,
            "max_compare_block_chars": 10000,
            "retry": {
                "honor_retry_after": True,
                "jitter_seconds": 0.5,
            },
            "retry_classifier": {
                "parse_max_attempts": 2,
                "infra_max_attempts": 2,
                "postprocess_max_attempts": 2,
                "fatal_max_attempts": 1,
            },
            "concurrency": {
                "default": 1,
                "providers": {
                    "minimax": 1,
                    "kimi-code": 1,
                    "deepseek-ark": 1,
                    "deepseek": 1,
                },
            },
            "task_routing": {
                "enabled": True,
                "batch_compare": [provider["provider"] for provider in providers],
            },
        },
        "execution": {
            "per_pair_max_workers": 2,
        },
        "compare": {
            "skip_section_patterns": [
                "签署页",
                "签字页",
                "盖章页",
                "签章页",
            ],
        },
        "paths": {
            "output_mode": "skill",
            "output_root": "output",
        },
        "pairing": {
            "month_pattern": r"(?P<month>\d{1,2})月",
        },
        "ui": {
            "poll_interval_seconds": 5.0,
            "host": "127.0.0.1",
            "port": 8765,
        },
    }


def load_file_comparison_runtime_config(base_path: Path | None = None) -> FileComparisonRuntimeConfig:
    """读取并归一化 file-comparison 的强类型运行配置。"""
    raw_config = read_runtime_config(base_path)
    config = _deep_merge(default_runtime_payload(), raw_config)
    llm = config["llm"]
    raw_llm = raw_config.get("llm", {}) if isinstance(raw_config.get("llm", {}), dict) else {}
    execution = config.get("execution", {})
    compare = config.get("compare", {})
    ui = config["ui"]
    paths = config["paths"]
    pairing = config["pairing"]
    raw_retry = llm.get("retry", {}) if isinstance(llm.get("retry"), dict) else {}
    raw_retry_classifier = llm.get("retry_classifier", {}) if isinstance(llm.get("retry_classifier"), dict) else {}
    raw_concurrency = llm.get("concurrency", {}) if isinstance(llm.get("concurrency"), dict) else {}
    raw_concurrency_providers = (
        raw_concurrency.get("providers", {})
        if isinstance(raw_concurrency.get("providers"), dict)
        else {}
    )
    default_max_concurrency = max(1, int(raw_concurrency.get("default", llm.get("max_concurrency", 1))))
    providers_payload = llm.get("providers")
    if isinstance(providers_payload, list) and providers_payload:
        merged_providers = llm.get("providers", [])
        raw_providers = merged_providers if isinstance(merged_providers, list) and merged_providers else providers_payload
        raw_providers = [
            _merge_legacy_provider_fields(provider_payload, raw_llm) if index == 0 else provider_payload
            for index, provider_payload in enumerate(raw_providers)
        ]
    else:
        raw_providers = [llm]
    providers = tuple(
        _build_provider_config(
            provider_payload,
            retry_payload=raw_retry,
            max_concurrency=int(raw_concurrency_providers.get(str(provider_payload.get("provider", "")).strip(), default_max_concurrency)),
        )
        for provider_payload in raw_providers
    )
    task_routing = _build_task_routing_config(llm, providers)
    return FileComparisonRuntimeConfig(
        llm_mode=str(config.get("llm_mode", "rule")).strip() or "rule",
        llm=LLMRuntimeConfig(
            providers=providers,
            chapter_batch_size=max(1, int(llm.get("chapter_batch_size", 2))),
            chapter_batch_char_limit=max(0, int(llm.get("chapter_batch_char_limit", 10000))),
            oversized_chapter_batch_size=max(1, int(llm.get("oversized_chapter_batch_size", 2))),
            max_compare_blocks_per_batch=max(
                0,
                int(
                    raw_llm.get(
                        "max_compare_blocks_per_batch",
                        raw_llm.get("max_compare_units_per_batch", llm.get("max_compare_blocks_per_batch", 4)),
                    )
                ),
            ),
            max_compare_block_chars=max(
                0,
                int(
                    raw_llm.get(
                        "max_compare_block_chars",
                        raw_llm.get("max_compare_unit_chars", llm.get("max_compare_block_chars", 10000)),
                    )
                ),
            ),
            parse_max_attempts=max(1, int(raw_retry_classifier.get("parse_max_attempts", llm.get("parse_max_attempts", 3)))),
            infra_max_attempts=max(1, int(raw_retry_classifier.get("infra_max_attempts", llm.get("infra_max_attempts", 3)))),
            postprocess_max_attempts=max(1, int(raw_retry_classifier.get("postprocess_max_attempts", llm.get("postprocess_max_attempts", 2)))),
            fatal_max_attempts=max(1, int(raw_retry_classifier.get("fatal_max_attempts", llm.get("fatal_max_attempts", 1)))),
            task_routing=task_routing,
        ),
        execution=ExecutionRuntimeConfig(
            per_pair_max_workers=max(1, int(execution.get("per_pair_max_workers", 2))),
        ),
        compare=CompareRuntimeConfig(
            skip_section_patterns=tuple(
                str(item).strip()
                for item in compare.get("skip_section_patterns", ["签署页", "签字页", "盖章页", "签章页"])
                if str(item).strip()
            ),
        ),
        paths=PathsRuntimeConfig(
            output_mode=str(paths.get("output_mode", "project")).strip() or "project",
            output_root=str(paths.get("output_root", "output")).strip() or "output",
        ),
        pairing=PairingRuntimeConfig(
            month_pattern=str(pairing.get("month_pattern", r"(?P<month>\d{1,2})月")).strip() or r"(?P<month>\d{1,2})月",
        ),
        ui=UIRuntimeConfig(
            poll_interval_seconds=float(ui.get("poll_interval_seconds", 5.0)),
            host=str(ui.get("host", "127.0.0.1")).strip() or "127.0.0.1",
            port=int(ui.get("port", 8765)),
        ),
    )


def resolve_api_key(config: FileComparisonRuntimeConfig) -> str:
    """读取首选 provider 的有效 API key。"""
    return resolve_provider_api_key(config.llm.primary)


def resolve_provider_api_key(provider: LLMProviderConfig) -> str:
    """优先读取明文 key，否则退回到环境变量。"""
    if provider.api_key:
        return provider.api_key
    return os.environ.get(provider.api_key_env, "").strip()


def _build_provider_config(
    payload: dict[str, Any],
    *,
    retry_payload: dict[str, Any],
    max_concurrency: int,
) -> LLMProviderConfig:
    """把原始 JSON provider 段归一化成强类型配置对象。"""
    return LLMProviderConfig(
        provider=str(payload.get("provider", "openai-responses")).strip() or "openai-responses",
        model=str(payload.get("model", "gpt-5.4-mini")).strip() or "gpt-5.4-mini",
        api_key=str(payload.get("api_key", "")).strip(),
        api_key_env=str(payload.get("api_key_env", "OPENAI_API_KEY")).strip() or "OPENAI_API_KEY",
        base_url=str(payload.get("base_url", "https://api.openai.com")).strip() or "https://api.openai.com",
        timeout_seconds=float(payload.get("timeout_seconds", 60.0)),
        max_retries=max(0, int(payload.get("max_retries", 3))),
        retry_backoff_seconds=float(payload.get("retry_backoff_seconds", 2.0)),
        max_concurrency=max(1, int(payload.get("max_concurrency", max_concurrency))),
        honor_retry_after=bool(payload.get("honor_retry_after", retry_payload.get("honor_retry_after", True))),
        jitter_seconds=max(0.0, float(payload.get("jitter_seconds", retry_payload.get("jitter_seconds", 0.5)))),
        min_interval_seconds=max(0.0, float(payload.get("min_interval_seconds", 0.0))),
        failure_cooldown_seconds=max(0.0, float(payload.get("failure_cooldown_seconds", 40.0))),
    )


def _build_task_routing_config(
    llm_payload: dict[str, Any],
    providers: tuple[LLMProviderConfig, ...],
) -> LLMTaskRoutingRuntimeConfig:
    """把批次路由配置规整成只包含有效 provider 的顺序。"""
    task_routing_payload = llm_payload.get("task_routing", {}) if isinstance(llm_payload.get("task_routing"), dict) else {}
    provider_names = [provider.provider for provider in providers]
    configured_names = task_routing_payload.get("batch_compare")
    if isinstance(configured_names, list) and configured_names:
        filtered_names = tuple(name for name in (str(item).strip() for item in configured_names) if name in provider_names)
        if filtered_names:
            provider_names = list(filtered_names)
    return LLMTaskRoutingRuntimeConfig(
        enabled=bool(task_routing_payload.get("enabled", True)),
        batch_compare=tuple(provider_names),
    )


def _merge_legacy_provider_fields(provider_payload: dict[str, Any], llm_payload: dict[str, Any]) -> dict[str, Any]:
    """把旧版扁平 llm 字段并回首个 provider，维持兼容。"""
    merged = dict(provider_payload)
    for key in (
        "provider",
        "model",
        "api_key",
        "api_key_env",
        "base_url",
        "timeout_seconds",
        "max_retries",
        "retry_backoff_seconds",
        "min_interval_seconds",
        "failure_cooldown_seconds",
    ):
        if key in llm_payload:
            merged[key] = llm_payload[key]
    return merged


def _strip_json_comments(text: str) -> str:
    """移除 JSONC 风格注释，同时保留字符串字面量里的斜杠内容。"""
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                result.append("\n" if text[index] in "\r\n" else " ")
                index += 1
            index += 2 if index + 1 < len(text) else 0
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置字典。"""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
