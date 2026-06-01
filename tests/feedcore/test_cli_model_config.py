from pathlib import Path

import pytest

from feedcore.cli import DeepTranslatorClient, _make_summary_client, main
from feedcore.config import parse_config
from feedcore.openai_compatible_client import OpenAICompatibleClient


def test_make_summary_client_requires_complete_model_config(monkeypatch):
    for key in ("MODEL", "BASE_URL", "API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config = parse_config({"rss_urls": ["https://news.google.com/rss/search?q=OpenAI"]})

    with pytest.raises(ValueError, match="API_KEY"):
        _make_summary_client(config)


def test_make_summary_client_uses_configured_model(monkeypatch):
    monkeypatch.setenv("MODEL", "configured-model")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("API_KEY", "test-key")
    config = parse_config({"rss_urls": ["https://news.google.com/rss/search?q=OpenAI"]})

    client = _make_summary_client(config)

    assert isinstance(client, OpenAICompatibleClient)
    assert client.model == "configured-model"
    assert client.base_url == "https://api.example.com"


def test_deep_translator_client_splits_long_text(monkeypatch):
    calls = []

    class FakeGoogleTranslator:
        def __init__(self, source: str, target: str) -> None:
            self.source = source
            self.target = target

        def translate(self, text: str) -> str:
            calls.append(text)
            if len(text) > 5000:
                raise AssertionError("chunk too long")
            return f"译:{len(text)}"

    monkeypatch.setattr("deep_translator.GoogleTranslator", FakeGoogleTranslator)

    result = DeepTranslatorClient().translate_to_chinese(("a" * 4800) + "\n\n" + ("b" * 4800))

    assert result == "译:4800\n\n译:4800"
    assert len(calls) == 2


def test_cli_passes_workflow_concurrency_to_run_workflow(tmp_path: Path, monkeypatch):
    import json

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "rss_urls": ["https://feed.example/rss"],
                "output_dir": tmp_path.as_posix(),
                "model": {"enabled": True},
                "workflow": {
                    "rss_concurrency": 3,
                    "article_fetch_concurrency": 4,
                    "model_concurrency": 7,
                    "type_classification_concurrency": 5,
                    "type_synthesis_concurrency": 6,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["news-brief", "--config", str(config_path)])
    monkeypatch.setattr("feedcore.cli.load_env_file", lambda path: None)
    monkeypatch.setattr("feedcore.cli._make_summary_client", lambda config: object())
    monkeypatch.setattr("feedcore.cli.RequestsRssClient", lambda: object())
    monkeypatch.setattr("feedcore.cli.ArticleClient", lambda **kwargs: object())
    monkeypatch.setattr("feedcore.cli.DeepTranslatorClient", lambda: object())
    captured = {}

    def fake_run_workflow(**kwargs):
        captured.update(kwargs)

        class Result:
            run_dir = tmp_path / "run"

        return Result()

    monkeypatch.setattr("feedcore.cli.run_workflow", fake_run_workflow)

    main()

    assert captured["rss_concurrency"] == 3
    assert captured["article_fetch_concurrency"] == 4
    assert captured["model_concurrency"] == 7
    assert captured["type_classification_concurrency"] == 5
    assert captured["type_synthesis_concurrency"] == 6
