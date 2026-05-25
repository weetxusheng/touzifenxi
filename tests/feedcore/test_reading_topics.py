from feedcore.models import TypeFourDimRecord
from feedcore.workflow.reading_topics import (
    ReadingCategoryRecord,
    ReadingEventRecord,
    ReadingTopicRecord,
    build_reading_topic_groups,
    collapse_type_dims_for_type_brief,
    fallback_category_intro,
    render_reading_topic_brief_markdown,
    render_reading_topic_brief_html,
)


def test_fallback_category_intro_is_based_on_topic_facts_not_template():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="公司财报",
        category_group="财经信息",
        facts=["Unity披露季度营收下降，同时下调全年收入指引。"],
        background=["公司处于游戏引擎业务调整期。"],
        impact=["投资者重新评估公司增长预期。"],
        contradictions=["管理层称成本控制有效，但收入指引仍被下调。"],
    )

    category = build_reading_topic_groups([record])[0]
    intro = fallback_category_intro(category)

    assert "Unity" in intro
    assert "营收下降" in intro
    assert "本组覆盖" not in intro
    assert "重点看公司层面的经营变化" not in intro


def test_event_dimension_renderer_keeps_one_paragraph_per_event_without_model_synthesis():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="软银投资",
        category_group="人工智能与科技",
        facts=[
            "软银已向OpenAI投入超300亿美元。",
            "软银同意在2026年进一步向OpenAI投资300亿美元。",
        ],
        background=[],
        impact=[],
        contradictions=[],
    )
    category = ReadingCategoryRecord(
        category_group="人工智能与科技",
        intro="",
        topics=[
            ReadingTopicRecord(
                name="AI资本与前沿研究",
                type_records=[record],
                category_group="人工智能与科技",
                events=[ReadingEventRecord(name="软银投资OpenAI", type_records=[record])],
            )
        ],
    )

    markdown = render_reading_topic_brief_markdown([category])

    assert "- 软银已向OpenAI投入超300亿美元；软银同意在2026年进一步向OpenAI投资300亿美元。" in markdown
    assert markdown.count("- 软银已向OpenAI投入超300亿美元；软银同意在2026年进一步向OpenAI投资300亿美元。") == 1
    assert "- 软银同意在2026年进一步向OpenAI投资300亿美元。" not in markdown


def test_event_dimension_renderer_uses_only_model_synthesized_sentence_when_available():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="软银投资",
        category_group="人工智能与科技",
        facts=[
            "软银已向OpenAI投入超300亿美元。",
            "软银同意在2026年进一步向OpenAI投资300亿美元。",
        ],
        background=[],
        impact=[],
        contradictions=[],
    )
    category = ReadingCategoryRecord(
        category_group="人工智能与科技",
        intro="",
        topics=[
            ReadingTopicRecord(
                name="AI资本与前沿研究",
                type_records=[record],
                category_group="人工智能与科技",
                events=[
                    ReadingEventRecord(
                        name="软银投资OpenAI",
                        type_records=[record],
                        synthesized_facts=["软银围绕OpenAI形成超300亿美元既有投入，并计划在2026年继续追加300亿美元投资。"],
                    )
                ],
            )
        ],
    )

    markdown = render_reading_topic_brief_markdown([category])

    assert "- 软银围绕OpenAI形成超300亿美元既有投入，并计划在2026年继续追加300亿美元投资。" in markdown
    assert "- 软银已向OpenAI投入超300亿美元。" not in markdown
    assert "- 软银同意在2026年进一步向OpenAI投资300亿美元。" not in markdown


def test_event_dimension_renderer_does_not_fallback_to_raw_items_after_synthesis_failure():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="软银投资",
        category_group="人工智能与科技",
        facts=[
            "软银已向OpenAI投入超300亿美元。",
            "软银同意在2026年进一步向OpenAI投资300亿美元。",
        ],
        background=[],
        impact=[],
        contradictions=[],
    )
    category = ReadingCategoryRecord(
        category_group="人工智能与科技",
        intro="",
        topics=[
            ReadingTopicRecord(
                name="AI资本与前沿研究",
                type_records=[record],
                category_group="人工智能与科技",
                events=[
                    ReadingEventRecord(
                        name="软银投资OpenAI",
                        type_records=[record],
                        synthesis_attempted=True,
                        synthesis_error="timeout",
                    )
                ],
            )
        ],
    )

    markdown = render_reading_topic_brief_markdown([category])

    assert "#### 事实" not in markdown


