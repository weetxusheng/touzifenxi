from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def test_remote_fetcher_deploy_folder_exists_with_entrypoint_and_docs():
    root = Path(__file__).resolve().parents[2]
    deploy_dir = root / "feedcore_remote_fetcher"

    assert (deploy_dir / "README.md").exists()
    assert (deploy_dir / "app.py").exists()
    assert (deploy_dir / "requirements.txt").exists()
    assert (deploy_dir / "run_fetcher.py").exists()
    assert (deploy_dir / "run_fetcher.bat").exists()
    assert (deploy_dir / "run_fetcher.sh").exists()
    assert (deploy_dir / ".env.example").exists()

    readme = (deploy_dir / "README.md").read_text(encoding="utf-8")
    assert "只需要上传整个 `feedcore_remote_fetcher` 文件夹" in readme
    assert "FEEDCORE_FETCHER_TOKEN" in readme
    assert "/api/fetch-runs" in readme


def test_remote_fetcher_entrypoint_loads_project_src_without_editable_install():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "feedcore_remote_fetcher/run_fetcher.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Run the standalone FeedCore remote fetcher HTTP service" in result.stdout


def test_remote_fetcher_folder_runs_when_copied_without_project_src(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    copied = tmp_path / "feedcore_remote_fetcher"
    shutil.copytree(root / "feedcore_remote_fetcher", copied)

    result = subprocess.run(
        [sys.executable, "run_fetcher.py", "--help"],
        cwd=copied,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Run the standalone FeedCore remote fetcher HTTP service" in result.stdout


def test_standalone_remote_fetcher_loads_complete_result_from_artifact(tmp_path: Path):
    app = _load_standalone_app()
    run_dir = tmp_path / "fetch_json"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "fetch_json", "counts": {"feed_items": 1}}),
        encoding="utf-8",
    )
    (run_dir / "step1_rss_sources.json").write_text(
        json.dumps([{"url": "https://feed.example/rss", "status": "selected"}]),
        encoding="utf-8",
    )
    (run_dir / "step2_feed_items.json").write_text(
        json.dumps([{"article": {"title": "Example article"}}]),
        encoding="utf-8",
    )
    (run_dir / "step3_article_contents.json").write_text(
        json.dumps([{"text": "Full article text"}]),
        encoding="utf-8",
    )
    artifact_path = app.create_artifact_zip(run_dir)

    result = app.load_artifact_result_json(artifact_path)

    assert result == {
        "run_id": "fetch_json",
        "manifest": {"run_id": "fetch_json", "counts": {"feed_items": 1}},
        "rss_sources": [{"url": "https://feed.example/rss", "status": "selected"}],
        "feed_items": [{"article": {"title": "Example article"}}],
        "article_contents": [{"text": "Full article text"}],
    }


def test_standalone_remote_fetcher_manager_deletes_completed_run_dir(tmp_path: Path):
    app = _load_standalone_app()
    manager = app.FetchRunManager(output_dir=tmp_path, run_inline=True)
    created = manager.create_run(
        app.FetchRunRequest(
            rss_urls=["https://feed.example/rss"],
            client_run_id="delete_me",
            quick_sample_size=1,
            max_articles=1,
        )
    )
    run_dir = tmp_path / created.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "artifact.zip").write_text("zip", encoding="utf-8")

    result = manager.delete_run(created.run_id)

    assert result == {"run_id": created.run_id, "deleted": True}
    assert manager.get_status(created.run_id) is None
    assert not run_dir.exists()


def test_standalone_remote_fetcher_manager_keeps_shared_article_html_after_delete(tmp_path: Path):
    app = _load_standalone_app()
    manager = app.FetchRunManager(output_dir=tmp_path, run_inline=True)
    created = manager.create_run(
        app.FetchRunRequest(
            rss_urls=["https://feed.example/rss"],
            client_run_id="delete_keep_html",
            quick_sample_size=1,
            max_articles=1,
        )
    )
    run_dir = tmp_path / created.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "artifact.zip").write_text("zip", encoding="utf-8")
    html_file = tmp_path / "articles" / "20260519" / "rss001_a001.html"
    html_file.parent.mkdir(parents=True, exist_ok=True)
    html_file.write_text("<html>kept</html>", encoding="utf-8")

    result = manager.delete_run(created.run_id)

    assert result == {"run_id": created.run_id, "deleted": True}
    assert not run_dir.exists()
    assert html_file.exists()


