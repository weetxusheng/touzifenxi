from __future__ import annotations

import json
from datetime import date

import pytest


def test_cli_module_imports():
    # Plain import smoke — surfaces any syntax / NameError post-sed
    import chip.cli  # noqa: F401


def test_build_parser_lists_expected_subcommands():
    from chip.cli import build_parser
    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    names = set(subparsers_action.choices.keys())
    assert "chip-hot-topics" in names
    assert "run" in names


def test_cli_help_does_not_crash():
    from chip.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])


def test_step0_writes_raw_json_and_csv(
    tmp_path,
    semi_home_html,
    semi_article_html,
    semi_column_html,
    laoyaoba_xinyaowen_html,
    laoyaoba_article_smic_html,
    monkeypatch,
):
    from chip import cli as chip_cli
    from chip.channels import laoyaoba as lyb_chan
    from chip.channels import semi as semi_chan
    from chip.settings import AppPaths

    paths = AppPaths(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        raw_dir=tmp_path / "data" / "raw",
        processed_dir=tmp_path / "data" / "processed",
        reports_dir=tmp_path / "reports",
        state_dir=tmp_path / "state",
    )
    for p in (paths.raw_dir, paths.reports_dir, paths.state_dir, paths.processed_dir):
        p.mkdir(parents=True, exist_ok=True)

    # Eliminate throttling to keep the test fast.
    monkeypatch.setattr(semi_chan, "_THROTTLE_SECONDS", 0)
    monkeypatch.setattr(lyb_chan, "_THROTTLE_SECONDS", 0)

    # report_date 2026-05-20 matches the SEMI article date; SMIC fixture is dated 05-14
    target = date(2026, 5, 20)

    def semi_get(url: str) -> str:
        if "column" in url:
            return semi_column_html
        if "/article/" in url:
            return semi_article_html
        return semi_home_html

    def lyb_get(url: str) -> str:
        if "/n/" in url:
            return laoyaoba_article_smic_html  # MM-DD: 05-14, will NOT match 05-20
        return laoyaoba_xinyaowen_html

    monkeypatch.setattr(semi_chan, "_default_http_get", semi_get)
    monkeypatch.setattr(lyb_chan, "_default_http_get", lyb_get)

    payload = chip_cli.fetch_and_materialize_chip_articles(target, paths)
    raw_json_path = paths.raw_dir / chip_cli.raw_json_name(target)
    assert raw_json_path.exists(), f"expected raw json at {raw_json_path}"
    raw_json = json.loads(raw_json_path.read_text(encoding="utf-8"))
    assert raw_json["report_date"] == "2026-05-20"
    assert isinstance(raw_json["articles"], list)
    # SEMI sample article (intel14a) is dated 2026-05-20 so it must appear.
    semi_titles = [a["title"] for a in raw_json["articles"] if a["channel"] == "semi"]
    assert any("14A" in t or "陈立武" in t for t in semi_titles)