def test_reading_topic_html_adds_topic_buttons_and_hides_source_type_names():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="白宫翻修",
            category_group="国际形式",
            facts=["特朗普称白宫翻修前维护不善，并展示新的装饰。"],
            background=["相关报道围绕美国总统任期内白宫改造展开。"],
            impact=["事件引发外界对白宫建筑改造和费用来源的关注。"],
            contradictions=["报道未提供第三方评估支撑其维护不善的说法。"],
            source_refs=[{"title": "特朗普谈白宫翻修", "url": "https://example.com/white-house"}],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="网络管控",
            category_group="国际形式",
            facts=["武汉高校通知学生排查翻墙行为，并要求签署保证书。"],
            background=["报道将该行动与校园网络管理和意识形态要求联系起来。"],
            impact=["学生获取境外学术资源的便利性可能受到影响。"],
            contradictions=["通知截图真实性尚未得到校方公开确认。"],
            source_refs=[{"title": "高校排查翻墙", "url": "https://example.com/vpn"}],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    html = render_reading_topic_brief_html(categories)

    assert '<nav class="topic-nav">' in html
    assert 'href="#topic-' in html
    assert "特朗普称白宫翻修前维护不善" in html
    assert "细分来源" not in html
    assert "白宫翻修、网络管控" not in html


def test_reading_topic_groups_reinfer_category_from_type_and_facts():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="国际形式",
            facts=["特斯拉AI6芯片生产订单可能从三星转移至英特尔。"],
            background=["AI芯片订单调整发生在美国本土制造与代工重组背景下。"],
            impact=["订单转移将影响特斯拉芯片供应链安排。"],
            contradictions=["该AI芯片订单转移是否最终落地仍存在不确定性。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="移民拘留",
            category_group="财经信息",
            facts=["美国上诉法院推翻非法越境者不得保释政策。"],
            background=["美国法院近期持续审查移民拘留与保释政策。"],
            impact=["该裁决可能影响非法越境案件的执法安排。"],
            contradictions=["联邦政府是否继续就移民保释裁决上诉仍存在不确定性。"],
        ),
    ]

    categories = build_reading_topic_groups(records)
    by_name = {item.category_group: item for item in categories}

    assert by_name["人工智能与科技"].topics[0].name == "AI基础设施"
    assert by_name["国际形式"].topics[0].name == "美国治理动态"
    assert "AI6芯片" in by_name["人工智能与科技"].intro
    assert "不得保释政策" in by_name["国际形式"].intro


