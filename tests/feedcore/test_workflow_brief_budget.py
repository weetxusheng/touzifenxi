import json

from feedcore.models import Article
from feedcore.workflow.orchestrator import WorkflowClients, run_workflow


class FakeRssClient:
    def fetch(self, url: str) -> str:
        return "<rss />"


class ShortArticleClient:
    document_dir = None

    def fetch_text(self, url: str) -> str:
        return (
            "OpenAI announced a product update with limited confirmed details. "
            "The article mentions timing, adoption, and cost questions, but stays brief. "
            "This sentence is only here to clear the quality gate."
        )


class OverlongSummaryClient:
    def summarize_article(self, article: Article, content: str) -> str:
        return "\n".join(
            [
                "#### 事实",
                "- OpenAI announced a product update.",
                "- The article allegedly covers partnerships, pricing, adoption, roadmap timing, competition, market reaction, and product capabilities in detail.",
                "",
                "#### 背景",
                "- The launch is framed against broader AI competition, enterprise demand, infrastructure pressure, and previous milestones.",
                "- It also implies changes in partner strategy and procurement behavior.",
                "",
                "#### 产生的影响",
                "- The update may influence adoption, market expectations, procurement decisions, and product planning across the industry.",
                "- It may also affect cloud demand, partner execution, and cost assumptions.",
                "",
                "#### 反面观点 / 数据矛盾点",
                "- The source text is too short to support this level of expansion.",
                "- Several conclusions are broader than the article itself.",
            ]
        )

    def plan_types(self, prompt: str) -> str:
        return '{"types":[],"ungrouped":[]}'

    def summarize_type_collection(self, type_name: str, articles: list[object]) -> str:
        raise AssertionError("should not be called when article brief is rejected")


def test_run_workflow_skips_article_when_brief_exceeds_source_budget(tmp_path):
    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title="OpenAI product update",
                link="https://article.example/openai",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
        ]

    result = run_workflow(
        rss_urls=["https://feed.example/openai"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=ShortArticleClient(),
            summary=OverlongSummaryClient(),
            translator=None,
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_budget_test",
    )

    step4 = json.loads((result.run_dir / "step4_article_four_dims.json").read_text(encoding="utf-8"))
    low_quality = json.loads((result.run_dir / "low_quality_articles.json").read_text(encoding="utf-8"))
    compressed_brief = step4[0]["brief"]
    original_brief = OverlongSummaryClient().summarize_article(step4[0]["article"], "")

    assert len(step4) == 1
    assert low_quality == []
    assert len(compressed_brief) < len(original_brief)
    assert compressed_brief.count("\n- ") <= 4
