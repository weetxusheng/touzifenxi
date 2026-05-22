"""OpenAI Chat Completions 兼容层：统一 DeepSeek（含阿里云兼容入口）与 OpenAI 调用与留痕。"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from gzh_pipeline.audit.trace import TraceRecorder


def parse_llm_json_message(content: str) -> dict[str, Any]:
    """处理模型输出的纯 JSON 或 ```json fenced``` 包裹。"""
    t = content.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.I)
    if m:
        t = m.group(1).strip()
    return json.loads(t)


@dataclass(frozen=True)
class OpenAICompatConfig:
    """单次解析任务内复用的 LLM 端点配置。"""

    api_key: str
    api_base: str  # 无尾部斜杠，如 https://xxx/v1
    model: str
    provider_label: str
    dimension_http_slug: str  # 四维度请求在 trace 中的 http 标签前缀


def llm_temperature() -> float:
    return float(os.environ.get("GZH_LLM_TEMPERATURE", "0.2"))


def llm_timeout_seconds() -> float:
    return float(os.environ.get("GZH_LLM_TIMEOUT_SECONDS", "180"))


def llm_http_extra_retries() -> int:
    """首次请求失败后额外重试次数（指数退避）。"""
    return max(0, int(os.environ.get("GZH_LLM_HTTP_RETRIES", "3")))


def llm_retry_base_seconds() -> float:
    return float(os.environ.get("GZH_LLM_RETRY_BASE_SECONDS", "2"))


def _is_transient_http_failure(exc: BaseException) -> bool:
    """连接被对端复位、超时、网关抖动等——适合重试。"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 425, 429, 500, 502, 503, 504)
    if isinstance(exc, (TimeoutError, ConnectionResetError, BrokenPipeError, OSError)):
        win = getattr(exc, "winerror", None)
        if win in (10053, 10054, 10060):
            return True
        errno = getattr(exc, "errno", None)
        if errno in (104, 110):  # ECONNRESET, ETIMEDOUT on Linux/Mac
            return True
        if isinstance(exc, OSError) and exc.errno in (10053, 10054, 10060):
            return True
    if isinstance(exc, urllib.error.URLError):
        r = getattr(exc, "reason", None)
        if isinstance(r, BaseException):
            return _is_transient_http_failure(r)
        if isinstance(r, str) and ("10054" in r or "10053" in r or "timed out" in r.lower()):
            return True
    s = str(exc).lower()
    if any(
        x in s
        for x in (
            "10054",
            "10053",
            "10060",
            "远程主机强迫关闭",
            "forcibly closed",
            "connection reset",
            "connection aborted",
            "timed out",
            "without response",
            "remote end closed",
            "connection closed",
            "_eof",
            "ssl",
            "temporary failure",
        )
    ):
        return True
    return False


def parse_strict_llm_enabled() -> bool:
    """为真时：已配置 LLM 的前提下，预筛/四维/审稿任一失败则终止任务，且四维度不使用规则引擎回退。"""
    return os.environ.get("GZH_PARSE_STRICT_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def use_deepseek_per_env() -> bool:
    if os.environ.get("GZH_USE_DEEPSEEK", "1").strip().lower() in ("0", "false", "no"):
        return False
    key = (
        os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or os.environ.get("ALIYUN_DEEPSEEK_API_KEY", "").strip()
    )
    return bool(key)


def resolve_openai_compat_llm() -> OpenAICompatConfig | None:
    """
    解析任务统一入口配置。

    优先级：

    1. ``DEEPSEEK_API_KEY`` 或 ``ALIYUN_DEEPSEEK_API_KEY``（二选一），且未显式关闭 ``GZH_USE_DEEPSEEK``。
       ``DEEPSEEK_API_BASE`` 可指向阿里云百炼等 OpenAI 兼容地址。
    2. ``OPENAI_API_KEY`` + ``GZH_DIMENSION_LLM=1|true|yes``。
    """
    if use_deepseek_per_env():
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY", "").strip()
            or os.environ.get("ALIYUN_DEEPSEEK_API_KEY", "").strip()
        )
        root = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1").strip().rstrip("/")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
        return OpenAICompatConfig(
            api_key=api_key,
            api_base=root,
            model=model,
            provider_label=f"deepseek/{model}",
            dimension_http_slug="deepseek_chat_completions",
        )

    oa_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if oa_key and os.environ.get("GZH_DIMENSION_LLM", "").strip().lower() in ("1", "true", "yes"):
        model = os.environ.get("GZH_LLM_MODEL", "gpt-4o-mini").strip()
        return OpenAICompatConfig(
            api_key=oa_key,
            api_base="https://api.openai.com/v1",
            model=model,
            provider_label=f"openai/{model}",
            dimension_http_slug="openai_chat_completions",
        )
    return None


