from kr36.volc_speech import (
    VOLC_BIGASR_RESOURCE_ID,
    VolcSpeechCredentials,
    resolve_volc_speech_credentials,
    resolve_volc_speech_credentials_from_mapping,
)


def test_volc_speech_api_key_headers() -> None:
    creds = VolcSpeechCredentials(uid="u1", api_key="key9")
    h = creds.build_headers(request_id="rid-fixed")
    assert h["X-Api-Key"] == "key9"
    assert h["X-Api-Request-Id"] == "rid-fixed"
    assert h["X-Api-Resource-Id"] == VOLC_BIGASR_RESOURCE_ID
    assert h["X-Api-Sequence"] == "-1"


def test_volc_speech_legacy_headers() -> None:
    creds = VolcSpeechCredentials(uid="u2", app_key="app", access_key="tok")
    h = creds.build_headers()
    assert h["X-Api-App-Key"] == "app"
    assert h["X-Api-Access-Key"] == "tok"
    assert "X-Api-Key" not in h


def test_volc_speech_from_mapping_api_key() -> None:
    c = resolve_volc_speech_credentials_from_mapping(
        {"volc_speech_api_key": "k-json", "volc_speech_uid": "uid1"}
    )
    assert c is not None
    assert c.api_key == "k-json"
    assert c.uid == "uid1"


def test_resolve_volc_speech_credentials_from_runtime_only_ignores_env(monkeypatch) -> None:
    monkeypatch.setenv("VOLC_SPEECH_API_KEY", "from-env")
    assert resolve_volc_speech_credentials(config=None) is None
    assert resolve_volc_speech_credentials(config={}) is None
    c = resolve_volc_speech_credentials(config={"volc_speech_api_key": "from-config"})
    assert c is not None
    assert c.api_key == "from-config"