def test_standalone_remote_fetcher_reports_progress_from_run_files(tmp_path: Path):
    app = _load_standalone_app()
    run_dir = tmp_path / "fetch_progress"
    tasks_dir = run_dir / "rss_tasks"
    tasks_dir.mkdir(parents=True)
    (run_dir / "step2_feed_items.json").write_text(json.dumps([{}, {}, {}]), encoding="utf-8")
    (tasks_dir / "rss_001_articles_text.json").write_text(
        json.dumps(
            [
                {"text": "ok", "fetch_error": None, "extract_error": None, "translation_error": None},
                {"text": "", "fetch_error": "timeout", "extract_error": None, "translation_error": None},
            ]
        ),
        encoding="utf-8",
    )

    progress = app.load_run_progress(run_id="fetch_progress", run_dir=run_dir, status="running", message="fetching")

    assert progress == {
        "run_id": "fetch_progress",
        "status": "running",
        "message": "fetching",
        "rss_sources": 0,
        "feed_items": 3,
        "articles_total": 3,
        "articles_done": 2,
        "fetch_failed": 1,
        "artifact_ready": False,
    }


def test_standalone_remote_fetcher_fetch_url_result_returns_html_and_text():
    app = _load_standalone_app()
    html = """<!doctype html><html><body><article>
<p>Original paragraph one from the proxied article.</p>
<p>Original paragraph two with enough details to inspect.</p>
</article></body></html>"""

    original_http_get_text = app._http_get_text
    app._http_get_text = lambda url, timeout: html
    try:
        result = app.fetch_url_result("https://article.example/story")
    finally:
        app._http_get_text = original_http_get_text

    assert result["url"] == "https://article.example/story"
    assert result["html"] == html
    assert result["text"] == "Original paragraph one from the proxied article.\n\nOriginal paragraph two with enough details to inspect."
    assert result["text_len"] == len(result["text"])


def test_standalone_remote_fetcher_fetch_url_html_returns_raw_html():
    app = _load_standalone_app()
    html = "<!doctype html><html><body>Original HTML</body></html>"

    original_http_get_text = app._http_get_text
    app._http_get_text = lambda url, timeout: html
    try:
        result = app.fetch_url_html("https://article.example/story")
    finally:
        app._http_get_text = original_http_get_text

    assert result == html


def test_standalone_remote_fetcher_fetches_one_article(tmp_path: Path):
    app_path = Path(__file__).resolve().parents[2] / "feedcore_remote_fetcher" / "app.py"
    spec = importlib.util.spec_from_file_location("standalone_remote_fetcher_app", app_path)
    assert spec is not None and spec.loader is not None
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Smoke</title><item>
<title>Standalone smoke article</title>
<link>http://127.0.0.1:{port}/article.html</link>
<pubDate>Mon, 18 May 2026 06:00:00 GMT</pubDate>
<description>Fallback summary</description>
<source>Local Test</source>
</item></channel></rss>"""
    article_html = """<!doctype html><html><body><article>
<p>Standalone remote fetcher paragraph one with enough useful text for extraction.</p>
<p>Paragraph two discusses AI infrastructure, market impact, deployment context, and cost questions.</p>
</article></body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/rss.xml":
                body = rss_xml.format(port=self.server.server_port).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/article.html":
                body = article_html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = app.FetchRunRequest(
            rss_urls=[f"http://127.0.0.1:{server.server_port}/rss.xml"],
            max_articles=1,
            quick_sample_size=1,
        )
        result = app.run_fetch_only(request=request, output_dir=tmp_path, run_id="standalone_smoke")
    finally:
        server.shutdown()
        server.server_close()

    step3 = json.loads(result.article_contents_path.read_text(encoding="utf-8"))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert len(step3) == 1
    assert step3[0]["article"]["title"] == "Standalone smoke article"
    assert step3[0]["fetch_error"] is None
    assert "Standalone remote fetcher paragraph one" in step3[0]["text"]
    assert result.artifact_path.exists()
    assert manifest["counts"]["article_contents"] == 1
    assert manifest["counts"]["fetch_failed"] == 0


