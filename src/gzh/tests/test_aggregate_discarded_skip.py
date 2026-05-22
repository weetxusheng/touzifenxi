"""价值预筛剔除：不生成成品 HTML、不做后续维度。"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from gzh_pipeline.dajiala.html import wrap_html
from gzh_pipeline.parse import aggregate as agg_mod
from gzh_pipeline.parse import dimensions as dims_mod
from gzh_pipeline.parse import llm_compat
from gzh_pipeline.parse import review as rev_mod
from gzh_pipeline.parse import value_gate as vg_mod
from gzh_pipeline.parse.aggregate import run_aggregate_job
from gzh_pipeline.constants import AGGREGATE_PARSE_VERSION


def _stub_cfg() -> MagicMock:
    cfg = MagicMock(spec=llm_compat.OpenAICompatConfig)
    cfg.dimension_http_slug = "stub_cc"
    cfg.model = "stub-m"
    cfg.provider_label = "stub/x"
    cfg.api_base = "https://stub.example/v1"
    cfg.api_key = "stub-key"
    return cfg


def _install_gate_stub(monkeypatch, *, valuable_by_stem: dict[str, bool]) -> None:
    cfg = _stub_cfg()

    def fake_post(
        _cfg,
        messages,
        _trace,
        *,
        http_label: str,
        **_: Any,
    ):
        if http_label.endswith("_value_gate"):
            blob = ""
            for m in messages:
                c = m.get("content")
                if isinstance(c, str):
                    blob += c
            stem = ""
            for line in blob.splitlines():
                if line.startswith("源文件名："):
                    stem = line.split("：", 1)[-1].strip()
                    break
            valuable = valuable_by_stem.get(stem, True)
            return (
                json.dumps(
                    {
                        "valuable": valuable,
                        "category": "news" if valuable else "ad",
                        "reason_zh": "ok" if valuable else "low value",
                    },
                    ensure_ascii=False,
                ),
                None,
            )
        if http_label.endswith("_dimension_review"):
            return (
                json.dumps(
                    {
                        "facts_html": "<p>rf</p>",
                        "background_html": "<p>rb</p>",
                        "impact_html": "<p>ri</p>",
                        "counterpoints_html": "<p>rc</p>",
                        "review_notes_zh": "",
                    },
                    ensure_ascii=False,
                ),
                None,
            )
        return (
            json.dumps(
                {
                    "facts_html": "<p>f</p>",
                    "background_html": "<p>b</p>",
                    "impact_html": "<p>i</p>",
                    "counterpoints_html": "<p>c</p>",
                },
                ensure_ascii=False,
            ),
            None,
        )

    monkeypatch.setattr(agg_mod, "resolve_openai_compat_llm", lambda: cfg)
    monkeypatch.setattr(dims_mod, "resolve_openai_compat_llm", lambda: cfg)
    monkeypatch.setattr(dims_mod, "post_chat_completions", fake_post)
    monkeypatch.setattr(vg_mod, "post_chat_completions", fake_post)
    monkeypatch.setattr(rev_mod, "post_chat_completions", fake_post)
    monkeypatch.setattr(llm_compat, "post_chat_completions", fake_post)

def test_per_source_discarded_skips_html_and_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv("GZH_AGGREGATE_ONE_HTML_PER_SOURCE", "1")
    _install_gate_stub(monkeypatch, valuable_by_stem={"good": True, "bad": False})

    day_dir = "20260518"
    acc = "AccSkip"
    src_dir = tmp_path / "exports" / day_dir / acc
    src_dir.mkdir(parents=True)
    (src_dir / "good.html").write_text(
        wrap_html("好", "https://mp.weixin.qq.com/s/g", "<p>好</p>"), encoding="utf-8"
    )
    (src_dir / "bad.html").write_text(
        wrap_html("差", "https://mp.weixin.qq.com/s/b", "<p>差</p>"), encoding="utf-8"
    )

    out_dir = tmp_path / "parsed" / day_dir / acc
    stale = out_dir / "bad.html"
    out_dir.mkdir(parents=True, exist_ok=True)
    stale.write_text("<html>old discarded product</html>", encoding="utf-8")

    summary = run_aggregate_job(
        biz_date="2026-05-18",
        account=acc,
        input_root=tmp_path / "exports",
        output_root=tmp_path / "parsed",
        audit_root=tmp_path / "audit",
        output_stem="summary",
        force=True,
    )

    assert summary["status"] == "success"
    assert summary["valuable_source_count"] == 1
    assert summary["discarded_source_count"] == 1
    assert (out_dir / "good.html").is_file()
    assert not stale.is_file()
    bad_state = out_dir / ".bad.aggregate.state.json"
    assert bad_state.is_file()
    st = json.loads(bad_state.read_text(encoding="utf-8"))
    assert st["valuable"] is False
    assert st["parse_version"] == AGGREGATE_PARSE_VERSION

    rows = {r["stem"]: r for r in summary["per_source_outputs"]}
    assert rows["good"]["path"] and rows["good"]["valuable"] is True
    assert rows["bad"]["path"] is None and rows["bad"]["valuable"] is False

    trace = json.loads(list((tmp_path / "audit").glob("**/*.trace.json"))[0].read_text(encoding="utf-8"))
    kinds = [s.get("kind") for s in trace["steps"]]
    assert "value_gate_skip_output" in kinds
    assert "dimension_infer" not in kinds or all(
        "bad" not in (s.get("label") or "") for s in trace["steps"] if s.get("kind") == "dimension_infer"
    )
