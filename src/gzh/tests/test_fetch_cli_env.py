"""cli.fetch：环境变量默认抓取模式。"""

from pathlib import Path

from gzh_pipeline.cli import fetch as fetch_mod


def test_canonical_fetch_mode_aliases():
    assert fetch_mod._canonical_fetch_mode("today") == "today"
    assert fetch_mod._canonical_fetch_mode("yesterday") == "yesterday"
    assert fetch_mod._canonical_fetch_mode("prev_day") == "yesterday"
    assert fetch_mod._canonical_fetch_mode("lastday") == "latest"
    assert fetch_mod._canonical_fetch_mode("LAST") == "latest"
    assert fetch_mod._canonical_fetch_mode("allday") == "all"
    assert fetch_mod._canonical_fetch_mode("all-day") == "all"
    assert fetch_mod._canonical_fetch_mode("bogus") is None


def test_fetch_mode_default_from_env(monkeypatch):
    monkeypatch.delenv("GZH_DAJIALA_FETCH_MODE", raising=False)
    assert fetch_mod.fetch_mode_default_from_env() == "today"
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MODE", "yesterday")
    assert fetch_mod.fetch_mode_default_from_env() == "yesterday"
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MODE", "all")
    assert fetch_mod.fetch_mode_default_from_env() == "all"


def test_resolve_fetch_target_date_yesterday():
    export_mode, iso = fetch_mod.resolve_fetch_target_date("yesterday", "2026-05-21")
    assert export_mode == "yesterday"
    assert iso == "2026-05-20"


def test_resolve_fetch_target_date_today():
    export_mode, iso = fetch_mod.resolve_fetch_target_date("today", "2026-05-21")
    assert export_mode == "today"
    assert iso == "2026-05-21"


def test_fetch_max_pages_default_from_env(monkeypatch):
    monkeypatch.delenv("GZH_DAJIALA_FETCH_MAX_PAGES", raising=False)
    assert fetch_mod.fetch_max_pages_default_from_env() == 1
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MAX_PAGES", "5")
    assert fetch_mod.fetch_max_pages_default_from_env() == 5
    monkeypatch.setenv("GZH_DAJIALA_FETCH_MAX_PAGES", "0")
    assert fetch_mod.fetch_max_pages_default_from_env() == 1


def test_load_accounts_from_config(tmp_path):
    cfg = tmp_path / "accounts.txt"
    cfg.write_text("# comment\n证券时报\n\n人民日报\n", encoding="utf-8")
    got = fetch_mod.load_accounts_from_config(cfg)
    assert got == ["证券时报", "人民日报"]


def test_resolve_fetch_accounts_prefers_cli(tmp_path):
    got, used = fetch_mod.resolve_fetch_accounts("cli_only", None)
    assert got == ["cli_only"]
    assert used is None


def test_resolve_fetch_accounts_from_config_file(tmp_path):
    cfg = tmp_path / "my_accounts.txt"
    cfg.write_text("acc_a\nacc_b\n", encoding="utf-8")
    got, used = fetch_mod.resolve_fetch_accounts(None, str(cfg))
    assert got == ["acc_a", "acc_b"]
    assert used == str(cfg.resolve())


def test_resolve_fetch_accounts_skips_hash_comments_in_file(tmp_path):
    cfg = tmp_path / "accounts.txt"
    cfg.write_text(
        "# 待抓取公众号\n证券时报\n第一财经\n",
        encoding="utf-8",
    )
    got, _used = fetch_mod.resolve_fetch_accounts(None, str(cfg))
    assert got == ["证券时报", "第一财经"]


def test_resolve_fetch_accounts_default_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.txt").write_text("默认号\n", encoding="utf-8")
    got, used = fetch_mod.resolve_fetch_accounts(None, None)
    assert got == ["默认号"]
    assert used and "accounts.txt" in used
