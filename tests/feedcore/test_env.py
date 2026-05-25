import os
from pathlib import Path

from feedcore.env import load_env_file


def test_load_env_file_overrides_existing_environment_values(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "API_KEY=sk-test-key",
                "BASE_URL=https://api.example.com",
                "EMPTY_VALUE=",
                "QUOTED_VALUE=\"hello world\"",
                "# ignored comment",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("BASE_URL", "old-value")
    monkeypatch.delenv("EMPTY_VALUE", raising=False)
    monkeypatch.delenv("QUOTED_VALUE", raising=False)

    load_env_file(env_file)

    assert os.environ["API_KEY"] == "sk-test-key"
    assert os.environ["BASE_URL"] == "https://api.example.com"
    assert os.environ["EMPTY_VALUE"] == ""
    assert os.environ["QUOTED_VALUE"] == "hello world"
