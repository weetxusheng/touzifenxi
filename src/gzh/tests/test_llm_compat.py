"""llm_compat：VL 专用密钥解析。"""

import pytest

from gzh_pipeline.parse.llm_compat import OpenAICompatConfig, resolve_vision_preclean_openai_compat_cfg


@pytest.fixture
def main_cfg() -> OpenAICompatConfig:
    return OpenAICompatConfig(
        api_key="main-key",
        api_base="https://api.example/v1",
        model="deepseek-chat",
        provider_label="deepseek/deepseek-chat",
        dimension_http_slug="deepseek_chat_completions",
    )


def test_resolve_vision_preclean_fallback_shared(monkeypatch, main_cfg):
    monkeypatch.delenv("GZH_VISION_PRECLEAN_API_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_VISION_PRECLEAN_API_KEY", raising=False)
    v = resolve_vision_preclean_openai_compat_cfg(main_cfg)
    assert v is main_cfg


def test_resolve_vision_preclean_dedicated_key(monkeypatch, main_cfg):
    monkeypatch.setenv("GZH_VISION_PRECLEAN_API_KEY", "vl-only-key")
    monkeypatch.delenv("ALIYUN_VISION_PRECLEAN_API_KEY", raising=False)
    v = resolve_vision_preclean_openai_compat_cfg(main_cfg)
    assert v is not main_cfg
    assert v.api_key == "vl-only-key"
    assert v.api_base == main_cfg.api_base


def test_resolve_vision_preclean_dedicated_base(monkeypatch, main_cfg):
    monkeypatch.setenv("GZH_VISION_PRECLEAN_API_KEY", "vl-only-key")
    monkeypatch.setenv("GZH_VISION_PRECLEAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("GZH_VISION_PRECLEAN_MODEL", "qwen-vl-max")
    v = resolve_vision_preclean_openai_compat_cfg(main_cfg)
    assert v.api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert v.model == "qwen-vl-max"


def test_resolve_vision_preclean_none_main():
    assert resolve_vision_preclean_openai_compat_cfg(None) is None
