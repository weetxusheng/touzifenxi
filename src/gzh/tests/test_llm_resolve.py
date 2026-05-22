"""LLM 端点解析（无网络）。"""

import os

import pytest

from gzh_pipeline.parse import llm_compat


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    keys = (
        "DEEPSEEK_API_KEY",
        "ALIYUN_DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "GZH_USE_DEEPSEEK",
        "GZH_DIMENSION_LLM",
    )
    for k in keys:
        monkeypatch.delenv(k, raising=False)


def test_resolve_prefers_deepseek_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("DEEPSEEK_API_BASE", "https://example.com/custom/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m1")
    cfg = llm_compat.resolve_openai_compat_llm()
    assert cfg is not None
    assert cfg.api_base == "https://example.com/custom/v1"
    assert cfg.model == "m1"
    assert llm_compat.chat_completions_url(cfg) == "https://example.com/custom/v1/chat/completions"


def test_resolve_accepts_aliyun_key(monkeypatch):
    monkeypatch.setenv("ALIYUN_DEEPSEEK_API_KEY", "yk")
    cfg = llm_compat.resolve_openai_compat_llm()
    assert cfg is not None
    assert cfg.api_key == "yk"


def test_resolve_openai_when_flag(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("GZH_DIMENSION_LLM", "1")
    monkeypatch.setenv("GZH_LLM_MODEL", "gpt-test")
    cfg = llm_compat.resolve_openai_compat_llm()
    assert cfg is not None
    assert "openai" in cfg.provider_label
    assert cfg.model == "gpt-test"


def test_resolve_none_without_keys(monkeypatch):
    assert llm_compat.resolve_openai_compat_llm() is None


def test_use_deepseek_respects_off(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("GZH_USE_DEEPSEEK", "0")
    assert llm_compat.use_deepseek_per_env() is False
    assert llm_compat.resolve_openai_compat_llm() is None


def test_parse_json_message_shared():
    from gzh_pipeline.parse.llm_compat import parse_llm_json_message

    d = parse_llm_json_message('{"a":1}')
    assert d["a"] == 1
