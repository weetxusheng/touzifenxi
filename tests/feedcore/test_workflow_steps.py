from feedcore.models import Article, ArticleContentRecord, ArticleFourDimRecord
from feedcore.workflow.steps import (
    fetch_article_contents,
    fetch_feed_items,
    generate_article_four_dims,
    generate_subcategory_four_dims,
    group_validated_subcategories,
    parse_four_dimensions,
    plan_dynamic_subcategories,
    select_rss_sources,
)
import feedcore.workflow.steps as workflow_steps


class FakeRssClient:
    def fetch(self, url: str) -> str:
        return f"<rss>{url}</rss>"


class FakeArticleClient:
    def fetch_text(self, url: str) -> str:
        if url.endswith("/en"):
            return "OpenAI announced a new AI chip partnership. However, costs may rise."
        return "财政部发布新的市场政策，可能影响债券市场。"


class FakeTranslator:
    def translate_to_chinese(self, text: str) -> str:
        return "OpenAI宣布新的AI芯片合作。不过，成本可能上升。"


class FailingTranslator:
    def translate_to_chinese(self, text: str) -> str:
        raise ValueError("Text length need to be between 0 and 5000 characters")


class FakeFourDimClient:
    def summarize_article(self, article: Article, content: str) -> str:
        return "\n".join(
            [
                "#### 事实",
                f"- {article.title} 有新的进展。",
                "",
                "#### 背景",
                "- 该事件与此前行业变化有关。",
                "",
                "#### 产生的影响",
                "- 可能影响市场预期。",
                "",
                "#### 反面观点 / 数据矛盾点",
                "- 相关成本或效果仍存在不确定性。",
            ]
        )

    def plan_subcategories(self, parent_category: str, prompt: str) -> str:
        return """
{
  "parent_category": "人工智能与科技",
  "subcategories": [
    {
      "name": "AI芯片与算力基础设施",
      "rationale": "文章共同涉及芯片和算力。",
      "article_ids": ["A1", "A2"]
    }
  ],
  "ungrouped": []
}
""".strip()


class FailingSummaryClient(FakeFourDimClient):
    def summarize_article(self, article: Article, content: str) -> str:
        raise RuntimeError("model rejected request: input too long")


def test_select_rss_sources_samples_ten_across_categories():
    urls = [f"https://example.com/{i}" for i in range(12)]
    categories = ["绉戞妧", "璐㈢粡", "鍥介檯褰㈠娍", "绀句細"]

    selected = select_rss_sources(
        urls,
        default_categories={url: categories[i % len(categories)] for i, url in enumerate(urls)},
        max_sources=10,
    )

    assert len(selected) == 10
    assert len({item.default_category for item in selected}) == 4
    assert selected[0].url == urls[0]


def test_fetch_feed_items_writes_default_category_and_dedupes_links():
    sources = select_rss_sources(
        ["https://feed.example/tech", "https://feed.example/finance"],
        default_categories={
            "https://feed.example/tech": "绉戞妧",
            "https://feed.example/finance": "璐㈢粡",
        },
    )

    def parse_feed(_xml: str, feed_url: str):
        return [
            Article(
                title="Shared headline",
                link="https://article.example/shared",
                pub_date="",
                description="",
                source="Example",
                feed_url=feed_url,
            )
        ]

    records = fetch_feed_items(sources, FakeRssClient(), parse_feed)

    assert len(records) == 1
    assert records[0].default_category == "绉戞妧"
    assert records[0].article.feed_url == "https://feed.example/tech"


def test_fetch_article_contents_translates_english():
    source_article = Article(
        title="OpenAI partnership",
        link="https://article.example/en",
        pub_date="",
        description="",
        source="Example",
        feed_url="https://feed.example/tech",
    )
    feed_records = fetch_feed_items(
        [select_rss_sources(["https://feed.example/tech"], default_categories={"https://feed.example/tech": "绉戞妧"})[0]],
        FakeRssClient(),
        lambda _xml, _feed_url: [source_article],
    )

    contents = fetch_article_contents(feed_records, FakeArticleClient(), translator=FakeTranslator())

    assert len(contents) == 1
    assert contents[0].detected_language == "en"
    assert contents[0].translated is True
    assert "OpenAI宣布" in contents[0].analysis_content


def test_fetch_article_contents_keeps_original_when_translation_fails():
    source_article = Article(
        title="Long English article",
        link="https://article.example/en",
        pub_date="",
        description="fallback",
        source="Example",
        feed_url="https://feed.example/tech",
    )
    feed_records = fetch_feed_items(
        [select_rss_sources(["https://feed.example/tech"], default_categories={"https://feed.example/tech": "绉戞妧"})[0]],
        FakeRssClient(),
        lambda _xml, _feed_url: [source_article],
    )

    contents = fetch_article_contents(feed_records, FakeArticleClient(), translator=FailingTranslator())

    assert len(contents) == 1
    assert contents[0].translated is False
    assert "translation failed" in (contents[0].fetch_error or "")
    assert contents[0].analysis_content.startswith("OpenAI announced")


def test_generate_article_four_dims_allows_category_override():
    article = Article(
        title="OpenAI releases AI chip plan",
        link="https://article.example/ai",
        pub_date="",
        description="",
        source="Example",
        feed_url="https://feed.example/general",
    )
    content = ArticleContentRecord(
        article=article,
        default_category="绀句細",
        original_content="OpenAI AI GPU data center",
        analysis_content="OpenAI AI GPU data center",
        detected_language="en",
    )

    records = generate_article_four_dims([content], FakeFourDimClient())

    assert records[0].default_category == "绀句細"
    assert records[0].final_category == "人工智能与科技"
    assert records[0].facts
    assert records[0].facts[0].startswith("OpenAI releases AI chip")
    assert records[0].brief.count("\n- ") <= 4