def test_reading_topic_groups_split_europe_relation_from_us_governance():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="白宫翻修",
            category_group="国际形式",
            facts=["特朗普于2026年5月12日向执法部门领导人称翻修前白宫像烂房子。"],
            background=["特朗普2025年1月重返白宫后启动翻修。"],
            impact=["白宫翻修引发对公共支出与审美取向的讨论。"],
            contradictions=["翻修费用与维护不善说法仍存在争议。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="美欧关系",
            category_group="国际形式",
            facts=["美国在欧洲总驻军约86000人。"],
            background=["德国联邦国防军现有人数约18.3万至18.5万。"],
            impact=["相关讨论牵动北约分工与欧洲安全。"],
            contradictions=["欧洲能否填补美军调整后的安全缺口仍存在争议。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    intl = next(item for item in categories if item.category_group == "国际形式")
    by_topic = {topic.name: topic for topic in intl.topics}

    assert "美国治理动态" in by_topic
    assert "美欧与欧洲安全" in by_topic
    assert by_topic["美欧与欧洲安全"].source_type_names == ["美欧关系"]


def test_reading_topic_groups_route_xi_trump_meeting_to_international_conflict():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="白宫翻修",
            category_group="国际形式",
            facts=["特朗普于2026年5月12日称翻修前白宫像烂房子。"],
            background=["特朗普2025年1月重返白宫后启动翻修。"],
            impact=["白宫翻修引发对公共支出与审美取向的讨论。"],
            contradictions=["翻修费用与维护不善说法仍存在争议。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="习特会",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日至15日在北京与习近平会晤。"],
            background=["中美双方将讨论贸易与伊朗问题。"],
            impact=["此次访华被视为近年重要中美元首外交时刻。"],
            contradictions=["会晤能否形成中美贸易安排仍存在不确定性。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    intl = next(item for item in categories if item.category_group == "国际形式")
    by_topic = {topic.name: topic for topic in intl.topics}

    assert by_topic["美国治理动态"].source_type_names == ["白宫翻修"]
    assert by_topic["中美与国际冲突"].source_type_names == ["习特会"]


def test_reading_topic_groups_keep_state_visit_in_international_when_ai_is_only_sub_issue():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="特朗普访华",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日至15日在北京与习近平会晤，议题包括贸易、台湾和人工智能竞争。"],
            background=["中美双方将讨论出口管制与高端芯片问题。"],
            impact=["外界关注双方是否讨论AI安全规范与军事应用风险。"],
            contradictions=["本次访华能否形成明确中美安排仍存在不确定性。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    by_name = {item.category_group: item for item in categories}

    assert "国际形式" in by_name
    assert by_name["国际形式"].topics[0].name == "中美与国际冲突"
    if "人工智能与科技" in by_name:
        assert all("特朗普访华" not in topic.source_type_names for topic in by_name["人工智能与科技"].topics)


def test_reading_topic_groups_drop_topic_when_background_belongs_to_parent_event():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="人工智能与科技",
            facts=["英伟达正推动向中国销售H200人工智能芯片，但出货尚未启动。"],
            background=["此次访问正值华盛顿与北京试图维持脆弱贸易休战。"],
            impact=["美国企业高管随行旨在争取中国市场机会与监管审批。"],
            contradictions=["部分美国国会议员及科技业界反对英伟达向中国出口H200芯片。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="特朗普访华",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日至15日在北京与习近平会晤，议题包括贸易、台湾和人工智能竞争。"],
            background=["中美双方将讨论出口管制与高端芯片问题。"],
            impact=["外界关注双方是否讨论AI安全规范与军事应用风险。"],
            contradictions=["本次会谈能否达成贸易安排仍存在不确定性。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    by_category = {item.category_group: item for item in categories}

    assert "人工智能与科技" not in by_category
    assert "国际形式" in by_category


def test_reading_topic_groups_keep_topic_when_four_factors_are_self_owned():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="人工智能与科技",
            facts=["英伟达正推动向中国销售H200人工智能芯片，但出货尚未启动。"],
            background=["美国对华高端芯片出口限制持续收紧，H200审批因此成为焦点。"],
            impact=["美国企业高管随行旨在争取中国市场机会与监管审批。"],
            contradictions=["部分美国国会议员及科技业界反对英伟达向中国出口H200芯片。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    tech = next(item for item in categories if item.category_group == "人工智能与科技")

    assert [topic.name for topic in tech.topics] == ["AI基础设施"]


def test_collapse_type_dims_for_type_brief_merges_same_international_event_chain():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="特朗普访华",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日至15日在北京与习近平会晤。"],
            background=["这是美国领导人自2017年以来首次访华。"],
            impact=["此次访问被视为近年最重要的中美元首外交事件之一。"],
            contradictions=["本次会谈能否形成机制性安排仍存在不确定性。"],
            source_refs=[{"title": "访华主线", "url": "https://example.com/visit"}],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="地缘诉求",
            category_group="国际形式",
            facts=["美国议员希望中国在伊朗与乌克兰问题上发挥更积极作用。"],
            background=["相关诉求发生在特朗普访华与中美高层会晤前夕。"],
            impact=["伊朗与俄乌议题可能被推入峰会核心议程。"],
            contradictions=["报道主要呈现美方诉求，未给出中方正式回应。"],
            source_refs=[{"title": "地缘议题", "url": "https://example.com/geopolitics"}],
        ),
        TypeFourDimRecord(
            type_id="type_003",
            name="中美经贸",
            category_group="国际形式",
            facts=["美中团队正在讨论贸易机制与采购安排。"],
            background=["此前双方已在首尔启动经贸磋商。"],
            impact=["贸易机制若落地，可能成为本轮会晤最明确的机制增量。"],
            contradictions=["贸易委员会仍停留在预期阶段。"],
            source_refs=[{"title": "经贸议题", "url": "https://example.com/trade"}],
        ),
    ]

    collapsed = collapse_type_dims_for_type_brief(records, max_topics_per_category=5)

    assert [item.name for item in collapsed] == ["特朗普访华"]
    assert any("伊朗" in value or "乌克兰" in value for value in collapsed[0].facts + collapsed[0].impact)
    assert any("贸易" in value or "机制" in value for value in collapsed[0].facts + collapsed[0].impact)
    assert len(collapsed[0].source_refs) == 3


def test_collapse_type_dims_for_type_brief_prefers_ai_chip_primary_name():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="人工智能与科技",
            facts=["黄仁勋随特朗普访华，AI芯片审批成为随行焦点。"],
            background=["美国对华高端芯片出口限制持续收紧。"],
            impact=["H200等产品准入预期影响半导体板块。"],
            contradictions=["芯片审批能否真正放松仍存在不确定性。"],
            source_refs=[{"title": "芯片议题", "url": "https://example.com/chip"}],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="科技高管随行",
            category_group="人工智能与科技",
            facts=["黄仁勋、马斯克等美国科技高管随特朗普访华。"],
            background=["企业代表团被视为中美科技与市场准入议题的延伸。"],
            impact=["高管阵容强化了AI与半导体是本次访问实质商业议题的判断。"],
            contradictions=["随行企业能否获得明确政策松动仍未确定。"],
            source_refs=[{"title": "高管随行", "url": "https://example.com/delegation"}],
        ),
    ]

    collapsed = collapse_type_dims_for_type_brief(records, max_topics_per_category=5)

    assert [item.name for item in collapsed] == ["AI芯片"]
    assert any("黄仁勋" in value for value in collapsed[0].facts)
    assert all(item.name != "科技高管随行" for item in collapsed)


def test_reading_topic_brief_adds_report_date_and_numbered_titles():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI芯片",
            category_group="人工智能与科技",
            facts=["AI芯片订单出现变化。"],
            background=["美国对华AI芯片限制持续收紧。"],
            impact=["订单变化可能影响芯片供应与审批节奏。"],
            contradictions=["该AI芯片订单调整是否落地仍存在不确定性。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="模型发布",
            category_group="人工智能与科技",
            facts=["模型产品发布节奏加快。"],
            background=["相关产品发布发生在AI模型竞争升温背景下。"],
            impact=["发布节奏加快可能影响企业采购与产品迭代。"],
            contradictions=["新模型商业化效果仍存在不确定性。"],
        ),
    ]
    categories = build_reading_topic_groups(records)

    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-12")
    html = render_reading_topic_brief_html(categories, report_date="2026-05-12")

    assert markdown.startswith("# 国际新闻简报（2026-05-12）")
    assert "## 一、人工智能与科技" in markdown
    assert "### 1. AI基础设施" in markdown
    assert "### 2. 模型与产品应用" in markdown
    assert "<h1>国际新闻简报（2026-05-12）</h1>" in html
    assert '<a href="#category-人工智能与科技">一、人工智能与科技</a>' in html
    assert '<h2 class="category-title" id="category-人工智能与科技">一、人工智能与科技</h2>' in html
    assert '<a href="#topic-人工智能与科技-AI基础设施">1. AI基础设施</a>' in html
    assert '<h3 class="entry-title" id="topic-人工智能与科技-AI基础设施">1. AI基础设施</h3>' in html


def test_reading_topic_brief_renders_summary_narrative_and_visible_four_factors():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="特朗普访华",
            category_group="国际形式",
            facts=[
                "特朗普将于5月14日与习近平举行第七次面对面会晤，议题包括贸易、台湾和伊朗。",
            ],
            background=["这是美国领导人自2017年以来首次访华。"],
            impact=["后续是否形成采购或机制性安排，是此次访问最值得跟踪的业务焦点。"],
            contradictions=["本次访问能否达成贸易协议仍存在不确定性。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="企业高管随行",
            category_group="国际形式",
            facts=["黄仁勋、马斯克等十余位美国企业高管随特朗普访华。"],
            background=[],
            impact=["这说明市场准入和科技审批是本次访问的实质商业议题。"],
            contradictions=[],
        ),
        TypeFourDimRecord(
            type_id="type_003",
            name="贸易委员会",
            category_group="国际形式",
            facts=["美方“贸易委员会”由格里尔于2026年3月在巴黎双边会谈中提出。"],
            background=[],
            impact=["若该机制落地，可能成为本轮会晤最明确的机制性增量。"],
            contradictions=["目前仍停留在预期阶段。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    intl = next(item for item in categories if item.category_group == "国际形式")
    topic = next(item for item in intl.topics if item.name == "中美与国际冲突")

    assert topic.summary
    assert 1 <= len(topic.key_points) <= 3
    assert topic.watchout
    assert intl.intro == ""

    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-12")
    html = render_reading_topic_brief_html(categories, report_date="2026-05-12")

    assert "#### 事实" in markdown
    assert "#### 背景" in markdown
    assert "#### 产生的影响" in markdown
    assert "#### 反面观点 / 数据矛盾点" in markdown
    assert "这是美国领导人自2017年以来首次访华" in markdown
    assert "本次访问能否达成贸易协议仍存在不确定性" in markdown
    assert '<p class="topic-summary">' not in html
    assert 'class="category-intro"' not in html
    assert '<p class="topic-narrative">' in html
    assert '<ul class="topic-points">' not in html
    assert '<ul class="event-list">' not in html
    assert '<div class="event-factors">' not in html
    assert '<section class="factor-section event-dimension-section event-dimension-facts">' in html
    assert '<section class="factor-section event-dimension-section event-dimension-background">' in html
    assert '<section class="factor-section event-dimension-section event-dimension-impact">' in html
    assert '<section class="factor-section event-dimension-section event-dimension-contradictions">' in html
    assert '<h3 class="factor-title">事实</h3>' in html
    assert '<h3 class="factor-title">背景</h3>' in html
    assert '<h3 class="factor-title">产生的影响</h3>' in html
    assert '<h3 class="factor-title">反面观点 / 数据矛盾点</h3>' in html
    assert '<span class="event-dimension-label">' not in html
    assert '<li>特朗普将于5月14日与习近平举行第七次面对面会晤' in html


def test_reading_topic_brief_groups_same_event_into_one_paragraph_and_splits_different_events():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="软银投资",
            category_group="人工智能与科技",
            facts=["软银已向OpenAI投入超300亿美元，并推动下一轮OpenAI融资。"],
            background=["软银创办人孙正义持续积极押注OpenAI。"],
            impact=["对OpenAI的大规模押注使市场更加关注软银的融资压力。"],
            contradictions=["软银相关报道存在投资收益与回报兑现节奏的口径差异。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="软银资本投入",
            category_group="人工智能与科技",
            facts=["软银已同意在2026年进一步向OpenAI投资300亿美元，继续加码AI资本投入。"],
            background=["该投资安排发生在OpenAI估值继续上行的背景下。"],
            impact=["软银其他投资业务出现亏损，整体收益更加依赖OpenAI单一标的。"],
            contradictions=["市场质疑OpenAI投资回报兑现节奏是否足以支撑当前估值。"],
        ),
        TypeFourDimRecord(
            type_id="type_003",
            name="微软OpenAI分成",
            category_group="人工智能与科技",
            facts=["微软在2023年至2025年期间从OpenAI相关业务获得约300亿美元收入。"],
            background=["微软与OpenAI建立战略合作伙伴关系，双方深度绑定。"],
            impact=["OpenAI通过新协议大幅降低未来支出负担，可能加速其独立发展。"],
            contradictions=["微软早期设定的920亿美元AI投资回报目标与当前新协议380亿美元分成上限存在矛盾。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    tech = next(item for item in categories if item.category_group == "人工智能与科技")
    topic = next(item for item in tech.topics if item.name == "AI资本与前沿研究")
    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-14")
    html = render_reading_topic_brief_html(categories, report_date="2026-05-14")

    assert len(topic.events) == 2
    assert topic.events[0].source_type_names == ["软银投资", "软银资本投入"]
    assert topic.events[1].source_type_names == ["微软OpenAI分成"]
    assert "**软银投资**：" not in markdown
    assert "**微软OpenAI分成**：" not in markdown
    assert "- 软银已向OpenAI投入超300亿美元" in markdown
    assert "软银已同意在2026年进一步向OpenAI投资300亿美元" in markdown
    assert "- 微软在2023年至2025年期间从OpenAI相关业务获得约300亿美元收入" in markdown
    assert "- 软银创办人孙正义持续积极押注OpenAI；该投资安排发生在OpenAI估值继续上行的背景下。" in markdown
    assert "- 对OpenAI的大规模押注使市场更加关注软银的融资压力；软银其他投资业务出现亏损，整体收益更加依赖OpenAI单一标的。" in markdown
    assert "- 软银相关报道存在投资收益与回报兑现节奏的口径差异；市场质疑OpenAI投资回报兑现节奏是否足以支撑当前估值。" in markdown
    assert "2026年进一步向OpenAI投资300亿美元" in markdown
    assert "920亿美元AI投资回报目标" in markdown
    assert "#### 事实" in markdown
    assert "#### 背景" in markdown
    assert '<ul class="event-list">' not in html
    assert '<span class="event-dimension-label">' not in html
    assert '<li>软银已向OpenAI投入超300亿美元' in html
    assert "软银已同意在2026年进一步向OpenAI投资300亿美元" in html
    assert '<li>微软在2023年至2025年期间从OpenAI相关业务获得约300亿美元收入' in html
    assert '<li>软银创办人孙正义持续积极押注OpenAI；该投资安排发生在OpenAI估值继续上行的背景下。' in html
    assert '<li>对OpenAI的大规模押注使市场更加关注软银的融资压力；软银其他投资业务出现亏损，整体收益更加依赖OpenAI单一标的。' in html
    assert '<li>软银相关报道存在投资收益与回报兑现节奏的口径差异；市场质疑OpenAI投资回报兑现节奏是否足以支撑当前估值。' in html


def test_reading_topic_brief_splits_mixed_type_record_into_event_paragraphs():
    record = TypeFourDimRecord(
        type_id="type_001",
        name="AI投资回报",
        category_group="人工智能与科技",
        facts=[
            "软银已向OpenAI投入超300亿美元，愿景基金在截至3月的财年录得460亿美元收益。",
            "微软在2023年至2025年期间从OpenAI相关业务获得约300亿美元收入。",
        ],
        background=[
            "软银为加码OpenAI投资，重仓押注。",
            "微软与OpenAI建立战略合作伙伴关系，深度绑定。",
        ],
        impact=[
            "软银其他投资业务出现亏损，整体收益严重依赖OpenAI单一标的。",
            "OpenAI通过新协议大幅降低未来支出负担，可能加速其独立发展。",
        ],
        contradictions=[
            "软银相关报道存在数据矛盾。",
            "微软早期设定的920亿美元AI投资回报目标与当前新协议380亿美元分成上限存在矛盾。",
        ],
    )

    categories = build_reading_topic_groups([record], max_topics_per_category=5)
    tech = next(item for item in categories if item.category_group == "人工智能与科技")
    topic = next(item for item in tech.topics if item.name == "AI资本与前沿研究")
    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-14")

    assert len(topic.events) == 2
    assert "**软银**：" not in markdown
    assert "**微软**：" not in markdown
    assert "- 软银已向OpenAI投入超300亿美元" in markdown
    assert "- 微软在2023年至2025年期间从OpenAI相关业务获得约300亿美元收入" in markdown
    assert "整体收益严重依赖OpenAI单一标的" in markdown
    assert "920亿美元AI投资回报目标" in markdown



def test_reading_topic_brief_filters_generic_missing_contradiction_phrases():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="特朗普访华",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日至15日赴北京与习近平会晤。"],
            background=["中美双方将讨论贸易、台湾和人工智能竞争。"],
            impact=["此次访问被视为近年最重要的中美元首外交时刻之一。"],
            contradictions=[
                "本次访问能否达成贸易协议仍存在不确定性。",
                "一篇报道指出，在成熟商业模式跑出前，行业共识尚未形成；另一篇则未提及任何争议或数据矛盾点。",
            ],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-14")

    assert "未提及任何争议或数据矛盾点" not in markdown
    assert "不确定性" in markdown


def test_reading_topic_groups_drop_topic_when_contradiction_is_bare_missing_placeholder():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI代理安全",
            category_group="人工智能与科技",
            facts=["IDC预测70%的组织将落地复合AI架构。"],
            background=["AI代理跨系统交互使传统安全边界概念模糊。"],
            impact=["网络安全从被动封堵转向前置预测与主动干预。"],
            contradictions=["报道未提及反面观点或数据矛盾点。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)

    assert categories == []


def test_reading_topic_groups_drop_topic_when_contradiction_is_only_placeholder_text():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="企业财报",
            category_group="财经信息",
            facts=["公司披露季度营收同比增长35%。"],
            background=["财报于周一盘后公布。"],
            impact=["营收超预期，但盈利能力仍待验证。"],
            contradictions=["正文未呈现反面观点或数据矛盾点。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)

    assert categories == []


def test_reading_topic_groups_drop_topic_when_contradiction_is_extraction_failure_note():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="AI算力",
            category_group="人工智能与科技",
            facts=["Cerebras与OpenAI达成750MW推理算力合作。"],
            background=["AI基础设施竞争继续围绕推理算力与资本开支展开。"],
            impact=["相关合作推高了市场对AI算力商业化节奏的预期。"],
            contradictions=["正文缺失（算力账单暴涨相关文章）。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)

    assert categories == []


def test_reading_topic_brief_narrative_prefers_summary_over_labeled_clause_chain():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="特朗普访华",
            category_group="国际形式",
            facts=["5月14日上午10时举行中美高层会晤。"],
            background=["这是美国领导人自2017年以来首次访华。"],
            impact=["中美经贸磋商为元首会谈铺垫，贸易停火协议后续走向将受关注。"],
            contradictions=["美中官员未提供会谈摘要。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="地缘议题",
            category_group="国际形式",
            facts=["乌克兰总统泽连斯基希望特朗普访华期间提及结束俄对乌战争问题。"],
            background=["相关表态发生在布加勒斯特9国峰会期间。"],
            impact=["欧洲援助计划最迟于6月初生效。"],
            contradictions=["援助计划具体落实节奏仍待观察。"],
        ),
        TypeFourDimRecord(
            type_id="type_003",
            name="黄仁勋随行",
            category_group="国际形式",
            facts=["市场传闻英伟达行政总裁黄仁勋随特朗普访华，随后被特朗普确认。"],
            background=["英伟达在华业务前景受到出口管制影响。"],
            impact=["市场开始重估Nvidia在华业务预期。"],
            contradictions=["该消息最初仅为市场传闻。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-14")

    assert "**特朗普访华**：" not in markdown
    assert "#### 事实" in markdown
    assert "#### 背景" in markdown
    assert "#### 产生的影响" in markdown
    assert "#### 反面观点 / 数据矛盾点" in markdown
    assert "**地缘议题**：" not in markdown
    assert "**黄仁勋随行**：" not in markdown


def test_reading_topic_groups_drop_surface_detail_record_from_parent_event_chain():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="特朗普访华",
            category_group="国际形式",
            facts=["特朗普将于2026年5月14日在北京与习近平会晤，议题包括贸易、台湾和伊朗问题。"],
            background=["这是美国领导人近年最重要的访华安排之一。"],
            impact=["会晤若形成贸易或机制安排，将直接影响后续市场预期。"],
            contradictions=["此次会晤能否达成具体成果仍存在不确定性。"],
        ),
        TypeFourDimRecord(
            type_id="type_002",
            name="代表团现场细节",
            category_group="国际形式",
            facts=["2026年5月14日，美国国务卿卢比奥随总统特朗普访华登机时身穿灰色运动服。"],
            background=["相关画面出现在代表团登机与启程阶段。"],
            impact=["相关穿着在社交媒体引发讨论。"],
            contradictions=["报道未提供这一细节与政策议题存在直接关联的证据。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)
    intl = next(item for item in categories if item.category_group == "国际形式")
    topic = next(item for item in intl.topics if item.name == "中美与国际冲突")
    markdown = render_reading_topic_brief_markdown(categories, report_date="2026-05-14")

    assert topic.source_type_names == ["特朗普访华"]
    assert "运动服" not in markdown
    assert "社交媒体引发讨论" not in markdown


def test_reading_topic_groups_drop_surface_detail_event_without_meaningful_core():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="代表团现场细节",
            category_group="国际形式",
            facts=["2026年5月14日，美国国务卿卢比奥随总统特朗普访华登机时身穿灰色运动服。"],
            background=["相关画面出现在代表团登机与启程阶段。"],
            impact=["相关穿着在社交媒体引发讨论。"],
            contradictions=["报道未提供这一细节与政策议题存在直接关联的证据。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)

    assert categories == []


def test_reading_topic_groups_drop_surface_detail_event_even_with_complete_four_factors():
    records = [
        TypeFourDimRecord(
            type_id="type_001",
            name="访华途中花絮",
            category_group="国际形式",
            facts=["2026年5月14日，美国国务卿卢比奥随总统特朗普访华登机时身穿灰色运动服。"],
            background=["卢比奥此前曾受中国制裁，这次随团访华因此引发额外关注。"],
            impact=["部分舆论将这一着装解读为带有对抗意味的外交姿态。"],
            contradictions=["报道未提供这一着装与谈判议题或政策安排存在直接关联的证据。"],
        ),
    ]

    categories = build_reading_topic_groups(records, max_topics_per_category=5)

    assert categories == []
