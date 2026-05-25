import json
import re
import threading
import time
from pathlib import Path

from feedcore.models import Article, ArticleFourDimRecord, ResearchScoreRecord, SubcategoryPlanRecord, SubcategoryProposalRecord, TypeCollectionRecord, TypeFourDimRecord
from feedcore.workflow.concurrent import _filter_type_collections_for_brief, render_type_brief_markdown
from feedcore.workflow.orchestrator import WorkflowClients, render_subcategory_plan_yaml, run_workflow
from feedcore.workflow.steps import group_validated_subcategories


class FakeRssClient:
    def fetch(self, url: str) -> str:
        return "<rss />"


class FakeArticleClient:
    document_dir = None

    def fetch_text(self, url: str) -> str:
        return (
            "OpenAI announced an AI product. However, costs remain uncertain. "
            "The article discusses model release timing, product capabilities, market reaction, "
            "competitive pressure, enterprise adoption, infrastructure costs, and unresolved risks. "
            "It includes enough context for classification, source tracing, and four-dimension analysis. "
            "The same topic is described with facts, background, impact, and contradictory signals. "
            "This extra text is present only so workflow tests pass the article quality gate."
        )


class FakeTranslator:
    def translate_to_chinese(self, text: str) -> str:
        return (
            "OpenAI发布AI产品。不过，成本仍不确定。文章讨论了模型发布时间、产品能力、市场反应、"
            "竞争压力、企业采用、基础设施成本和仍未解决的风险。材料提供了足够上下文，可用于分类、"
            "来源追溯和四要素分析。报道同时包含事实、背景、影响和相互矛盾的信号。"
            "这些额外文字仅用于让工作流测试通过文章质量门。"
        )


class FakeSummaryClient:
    def __init__(self) -> None:
        self.reading_event_calls = 0

    def score_research_article(self, article: Article, content: str, default_category: str) -> str:
        return json.dumps(
            {
                "score": 66,
                "decision": "keep",
                "reason": "The article contains concrete facts and enough research value for the brief.",
                "investment_relevance": 24,
                "information_increment": 20,
                "decision_value": 18,
                "verifiability": 13,
                "noise_penalty": 3,
                "evidence": [
                    "The article discusses product timing and market reaction.",
                    "The text includes enough detail for classification and tracing.",
                ],
                "tags": ["technology", "product"],
            },
            ensure_ascii=False,
        )

    def summarize_article(self, article: Article, content: str) -> str:
        return "\n".join(
            [
                "#### 事实",
                "- OpenAI发布AI产品。",
                "",
                "#### 背景",
                "- 该产品与AI竞争有关。",
                "",
                "#### 产生的影响",
                "- 可能影响科技市场。",
                "",
                "#### 反面观点 / 数据矛盾点",
                "- 成本仍不确定。",
            ]
        )

    def plan_subcategories(self, parent_category: str, prompt: str) -> str:
        return (
            '{"parent_category":"人工智能与科技",'
            '"subcategories":[{"name":"AI产品发布与成本不确定性",'
            '"rationale":"文章共同围绕OpenAI产品发布及成本不确定性。",'
            '"article_ids":["A1"]}],'
            '"ungrouped":[]}'
        )

    def plan_types(self, prompt: str) -> str:
        return (
            '{"types":[{"type_id":"type_001","name":"模型发布",'
            '"rationale":"文章围绕AI产品发布。",'
            '"article_ids":["rss001_a001"]}],"ungrouped":[]}'
        )

    def summarize_type_collection(self, type_name: str, articles: list[ArticleFourDimRecord]) -> str:
        return "\n".join(
            [
                "#### 事实",
                f"- {type_name}包含OpenAI产品发布。",
                "",
                "#### 背景",
                "- 相关报道属于AI竞争背景。",
                "",
                "#### 产生的影响",
                "- 可能影响科技市场。",
                "",
                "#### 反面观点 / 数据矛盾点",
                "- 成本仍不确定。",
            ]
        )

    def summarize_reading_event(self, category_name: str, topic_name: str, event_name: str, records: list[TypeFourDimRecord]) -> str:
        self.reading_event_calls += 1
        return json.dumps(
            {
                "facts": ["OpenAI发布AI产品，相关报道同时给出产品发布时间、能力描述和市场反应。"],
                "background": ["该产品发布处于AI竞争加剧、企业采用和基础设施投入持续升温的背景下。"],
                "impact": ["这一产品发布可能影响科技市场对AI应用落地和算力成本的评估。"],
                "contradictions": ["报道显示产品前景受到关注，但基础设施成本和商业化节奏仍不确定。"],
            },
            ensure_ascii=False,
        )


