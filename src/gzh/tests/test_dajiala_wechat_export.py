from pathlib import Path

import pytest

from gzh_pipeline.constants import ENDPOINT_PRICES
from gzh_pipeline.dajiala.client import (
    CostTracker,
    DajialaClient,
    _build_post_condition_body,
    _build_post_history_body,
    _classify_dajiala_target,
)
from gzh_pipeline.dajiala.export import BatchExporter


def _exporter(client, tmp_path, **kwargs):
    """单测默认仅大佳啦 API，避免依赖直连微信。"""
    return BatchExporter(client, tmp_path, body_sources=frozenset({"dajiala"}), **kwargs)
from gzh_pipeline.util.text import parse_accounts, safe_filename


def test_parse_accounts_accepts_commas_newlines_and_trims_duplicates():
    raw = "证券时报, 人民日报\n证券时报\n央视新闻"

    assert parse_accounts(raw) == ["证券时报", "人民日报", "央视新闻"]


def test_cost_tracker_summarizes_calls_by_account_and_endpoint():
    tracker = CostTracker()
    tracker.record("证券时报", "post_condition")
    tracker.record("证券时报", "article_html", count=2)
    tracker.record("人民日报", "post_history", count=3)

    summary = tracker.summary()

    assert summary["total_calls"] == 6
    assert summary["total_estimated_cost"] == pytest.approx(
        ENDPOINT_PRICES["post_condition"]
        + 2 * ENDPOINT_PRICES["article_html"]
        + 3 * ENDPOINT_PRICES["post_history"]
    )
    assert summary["accounts"]["证券时报"]["calls"] == 3
    assert summary["accounts"]["人民日报"]["calls"] == 3


def test_client_sends_expected_requests():
    session = FakeSession(
        [
            {"code": 0, "data": {"remain_money": 6.0}},
            {"code": 0, "data": [{"title": "A", "url": "https://mp.weixin.qq.com/s/a"}]},
            {"code": 0, "data": [{"title": "B", "url": "https://mp.weixin.qq.com/s/b"}]},
            {"code": 0, "data": {"html": "<p>A</p>"}},
        ]
    )
    client = DajialaClient("secret-key", session=session)

    assert client.get_remain_money() == 6.0
    assert client.fetch_history("证券时报", page=2)
    assert client.fetch_latest("证券时报")
    assert client.fetch_article_html("https://mp.weixin.qq.com/s/a") == "<p>A</p>"

    assert session.calls == [
        ("POST", "https://www.dajiala.com/fbmain/monitor/v3/get_remain_money", {"key": "secret-key"}),
        (
            "POST",
            "https://www.dajiala.com/fbmain/monitor/v3/post_history",
            {"key": "secret-key", "biz": "", "url": "", "name": "证券时报", "page": 2},
        ),
        (
            "POST",
            "https://www.dajiala.com/fbmain/monitor/v3/post_condition",
            {"key": "secret-key", "biz": "", "url": "", "name": "证券时报"},
        ),
        (
            "POST",
            "https://www.dajiala.com/fbmain/monitor/v3/article_html",
            {"key": "secret-key", "url": "https://mp.weixin.qq.com/s/a"},
        ),
    ]


def test_batch_exporter_writes_latest_html_and_tracks_cost(tmp_path):
    client = FakeClient(
        latest={
            "证券时报": [
                {"title": "收盘点评", "url": "https://mp.weixin.qq.com/s/a"},
                {"title": "收盘点评", "url": "https://mp.weixin.qq.com/s/a"},
            ],
            "人民日报": [{"title": "今日要闻", "url": "https://mp.weixin.qq.com/s/b"}],
        },
        html={
            "https://mp.weixin.qq.com/s/a": "<section>证券时报正文</section>",
            "https://mp.weixin.qq.com/s/b": "<section>人民日报正文</section>",
        },
    )
    exporter = _exporter(client, tmp_path, biz_date="2026-01-01")

    result = exporter.export(["证券时报", "人民日报"], mode="latest")

    assert (tmp_path / "20260101" / "证券时报" / "收盘点评.html").read_text(encoding="utf-8")
    assert (tmp_path / "20260101" / "人民日报" / "今日要闻.html").read_text(encoding="utf-8")
    assert result["summary"]["total_calls"] == 4
    assert result["accounts"]["证券时报"]["exported"] == 1
    assert result["accounts"]["人民日报"]["exported"] == 1


