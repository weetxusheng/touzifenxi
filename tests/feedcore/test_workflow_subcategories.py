from feedcore.models import (
    Article,
    ArticleFourDimRecord,
    SubcategoryFourDimRecord,
    SubcategoryGroupRecord,
    SubcategoryPlanRecord,
    SubcategoryProposalRecord,
)
from feedcore.workflow.subcategories import (
    build_subcategory_prompt,
    fallback_subcategory_groups,
    parse_subcategory_plan,
    validate_subcategory_plan,
)


def _article(title: str, link: str = "https://example.com/a") -> Article:
    return Article(
        title=title,
        link=link,
        pub_date="",
        description="",
        source="Example",
        feed_url="",
    )


def _article_dims(title: str, link: str = "https://example.com/a") -> ArticleFourDimRecord:
    return ArticleFourDimRecord(
        article=_article(title, link),
        default_category="人工智能与科技",
        final_category="人工智能与科技",
        facts=[f"{title} fact"],
        background=[f"{title} background"],
        impact=[f"{title} impact"],
        contradictions=[f"{title} contradiction"],
    )


def test_subcategory_records_serialize_for_checkpoints():
    proposal = SubcategoryProposalRecord(
        parent_category="人工智能与科技",
        name="AI芯片与算力基础设施",
        rationale="文章共同涉及GPU、数据中心与算力投资。",
        article_ids=["A1", "A2"],
    )
    plan = SubcategoryPlanRecord(
        parent_category="人工智能与科技",
        subcategories=[proposal],
        ungrouped=[{"article_id": "A3", "reason": "缺少共同议题"}],
        validation_errors=["ignored unknown article id A9"],
    )
    group = SubcategoryGroupRecord(
        parent_category="人工智能与科技",
        subcategory="AI芯片与算力基础设施",
        rationale=proposal.rationale,
        articles=[_article_dims("OpenAI chip plan")],
    )
    four_dims = SubcategoryFourDimRecord(
        parent_category="人工智能与科技",
        subcategory="AI芯片与算力基础设施",
        facts=["fact"],
        background=["background"],
        impact=["impact"],
        contradictions=["contradiction"],
        source_links=["https://example.com/a"],
    )

    assert proposal.to_dict()["article_ids"] == ["A1", "A2"]
    assert plan.to_dict()["subcategories"][0]["name"] == "AI芯片与算力基础设施"
    assert group.to_dict()["articles"][0]["article"]["title"] == "OpenAI chip plan"
    assert four_dims.to_dict()["subcategory"] == "AI芯片与算力基础设施"


def test_build_subcategory_prompt_uses_article_dimensions_not_raw_content():
    records = [_article_dims("OpenAI chip plan")]

    prompt = build_subcategory_prompt("人工智能与科技", records)

    assert "A1" in prompt
    assert "OpenAI chip plan" in prompt
    assert "OpenAI chip plan fact" in prompt
    assert "original_content" not in prompt
    assert "analysis_content" not in prompt


def test_parse_subcategory_plan_reads_strict_json():
    raw = """
{
  "parent_category": "人工智能与科技",
  "subcategories": [
    {
      "name": "AI芯片与算力基础设施",
      "rationale": "文章共同涉及GPU和数据中心。",
      "article_ids": ["A1", "A2"]
    }
  ],
  "ungrouped": [
    {"article_id": "A3", "reason": "缺少共同议题"}
  ]
}
""".strip()

    plan = parse_subcategory_plan("人工智能与科技", raw)

    assert plan.parent_category == "人工智能与科技"
    assert plan.subcategories[0].name == "AI芯片与算力基础设施"
    assert plan.subcategories[0].article_ids == ["A1", "A2"]
    assert plan.ungrouped == [{"article_id": "A3", "reason": "缺少共同议题"}]
    assert plan.validation_errors == []


def test_parse_subcategory_plan_returns_validation_error_for_invalid_json():
    plan = parse_subcategory_plan("人工智能与科技", "not json")

    assert plan.parent_category == "人工智能与科技"
    assert plan.subcategories == []
    assert plan.validation_errors


def test_validate_subcategory_plan_rejects_unknown_duplicate_vague_and_missing_rationale():
    articles = [
        _article_dims("OpenAI chip plan", "https://example.com/a1"),
        _article_dims("NVIDIA data center", "https://example.com/a2"),
        _article_dims("AI model release", "https://example.com/a3"),
    ]
    plan = SubcategoryPlanRecord(
        parent_category="人工智能与科技",
        subcategories=[
            SubcategoryProposalRecord(
                parent_category="人工智能与科技",
                name="AI芯片与算力基础设施",
                rationale="共同涉及芯片、GPU和数据中心。",
                article_ids=["A1", "A2", "A9"],
            ),
            SubcategoryProposalRecord(
                parent_category="人工智能与科技",
                name="科技新闻",
                rationale="名称过于宽泛。",
                article_ids=["A3"],
            ),
            SubcategoryProposalRecord(
                parent_category="人工智能与科技",
                name="模型产品发布",
                rationale="",
                article_ids=["A2"],
            ),
        ],
    )

    groups, validated = validate_subcategory_plan("人工智能与科技", articles, plan)

    assert len(groups) == 2
    assert groups[0].subcategory == "AI芯片与算力基础设施"
    assert [item.article.title for item in groups[0].articles] == ["OpenAI chip plan", "NVIDIA data center"]
    assert groups[1].subcategory == "AI model release"
    assert "unknown article id A9" in " | ".join(validated.validation_errors)
    assert "vague subcategory name" in " | ".join(validated.validation_errors)
    assert "missing rationale" in " | ".join(validated.validation_errors)
    assert "duplicate article id A2" in " | ".join(validated.validation_errors)


def test_fallback_subcategory_groups_uses_one_article_per_subcategory():
    articles = [
        _article_dims("OpenAI chip plan", "https://example.com/a1"),
        _article_dims("NVIDIA data center", "https://example.com/a2"),
    ]

    groups = fallback_subcategory_groups("人工智能与科技", articles, reason="invalid model output")

    assert [group.subcategory for group in groups] == ["OpenAI chip plan", "NVIDIA data center"]
    assert all(group.rationale == "invalid model output" for group in groups)
