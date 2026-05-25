from feedcore.models import (
    Article,
    ArticleFourDimRecord,
    ArticleTextRecord,
    TypeCollectionRecord,
    TypeFourDimRecord,
    TypePlanRecord,
    TypeProposalRecord,
)
from feedcore.workflow.concurrent import _category_group_for_collection, _prune_dimension_items
from feedcore.workflow.types import parse_type_plan, validate_type_plan


def _article() -> Article:
    return Article(
        title="NVIDIA GPU demand",
        link="https://example.com/gpu",
        pub_date="",
        description="",
        source="Example",
        feed_url="https://feed.example/ai",
    )


def _article_dims() -> ArticleFourDimRecord:
    return ArticleFourDimRecord(
        article=_article(),
        default_category="人工智能与科技",
        final_category="人工智能与科技",
        facts=["GPU demand rises"],
        background=["AI capex"],
        impact=["Chip supply chain"],
        contradictions=["Cost risk"],
    )


def test_type_workflow_records_serialize_for_checkpoints():
    text_record = ArticleTextRecord(
        rss_id="rss_001",
        article_id="rss001_a001",
        article=_article(),
        default_category="人工智能与科技",
        text="正文文本",
        detected_language="zh",
        translated=False,
    )
    proposal = TypeProposalRecord(
        type_id="type_001",
        name="AI芯片",
        rationale="文章均涉及GPU和AI芯片供应。",
        article_ids=["rss001_a001"],
    )
    plan = TypePlanRecord(
        types=[proposal],
        ungrouped=[{"article_id": "rss001_a002", "reason": "缺少共同主题"}],
        validation_errors=["ignored duplicate id"],
    )
    collection = TypeCollectionRecord(
        type_id="type_001",
        name="AI芯片",
        rationale=proposal.rationale,
        articles=[_article_dims()],
        source_rss_ids=["rss_001"],
    )
    four_dims = TypeFourDimRecord(
        type_id="type_001",
        name="AI芯片",
        facts=["fact"],
        background=["background"],
        impact=["impact"],
        contradictions=["contradiction"],
        source_links=["https://example.com/gpu"],
    )

    assert text_record.to_dict()["article_id"] == "rss001_a001"
    assert text_record.to_dict()["text"] == "正文文本"
    assert proposal.to_dict()["name"] == "AI芯片"
    assert plan.to_dict()["types"][0]["article_ids"] == ["rss001_a001"]
    assert collection.to_dict()["source_rss_ids"] == ["rss_001"]
    assert four_dims.to_dict()["name"] == "AI芯片"


def test_parse_and_validate_type_plan_rejects_invalid_assignments():
    records = [_article_dims(), _article_dims()]
    article_ids = {id(records[0]): "rss001_a001", id(records[1]): "rss001_a002"}
    rss_ids = {id(records[0]): "rss_001", id(records[1]): "rss_001"}
    plan = parse_type_plan(
        """
{
  "types": [
    {"type_id": "type_001", "name": "AI芯片", "rationale": "都涉及GPU。", "article_ids": ["rss001_a001"]},
    {"type_id": "type_002", "name": "科技新闻", "rationale": "泛化分类。", "article_ids": ["rss001_a002"]},
    {"type_id": "type_003", "name": "算力基建", "rationale": "重复。", "article_ids": ["rss001_a001"]},
    {"type_id": "type_004", "name": "模型发布", "rationale": "未知。", "article_ids": ["missing"]}
  ],
  "ungrouped": []
}
""".strip()
    )

    collections, validated = validate_type_plan(records, plan, article_ids, rss_ids)

    assert collections[0].name == "AI芯片"
    errors = " | ".join(validated.validation_errors)
    assert "invalid type name" in errors
    assert "duplicate article id" in errors
    assert "unknown article id" in errors


def test_category_group_for_collection_uses_article_votes_before_title_blob():
    article_one = Article(
        title="Nvidia CEO joins Trump's Beijing trip",
        link="https://example.com/nvidia-trip",
        pub_date="",
        description="",
        source="Example",
        feed_url="https://feed.example/world",
    )
    article_two = Article(
        title="Tech leaders follow Trump to China summit",
        link="https://example.com/tech-trip",
        pub_date="",
        description="",
        source="Example",
        feed_url="https://feed.example/world",
    )
    collection = TypeCollectionRecord(
        type_id="type_tech_execs",
        name="Executive delegation",
        rationale="Covers CEOs accompanying Trump on the China trip and related chip export questions.",
        articles=[
            ArticleFourDimRecord(
                article=article_one,
                default_category="产业与公司",
                final_category="产业与公司",
                facts=["Nvidia sought approval for H200 chip sales in China."],
                background=["Chip controls remain a pressure point."],
                impact=["The delegation could shape market access talks."],
                contradictions=["Export policy is still unsettled."],
            ),
            ArticleFourDimRecord(
                article=article_two,
                default_category="人工智能与科技",
                final_category="人工智能与科技",
                facts=["The trip included AI infrastructure and chip discussions."],
                background=["US-China technology tensions remain active."],
                impact=["Executives want clearer rules for advanced hardware."],
                contradictions=["Washington still limits top-end chip exports."],
            ),
        ],
        source_rss_ids=["rss_001"],
    )

    assert _category_group_for_collection(collection) == "人工智能与科技"


def test_prune_dimension_items_removes_ceremonial_scene_details_when_better_facts_exist():
    items = [
        "约300名青年挥舞美中两国国旗并用普通话高喊“欢迎欢迎”。",
        "天坛5月13日至14日暂停开放。",
        "每隔100至200米部署安保人员并要求禁止拍照。",
        "美国贸易代表格里尔于2026年3月提出设立“美中贸易委员会”。",
        "特朗普将请习近平“打开”中国市场。",
    ]

    pruned = _prune_dimension_items("facts", items)

    assert "美国贸易代表格里尔于2026年3月提出设立“美中贸易委员会”。" in pruned
    assert "特朗普将请习近平“打开”中国市场。" in pruned
    assert "约300名青年挥舞美中两国国旗并用普通话高喊“欢迎欢迎”。" not in pruned
    assert "天坛5月13日至14日暂停开放。" not in pruned
    assert "每隔100至200米部署安保人员并要求禁止拍照。" not in pruned