def test_today_mode_requests_post_condition_for_target_date(tmp_path):
    client = FakeClient(
        html={"https://mp.weixin.qq.com/s/today": "<p>today</p>"},
        latest={
            "证券时报": [
                {
                    "title": "今日文章",
                    "url": "https://mp.weixin.qq.com/s/today",
                    "post_time_str": "2026-05-13 10:00:00",
                }
            ]
        },
    )
    exporter = _exporter(client, tmp_path, biz_date="2026-05-13")

    result = exporter.export(["证券时报"], mode="today", target_date="2026-05-13")

    assert (tmp_path / "20260513" / "证券时报" / "今日文章.html").exists()
    assert result["accounts"]["证券时报"]["found"] == 1


def test_post_condition_body_uses_name_for_chinese_account():
    assert _classify_dajiala_target("证券时报") == "name"
    body = _build_post_condition_body("k", "证券时报")
    assert body["name"] == "证券时报"
    assert body["url"] == ""
    assert body["biz"] == ""


def test_post_history_body_uses_name_for_chinese_account():
    body = _build_post_history_body("k", "证券时报", page=2)
    assert body["name"] == "证券时报"
    assert body["url"] == ""
    assert body["page"] == 2


def test_post_condition_body_uses_url_for_article_link():
    url = "https://mp.weixin.qq.com/s?__biz=MjM5MTM5NjUzNA==&mid=1"
    assert _classify_dajiala_target(url) == "url"
    body = _build_post_condition_body("k", url)
    assert body["url"] == url
    assert body["name"] == ""


def test_yesterday_mode_uses_post_history_and_filters_by_date(tmp_path):
    """yesterday 直接用公众号名调 post_history，按目标日过滤；遇更早一页则停止翻页。"""
    client = FakeClient(
        history={
            ("证券时报", 1): [
                {
                    "title": "昨天文",
                    "url": "https://mp.weixin.qq.com/s/y1",
                    "post_time_str": "2026-05-20 10:00:00",
                },
                {
                    "title": "更早文",
                    "url": "https://mp.weixin.qq.com/s/old",
                    "post_time_str": "2026-05-19 10:00:00",
                },
            ],
            ("证券时报", 2): [{"title": "不应翻到", "url": "https://mp.weixin.qq.com/s/x", "post_time_str": "2026-05-18 10:00:00"}],
        },
        html={"https://mp.weixin.qq.com/s/y1": "<p>y</p>"},
    )
    exporter = _exporter(client, tmp_path, biz_date="20260520")

    result = exporter.export(["证券时报"], mode="yesterday", target_date="2026-05-20", max_pages=5)

    assert result["accounts"]["证券时报"]["found"] == 1
    assert result["accounts"]["证券时报"]["exported"] == 1
    assert result["accounts"]["证券时报"]["fallback"] == "post_history:2026-05-20"
    assert client.history_calls == [("证券时报", 1)]
    assert (tmp_path / "20260520" / "证券时报" / "昨天文.html").is_file()


def test_today_mode_uses_post_condition_and_filters_by_date(tmp_path):
    """today 走 post_condition（name=公众号名）并按日期过滤。"""
    client = FakeClient(
        latest={
            "证券时报": [
                {
                    "title": "今天文章",
                    "url": "https://mp.weixin.qq.com/s/today",
                    "post_time_str": "2026-05-13 18:46:31",
                },
                {
                    "title": "昨天文章",
                    "url": "https://mp.weixin.qq.com/s/yesterday",
                    "post_time_str": "2026-05-12 18:46:31",
                },
            ]
        },
        html={"https://mp.weixin.qq.com/s/today": "<p>today</p>"},
    )
    exporter = _exporter(client, tmp_path, biz_date="2026-05-13")

    result = exporter.export(["证券时报"], mode="today", target_date="2026-05-13")

    assert result["accounts"]["证券时报"]["error"] is None
    assert result["accounts"]["证券时报"]["found"] == 1
    assert result["accounts"]["证券时报"]["fallback"] == "post_condition:2026-05-13"
    assert client.history_calls == []


def test_batch_exporter_fetches_all_pages_until_empty(tmp_path):
    client = FakeClient(
        history={
            ("证券时报", 1): [
                {"title": "第一篇", "url": "https://mp.weixin.qq.com/s/1"},
                {"title": "第二篇", "url": "https://mp.weixin.qq.com/s/2"},
            ],
            ("证券时报", 2): [{"title": "第二篇", "url": "https://mp.weixin.qq.com/s/2"}],
            ("证券时报", 3): [],
        },
        html={
            "https://mp.weixin.qq.com/s/1": "<p>1</p>",
            "https://mp.weixin.qq.com/s/2": "<p>2</p>",
        },
    )
    exporter = _exporter(client, tmp_path, biz_date="2026-01-01")

    result = exporter.export(["证券时报"], mode="all", max_pages=5)

    assert sorted(p.name for p in (tmp_path / "20260101" / "证券时报").glob("*.html")) == ["第一篇.html", "第二篇.html"]
    assert client.history_calls == [("证券时报", 1), ("证券时报", 2), ("证券时报", 3)]
    assert result["accounts"]["证券时报"]["exported"] == 2


