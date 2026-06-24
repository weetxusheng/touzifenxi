"""GZH_PARSE_STRICT_LLM：失败即中止、禁止规则回退。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gzh_pipeline.dajiala.html import wrap_html
from gzh_pipeline.parse import aggregate as agg_mod
from gzh_pipeline.parse import dimensions as dims_mod
from gzh_pipeline.parse import llm_compat
from gzh_pipeline.parse import value_gate as value_gate_mod
from gzh_pipeline.parse.aggregate import run_aggregate_job
from gzh_pipeline.parse.errors import StrictLlmFailure


def test_build_dimensions_raises_on_llm_failure(monkeypatch):
    cfg = MagicMock(spec=llm_compat.OpenAICompatConfig)
    cfg.model = "m"
    cfg.provider_label = "fake/x"
    cfg.dimension_http_slug = "fake_slug"
    monkeypatch.setattr(dims_mod, "resolve_openai_compat_llm", lambda: cfg)
    monkeypatch.setattr(
        dims_mod,
        "post_chat_completions",
        lambda *a, **k: (None, "simulated transport error"),
    )

    from gzh_pipeline.parse.extract import SourceArticle

    ar = SourceArticle(
        Path("t.html"),
        "t",
        "T",
        "",
        "<p>x</p>",
        "0" * 64,
        "正文",
    )
    with pytest.raises(StrictLlmFailure) as ei:
        dims_mod.build_dimensions([ar], None)
    assert ei.value.step == "dimension_infer"


def test_strict_aggregate_job_writes_no_html(tmp_path, monkeypatch):
    """严格模式价值预筛 API 失败：status=failed，无 summary.html，trace 含 job_failed。"""
    monkeypatch.setenv("GZH_PARSE_STRICT_LLM", "1")
    monkeypatch.setenv("GZH_DIMENSION_REVIEW_LLM", "0")

    cfg = MagicMock(spec=llm_compat.OpenAICompatConfig)
    cfg.model = "m"
    cfg.provider_label = "fake/x"
    cfg.dimension_http_slug = "fake_slug"
    monkeypatch.setattr(agg_mod, "resolve_openai_compat_llm", lambda: cfg)
    monkeypatch.setattr(
        value_gate_mod,
        "post_chat_completions",
        lambda *a, **k: (None, "gate down"),
    )

    day_in = "2026-05-18"
    day_dir = "20260518"
    acc = "TestAcc"
    src_root = tmp_path / "exports"
    src_dir = src_root / day_dir / acc
    src_dir.mkdir(parents=True)
    (src_dir / "a.html").write_text(
        wrap_html("标题", "https://mp.weixin.qq.com/s/a", "<p>正文</p>"), encoding="utf-8"
    )

    out_root = tmp_path / "parsed"
    audit_root = tmp_path / "audit"
    summary = run_aggregate_job(
        biz_date=day_in,
        account=acc,
        input_root=src_root,
        output_root=out_root,
        audit_root=audit_root,
        output_stem="summary",
    )

    assert summary["status"] == "failed"
    assert summary.get("strict_llm") is True
    assert summary.get("strict_llm_step") == "value_gate"
    assert summary.get("output_path") is None
    html_path = out_root / day_dir / acc / "summary.html"
    assert not html_path.is_file()

    traces = list(audit_root.glob("**/*.trace.json"))
    assert traces
    text = traces[0].read_text(encoding="utf-8")
    assert "job_failed" in text
    assert "value_gate" in text
