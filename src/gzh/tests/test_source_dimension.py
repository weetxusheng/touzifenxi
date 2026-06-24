from pathlib import Path

from gzh_pipeline.dajiala.html import wrap_html
from gzh_pipeline.parse.extract import extract_from_export_html
from gzh_pipeline.parse.source_dimension import attach_source_dimension, build_source_dimension_html


def test_build_source_dimension_html_single_link():
    raw = wrap_html("标题", "https://mp.weixin.qq.com/s/abc", "<p>正文</p>")
    ar = extract_from_export_html(raw, Path("a.html"))
    html = build_source_dimension_html([ar])
    assert 'href="https://mp.weixin.qq.com/s/abc"' in html
    assert "原文" not in html or "https://mp.weixin.qq.com" in html
    assert "标题" in html


def test_attach_source_dimension_merges():
    dims = attach_source_dimension({"facts": "<p>x</p>"}, [])
    assert "source" in dims
    assert dims["facts"] == "<p>x</p>"