def test_standalone_remote_fetcher_stores_article_html_under_shared_output_dir(tmp_path: Path):
    app = _load_standalone_app()
    output_dir = tmp_path / "remote_output"
    request = app.FetchRunRequest(rss_urls=["https://feed.example/rss"], max_articles=1, quick_sample_size=1)

    def parse_feed(_xml: str, feed_url: str):
        return [
            app.Article(
                title="Stored article",
                link="https://article.example/story",
                pub_date="Tue, 19 May 2026 08:00:00 GMT",
                description="fallback",
                source="Example Source",
                feed_url=feed_url,
            )
        ]

    original_http_get_text = app._http_get_text
    original_parse_feed = app.parse_feed
    original_today = app._today_yyyymmdd
    app._http_get_text = lambda url, timeout: (
        "<rss />"
        if url == "https://feed.example/rss"
        else "<!doctype html><html><body><article><p>Stored article paragraph.</p></article></body></html>"
    )
    app.parse_feed = parse_feed
    app._today_yyyymmdd = lambda: "20260519"
    try:
        result = app.run_fetch_only(request=request, output_dir=output_dir, run_id="stored_run")
    finally:
        app._http_get_text = original_http_get_text
        app.parse_feed = original_parse_feed
        app._today_yyyymmdd = original_today

    stored_file = output_dir / "articles" / "20260519" / "rss001_a001.html"
    step3 = json.loads(result.article_contents_path.read_text(encoding="utf-8"))

    assert stored_file.exists()
    stored_html = stored_file.read_text(encoding="utf-8")
    assert "Stored article paragraph." in stored_html
    assert "2026-05-19 16:00:00" in stored_html
    assert '<a class="original-link" href="https://article.example/story"' in stored_html
    assert "原文链接" in stored_html
    assert step3[0]["stored_html_path"] == "articles/20260519/rss001_a001.html"
    assert step3[0]["stored_html_url"] == "http://81.69.47.226:3000/articles/20260519/rss001_a001.html"
    with zipfile.ZipFile(result.artifact_path) as archive:
        assert "articles/20260519/rss001_a001.html" not in archive.namelist()


def test_standalone_remote_fetcher_cleans_article_html_dirs_older_than_one_week(tmp_path: Path):
    app = _load_standalone_app()
    articles_root = tmp_path / "articles"
    stale = articles_root / "20260510"
    recent = articles_root / "20260513"
    today = articles_root / "20260520"
    for directory in (stale, recent, today):
        directory.mkdir(parents=True)
        (directory / "article.html").write_text("html", encoding="utf-8")

    app.cleanup_old_article_html_dirs(tmp_path, today="20260520", keep_days=7)

    assert not stale.exists()
    assert recent.exists()
    assert today.exists()


def test_standalone_remote_fetcher_serves_article_html_without_token(tmp_path: Path):
    app_module = _load_standalone_app()
    from fastapi.testclient import TestClient

    html_file = tmp_path / "articles" / "20260519" / "rss001_a004.html"
    html_file.parent.mkdir(parents=True)
    html_file.write_text("<!doctype html><html><body>stored article</body></html>", encoding="utf-8")
    client = TestClient(app_module.create_app(output_dir=tmp_path, token="secret-token"))

    response = client.get("/articles/20260519/rss001_a004.html")

    assert response.status_code == 200
    assert "stored article" in response.text


def test_standalone_remote_fetcher_fetches_sources_concurrently(tmp_path: Path):
    app = _load_standalone_app()
    sources = [
        app.RssSource(rss_id=f"rss_{index:03d}", url=f"https://feed.example/{index}", label="feed.example", default_category="社会与其它")
        for index in range(1, 4)
    ]
    seen: list[str] = []
    lock = threading.Lock()

    def slow_fetch_one_feed(run_dir, source, request):
        time.sleep(0.2)
        with lock:
            seen.append(source.rss_id)
        return []

    original = app._fetch_one_feed
    app._fetch_one_feed = slow_fetch_one_feed
    try:
        started = time.perf_counter()
        result = app._fetch_all_feeds(
            tmp_path,
            sources,
            app.FetchRunRequest(rss_urls=[source.url for source in sources], rss_concurrency=len(sources)),
        )
        elapsed = time.perf_counter() - started
    finally:
        app._fetch_one_feed = original

    assert sorted(seen) == ["rss_001", "rss_002", "rss_003"]
    assert sorted(result) == ["rss_001", "rss_002", "rss_003"]
    assert elapsed < 0.45


