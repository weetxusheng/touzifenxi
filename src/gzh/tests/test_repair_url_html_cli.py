from pathlib import Path

from gzh_pipeline.cli.repair_url_html import discover_url_html_files, repair_url_html_file
from gzh_pipeline.dajiala.weixin_media import normalize_weixin_media_html

LAZY = """
<div id="js_content" style="visibility: hidden;">
  <img data-src="https://mmbiz.qpic.cn/mmbiz_png/x/640" src=""/>
</div>
"""


def test_repair_url_html_file_writes_normalized(tmp_path):
    fp = tmp_path / "a.url.html"
    fp.write_text(LAZY, encoding="utf-8")
    row = repair_url_html_file(fp)
    assert row["changed"] is True
    out = fp.read_text(encoding="utf-8")
    assert "mmbiz.qpic.cn" in out
    assert 'src="https://mmbiz.qpic.cn' in out


def test_discover_url_html_files(tmp_path):
    d = tmp_path / "20260520" / "acc" / "_compare"
    d.mkdir(parents=True)
    (d / "x.url.html").write_text(LAZY, encoding="utf-8")
    (d / "y.html").write_text("<p>n</p>", encoding="utf-8")
    found = discover_url_html_files(tmp_path)
    assert len(found) == 1
    assert found[0].name == "x.url.html"
