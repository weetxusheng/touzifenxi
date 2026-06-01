from __future__ import annotations

import dataclasses

from chip.topic_grouping import OTHER_TOPIC, group_analyses_by_chip_themes
from utils.tools.facades.intelligence import ArticleAnalysis


def _analysis(title: str, normalized_keywords: list[str] | None = None, core_summary: str = "") -> ArticleAnalysis:
    return ArticleAnalysis(
        report_date="2026-05-19",
        channel_key="semi",
        channel_name="SEMI 中国",
        title=title,
        publish_date="2026-05-19",
        source_keywords=[],
        normalized_keywords=list(normalized_keywords or []),
        entities=[],
        signals=[],
        topic="",
        core_summary=core_summary,
        followup_queries=[],
        url=f"https://example.com/{abs(hash(title))}",
    )


def test_groups_into_chip_themes():
    analyses = [
        _analysis("台积电2nm新工艺良率提升"),
        _analysis("DRAM合约价上涨7%"),
        _analysis("中微四川公司新厂投产"),
        _analysis("HBM供应紧张"),
        _analysis("通用商业新闻无关半导体"),
    ]
    new_analyses, briefs = group_analyses_by_chip_themes(analyses)
    topics = {a.topic for a in new_analyses}
    assert "技术节点突破" in topics
    assert "价格变动" in topics
    assert "产能/扩产" in topics
    assert "供需/缺货" in topics
    assert OTHER_TOPIC in topics
    assert len(briefs) >= 5


def test_briefs_carry_titles():
    # Use a title that only matches 技术节点突破 keywords ("制程"); avoid "量产"
    # which would land it in 新品发布 due to first-hit-by-CHIP_THEMES-order.
    analyses = [_analysis("英特尔14A制程突破")]
    _, briefs = group_analyses_by_chip_themes(analyses)
    node_brief = next(b for b in briefs if b.topic == "技术节点突破")
    assert "英特尔14A制程突破" in node_brief.titles
    assert node_brief.article_count == 1


def test_briefs_aggregate_channels_and_keywords():
    analyses = [
        _analysis(
            "台积电推出2nm",
            normalized_keywords=["台积电", "2nm"],
        ),
        _analysis(
            # Must contain a 新品发布 keyword ("推出") so it lands in the same
            # bucket as a1; otherwise "2nm" alone would route it to 技术节点突破.
            "三星推出2nm",
            normalized_keywords=["三星"],
        ),
    ]
    # Force the second one onto laoyaoba to test channel aggregation
    analyses[1] = dataclasses.replace(analyses[1], channel_key="laoyaoba", channel_name="爱集微")
    _, briefs = group_analyses_by_chip_themes(analyses)
    # Both fall into 新品发布 (推出) + 技术节点突破 (2nm); first hit by CHIP_THEMES iteration wins.
    # Order in CHIP_THEMES: 新品发布 then 技术节点突破 — so both go to 新品发布.
    launch = next((b for b in briefs if b.topic == "新品发布"), None)
    assert launch is not None
    assert sorted(launch.channels) == ["laoyaoba", "semi"]
    assert "台积电" in launch.keywords
    assert "三星" in launch.keywords


def test_no_match_goes_to_other():
    analyses = [_analysis("纯商业新闻")]
    new_analyses, briefs = group_analyses_by_chip_themes(analyses)
    assert new_analyses[0].topic == OTHER_TOPIC
    other = next(b for b in briefs if b.topic == OTHER_TOPIC)
    assert other.article_count == 1


from chip.topic_grouping import NON_SEMI_TOPIC


def test_groups_non_semiconductor_separately():
    analyses = [
        _analysis("台积电2nm新工艺良率提升"),                  # 半导体 → 技术节点突破
        _analysis("长江存储IPO辅导"),                          # 半导体 → 投融资/并购
        _analysis("特斯拉放弃印度建厂计划：汽车供应链短板"),    # 非半导体 → 非半导体内容
        _analysis("云南大理774.95MW风光项目"),                 # 非半导体 → 非半导体内容
    ]
    new_analyses, briefs = group_analyses_by_chip_themes(analyses)
    topics = {a.topic for a in new_analyses}
    assert "技术节点突破" in topics
    assert "投融资/并购" in topics
    assert NON_SEMI_TOPIC in topics
    # 非半导体桶应该有两个
    non_semi_brief = next(b for b in briefs if b.topic == NON_SEMI_TOPIC)
    assert non_semi_brief.article_count == 2


def test_bucket_order():
    """简报桶顺序：新品发布 → 价格变动 → 投融资/并购 → 产能 → 供需 → 节点 → 其他 → 非半导体"""
    analyses = [
        _analysis("台积电2nm工艺"),                # 技术节点
        _analysis("长江存储IPO"),                  # 投融资
        _analysis("台积电推出2nm芯片"),            # 新品发布
        _analysis("无关泛科技新闻"),               # 其他
        _analysis("光伏风电项目"),                 # 非半导体
    ]
    _, briefs = group_analyses_by_chip_themes(analyses)
    topics_in_order = [b.topic for b in briefs]
    print("bucket order:", topics_in_order)
    # 投融资在产能/扩产前面（CHIP_THEMES 字典顺序）
    assert topics_in_order.index("新品发布") < topics_in_order.index("投融资/并购")
    assert topics_in_order.index("投融资/并购") < topics_in_order.index("技术节点突破")
    assert topics_in_order[-1] == NON_SEMI_TOPIC  # 非半导体永远最后
