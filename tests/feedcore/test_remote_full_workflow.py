from pathlib import Path

from feedcore.workflow.concurrent import run_concurrent_workflow_from_prefetched
from feedcore.workflow.orchestrator import WorkflowClients


class FakeRssClient:
    def fetch(self, url: str) -> str:
        raise AssertionError(f"RSS should already be prefetched: {url}")


class FakeArticleClient:
    def fetch_text(self, url: str) -> str:
        raise AssertionError(f"Article should already be prefetched: {url}")


class FakeSummaryClient:
    model = "fake"

    def score_research_article(self, _article, _content: str, _default_category: str) -> str:
        return (
            '{"score":80,"decision":"keep","reason":"政策和技术影响明确，适合进入简报",'
            '"investment_relevance":25,"information_increment":20,"decision_value":15,'
            '"verifiability":12,"noise_penalty":0,'
            '"evidence":["远程抓取结果包含可分析正文"],"tags":["AI","remote-fetch"]}'
        )

    def summarize_article(self, article, content: str) -> str:
        return "\n".join(
            [
                "#### 事实",
                f"- {article.title} 已经完成远程抓取并进入本地分析。",
                "",
                "#### 背景",
                "- 远程抓取负责 RSS、正文抽取和 artifact 下发。",
                "",
                "#### 产生的影响",
                "- 本地流程可以从 step3 继续生成简报。",
                "",
                "#### 反面观点 / 数据矛盾点",
                "- 仍需关注反爬和正文质量。",
            ]
        )

    def plan_types(self, _prompt: str) -> str:
        return (
            '{"types":[{"type_id":"type_001","name":"远程抓取",'
            '"rationale":"验证远程抓取到本地分析的完整流程",'
            '"article_ids":["rss001_a001"]}],"ungrouped":[]}'
        )

    def summarize_type_collection(self, name: str, articles) -> str:
        return "\n".join(
            [
                "#### 事实",
                f"- {name} 类型共包含 {len(articles)} 篇文章。",
                "",
                "#### 背景",
                "- 文章正文来自远程 fetcher artifact。",
                "",
                "#### 产生的影响",
                "- 本地模型步骤无需重新抓取 RSS 或正文。",
                "",
                "#### 反面观点 / 数据矛盾点",
                "- 如果远程 step3 质量低，本地简报仍会受影响。",
            ]
        )


def test_run_workflow_from_prefetched_continues_after_remote_step3(tmp_path: Path):
    result = run_concurrent_workflow_from_prefetched(
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=FakeSummaryClient(),
            translator=None,
        ),
        rss_sources=[
            {
                "rss_id": "rss_001",
                "url": "https://feed.example/openai",
                "label": "",
                "default_category": "人工智能与科技",
                "selected_reason": "test",
                "status": "selected",
            }
        ],
        feed_items=[
            {
                "article": {
                    "title": "OpenAI remote workflow",
                    "link": "https://article.example/openai",
                    "pub_date": "Tue, 19 May 2026 03:59:00 GMT",
                    "description": "remote artifact",
                    "source": "Example",
                    "feed_url": "https://feed.example/openai",
                },
                "default_category": "人工智能与科技",
                "source_label": "",
                "status": "queued",
                "skip_reason": "",
            }
        ],
        article_contents=[
            {
                "rss_id": "rss_001",
                "article_id": "rss001_a001",
                "article": {
                    "title": "OpenAI remote workflow",
                    "link": "https://article.example/openai",
                    "pub_date": "Tue, 19 May 2026 03:59:00 GMT",
                    "description": "remote artifact",
                    "source": "Example",
                    "feed_url": "https://feed.example/openai",
                },
                "default_category": "人工智能与科技",
                "text": "OpenAI remote fetcher produced enough article text for downstream analysis. "
                "This verifies that local workflow can continue from step3 without fetching again. "
                "The imported content includes market impact, technology context, policy risk, "
                "competitive pressure, deployment constraints, and unresolved data quality questions. "
                "It is intentionally long enough to pass the quality gate before model analysis starts.",
                "detected_language": "en",
                "translated": False,
                "fetch_error": None,
                "extract_error": None,
                "translation_error": None,
                "stored_html_url": "http://81.69.47.226:3000/articles/20260519/rss001_a001.html",
            }
        ],
        run_id="full_remote_test",
        model_concurrency=1,
        type_classification_concurrency=1,
        type_synthesis_concurrency=1,
    )

    assert result.run_id == "full_remote_test"
    assert result.brief_md.exists()
    assert result.brief_html.exists()
    assert (tmp_path / "full_remote_test" / "step3_article_contents.json").exists()
    step7 = (tmp_path / "full_remote_test" / "step7_type_four_dims.json").read_text(encoding="utf-8")
    assert "远程抓取" in step7
    assert "http://81.69.47.226:3000/articles/20260519/rss001_a001.html" in step7