def test_latest_mode_falls_back_to_history_first_page_when_latest_api_fails(tmp_path):
    client = FakeClient(
        post_condition_fail_once={
            "证券时报": RuntimeError("Dajiala API error: 文章被删除或公众号被封被迁移，请检查链接！")
        },
        history={("证券时报", 1): [{"title": "回退文章", "url": "https://mp.weixin.qq.com/s/fallback"}]},
        html={"https://mp.weixin.qq.com/s/fallback": "<p>fallback</p>"},
    )
    exporter = _exporter(client, tmp_path, biz_date="2026-01-01")

    result = exporter.export(["证券时报"], mode="latest")

    assert (tmp_path / "20260101" / "证券时报" / "回退文章.html").exists()
    assert client.history_calls == [("证券时报", 1)]
    assert result["accounts"]["证券时报"]["fallback"] == "history_page_1"
    assert result["accounts"]["证券时报"]["error"] is None


def test_batch_export_continues_when_one_account_fails(tmp_path):
    client = FakeClient(
        latest_errors={"坏账号": RuntimeError("Dajiala API error: account failed")},
        latest={"好账号": [{"title": "好文章", "url": "https://mp.weixin.qq.com/s/good"}]},
        html={"https://mp.weixin.qq.com/s/good": "<p>good</p>"},
    )
    exporter = _exporter(client, tmp_path, biz_date="2026-01-01")

    result = exporter.export(["坏账号", "好账号"], mode="latest")

    assert result["accounts"]["坏账号"]["exported"] == 0
    assert "account failed" in result["accounts"]["坏账号"]["error"]
    assert result["accounts"]["好账号"]["exported"] == 1


def test_safe_filename_removes_windows_reserved_characters():
    assert safe_filename('A/B:C*D?"E<F>G|') == "A_B_C_D__E_F_G_"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.ok = True
        self.reason = "OK"
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json if json is not None else data))
        return FakeResponse(self.payloads.pop(0))

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        return FakeResponse(self.payloads.pop(0))


class FakeClient:
    def __init__(
        self,
        latest=None,
        history=None,
        html=None,
        latest_errors=None,
        post_condition_fail_once=None,
    ):
        self.latest = latest or {}
        self.history = history or {}
        self.html = html or {}
        self.latest_errors = latest_errors or {}
        self.post_condition_fail_once = dict(post_condition_fail_once or {})
        self.history_calls = []
        self.session = None
        self.timeout = 30

    def get_remain_money(self):
        return 10.0

    def fetch_latest(self, account):
        return self.fetch_post_condition(account)

    def fetch_post_condition(self, account):
        if account in self.post_condition_fail_once:
            raise self.post_condition_fail_once.pop(account)
        if account in self.latest_errors:
            raise self.latest_errors[account]
        return self.latest.get(account, [])

    def fetch_history(self, account, page):
        self.history_calls.append((account, page))
        result = self.history.get((account, page), [])
        if isinstance(result, Exception):
            raise result
        return result

    def fetch_article_html(self, url):
        return self.html[url]

    def fetch_article_html_from_url(self, url):
        return self.html.get(url, "")


def test_export_writes_parallel_compare_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gzh_pipeline.dajiala.body_fetch.fetch_weixin_article_by_mode",
        lambda session, url, mode="full", timeout=30, trace=None: (
            "<!doctype html><html><body><motion.div id='js_content'><p>链接正文</p></motion.div></body></html>"
            if mode == "full"
            else "<p>链接正文</p>"
        ),
    )
    client = FakeClient(
        latest={"证券时报": [{"title": "对比文", "url": "https://mp.weixin.qq.com/s/cmp"}]},
        html={"https://mp.weixin.qq.com/s/cmp": "<p>大佳啦正文</p>"},
    )
    client.session = object()
    client.timeout = 30
    exporter = BatchExporter(
        client,
        tmp_path,
        biz_date="20260102",
        body_sources=frozenset({"dajiala", "url"}),
    )
    result = exporter.export(["证券时报"], mode="latest")
    base = tmp_path / "20260102" / "证券时报"
    assert (base / "对比文.html").read_text(encoding="utf-8")
    assert "大佳啦正文" in (base / "_compare" / "对比文.dajiala.html").read_text(encoding="utf-8")
    url_html = (base / "_compare" / "对比文.url.html").read_text(encoding="utf-8")
    assert "链接正文" in url_html
    assert "<!doctype html>" in url_html.lower()
    assert "https://mp.weixin.qq.com/s/cmp" in url_html
    assert "原文链接" in url_html
    assert result["accounts"]["证券时报"]["exported"] == 1