def test_generate_article_four_dims_keeps_article_when_model_fails():
    article = Article(
        title="Oversized article",
        link="https://article.example/too-long",
        pub_date="",
        description="",
        source="Example",
        feed_url="",
    )
    content = ArticleContentRecord(
        article=article,
        default_category="绉戞妧",
        original_content="x",
        analysis_content="x",
    )

    records = generate_article_four_dims([content], FailingSummaryClient())

    assert len(records) == 1
    assert records[0].facts == ["模型生成失败：model rejected request: input too long"]
    assert records[0].contradictions == ["该条文章未完成模型归纳，需人工复核。"]


def test_legacy_topic_helpers_are_not_part_of_workflow_api():
    assert not hasattr(workflow_steps, "group_article_topics")
    assert not hasattr(workflow_steps, "generate_topic_four_dims")


def test_plan_dynamic_subcategories_uses_model_per_parent_category():
    records = [
        ArticleFourDimRecord(
            article=Article("OpenAI chip", "https://a1", "", "", "Example", ""),
            default_category="人工智能与科技",
            final_category="人工智能与科技",
            facts=["chip fact"],
            background=[],
            impact=[],
            contradictions=[],
        ),
        ArticleFourDimRecord(
            article=Article("NVIDIA GPU", "https://a2", "", "", "Example", ""),
            default_category="人工智能与科技",
            final_category="人工智能与科技",
            facts=["gpu fact"],
            background=[],
            impact=[],
            contradictions=[],
        ),
    ]

    plans = plan_dynamic_subcategories(records, FakeFourDimClient())

    assert plans[0].parent_category == "人工智能与科技"
    assert plans[0].subcategories[0].name == "AI芯片与算力基础设施"


def test_group_validated_subcategories_materializes_model_assignments():
    records = [
        ArticleFourDimRecord(
            article=Article("OpenAI chip", "https://a1", "", "", "Example", ""),
            default_category="人工智能与科技",
            final_category="人工智能与科技",
            facts=["chip fact"],
            background=[],
            impact=[],
            contradictions=[],
        ),
        ArticleFourDimRecord(
            article=Article("NVIDIA GPU", "https://a2", "", "", "Example", ""),
            default_category="人工智能与科技",
            final_category="人工智能与科技",
            facts=["gpu fact"],
            background=[],
            impact=[],
            contradictions=[],
        ),
    ]
    plans = plan_dynamic_subcategories(records, FakeFourDimClient())

    groups, validated = group_validated_subcategories(records, plans)

    assert groups[0].subcategory == "AI芯片与算力基础设施"
    assert [item.article.title for item in groups[0].articles] == ["OpenAI chip", "NVIDIA GPU"]
    assert validated[0].validation_errors == []


def test_generate_subcategory_four_dims_merges_only_same_subcategory():
    class TwoGroupPlanner:
        def plan_subcategories(self, parent_category: str, prompt: str) -> str:
            return """
{
  "parent_category": "浜哄伐鏅鸿兘涓庣鎶€",
  "subcategories": [
    {
      "name": "AI鑺墖鍚堜綔",
      "rationale": "A1鍥寸粫鑺墖鍚堜綔銆?,
      "article_ids": ["A1"]
    },
    {
      "name": "AI妯″瀷鍙戝竷",
      "rationale": "A2鍥寸粫妯″瀷鍙戝竷銆?,
      "article_ids": ["A2"]
    }
  ],
  "ungrouped": []
}
""".strip()

    records = [
        ArticleFourDimRecord(
            article=Article("OpenAI chip", "https://a1", "", "", "Example", ""),
            default_category="浜哄伐鏅鸿兘涓庣鎶€",
            final_category="浜哄伐鏅鸿兘涓庣鎶€",
            facts=["chip fact"],
            background=["chip bg"],
            impact=["chip impact"],
            contradictions=["chip contra"],
        ),
        ArticleFourDimRecord(
            article=Article("AI model", "https://a2", "", "", "Example", ""),
            default_category="浜哄伐鏅鸿兘涓庣鎶€",
            final_category="浜哄伐鏅鸿兘涓庣鎶€",
            facts=["model fact"],
            background=[],
            impact=[],
            contradictions=[],
        ),
    ]
    plans = plan_dynamic_subcategories(records, TwoGroupPlanner())
    groups, _validated = group_validated_subcategories(records, plans)

    dims = generate_subcategory_four_dims(groups)

    assert dims[0].parent_category == "浜哄伐鏅鸿兘涓庣鎶€"
    assert dims[0].facts == ["chip fact"]
    assert dims[1].facts == ["model fact"]
def test_parse_four_dimensions_accepts_normal_chinese_headings():
    result = parse_four_dimensions(
        "\n".join(
            [
                "#### 事实",
                "- A fact",
                "#### 背景",
                "- A background",
                "#### 产生的影响",
                "- An impact",
                "#### 反面观点 / 数据矛盾点",
                "- A contradiction",
            ]
        )
    )

    assert result["facts"] == ["A fact"]
    assert result["background"] == ["A background"]
    assert result["impact"] == ["An impact"]
    assert result["contradictions"] == ["A contradiction"]
