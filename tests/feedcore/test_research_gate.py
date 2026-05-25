from feedcore.models import Article, ResearchScoreRecord
from feedcore.workflow.research_gate import (
    enforce_research_value_heuristics,
    parse_research_score,
    should_pass_through_research_pending,
)


def _article() -> Article:
    return Article(
        title="Policy update",
        link="https://example.com/policy",
        pub_date="2026-05-14",
        description="",
        source="Example",
        feed_url="https://feed.example/policy",
    )


def test_parse_research_score_accepts_valid_keep_record():
    record = parse_research_score(
        '{"score":66,"decision":"keep","reason":"The article includes concrete policy details and market implications.","investment_relevance":24,"information_increment":18,"decision_value":15,"verifiability":12,"noise_penalty":3,"evidence":["Tariff details were updated.","The article names the affected sectors."],"tags":["policy","trade"]}',
        rss_id="rss_001",
        article_id="rss001_a001",
        article=_article(),
        default_category="macro",
    )

    assert record.score == 66
    assert record.decision == "keep"
    assert record.validation_errors == []
    assert len(record.evidence) == 2


def test_parse_research_score_downgrades_keep_without_evidence_to_pending():
    record = parse_research_score(
        '{"score":70,"decision":"keep","reason":"The article has useful research detail.","investment_relevance":24,"information_increment":18,"decision_value":15,"verifiability":13,"noise_penalty":0,"evidence":[],"tags":["policy"]}',
        rss_id="rss_001",
        article_id="rss001_a001",
        article=_article(),
        default_category="macro",
    )

    assert record.decision == "pending"
    assert "missing evidence" in " ".join(record.validation_errors)


def test_parse_research_score_normalizes_common_decision_aliases():
    record = parse_research_score(
        '{"score":15,"decision":"exclude","reason":"The article does not contain investable information.","investment_relevance":0,"information_increment":0,"decision_value":0,"verifiability":15,"noise_penalty":0,"evidence":["No concrete market data."],"tags":["politics"]}',
        rss_id="rss_001",
        article_id="rss001_a001",
        article=_article(),
        default_category="macro",
    )

    assert record.decision == "drop"
    assert not any("invalid decision" in err for err in record.validation_errors)


def test_schema_loose_pending_record_can_pass_through():
    record = parse_research_score(
        '{"score":65,"decision":"include","reason":"The article is relevant for macro risk assessment.","evidence":[],"tags":["macro"]}',
        rss_id="rss_001",
        article_id="rss001_a001",
        article=_article(),
        default_category="macro",
    )

    assert record.decision == "pending"
    assert should_pass_through_research_pending(record) is True


def test_parse_research_score_marks_invalid_json_as_pending():
    record = parse_research_score(
        '{"score":72,"decision":"keep"',
        rss_id="rss_001",
        article_id="rss001_a001",
        article=_article(),
        default_category="macro",
    )

    assert record.decision == "pending"
    assert record.validation_errors


def test_enforce_research_value_heuristics_drops_ceremonial_arrival_article():
    article = Article(
        title="特朗普专机抵达北京 韩正等在机场迎接",
        link="https://example.com/arrival",
        pub_date="2026-05-14",
        description="",
        source="Example",
        feed_url="https://feed.example/world",
    )
    record = ResearchScoreRecord(
        rss_id="rss_001",
        article_id="rss001_a001",
        article=article,
        default_category="国际形势与地缘政治",
        score=68,
        decision="keep",
        reason="The article is relevant to current events.",
        investment_relevance=18,
        information_increment=16,
        decision_value=18,
        verifiability=14,
        noise_penalty=0,
        evidence=["Trump arrived in Beijing.", "Security restrictions were visible."],
        tags=["world"],
    )
    text = (
        "特朗普专机抵达北京，约300名青年挥舞美中两国国旗并高喊欢迎欢迎。"
        "天坛5月13日至14日暂停开放。"
        "现场每隔100至200米部署安保人员并要求禁止拍照。"
    )

    adjusted = enforce_research_value_heuristics(record, text)

    assert adjusted.decision == "drop"
    assert adjusted.score <= 45
    assert "ceremonial" in adjusted.reason


def test_enforce_research_value_heuristics_keeps_policy_and_trade_article():
    article = Article(
        title="“贸易委员会”或成为美中峰会可交付的关键成果之一",
        link="https://example.com/trade-committee",
        pub_date="2026-05-14",
        description="",
        source="Example",
        feed_url="https://feed.example/trade",
    )
    record = ResearchScoreRecord(
        rss_id="rss_001",
        article_id="rss001_a002",
        article=article,
        default_category="财经信息",
        score=72,
        decision="keep",
        reason="The article contains specific policy and tariff details.",
        investment_relevance=24,
        information_increment=18,
        decision_value=16,
        verifiability=14,
        noise_penalty=0,
        evidence=["The committee was proposed in March 2026.", "Tariff reductions are under discussion."],
        tags=["trade", "policy"],
    )
    text = (
        "美国贸易代表格里尔于2026年3月提出设立贸易委员会。"
        "路透社称双方可能各自确定300亿美元商品清单并降低关税。"
        "该机制聚焦非敏感商品和市场准入。"
    )

    adjusted = enforce_research_value_heuristics(record, text)

    assert adjusted.decision == "keep"
    assert adjusted.score == 72
