"""gzh_pipeline.cli.parse 参数默认值。"""

from gzh_pipeline.cli.parse import build_parser


def test_cli_default_always_overwrites_unless_idempotent_flag():
    p = build_parser()
    ns = p.parse_args(["--biz-date", "20260518", "--account", "A"])
    assert ns.idempotent_skip is False
    assert ns.biz_date == "20260518"
    assert ns.account == "A"


def test_cli_biz_date_and_account_optional():
    p = build_parser()
    ns = p.parse_args([])
    assert ns.biz_date is None
    assert ns.account is None


def test_cli_idempotent_skip_flag():
    p = build_parser()
    ns = p.parse_args(["--biz-date", "20260518", "--account", "A", "--idempotent-skip"])
    assert ns.idempotent_skip is True