def test_run_workflow_writes_all_step_files_under_output_run_id(tmp_path: Path):
    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title="OpenAI AI product",
                link="https://article.example/en",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
        ]

    article_client = FakeArticleClient()
    summary_client = FakeSummaryClient()
    result = run_workflow(
        rss_urls=["https://feed.example/openai"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=article_client,
            summary=summary_client,
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_test",
        quick_sample_size=10,
    )

    expected = [
        "step1_rss_sources.json",
        "step2_feed_items.json",
        "step3_article_contents.json",
        "step4_research_scores.json",
        "filtered_by_research_score.json",
        "step4_article_four_dims.json",
        "step5_type_plan.json",
        "step6_grouped_types.json",
        "step7_type_four_dims.json",
        "step8_brief.md",
        "step8_brief.html",
        "step9_reading_topic_groups.json",
        "step10_brief.md",
        "step10_brief.html",
        "brief.md",
        "brief.html",
        "execution_log.md",
    ]
    for name in expected:
        assert (tmp_path / "news_brief_test" / name).exists()

    assert result.run_dir == tmp_path / "news_brief_test"
    assert article_client.document_dir is None
    assert not (result.run_dir / "documents").exists()
    assert "output/runs" not in result.run_dir.as_posix()
    assert (result.run_dir / "rss_tasks" / "rss_001_feed_items.json").exists()
    assert (result.run_dir / "rss_tasks" / "rss_001_articles_text.json").exists()
    assert (result.run_dir / "rss_tasks" / "rss_001_article_four_dims.json").exists()
    assert (result.run_dir / "logs" / "rss_001.log").exists()
    assert (result.run_dir / "logs" / "model_calls.log").exists()
    step3 = json.loads((result.run_dir / "step3_article_contents.json").read_text(encoding="utf-8"))
    assert step3[0]["translated"] is True
    assert step3[0]["text"]
    assert "html" not in step3[0]
    assert "original_content" not in step3[0]
    assert "analysis_content" not in step3[0]
    brief_md = (result.run_dir / "brief.md").read_text(encoding="utf-8")
    brief_html = (result.run_dir / "brief.html").read_text(encoding="utf-8")
    assert "[OpenAI AI product](https://article.example/en)" in brief_md
    assert "- https://article.example/en" not in brief_md
    assert '<a href="https://article.example/en">OpenAI AI product</a>' in brief_html
    assert '<article class="brief-entry">' in brief_html
    assert '<nav class="category-nav">' in brief_html
    assert '<nav class="topic-nav">' in brief_html
    assert brief_html.index("<h1>") < brief_html.index('<nav class="category-nav">')
    assert '<a href="#category-人工智能与科技">一、人工智能与科技</a>' in brief_html
    assert '<h2 class="category-title" id="category-人工智能与科技">一、人工智能与科技</h2>' in brief_html
    assert '<h3 class="entry-title" id="topic-人工智能与科技-模型与产品应用">1. 模型与产品应用</h3>' in brief_html
    assert "第1篇" not in brief_html
    assert '<p class="topic-summary">' in brief_html
    assert '<ul class="topic-points">' in brief_html
    assert '<ul class="event-list">' not in brief_html
    assert '<div class="event-factors">' not in brief_html
    assert '<section class="factor-section event-dimension-section event-dimension-facts">' in brief_html
    assert '<section class="factor-section event-dimension-section event-dimension-background">' in brief_html
    assert '<section class="factor-section event-dimension-section event-dimension-impact">' in brief_html
    assert '<section class="factor-section event-dimension-section event-dimension-contradictions">' in brief_html
    assert ".topic-points{margin:0;padding-left:20px;list-style:disc;}" in brief_html
    assert "#### 事实" in brief_md
    assert "#### 背景" in brief_md
    assert "#### 产生的影响" in brief_md
    assert "#### 反面观点 / 数据矛盾点" in brief_md
    assert 'class="event-title"' not in brief_html
    assert summary_client.reading_event_calls >= 1
    assert "相关报道同时给出产品发布时间、能力描述和市场反应" in brief_md
    assert "基础设施成本和商业化节奏仍不确定" in brief_md
    step7 = json.loads((result.run_dir / "step7_type_four_dims.json").read_text(encoding="utf-8"))
    assert step7[0]["category_group"] == "人工智能与科技"
    step6 = json.loads((result.run_dir / "step6_grouped_types.json").read_text(encoding="utf-8"))
    assert step6[0]["name"] == "模型发布"
    type_files = list((result.run_dir / "type_collections").glob("type_001_*.json"))
    assert type_files
    assert "模型发布包含OpenAI产品发布" in (result.run_dir / "brief.md").read_text(encoding="utf-8")
    step8_md = (result.run_dir / "step8_brief.md").read_text(encoding="utf-8")
    assert "## 人工智能与科技" in step8_md
    assert "### 模型发布" in step8_md
    assert "第1篇" not in step8_md
    step9 = json.loads((result.run_dir / "step9_reading_topic_groups.json").read_text(encoding="utf-8"))
    assert step9[0]["topics"]
    assert "summary" in step9[0]["topics"][0]
    assert "key_points" in step9[0]["topics"][0]
    assert "watchout" in step9[0]["topics"][0]
    assert "source_type_names" in step9[0]["topics"][0]
    assert "events" in step9[0]["topics"][0]
    assert step9[0]["topics"][0]["events"]
    assert "细分来源" not in brief_html
    assert (result.run_dir / "brief.html").read_text(encoding="utf-8") == (result.run_dir / "step10_brief.html").read_text(encoding="utf-8")


def test_run_workflow_can_limit_articles_after_feed_parsing(tmp_path: Path):
    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title=f"OpenAI AI product {index}",
                link=f"https://article.example/{index}/en",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
            for index in range(3)
        ]

    result = run_workflow(
        rss_urls=["https://feed.example/openai"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=FakeSummaryClient(),
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_limit_test",
        max_articles=1,
    )

    step2 = json.loads((result.run_dir / "step2_feed_items.json").read_text(encoding="utf-8"))
    assert len(step2) == 1


def test_run_workflow_limits_distinct_articles_globally_across_feeds(tmp_path: Path):
    def parse_feed(_xml: str, feed_url: str):
        if feed_url.endswith("/one"):
            return [
                Article(
                    title="Shared article",
                    link="https://article.example/shared/en",
                    pub_date="",
                    description="",
                    source="Example",
                    feed_url=feed_url,
                ),
                Article(
                    title="First unique article",
                    link="https://article.example/one/en",
                    pub_date="",
                    description="",
                    source="Example",
                    feed_url=feed_url,
                ),
            ]
        return [
            Article(
                title="Shared article again",
                link="https://article.example/shared/en",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            ),
            Article(
                title="Second unique article",
                link="https://article.example/two/en",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            ),
        ]

    result = run_workflow(
        rss_urls=["https://feed.example/one", "https://feed.example/two"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=FakeSummaryClient(),
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_global_limit_test",
        max_articles=3,
    )

    step2 = json.loads((result.run_dir / "step2_feed_items.json").read_text(encoding="utf-8"))
    step3 = json.loads((result.run_dir / "step3_article_contents.json").read_text(encoding="utf-8"))
    assert len(step2) == 3
    assert len(step3) == 3
    assert [item["article"]["link"] for item in step2] == [
        "https://article.example/shared/en",
        "https://article.example/one/en",
        "https://article.example/two/en",
    ]


def test_run_workflow_skips_low_quality_article_text_before_analysis(tmp_path: Path):
    class LowQualityArticleClient:
        document_dir = None

        def fetch_text(self, url: str) -> str:
            return "播放中"

    class CountingSummaryClient(FakeSummaryClient):
        def __init__(self) -> None:
            self.article_calls = 0

        def summarize_article(self, article: Article, content: str) -> str:
            self.article_calls += 1
            return super().summarize_article(article, content)

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title="Video only article",
                link="https://article.example/video",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
        ]

    summary_client = CountingSummaryClient()
    result = run_workflow(
        rss_urls=["https://feed.example/video"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=LowQualityArticleClient(),
            summary=summary_client,
            translator=None,
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_low_quality_test",
    )

    assert summary_client.article_calls == 0
    assert json.loads((result.run_dir / "step4_article_four_dims.json").read_text(encoding="utf-8")) == []
    low_quality = json.loads((result.run_dir / "low_quality_articles.json").read_text(encoding="utf-8"))
    assert low_quality[0]["article_id"] == "rss001_a001"
    assert low_quality[0]["quality_error"] == "low_quality_text"


def test_run_workflow_writes_clear_step_logs(tmp_path: Path):
    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title="OpenAI AI product",
                link="https://article.example/en",
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
            article=FakeArticleClient(),
            summary=FakeSummaryClient(),
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_logs_test",
    )

    workflow_log = (result.run_dir / "logs" / "workflow.log").read_text(encoding="utf-8")
    assert "step2_fetch_feeds" in workflow_log
    assert "step3_fetch_and_extract_text" in workflow_log
    assert "step7_type_four_dims" in workflow_log

    rss_log = (result.run_dir / "logs" / "rss_001.log").read_text(encoding="utf-8")
    assert '"event":"rss_fetch_start"' in rss_log
    assert '"event":"article_text_extracted"' in rss_log

    model_log = (result.run_dir / "logs" / "model_calls.log").read_text(encoding="utf-8")
    assert '"task":"research_score"' in model_log
    assert '"task":"article_four_dims"' in model_log
    assert '"task":"type_classification"' in model_log
    assert '"task":"type_synthesis"' in model_log


def test_run_workflow_filters_articles_by_research_score_before_four_dims(tmp_path: Path):
    class ResearchGateSummaryClient(FakeSummaryClient):
        def __init__(self) -> None:
            self.article_calls: list[str] = []

        def score_research_article(self, article: Article, content: str, default_category: str) -> str:
            if "drop" in article.link:
                return json.dumps(
                    {
                        "score": 41,
                        "decision": "drop",
                        "reason": "The article is readable but does not add investable information.",
                        "investment_relevance": 12,
                        "information_increment": 10,
                        "decision_value": 8,
                        "verifiability": 11,
                        "noise_penalty": 0,
                        "evidence": ["The text mostly repeats a generic summary without new numbers."],
                        "tags": ["low-signal"],
                    },
                    ensure_ascii=False,
                )
            return super().score_research_article(article, content, default_category)

        def summarize_article(self, article: Article, content: str) -> str:
            self.article_calls.append(article.link)
            return super().summarize_article(article, content)

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article("Keep article", "https://article.example/keep", "", "", "Example", feed_url),
            Article("Drop article", "https://article.example/drop", "", "", "Example", feed_url),
        ]

    summary_client = ResearchGateSummaryClient()
    result = run_workflow(
        rss_urls=["https://feed.example/research-gate"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=summary_client,
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_research_gate_test",
    )

    scores = json.loads((result.run_dir / "step4_research_scores.json").read_text(encoding="utf-8"))
    filtered = json.loads((result.run_dir / "filtered_by_research_score.json").read_text(encoding="utf-8"))
    article_dims = json.loads((result.run_dir / "step4_article_four_dims.json").read_text(encoding="utf-8"))

    assert len(scores) == 2
    assert len(filtered) == 1
    assert filtered[0]["decision"] == "drop"
    assert filtered[0]["article"]["link"] == "https://article.example/drop"
    assert len(article_dims) == 1
    assert article_dims[0]["article"]["link"] == "https://article.example/keep"
    assert summary_client.article_calls == ["https://article.example/keep"]


def test_run_workflow_passes_through_schema_loose_pending_research_scores(tmp_path: Path):
    class SchemaLooseSummaryClient(FakeSummaryClient):
        def __init__(self) -> None:
            self.article_calls: list[str] = []

        def score_research_article(self, article: Article, content: str, default_category: str) -> str:
            if "drop" in article.link:
                return json.dumps(
                    {
                        "score": 5,
                        "decision": "exclude",
                        "reason": "The article is ceremonial and not useful for investment research.",
                        "evidence": ["The report contains no concrete market or company data."],
                        "tags": ["politics"],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "score": 65,
                    "decision": "include",
                    "reason": "The article is relevant for macro risk assessment.",
                    "evidence": [],
                    "tags": ["macro", "trade"],
                },
                ensure_ascii=False,
            )

        def summarize_article(self, article: Article, content: str) -> str:
            self.article_calls.append(article.link)
            return super().summarize_article(article, content)

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article("Pass-through article", "https://article.example/pass", "", "", "Example", feed_url),
            Article("Drop article", "https://article.example/drop", "", "", "Example", feed_url),
        ]

    summary_client = SchemaLooseSummaryClient()
    result = run_workflow(
        rss_urls=["https://feed.example/research-fallback"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=summary_client,
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_research_passthrough_test",
    )

    filtered = json.loads((result.run_dir / "filtered_by_research_score.json").read_text(encoding="utf-8"))
    article_dims = json.loads((result.run_dir / "step4_article_four_dims.json").read_text(encoding="utf-8"))

    assert len(filtered) == 1
    assert filtered[0]["article"]["link"] == "https://article.example/drop"
    assert len(article_dims) == 1
    assert article_dims[0]["article"]["link"] == "https://article.example/pass"
    assert summary_client.article_calls == ["https://article.example/pass"]


def test_filter_type_collections_for_brief_drops_edge_noise_but_keeps_substantive_pending_chain():
    def article(title: str, link: str) -> Article:
        return Article(title=title, link=link, pub_date="", description="", source="Example", feed_url="https://feed.example")

    def dims(
        title: str,
        link: str,
        *,
        facts: list[str],
        background: list[str],
        impact: list[str],
        contradictions: list[str],
        final_category: str = "国际形势与地缘政治",
    ) -> ArticleFourDimRecord:
        return ArticleFourDimRecord(
            article=article(title, link),
            default_category="美国政治与政策",
            final_category=final_category,
            facts=facts,
            background=background,
            impact=impact,
            contradictions=contradictions,
        )

    def score(title: str, link: str, *, decision: str, value: int, reason: str) -> ResearchScoreRecord:
        return ResearchScoreRecord(
            rss_id="rss_001",
            article_id=link.rsplit("/", 1)[-1],
            article=article(title, link),
            default_category="美国政治与政策",
            score=value,
            decision=decision,
            reason=reason,
            investment_relevance=max(value // 3, 0),
            information_increment=max(value // 4, 0),
            decision_value=max(value // 5, 0),
            verifiability=10,
            noise_penalty=0,
            evidence=[title],
            tags=[],
            validation_errors=["score exceeds component total"] if decision == "pending" else [],
        )

    collections = [
        TypeCollectionRecord(
            type_id="type_001",
            name="特朗普访华",
            rationale="聚焦特朗普访华与中美元首会晤。",
            articles=[
                dims(
                    "特朗普将与习近平会晤",
                    "https://example.com/visit",
                    facts=["特朗普将与习近平举行会晤。"],
                    background=["这是美国领导人自2017年以来首次访华。"],
                    impact=["本次访问可能影响中美后续机制安排。"],
                    contradictions=["能否形成明确成果仍存在不确定性。"],
                )
            ],
            source_rss_ids=["rss_001"],
        ),
        TypeCollectionRecord(
            type_id="type_002",
            name="地缘诉求",
            rationale="围绕伊朗与乌克兰议题进入中美峰会议程。",
            articles=[
                dims(
                    "议员盼中国在伊朗与乌克兰问题上发挥作用",
                    "https://example.com/geopolitics",
                    facts=["美国议员希望中国在伊朗与乌克兰问题上发挥更积极作用。"],
                    background=["相关诉求发生在特朗普访华与中美高层会晤前夕。"],
                    impact=["伊朗与俄乌议题可能进入峰会核心议程。"],
                    contradictions=["报道未给出中方正式回应。"],
                )
            ],
            source_rss_ids=["rss_001"],
        ),
        TypeCollectionRecord(
            type_id="type_003",
            name="卢比奥着装",
            rationale="围绕访华期间服装与网络舆论展开。",
            articles=[
                dims(
                    "卢比奥访华穿着引发网络热议",
                    "https://example.com/outfit",
                    facts=["卢比奥穿着与马杜罗同款服装引发热议。"],
                    background=["报道主要围绕穿着和社交媒体讨论展开。"],
                    impact=["网络舆论持续发酵。"],
                    contradictions=["报道未提供可验证的政策或市场信息。"],
                )
            ],
            source_rss_ids=["rss_001"],
        ),
        TypeCollectionRecord(
            type_id="type_004",
            name="宗教人权",
            rationale="围绕异见人士与宗教自由处境展开。",
            articles=[
                dims(
                    "异见人士是否被纳入会谈引发讨论",
                    "https://example.com/rights",
                    facts=["报道讨论异见人士是否会被纳入会谈。"],
                    background=["文章聚焦宗教自由与异见人士处境。"],
                    impact=["未体现直接市场或政策可交易增量。"],
                    contradictions=["报道未提供新的机制安排或政策信息。"],
                )
            ],
            source_rss_ids=["rss_001"],
        ),
    ]
    scores = [
        score("特朗普将与习近平会晤", "https://example.com/visit", decision="keep", value=72, reason="主事件主线。"),
        score("议员盼中国在伊朗与乌克兰问题上发挥作用", "https://example.com/geopolitics", decision="pending", value=35, reason="有地缘议题信息，但评分格式有误。"),
        score("卢比奥访华穿着引发网络热议", "https://example.com/outfit", decision="pending", value=0, reason="服饰话题无业务价值。"),
        score("异见人士是否被纳入会谈引发讨论", "https://example.com/rights", decision="pending", value=15, reason="议题边缘且无业务增量。"),
    ]

    filtered = _filter_type_collections_for_brief(collections, scores)

    assert [item.name for item in filtered] == ["特朗普访华", "地缘诉求"]


def test_run_workflow_batches_type_classification_and_merges_same_type(tmp_path: Path):
    class BatchTypeSummaryClient(FakeSummaryClient):
        def __init__(self) -> None:
            self.type_plan_calls: list[list[str]] = []

        def plan_types(self, prompt: str) -> str:
            article_ids = re.findall(r'"article_id": "([^"]+)"', prompt)
            self.type_plan_calls.append(article_ids)
            return json.dumps(
                {
                    "types": [
                        {
                            "type_id": "type_001",
                            "name": "AIType",
                            "rationale": "articles discuss the same AI product type",
                            "article_ids": article_ids,
                        }
                    ],
                    "ungrouped": [],
                }
            )

        def summarize_type_collection(self, type_name: str, articles: list[ArticleFourDimRecord]) -> str:
            return "\n".join(
                [
                    "#### 事实",
                    f"- {type_name} has {len(articles)} articles.",
                    "",
                    "#### 背景",
                    "- These AI product articles all describe the same OpenAI model launch and release context.",
                    "",
                    "#### 产生的影响",
                    "- Merging the AI product records gives readers one concise model-and-product section.",
                    "",
                    "#### 反面观点 / 数据矛盾点",
                    "- The AI product cost outlook and market reaction still need source-by-source review.",
                ]
            )

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title=f"OpenAI AI product {index}",
                link=f"https://article.example/{index}",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
            for index in range(21)
        ]

    summary_client = BatchTypeSummaryClient()
    result = run_workflow(
        rss_urls=["https://feed.example/openai"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=summary_client,
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_type_batch_test",
        type_classification_concurrency=2,
    )

    assert len(summary_client.type_plan_calls) >= 2
    assert sum(len(batch) for batch in summary_client.type_plan_calls) == 21

    grouped = json.loads((result.run_dir / "step6_grouped_types.json").read_text(encoding="utf-8"))
    assert len(grouped) == 1
    assert grouped[0]["name"] == "AIType"
    assert len(grouped[0]["articles"]) == 21
    assert "AIType has 21 articles." in (result.run_dir / "brief.md").read_text(encoding="utf-8")


def test_run_workflow_uses_one_global_article_fetch_pool(tmp_path: Path):
    class CountingRssClient:
        def __init__(self) -> None:
            self.fetch_count = 0
            self.lock = threading.Lock()

        def fetch(self, url: str) -> str:
            with self.lock:
                self.fetch_count += 1
            return "<rss />"

    class BlockingArticleClient:
        document_dir = None

        def __init__(self, rss_client: CountingRssClient) -> None:
            self.rss_client = rss_client
            self.active = 0
            self.max_active = 0
            self.first_fetch_seen_rss_count: int | None = None
            self.lock = threading.Lock()

        def fetch_text(self, url: str) -> str:
            with self.lock:
                if self.first_fetch_seen_rss_count is None:
                    self.first_fetch_seen_rss_count = self.rss_client.fetch_count
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.02)
                return FakeArticleClient().fetch_text(url)
            finally:
                with self.lock:
                    self.active -= 1

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title=f"OpenAI AI product {feed_url[-1]}-{index}",
                link=f"https://article.example/{feed_url[-1]}/{index}",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
            for index in range(3)
        ]

    rss_client = CountingRssClient()
    article_client = BlockingArticleClient(rss_client)
    result = run_workflow(
        rss_urls=[
            "https://feed.example/rss1",
            "https://feed.example/rss2",
            "https://feed.example/rss3",
        ],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=rss_client,
            article=article_client,
            summary=FakeSummaryClient(),
            translator=FakeTranslator(),
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_global_fetch_pool_test",
        rss_concurrency=1,
        article_fetch_concurrency=2,
        model_concurrency=2,
    )

    assert article_client.first_fetch_seen_rss_count == 3
    assert article_client.max_active <= 2
    step3 = json.loads((result.run_dir / "step3_article_contents.json").read_text(encoding="utf-8"))
    assert len(step3) == 9
    for rss_id in ("rss_001", "rss_002", "rss_003"):
        assert (result.run_dir / "rss_tasks" / f"{rss_id}_feed_items.json").exists()
        assert (result.run_dir / "rss_tasks" / f"{rss_id}_articles_text.json").exists()
        assert (result.run_dir / "rss_tasks" / f"{rss_id}_article_four_dims.json").exists()


def test_run_workflow_merges_related_short_types_before_synthesis(tmp_path: Path):
    class RelatedTypeSummaryClient(FakeSummaryClient):
        def __init__(self) -> None:
            self.synthesized: list[tuple[str, int]] = []

        def plan_types(self, prompt: str) -> str:
            article_ids = re.findall(r'"article_id": "([^"]+)"', prompt)
            return json.dumps(
                {
                    "types": [
                        {
                            "type_id": "type_001",
                            "name": "通胀冲击",
                            "rationale": "CPI inflation article",
                            "article_ids": [article_ids[0]],
                        },
                        {
                            "type_id": "type_002",
                            "name": "美联储加息预期",
                            "rationale": "Fed rate expectation article",
                            "article_ids": [article_ids[1]],
                        },
                    ],
                    "ungrouped": [],
                }
            )

        def summarize_type_collection(self, type_name: str, articles: list[ArticleFourDimRecord]) -> str:
            self.synthesized.append((type_name, len(articles)))
            return "\n".join(
                [
                    "#### 事实",
                    f"- {type_name} has {len(articles)} articles.",
                    "",
                    "#### 背景",
                    "- Related macro market records are merged.",
                    "",
                    "#### 产生的影响",
                    "- The section is less fragmented.",
                    "",
                    "#### 反面观点 / 数据矛盾点",
                    "- Some details may differ by source.",
                ]
            )

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article("CPI inflation rises", "https://article.example/cpi", "", "", "Example", feed_url),
            Article("Fed may hike rates", "https://article.example/fed", "", "", "Example", feed_url),
        ]

    summary_client = RelatedTypeSummaryClient()
    result = run_workflow(
        rss_urls=["https://feed.example/macro"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=summary_client,
            translator=None,
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_related_types_test",
    )

    grouped = json.loads((result.run_dir / "step6_grouped_types.json").read_text(encoding="utf-8"))
    assert len(grouped) == 1
    assert grouped[0]["name"] == "通胀与利率"
    assert len(grouped[0]["articles"]) == 2
    assert summary_client.synthesized == [("通胀与利率", 2)]


def test_category_group_prefers_type_and_evidence_over_final_category(tmp_path: Path):
    class VisitSummaryClient(FakeSummaryClient):
        def plan_types(self, prompt: str) -> str:
            article_ids = re.findall(r'"article_id": "([^"]+)"', prompt)
            return json.dumps(
                {
                    "types": [
                        {
                            "type_id": "type_001",
                            "name": "特朗普访华",
                            "rationale": "visit and summit coverage",
                            "article_ids": article_ids,
                        }
                    ],
                    "ungrouped": [],
                }
            )

        def summarize_type_collection(self, type_name: str, articles: list[ArticleFourDimRecord]) -> str:
            return "\n".join(
                [
                    "#### 事实",
                    "- 特朗普访华并与习近平举行会谈。",
                    "",
                    "#### 背景",
                    "- 议题涉及中美关系和外交安排。",
                    "",
                    "#### 产生的影响",
                    "- 可能影响双边关系。",
                    "",
                    "#### 反面观点 / 数据矛盾点",
                    "- 会谈结果仍不确定。",
                ]
            )

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                "特朗普访华并讨论人工智能议题",
                "https://article.example/trump-visit",
                "",
                "",
                "Example",
                feed_url,
            )
        ]

    result = run_workflow(
        rss_urls=["https://feed.example/ai"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=FakeArticleClient(),
            summary=VisitSummaryClient(),
            translator=None,
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_category_group_test",
    )

    step7 = json.loads((result.run_dir / "step7_type_four_dims.json").read_text(encoding="utf-8"))
    assert step7[0]["name"] == "特朗普访华"
    assert step7[0]["category_group"] == "国际形式"
    assert "## 一、国际形式" in (result.run_dir / "brief.md").read_text(encoding="utf-8")


def test_run_workflow_uses_lead_focused_input_for_long_feature_articles(tmp_path: Path):
    mid_marker = "TRADE_COMMITTEE_PROGRESS"
    late_marker = "ROBOT_INVEST_4000"
    lead_paragraphs = [
        ("Trump arrived in Beijing for a state visit centered on tariffs, Taiwan, and market access. " * 5).strip(),
        ("The opening agenda focused on summit protocol, trade friction, and the balance of power between Washington and Beijing. " * 4).strip(),
        ("Officials described the visit as a diplomatic reset while analysts debated how much leverage each side really has. " * 4).strip(),
        ("The feature then toured several Chinese cities to show how local industry and urban development have changed over the past decade. " * 4).strip(),
        ("Tourists crowded neon skylines and local guides described the city as an 8D cyberpunk destination. " * 5).strip(),
        ("Several residents spoke about daily life, education dreams, and how the city has changed over time. " * 5).strip(),
        ("Officials and delegates said a possible summit deliverable was a trade committee covering non-sensitive goods. "
         f"{mid_marker} may shape follow-up talks on tariffs, approvals, and market access. " * 4).strip(),
    ]
    late_paragraph = (
        "Deep in the feature, the story detours into a robot showroom and local industrial policy. "
        f"{late_marker} becomes a side anecdote rather than the main news line. "
    ) * 10
    long_feature_text = "\n\n".join(lead_paragraphs + [late_paragraph])

    class LongFeatureArticleClient(FakeArticleClient):
        def fetch_text(self, url: str) -> str:
            return long_feature_text

    class CapturingSummaryClient(FakeSummaryClient):
        def __init__(self):
            self.article_inputs: list[str] = []

        def summarize_article(self, article: Article, content: str) -> str:
            self.article_inputs.append(content)
            return super().summarize_article(article, content)

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                "Trump returns to Beijing for a high-stakes summit",
                "https://article.example/trump-feature",
                "",
                "",
                "Example",
                feed_url,
            )
        ]

    summary_client = CapturingSummaryClient()
    run_workflow(
        rss_urls=["https://feed.example/trump"],
        output_dir=tmp_path,
        clients=WorkflowClients(
            rss=FakeRssClient(),
            article=LongFeatureArticleClient(),
            summary=summary_client,
            translator=None,
        ),
        parse_feed_fn=parse_feed,
        run_id="news_brief_long_feature_focus_test",
    )

    assert summary_client.article_inputs
    assert "Trump arrived in Beijing for a state visit" in summary_client.article_inputs[0]
    assert mid_marker in summary_client.article_inputs[0]
    assert late_marker not in summary_client.article_inputs[0]
    assert len(summary_client.article_inputs[0]) < len(long_feature_text)


