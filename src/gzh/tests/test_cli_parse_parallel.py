"""多公众号并行解析 CLI。"""

import threading
import time
from pathlib import Path

from gzh_pipeline.cli import parse as parse_mod
from gzh_pipeline.dajiala.html import wrap_html


def test_resolve_parse_account_workers_defaults_and_caps():
    assert parse_mod.resolve_parse_account_workers(None) == 10
    assert parse_mod.resolve_parse_account_workers(3) == 3
    assert parse_mod.resolve_parse_account_workers(99) == 10
    assert parse_mod.resolve_parse_account_workers(0) == 1


def test_resolve_parse_account_workers_from_env(monkeypatch):
    monkeypatch.setenv("GZH_PARSE_ACCOUNT_WORKERS", "4")
    assert parse_mod.resolve_parse_account_workers(None) == 4


def _seed_exports(root: Path, biz_date: str, accounts: list[str]) -> Path:
    exports = root / "exports"
    for acc in accounts:
        d = exports / biz_date / acc
        d.mkdir(parents=True)
        (d / "a.html").write_text(
            wrap_html("标题", "https://mp.weixin.qq.com/s/x", "<p>正文</p>"),
            encoding="utf-8",
        )
    return exports


def test_main_parallel_accounts_runs_concurrently(tmp_path, monkeypatch):
    exports = _seed_exports(tmp_path, "20260520", ["A", "B", "C"])
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_job(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.25)
        with lock:
            active -= 1
        return {"status": "success", "account": kwargs["account"]}

    monkeypatch.setattr(parse_mod, "run_aggregate_job", lambda **kw: fake_job(**kw))
    monkeypatch.setenv("GZH_PARSE_ACCOUNT_WORKERS", "3")

    rc = parse_mod.main(
        [
            "--biz-date",
            "20260520",
            "--input-root",
            str(exports),
            "--output-root",
            str(tmp_path / "out"),
            "--audit-json-root",
            str(tmp_path / "audit"),
            "--json",
        ]
    )
    assert rc == 0
    assert peak >= 2


def test_main_single_account_stays_serial_workers_one(tmp_path, monkeypatch):
    exports = _seed_exports(tmp_path, "20260520", ["Only"])
    peak = 0
    lock = threading.Lock()

    def fake_job(**kwargs):
        nonlocal peak
        with lock:
            peak = max(peak, 1)
        return {"status": "success"}

    monkeypatch.setattr(parse_mod, "run_aggregate_job", lambda **kw: fake_job(**kw))
    monkeypatch.setenv("GZH_PARSE_ACCOUNT_WORKERS", "10")

    parse_mod.main(
        [
            "--biz-date",
            "20260520",
            "--account",
            "Only",
            "--input-root",
            str(exports),
            "--output-root",
            str(tmp_path / "out"),
            "--audit-json-root",
            str(tmp_path / "audit"),
            "--json",
        ]
    )
    assert peak == 1


def test_cli_account_workers_arg():
    ns = parse_mod.build_parser().parse_args(["--account-workers", "2"])
    assert ns.account_workers == 2
