from pathlib import Path


def test_run_feedcore_us_news_windows_bat_points_to_profile_script():
    text = Path("scripts/feedcore/run_feedcore_us_news_windows.bat").read_text(encoding="utf-8")

    assert "feedcore_us_news.py" in text
    assert "logs\\feedcore" in text
    assert "PROJECT_ROOT" in text


def test_cli_quick_sample_size_zero_means_all_sources():
    from feedcore.cli import _with_cli_overrides
    from feedcore.config import parse_config

    config = parse_config({"rss_urls": ["https://example.com/rss"], "quick_sample_size": 10})
    overridden = _with_cli_overrides(config, quick_sample_size=0, max_articles=None)
    assert overridden.quick_sample_size == 0