def test_type_brief_filters_polluted_source_titles():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="AI芯片",
        facts=["事实"],
        background=["背景"],
        impact=["影响"],
        contradictions=["矛盾"],
        category_group="人工智能与科技",
        source_refs=[
            {
                "title": "正常标题",
                "url": "https://example.com/good",
                "source": "Example",
            },
            {
                "title": "[Excerpt](IREN NASDAQ: IREN Upsizes $2.6B Convert. Why Now? - NAI500](https://news.google.com/rss/articles/bad?oc=5)",
                "url": "https://news.google.com/rss/articles/bad?oc=5",
                "source": "Google News",
            },
            {
                "title": "https://news.google.com/rss/articles/raw?oc=5",
                "url": "https://news.google.com/rss/articles/raw?oc=5",
                "source": "Google News",
            },
            {
                "title": r"[\[Excerpt\](IREN NASDAQ: IREN Upsizes $2.6B Convert. Why Now? - NAI500",
                "url": "https://news.google.com/rss/articles/real-bad?oc=5",
                "source": "NAI500",
            },
        ],
        source_links=[
            "https://news.google.com/rss/articles/bad?oc=5",
            "https://news.google.com/rss/articles/raw?oc=5",
            "https://news.google.com/rss/articles/real-bad?oc=5",
        ],
    )

    markdown = render_type_brief_markdown([record])

    assert "[正常标题](https://example.com/good)" in markdown
    assert "Excerpt" not in markdown
    assert "NAI500" not in markdown
    assert "rss/articles/bad" not in markdown
    assert "rss/articles/raw" not in markdown
    assert "rss/articles/real-bad" not in markdown


