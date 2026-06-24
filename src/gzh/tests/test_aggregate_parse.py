"""aggregate_parse MVP 测试."""

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
from gzh_pipeline.parse.extract import extract_from_export_html
from gzh_pipeline.parse.images import IMG_ATTR_REMOTE_FOR_VISION
from gzh_pipeline.parse.vision_llm import list_http_image_urls


def _stub_openai_compat_cfg() -> MagicMock:
    cfg = MagicMock(spec=llm_compat.OpenAICompatConfig)
    cfg.dimension_http_slug = "stub_cc"
    cfg.model = "stub-m"
    cfg.provider_label = "stub/x"
    cfg.api_base = "https://stub.example/v1"
    cfg.api_key = "stub-key"
    return cfg


def _flatten_llm_messages_for_assert(messages: list[dict[str, Any]]) -> str:
    """将 chat messages 打成单段文本，便于断言正文/配图块是否送入 LLM。"""
    blobs: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            blobs.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    blobs.append(str(part.get("text") or ""))
    return "\n".join(blobs)


def _install_stub_llm(
    monkeypatch,
    *,
    vision_preclean_stub: bool = False,
    capture_posts: list[tuple[str, str]] | None = None,
) -> None:
    cfg = _stub_openai_compat_cfg()

    def fake_post(
        _cfg,
        messages,
        _trace,
        *,
        http_label: str,
        temperature: float | None = None,
        model_override: str | None = None,
        merge_payload: dict | None = None,
        timeout_seconds: float | None = None,
        **_: Any,
    ):
        if capture_posts is not None:
            capture_posts.append((http_label, _flatten_llm_messages_for_assert(messages)))
        if "_vision_preclean_" in http_label and vision_preclean_stub:
            return (
                "【测试配图识读】图1：mock 数字与中文。",
                None,
            )
        if http_label.endswith("_value_gate"):
            return (
                json.dumps(
                    {"valuable": True, "category": "news", "reason_zh": "ok"},
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


def test_list_http_image_urls_reads_mirror_preserves_remote_attr():
    """mirror 后置 src 为本地文件名时，仍可经 data-gzh-vision-src 拉取远端供识图。"""
    frag = (
        f'<img src="art__img_1.png" {IMG_ATTR_REMOTE_FOR_VISION}='
        '"https://example.invalid/m.png" />'
    )
    assert list_http_image_urls(frag, limit=3) == ["https://example.invalid/m.png"]


def test_list_http_image_urls_skips_gif_data_type():
    """动图不参与 VL 识图，且不占用每篇配图数量上限。"""
    frag = (
        '<img data-type="gif" data-src="https://example.invalid/a.gif"/>'
        '<img data-type="png" data-src="https://example.invalid/b.png"/>'
        '<img data-type="jpeg" data-src="https://example.invalid/c.jpg"/>'
    )
    assert list_http_image_urls(frag, limit=2) == [
        "https://example.invalid/b.png",
        "https://example.invalid/c.jpg",
    ]


def test_extract_wrap_html_article(tmp_path):
    raw = wrap_html("测试标题", "https://mp.weixin.qq.com/s/xx", "<p>段落<strong>粗</strong></p>")
    fp = tmp_path / "t.html"
    fp.write_text(raw, encoding="utf-8")
    ar = extract_from_export_html(raw, fp)
    assert ar.title == "测试标题"
    assert ar.source_url == "https://mp.weixin.qq.com/s/xx"
    assert "段落" in ar.body_html


def test_run_aggregate_fails_when_no_llm_config(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GZH_DIMENSION_LLM", raising=False)

    day_in = "2026-05-18"
    day_dir = "20260518"
    acc = "证券时报"
    src_root = tmp_path / "exports"
    src_dir = src_root / day_dir / acc
    src_dir.mkdir(parents=True)
    (src_dir / "a.html").write_text(
        wrap_html("A", "https://mp.weixin.qq.com/s/a", "<p>x</p>"), encoding="utf-8"
    )

    summary = run_aggregate_job(
        biz_date=day_in,
        account=acc,
        input_root=src_root,
        output_root=tmp_path / "out",
        audit_root=tmp_path / "audit",
        output_stem="summary",
    )
    assert summary["status"] == "failed"
    assert summary.get("strict_llm_step") == "job_config"
    assert "聚合解析仅限大模型" in (summary.get("error_message") or "")


def test_run_aggregate_merges_multiple_sources(tmp_path, monkeypatch):
    _install_stub_llm(monkeypatch)

    day_in = "2026-05-18"
    day_dir = "20260518"
    acc = "证券时报"
    src_root = tmp_path / "exports"
    src_dir = src_root / day_dir / acc
    src_dir.mkdir(parents=True)
    (src_dir / "a.html").write_text(
        wrap_html("A股午评", "https://mp.weixin.qq.com/s/a", "<p>正文A</p>"), encoding="utf-8"
    )
    (src_dir / "b.html").write_text(
        wrap_html("港股综述", "https://mp.weixin.qq.com/s/b", "<p>正文B</p>"), encoding="utf-8"
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

    assert summary["status"] == "success"
    assert summary["biz_date"] == "20260518"
    assert summary["source_count"] == 2
    assert "stub/x" in (summary.get("dimension_engine") or "")
    out_html = Path(summary["output_path"])
    raw_out = out_html.read_text(encoding="utf-8")
    assert raw_out.count('<section class="factor-section') == 5
    assert 'id="dim-source"' in raw_out
    assert "mp.weixin.qq.com/s/a" in raw_out
    assert "event-dimension-facts" in raw_out
    assert "<p>rf</p>" in raw_out
    assert "mp.weixin.qq.com/s/b" in raw_out
    assert "body-omitted" not in raw_out
    assert "文章目录" not in raw_out
    assert "证券时报" in raw_out
    assert "2026-05-18" in raw_out

    traces = list(audit_root.glob(f"**/{day_dir}_{acc}.trace.json"))
    assert len(traces) == 1
    data = traces[0].read_text(encoding="utf-8")
    assert "file_read" in data
    assert "llm_aggregate" in data
    assert summary["run_id"] in data

    sm = summary  # returned dict mutated by _write_trace
    assert "vision_preclean_model_outputs" in sm
    assert sm["vision_preclean_model_outputs"] == []
    er = sm.get("vision_preclean_empty_reason_zh") or ""
    assert "未启用 GZH_LLM_VISION" in er


def test_run_aggregate_one_html_per_source(tmp_path, monkeypatch):
    """GZH_AGGREGATE_ONE_HTML_PER_SOURCE：每个源文件一份成品 HTML。"""
    monkeypatch.setenv("GZH_AGGREGATE_ONE_HTML_PER_SOURCE", "1")
    _install_stub_llm(monkeypatch)

    day_in = "2026-05-18"
    day_dir = "20260518"
    acc = "TestPerSource"
    src_root = tmp_path / "exports"
    src_dir = src_root / day_dir / acc
    src_dir.mkdir(parents=True)
    (src_dir / "alpha.html").write_text(
        wrap_html("标题甲", "https://mp.weixin.qq.com/s/al", "<p>甲</p>"), encoding="utf-8"
    )
    (src_dir / "beta.html").write_text(
        wrap_html("标题乙", "https://mp.weixin.qq.com/s/be", "<p>乙</p>"), encoding="utf-8"
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

    assert summary["status"] == "success"
    assert summary.get("aggregate_mode") == "one_html_per_source"
    outs = summary.get("output_paths") or []
    assert len(outs) == 2
    assert summary.get("per_source_rerun_count") == 2
    assert summary.get("per_source_idempotent_skips") == 0
    folder = out_root / day_dir / acc
    assert (folder / "alpha.html").is_file()
    assert (folder / "beta.html").is_file()
    assert "标题甲" in (folder / "alpha.html").read_text(encoding="utf-8")


def test_aggregate_vision_preclean_injects_reading_notes(tmp_path, monkeypatch):
    """两段式配图识读：不依赖真实下载/外网。"""
    import gzh_pipeline.parse.vision_llm as vv_mod

    monkeypatch.setenv("GZH_LLM_VISION", "1")
    monkeypatch.setenv("GZH_VISION_PRECLEAN_MODEL", "qwen-test-vl")
    monkeypatch.setattr(
        vv_mod,
        "fetch_image_data_uri",
        lambda *a, **k: ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/altXJQAAAAASUVORK5CYII=", None),
    )

    post_capture: list[tuple[str, str]] = []
    _install_stub_llm(monkeypatch, vision_preclean_stub=True, capture_posts=post_capture)

    day_in = "2026-05-18"
    day_dir = "20260518"
    acc = "TestAccVL"
    src_root = tmp_path / "exports"
    src_dir = src_root / day_dir / acc
    src_dir.mkdir(parents=True)
    body = '<p>x</p><img data-src="https://example.invalid/preclean-test.png"/>'
    (src_dir / "x.html").write_text(
        wrap_html("图文", "https://mp.weixin.qq.com/s/z", body), encoding="utf-8"
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
    assert summary["status"] == "success"
    stitched = "\n".join(blob for _, blob in post_capture)
    assert any("_vision_preclean_" in lbl for lbl, _ in post_capture)
    assert "===配图识读===" in stitched
    assert "测试配图识读" in stitched
    raw_out = Path(summary["output_path"]).read_text(encoding="utf-8")
    assert "event-dimension-facts" in raw_out
    assert 'id="vision-preclean"' not in raw_out
    assert raw_out.count('<section class="factor-section event-dimension-section') == 5
    assert 'id="dim-source"' in raw_out
    assert "测试配图识读" not in raw_out

    readings = summary.get("vision_preclean_readings")
    assert readings and isinstance(readings, list)
    r0_notes = readings[0].get("reading_notes") or readings[0]
    if isinstance(r0_notes, dict) and r0_notes.get("truncated"):
        assert "测试配图识读" in (str(r0_notes.get("head", "")) + str(r0_notes.get("tail", "")))
    else:
        assert "测试配图识读" in str(r0_notes)
    assert summary["vision_preclean_readings"][0].get("stem") == "x"
    mo = summary.get("vision_preclean_model_outputs") or []
    assert mo
    m0 = mo[0].get("model_output_text")
    assert m0 is not None
    if isinstance(m0, dict) and m0.get("truncated"):
        assert "测试配图识读" in (str(m0.get("head", "")) + str(m0.get("tail", "")))
    else:
        assert "测试配图识读" in str(m0)
    assert summary.get("vision_preclean_empty_reason_zh") is None
    assert summary.get("vision_preclean_output_find_in_trace_json")

    trace_files = list(audit_root.glob("**/*.trace.json"))
    assert trace_files
    doc = json.loads(trace_files[0].read_text(encoding="utf-8"))
    preclean_ok = [
        s for s in (doc.get("steps") or []) if s.get("kind") == "vision_preclean" and s.get("ok") is True
    ]
    assert preclean_ok
    det = preclean_ok[0].get("detail") or {}
    assert det.get("vision_image_delivery_https_first") is True
    grp = det.get("reading_topic_groups")
    assert isinstance(grp, list) and grp and isinstance(grp[0], dict)
    assert "category_group" in grp[0]
    tops = grp[0].get("topics") or []
    assert tops and tops[0].get("reading_topic") and tops[0].get("type_ids")
    dp = det.get("delivery_passes") or []
    assert dp and dp[0].get("per_image_delivery") == ["delivery_https_url"]
    assert "reading_text" in det
    assert det.get("model_output_text") == det.get("reading_text")
    assert "preclean_image_urls" in det
    rt = det["reading_text"]
    if isinstance(rt, str):
        assert "测试配图识读" in rt
    else:
        assert "测试配图识读" in (str(rt.get("head", "")) + str(rt.get("tail", "")))


def test_aggregate_vision_audit_in_per_source_mode(tmp_path, monkeypatch):
    """逐篇成品模式下 per_source_outputs 与 summary.vision_preclean_readings 均含配图识读留痕。"""
    import gzh_pipeline.parse.vision_llm as vv_mod

    monkeypatch.setenv("GZH_AGGREGATE_ONE_HTML_PER_SOURCE", "1")
    monkeypatch.setenv("GZH_LLM_VISION", "1")
    monkeypatch.setenv("GZH_VISION_PRECLEAN_MODEL", "qwen-test-vl")
    monkeypatch.setattr(
        vv_mod,
        "fetch_image_data_uri",
        lambda *a, **k: ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/altXJQAAAAASUVORK5CYII=", None),
    )
    _install_stub_llm(monkeypatch, vision_preclean_stub=True)

    day_in = "2026-05-18"
    day_dir = "20260518"
    acc = "TestVLPerStem"
    src_root = tmp_path / "exports"
    src_dir = src_root / day_dir / acc
    src_dir.mkdir(parents=True)
    body = '<p>x</p><img data-src="https://example.invalid/preclean.png"/>'
    (src_dir / "solo.html").write_text(
        wrap_html("solo", "https://mp.weixin.qq.com/s/solo", body), encoding="utf-8"
    )

    summary = run_aggregate_job(
        biz_date=day_in,
        account=acc,
        input_root=src_root,
        output_root=tmp_path / "parsed",
        audit_root=tmp_path / "audit",
        output_stem="summary",
    )
    assert summary["status"] == "success"
    rows = summary.get("per_source_outputs") or []
    assert len(rows) == 1
    assert rows[0].get("stem") == "solo"
    vone = rows[0].get("vision_preclean_reading")
    assert vone and vone.get("stem") == "solo"
    vsum = summary.get("vision_preclean_readings") or []
    assert len(vsum) == 1
    nm = vsum[0].get("reading_notes")
    if isinstance(nm, dict) and nm.get("truncated"):
        assert "测试配图识读" in (str(nm.get("head", "")) + str(nm.get("tail", "")))
    else:
        assert "测试配图识读" in str(nm)
def test_vision_https_first_skips_fetch_when_https_succeeds(monkeypatch):
    """https_first 首轮 VL 返回 OK 时不应触碰 fetch_image_data_uri。"""
    from gzh_pipeline.parse.vision_llm import _run_preclean_llm_once
    import gzh_pipeline.parse.vision_llm as vv_mod
    import gzh_pipeline.parse.llm_compat as lc

    monkeypatch.delenv("GZH_VISION_IMAGE_DELIVERY", raising=False)

    def no_fetch(*_args, **_kw):
        raise AssertionError("unexpected client download on https_first success path")

    monkeypatch.setattr(vv_mod, "fetch_image_data_uri", no_fetch)

    msgs: list[Any] = []

    def fp(
        cfg,
        messages,
        *_,
        http_label: str,
        **_kw: Any,
    ):
        msgs.append(messages)
        return ("图示OK首包", None)

    monkeypatch.setattr(lc, "post_chat_completions", fp)

    cfg = _stub_openai_compat_cfg()
    urls = ["https://example.invalid/only-https.png"]
    txt, n = _run_preclean_llm_once(
        urls=urls,
        session=MagicMock(),
        trace=None,
        stem_label="st",
        http_slug="ut",
        llm_cfg=cfg,
        vision_model="qwen-test-vl",
    )
    assert txt == "图示OK首包"
    assert n == 1
    assert len(msgs) == 1
    parts = msgs[0][0].get("content")
    assert isinstance(parts, list)
    img_urls = [
        ((p.get("image_url") or {}).get("url"))
        for p in parts
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert img_urls == urls


def test_vision_https_first_retries_fetch_all_after_api_error(monkeypatch):
    """首轮 HTTPS 直接传图但 API 报错时：打第二轮全员 client data-uri。"""
    from gzh_pipeline.parse.vision_llm import _run_preclean_llm_once
    import gzh_pipeline.parse.vision_llm as vv_mod
    import gzh_pipeline.parse.llm_compat as lc

    monkeypatch.delenv("GZH_VISION_IMAGE_DELIVERY", raising=False)
    png = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQImWNgYGD4DwAB"
        "BAEAOcVQZAAAAABJRU5ErkJggg=="
    )
    monkeypatch.setattr(vv_mod, "fetch_image_data_uri", lambda *a, **k: (png, None))

    ctr = {"n": 0}
    rounds: list[Any] = []

    def fp(
        _cfg,
        messages,
        *_,
        http_label: str,
        **_kw: Any,
    ):
        ctr["n"] += 1
        rounds.append(messages)
        if ctr["n"] == 1:
            return (None, "gateway_error_mock")
        return ("二抡成了", None)

    monkeypatch.setattr(lc, "post_chat_completions", fp)

    cfg = _stub_openai_compat_cfg()
    urls = ["https://example.invalid/retry-case.png"]
    txt, n = _run_preclean_llm_once(
        urls=urls,
        session=MagicMock(),
        trace=None,
        stem_label="st",
        http_slug="ut",
        llm_cfg=cfg,
        vision_model="qwen-test-vl",
    )
    assert ctr["n"] == 2
    assert txt == "二抡成了"
    assert n == 1
    assert len(rounds) == 2
    pt1 = rounds[0][0].get("content")
    u1 = [((p.get("image_url") or {}).get("url")) for p in (pt1 or []) if isinstance(p, dict) and p.get("type") == "image_url"]
    assert u1 == urls
    pt2 = rounds[1][0].get("content")
    u2 = [((p.get("image_url") or {}).get("url")) for p in (pt2 or []) if isinstance(p, dict) and p.get("type") == "image_url"]
    assert len(u2) == 1 and isinstance(u2[0], str) and u2[0].startswith("data:image/")


def test_run_aggregate_skips_missing_dir(tmp_path):
    summary = run_aggregate_job(
        biz_date="20260101",
        account="X",
        input_root=tmp_path / "nowhere",
        output_root=tmp_path / "out",
        audit_root=tmp_path / "audit",
        output_stem="summary",
    )
    assert summary["status"] == "skipped"
    assert "不存在" in (summary.get("error_message") or "")
