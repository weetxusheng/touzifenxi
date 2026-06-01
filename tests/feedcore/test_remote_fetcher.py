import json
import zipfile
from pathlib import Path

from feedcore.models import Article
from feedcore.remote_fetcher import (
    FetchOnlyClients,
    FetchRunManager,
    FetchRunRequest,
    RemoteFetchConfig,
    create_artifact_zip,
    run_fetch_only,
)


class FakeRssClient:
    def fetch(self, url: str) -> str:
        return "<rss />"


class FakeArticleClient:
    def fetch_text(self, url: str) -> str:
        return (
            "OpenAI announced a new product with market impact, deployment context, "
            "competitive pressure, and unresolved cost questions. This text is long "
            "enough to pass downstream quality checks when imported by the analyzer."
        )


def test_run_fetch_only_writes_step3_and_manifest(tmp_path: Path):
    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title="OpenAI product launch",
                link="https://article.example/openai",
                pub_date="",
                description="Fallback description",
                source="Example",
                feed_url=feed_url,
            )
        ]

    result = run_fetch_only(
        config=RemoteFetchConfig(
            rss_urls=["https://feed.example/openai"],
            output_dir=tmp_path,
            run_id="fetch_test",
            quick_sample_size=10,
        ),
        clients=FetchOnlyClients(rss=FakeRssClient(), article=FakeArticleClient(), translator=None),
        parse_feed_fn=parse_feed,
    )

    run_dir = tmp_path / "fetch_test"
    step3 = json.loads((run_dir / "step3_article_contents.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert result.run_id == "fetch_test"
    assert result.run_dir == run_dir
    assert step3[0]["rss_id"] == "rss_001"
    assert step3[0]["article_id"] == "rss001_a001"
    assert step3[0]["article"]["title"] == "OpenAI product launch"
    assert step3[0]["text"].startswith("OpenAI announced")
    assert manifest["artifact_version"] == 1
    assert manifest["run_id"] == "fetch_test"
    assert manifest["files"]["article_contents"] == "step3_article_contents.json"
    assert manifest["counts"]["rss_sources"] == 1
    assert manifest["counts"]["feed_items"] == 1
    assert manifest["counts"]["article_contents"] == 1
    assert manifest["counts"]["fetch_failed"] == 0


def test_create_artifact_zip_contains_manifest_and_step_outputs(tmp_path: Path):
    run_dir = tmp_path / "fetch_test"
    (run_dir / "rss_tasks").mkdir(parents=True)
    (run_dir / "logs").mkdir()
    for name in [
        "manifest.json",
        "step1_rss_sources.json",
        "step2_feed_items.json",
        "step3_article_contents.json",
    ]:
        (run_dir / name).write_text("[]", encoding="utf-8")
    (run_dir / "logs" / "workflow.log").write_text("ok", encoding="utf-8")

    artifact = create_artifact_zip(run_dir)

    assert artifact == run_dir / "fetch_test.zip"
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "step1_rss_sources.json" in names
    assert "step2_feed_items.json" in names
    assert "step3_article_contents.json" in names
    assert "logs/workflow.log" in names


def test_fetch_run_manager_tracks_completed_run(tmp_path: Path):
    def runner(config: RemoteFetchConfig):
        run_dir = config.output_dir / config.run_id
        run_dir.mkdir(parents=True)
        artifact = run_dir / f"{config.run_id}.zip"
        artifact.write_bytes(b"zip")
        manifest = run_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        step3 = run_dir / "step3_article_contents.json"
        step3.write_text("[]", encoding="utf-8")
        return type(
            "Result",
            (),
            {
                "run_id": config.run_id,
                "run_dir": run_dir,
                "artifact_path": artifact,
                "manifest_path": manifest,
                "article_contents_path": step3,
            },
        )()

    manager = FetchRunManager(output_dir=tmp_path, runner=runner, run_inline=True)

    created = manager.create_run(
        FetchRunRequest(
            rss_urls=["https://feed.example/openai"],
            client_run_id="local_001",
            quick_sample_size=1,
            rss_concurrency=1,
            article_fetch_concurrency=1,
        )
    )
    status = manager.get_status(created.run_id)

    assert created.status == "succeeded"
    assert status is not None
    assert status.run_id == created.run_id
    assert status.status == "succeeded"
    assert status.client_run_id == "local_001"
    assert status.artifact_path == tmp_path / created.run_id / f"{created.run_id}.zip"
    assert manager.get_artifact_path(created.run_id) == status.artifact_path


def test_fetch_run_request_from_payload_reads_browser_and_categories():
    request = FetchRunRequest.from_payload(
        {
            "client_run_id": "local run 001",
            "config": {
                "rss_sources": [
                    {
                        "url": "https://feed.example/openai",
                        "default_category": "人工智能与科技",
                    }
                ],
                "quick_sample_size": 1,
                "max_articles": 3,
                "browser": {"enabled": True, "timeout": 45},
                "workflow": {"rss_concurrency": 2, "article_fetch_concurrency": 4},
            },
        }
    )

    assert request.client_run_id == "local run 001"
    assert request.rss_urls == ["https://feed.example/openai"]
    assert request.default_categories == {"https://feed.example/openai": "人工智能与科技"}
    assert request.quick_sample_size == 1
    assert request.max_articles == 3
    assert request.browser_enabled is True
    assert request.browser_timeout == 45
    assert request.rss_concurrency == 2
    assert request.article_fetch_concurrency == 4