def resolve_vision_preclean_openai_compat_cfg(
    main_cfg: OpenAICompatConfig | None,
) -> OpenAICompatConfig | None:
    """
    配图识读（VL）专用 OpenAI 兼容配置。

    若设置 ``GZH_VISION_PRECLEAN_API_KEY``（或 ``ALIYUN_VISION_PRECLEAN_API_KEY``），则 **仅**
    ``post_chat_completions`` 识图调用使用该密钥（Bearer）。

    ``GZH_VISION_PRECLEAN_API_BASE`` 可选；未设时沿用主任务的 ``main_cfg.api_base``。

    未配置专用密钥时，行为与原先一致：识图与四维度/预筛共用 ``main_cfg``。
    """
    if main_cfg is None:
        return None
    vk = (
        os.environ.get("GZH_VISION_PRECLEAN_API_KEY", "").strip()
        or os.environ.get("ALIYUN_VISION_PRECLEAN_API_KEY", "").strip()
    )
    if not vk:
        return main_cfg
    vbase_raw = os.environ.get("GZH_VISION_PRECLEAN_API_BASE", "").strip().rstrip("/")
    vbase = vbase_raw if vbase_raw else main_cfg.api_base
    vm = os.environ.get("GZH_VISION_PRECLEAN_MODEL", "").strip() or main_cfg.model
    slug_extra = os.environ.get("GZH_VISION_PRECLEAN_HTTP_SLUG", "").strip()
    slug = slug_extra if slug_extra else main_cfg.dimension_http_slug
    return OpenAICompatConfig(
        api_key=vk,
        api_base=vbase,
        model=vm,
        provider_label=f"vision_preclean/{vm}",
        dimension_http_slug=slug,
    )


def chat_completions_url(cfg: OpenAICompatConfig) -> str:
    return f"{cfg.api_base}/chat/completions"


def _assistant_text_from_choice_message(msg: dict[str, Any]) -> str:
    """兼容纯文本内容与百炼/DashScope 等 thinking 场景的 ``reasoning_content``。"""
    if not isinstance(msg, dict):
        return str(msg).strip()
    txt = msg.get("content")
    txt = txt.strip() if isinstance(txt, str) else ""
    if txt:
        return txt
    reason = msg.get("reasoning_content")
    return str(reason).strip() if reason is not None else ""


def post_chat_completions(
    cfg: OpenAICompatConfig,
    messages: list[dict[str, Any]],
    trace: TraceRecorder | None,
    *,
    http_label: str,
    temperature: float | None = None,
    model_override: str | None = None,
    merge_payload: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[str | None, str | None]:
    """
    POST /chat/completions。

    对连接被复位、超时、429/5xx 等做有限次重试（``GZH_LLM_HTTP_RETRIES``）。

    返回 ``(assistant_content, error_message)``；成功时 error 为 ``None``。

    ``model_override``：与主任务不同型号的调用（例如两段式配图识读的 Qwen 多模态）。

    ``merge_payload``：合入请求 JSON 顶层（不得含 ``messages`` / ``model``），用于百炼等
    ``enable_thinking`` 等扩展字段。

    ``timeout_seconds``：覆盖单次 ``GZH_LLM_TIMEOUT_SECONDS``（识图较慢时可单独拉长）。
    """
    url = chat_completions_url(cfg)
    url_log = url.split("?", 1)[0]
    temp = llm_temperature() if temperature is None else temperature
    effective_model = (model_override if model_override else cfg.model).strip()
    payload: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": temp,
    }
    if merge_payload:
        for k, v in merge_payload.items():
            if k in ("messages", "model"):
                continue
            payload[k] = v
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timeout_s = llm_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
    extra = llm_http_extra_retries()
    base_sleep = llm_retry_base_seconds()
    last_err: str | None = None

    for attempt in range(extra + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                if trace:
                    trace.add_http_request(
                        http_label,
                        "POST",
                        url_log,
                        request_headers={"Content-Type": "application/json", "Authorization": "Bearer ***"},
                        request_body={
                            "model": effective_model,
                            "messages_omitted": True,
                            **({k: v for k, v in (merge_payload or {}).items() if k not in ("messages", "model")}),
                        },
                        response_status=getattr(resp, "status", 200),
                        response_body=parsed if len(raw) < 500_000 else {"note": "response large", "snippet": parsed},
                        elapsed_ms=elapsed_ms,
                        ok=True,
                    )
                choice0 = parsed["choices"][0]
                msg = choice0.get("message")
                if isinstance(msg, dict):
                    txt = _assistant_text_from_choice_message(msg)
                else:
                    txt = ""
                return txt, None
        except Exception as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            last_err = str(e)
            code = getattr(e, "code", None) if isinstance(e, urllib.error.HTTPError) else None
            if trace:
                trace.add_http_request(
                    f"{http_label}_try{attempt + 1}",
                    "POST",
                    url_log,
                    request_headers={"Content-Type": "application/json", "Authorization": "Bearer ***"},
                    request_body={
                        "model": effective_model,
                        "messages_omitted": True,
                    },
                    response_status=code,
                    response_body=None,
                    elapsed_ms=elapsed_ms,
                    ok=False,
                    err=last_err,
                )
            if attempt < extra and _is_transient_http_failure(e):
                delay = base_sleep * (2**attempt)
                if trace:
                    trace.add_step(
                        "http_retry_wait",
                        http_label,
                        True,
                        {"attempt": attempt + 1, "next_delay_s": round(delay, 2), "error": last_err[:500]},
                    )
                time.sleep(delay)
                continue
            return None, last_err

    return None, last_err or "unknown"
