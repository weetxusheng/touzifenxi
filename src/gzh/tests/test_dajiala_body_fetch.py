from pathlib import Path

import pytest

from gzh_pipeline.dajiala.body_fetch import (
    fetch_article_bodies_parallel,
    finalize_body_html,
    parse_body_sources,
    plain_text_from_wrapped_html,
    write_export_html_files,
)
from gzh_pipeline.dajiala.html import wrap_html
from gzh_pipeline.dajiala.url_fetch import extract_weixin_content_html, is_weixin_verify_page
from gzh_pipeline.dajiala.weixin_media import normalize_weixin_media_html


WEIXIN_FIXTURE = """
<html><body>
<div id="js_content"><p>直连正文段落</p></motion.div></div>
</body></html>
"""

WEIXIN_LAZY_IMG_FIXTURE = """
<html><body>
<div id="js_content" style="visibility: hidden; opacity: 0;">
  <img class="wxw-img" data-src="https://mmbiz.qpic.cn/mmbiz_png/test/640?wx_fmt=png" src=""/>
  <iframe data-src="https://mp.weixin.qq.com/s?fake=video"></iframe>
  <mpvoice data-src="https://res.wx.qq.com/voice/demo.mp3"></mpvoice>
</div>
</body></html>
"""


def test_parse_body_sources_both_aliases():
    assert parse_body_sources("both") == frozenset({"dajiala", "url"})
    assert parse_body_sources("dajiala,url") == frozenset({"dajiala", "url"})


def test_extract_weixin_content_html():
    html = extract_weixin_content_html(WEIXIN_FIXTURE)
    assert "直连正文段落" in html
    assert "js_content" in html


def test_normalize_weixin_media_promotes_lazy_src_and_unhides_content():
    out = normalize_weixin_media_html(WEIXIN_LAZY_IMG_FIXTURE)
    assert "visibility: visible !important" in out
    assert 'src="https://mmbiz.qpic.cn/mmbiz_png/test/640?wx_fmt=png"' in out
    assert "<audio" in out and "demo.mp3" in out
    assert 'src="https://mp.weixin.qq.com/s?fake=video"' in out


def test_finalize_body_html_url_applies_media_normalize():
    page = WEIXIN_LAZY_IMG_FIXTURE
    out = finalize_body_html(
        "url",
        page,
        title="T",
        source_url="https://mp.weixin.qq.com/s/x",
        url_fetch_mode="full",
    )
    assert "mmbiz.qpic.cn" in out
    assert 'src="https://mmbiz.qpic.cn' in out
    assert "visibility: hidden" not in out or "visible !important" in out


def test_finalize_body_html_full_page_injects_source_url():
    page = "<!doctype html><html><head><title>T</title></head><body><p>整页</p></body></html>"
    url = "https://mp.weixin.qq.com/s/x"
    out = finalize_body_html("url", page, title="T", source_url=url, url_fetch_mode="full")
    assert "整页" in out
    assert f'href="{url}"' in out
    assert "原文链接" in out
    assert "<h1>T</h1>" in out


def test_finalize_body_html_extract_still_wraps():
    out = finalize_body_html("url", "<p>段</p>", title="T", source_url="https://mp.weixin.qq.com/s/x", url_fetch_mode="extract")
    assert "<h1>T</h1>" in out


def test_is_weixin_verify_page():
    assert is_weixin_verify_page("<html>环境异常，完成验证后即可继续访问</html>")


def test_write_export_html_files_compare_mode(tmp_path):
    account_dir = tmp_path / "证券时报"
    account_dir.mkdir()
    written, meta = write_export_html_files(
        account_dir=account_dir,
        filename_stem="测试文",
        title="测试文",
        source_url="https://mp.weixin.qq.com/s/x",
        bodies={"dajiala": "<p>API</p>", "url": "<p>URL</p>"},
        errors={},
        sources=frozenset({"dajiala", "url"}),
    )
    assert (account_dir / "测试文.html").read_text(encoding="utf-8")
    assert (account_dir / "_compare" / "测试文.dajiala.html").is_file()
    assert (account_dir / "_compare" / "测试文.url.html").is_file()
    assert (account_dir / "_compare" / "测试文.compare.json").is_file()
    assert meta["dajiala"]["sha256_hex"] != meta["url"]["sha256_hex"]


def test_fetch_article_bodies_parallel():
    class Client:
        session = None
        timeout = 5

        def fetch_article_html(self, url):
            return f"<p>api:{url}</p>"

    bodies, errors = fetch_article_bodies_parallel(
        Client(),
        "https://mp.weixin.qq.com/s/a",
        frozenset({"dajiala"}),
    )
    assert errors == {}
    assert "api:" in bodies["dajiala"]


def test_plain_text_from_wrapped_differs_by_source():
    a = wrap_html("T", "https://mp.weixin.qq.com/s/a", "<p>甲</p>")
    b = wrap_html("T", "https://mp.weixin.qq.com/s/a", "<p>乙</p>")
    assert plain_text_from_wrapped_html(a) != plain_text_from_wrapped_html(b)