def test_render_subcategory_plan_yaml_maps_category_scoped_article_ids_to_titles():
    records = [
        ArticleFourDimRecord(
            article=Article("OpenAI chip", "https://a1", "", "", "Example", ""),
            default_category="人工智能与科技",
            final_category="人工智能与科技",
            facts=[],
            background=[],
            impact=[],
            contradictions=[],
        ),
        ArticleFourDimRecord(
            article=Article("AI model", "https://a2", "", "", "Example", ""),
            default_category="人工智能与科技",
            final_category="人工智能与科技",
            facts=[],
            background=[],
            impact=[],
            contradictions=[],
        ),
    ]
    plan = SubcategoryPlanRecord(
        parent_category="人工智能与科技",
        subcategories=[
            SubcategoryProposalRecord("人工智能与科技", "AI芯片合作", "A1围绕芯片。", ["A1"]),
            SubcategoryProposalRecord("人工智能与科技", "AI模型发布", "A2围绕模型。", ["A2"]),
        ],
    )
    groups, validated = group_validated_subcategories(records, [plan])

    report = render_subcategory_plan_yaml(
        report_date="2026-05-12",
        input_path="step4_article_four_dims.json",
        plans=validated,
        groups=groups,
        articles=records,
    )

    assert "article_id: 'A2'" in report
    assert "original_title: 'AI model'" in report
    assert "original_url: 'https://a2'" in report
