"""
从项目根「config/runtime.local.json」→ sources.kr36 读真实 volc 配置，验凭证解析与请求头（不发 HTTP）。

本机未配置或全为空时，整模块跳过。勿把密钥写进用例，一律读本地 json。
"""

from __future__ import annotations

import pytest
from c114.runtime.config import read_c114_local_config

from kr36.volc_speech import (
    VOLC_BIGASR_RESOURCE_ID,
    resolve_volc_speech_credentials,
)


def _kr36_volc_config_if_configured() -> dict | None:
    cfg = read_c114_local_config()
    k36 = (cfg.get("sources") or {}).get("kr36")
    if not isinstance(k36, dict):
        return None
    api = str(k36.get("volc_speech_api_key") or "").strip()
    app = str(k36.get("volc_speech_app_key") or "").strip()
    acc = str(k36.get("volc_speech_access_key") or "").strip()
    if api or (app and acc):
        return k36
    return None


def test_volc_speech_build_headers_uses_real_runtime_config() -> None:
    k36 = _kr36_volc_config_if_configured()
    if not k36:
        pytest.skip("config/runtime.local.json 中 sources.kr36 未配置 volc_speech_api_key 或 app_key+access_key")

    creds = resolve_volc_speech_credentials(config=k36)
    assert creds is not None
    h = creds.build_headers(request_id="00000000-0000-4000-8000-000000000001")

    assert h["X-Api-Resource-Id"] == VOLC_BIGASR_RESOURCE_ID
    assert h["X-Api-Request-Id"] == "00000000-0000-4000-8000-000000000001"
    assert h["X-Api-Sequence"] == "-1"

    if creds.api_key:
        assert not creds.app_key
        assert h["X-Api-Key"] == creds.api_key
    else:
        assert creds.app_key
        assert creds.access_key
        assert h["X-Api-App-Key"] == creds.app_key
        assert h["X-Api-Access-Key"] == creds.access_key
        assert "X-Api-Key" not in h