def test_standalone_remote_fetcher_fetches_articles_by_source_concurrently(tmp_path: Path):
    app = _load_standalone_app()
    sources = [
        app.RssSource(rss_id=f"rss_{index:03d}", url=f"https://feed.example/{index}", label="feed.example", default_category="社会与其它")
        for index in range(1, 4)
    ]
    feed_by_rss = {
        source.rss_id: [
            app.FeedItem(
                article=app.Article(
                    title=f"Article {source.rss_id}",
                    link=f"https://article.example/{source.rss_id}",
                    pub_date="",
                    description="fallback",
                    source="Example",
                    feed_url=source.url,
                ),
                default_category=source.default_category,
            )
        ]
        for source in sources
    }
    lock = threading.Lock()
    seen: list[str] = []

    def slow_fetch_one_article_text(source, article_id, item, request, log_path):
        time.sleep(0.2)
        with lock:
            seen.append(source.rss_id)
        return app.ArticleText(
            rss_id=source.rss_id,
            article_id=article_id,
            article=item.article,
            default_category=item.default_category,
            text="text",
        )

    original = app._fetch_one_article_text
    app._fetch_one_article_text = slow_fetch_one_article_text
    try:
        started = time.perf_counter()
        records = app._fetch_all_article_text(
            tmp_path,
            sources,
            feed_by_rss,
            app.FetchRunRequest(rss_urls=[source.url for source in sources], article_fetch_concurrency=len(sources)),
        )
        elapsed = time.perf_counter() - started
    finally:
        app._fetch_one_article_text = original

    assert sorted(seen) == ["rss_001", "rss_002", "rss_003"]
    assert [record.article_id for record in records] == ["rss001_a001", "rss002_a001", "rss003_a001"]
    assert elapsed < 0.45


def test_standalone_remote_fetcher_request_defaults_to_ten_concurrent_workers():
    app = _load_standalone_app()

    request = app.FetchRunRequest.from_payload(
        {
            "config": {
                "rss_urls": ["https://feed.example/rss"],
            }
        }
    )

    assert request.rss_concurrency == 10
    assert request.article_fetch_concurrency == 10


def test_standalone_remote_fetcher_caps_feed_items_per_source():
    app = _load_standalone_app()

    def _items(rss_id: str, count: int) -> list:
        return [
            app.FeedItem(
                article=app.Article(
                    title=f"{rss_id} article {index}",
                    link=f"https://article.example/{rss_id}/{index}",
                    pub_date="",
                    description="fallback",
                    source="Example",
                    feed_url=f"https://feed.example/{rss_id}",
                ),
                default_category="社会与其它",
            )
            for index in range(count)
        ]

    # rss_001 是 GN 式高产源(100 条); rss_002 是官方源(5 条,低于 cap)
    feed_by_rss = {"rss_001": _items("rss_001", 100), "rss_002": _items("rss_002", 5)}

    out = app._collect_distinct_feed_items(feed_by_rss, None, max_per_source=20)

    per_source: dict[str, int] = {}
    for item in out:
        per_source[item.article.feed_url] = per_source.get(item.article.feed_url, 0) + 1
    assert per_source["https://feed.example/rss_001"] == 20  # 高产源被砍到 20
    assert per_source["https://feed.example/rss_002"] == 5  # 低于 cap 的源不受影响
    assert len(out) == 25


def test_standalone_remote_fetcher_request_parses_max_articles_per_source():
    app = _load_standalone_app()

    with_cap = app.FetchRunRequest.from_payload(
        {"config": {"rss_urls": ["https://feed.example/rss"], "max_articles_per_source": 20}}
    )
    assert with_cap.max_articles_per_source == 20

    without_cap = app.FetchRunRequest.from_payload({"config": {"rss_urls": ["https://feed.example/rss"]}})
    assert without_cap.max_articles_per_source is None


def _load_standalone_app():
    app_path = Path(__file__).resolve().parents[2] / "feedcore_remote_fetcher" / "app.py"
    spec = importlib.util.spec_from_file_location(f"standalone_remote_fetcher_app_{time.time_ns()}", app_path)
    assert spec is not None and spec.loader is not None
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    return app
