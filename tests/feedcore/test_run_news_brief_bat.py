from pathlib import Path


def test_run_feedcore_news_brief_bat_points_to_profile_scripts():
    text = Path("scripts/run_feedcore_news_brief.bat").read_text(encoding="utf-8")

    assert ".env" in text
    assert "MODEL, BASE_URL, and API_KEY" in text
    assert "feedcore_%PROFILE%.py" in text
    assert "output\\reports\\feedcore_report" in text
    assert "config.yaml" not in text
    assert "MINIMAX_API_KEY" not in text


def test_cli_quick_sample_size_zero_means_all_sources():
    from feedcore.cli import _with_cli_overrides
    from feedcore.config import parse_config

    config = parse_config({"rss_urls": ["https://example.com/rss"], "quick_sample_size": 10})
    overridden = _with_cli_overrides(config, quick_sample_size=0, max_articles=None)
    assert overridden.quick_sample_size == 0
