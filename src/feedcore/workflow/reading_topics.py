from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from feedcore.models import TypeFourDimRecord


FIXED_CATEGORY_GROUPS = ["国际形式", "人工智能与科技", "金融市场与宏观", "财经信息"]

_TOPIC_HIGH_SIGNAL_TOKENS = (
    "协议",
    "安排",
    "委员会",
    "机制",
    "会晤",
    "会谈",
    "贸易",
    "关税",
    "采购",
    "审批",
    "准入",
    "订单",
    "芯片",
    "算力",
    "融资",
    "营收",
    "利润",
    "监管",
    "政策",
    "法案",
    "法院",
    "北约",
    "军售",
    "外交",
    "制裁",
    "通胀",
    "利率",
    "高管",
    "市场",
    "支出",
    "费用",
    "预算",
)

_TOPIC_LOW_SIGNAL_TOKENS = (
    "抵达",
    "启程",
    "欢迎",
    "致辞",
    "合影",
    "亮相",
    "晚宴",
    "现场",
    "机场",
    "行程",
    "装饰",
    "礼宾",
    "国宴",
    "接待地点",
    "中南海",
    "人民大会堂",
    "扩大会议",
)

_SURFACE_DETAIL_TOKENS = (
    "身穿",
    "穿着",
    "着装",
    "服装",
    "造型",
    "画面",
    "镜头",
    "合影",
    "礼宾",
    "欢迎",
    "迎接",
    "登机",
    "启程",
    "抵达",
    "抵京",
    "亮相",
    "晚宴",
    "社交媒体",
)

_SOFT_REACTION_TOKENS = (
    "引发讨论",
    "引发关注",
    "热议",
    "舆论",
    "社交媒体",
    "视觉",
    "画面",
)

_TOPIC_UNCERTAINTY_TOKENS = (
    "仍",
    "尚未",
    "可能",
    "预计",
    "若",
    "能否",
    "不确定",
    "预期",
    "观察",
    "待定",
    "停留在预期阶段",
)

_INVALID_FACT_PATTERNS = (
    "正文缺失，无法提取具体事实",
    "无法提取具体事实",
    "正文缺失",
    "事实部分不完整",
)

_INVALID_BACKGROUND_PATTERNS = (
    "相关报道由",
)

_INVALID_IMPACT_PATTERNS = (
    "未提及具体影响",
    "可见影响仍待补充",
    "未明示或支撑任何具体影响",
    "无法评估市场影响",
    "无法评估",
    "未明示直接影响",
)

_INVALID_CONTRADICTION_PATTERNS = (
    "未呈现反面观点或数据矛盾点",
    "未提供反面观点或数据矛盾点",
    "未提及反面观点或数据矛盾点",
    "未提及任何反面观点或数据矛盾点",
    "未提及任何争议或数据矛盾点",
    "未提及任何争议",
    "未提供对立口径或数据冲突",
    "无法进一步整合",
    "正文缺失",
    "标题或正文缺失关键数据",
    "部分报道标题或正文缺失关键数据",
)

_CONTEXT_FRAME_PREFIXES = (
    "此次访问正值",
    "此次会晤正值",
    "这次访问正值",
    "这次会晤正值",
    "此访正值",
)

_FRAGMENTARY_PREFIXES = (
    "其曾",
    "其于",
    "其在",
    "其将",
    "其已",
    "其正",
)

_GENERIC_SHORT_PREFIXES = (
    "报道明确指出",
    "报道指出",
    "报道称",
    "消息称",
)

_PREDICATE_HINT_TOKENS = (
    "旨在",
    "推动",
    "争取",
    "讨论",
    "表明",
    "意味着",
    "引发",
    "带动",
    "涉及",
    "形成",
    "构成",
    "显示",
    "发布",
    "推出",
    "举行",
    "推翻",
    "销售",
    "出口",
    "会晤",
    "会谈",
    "达成",
    "建立",
    "启动",
    "批准",
    "限制",
    "影响",
    "加快",
    "变化",
    "下降",
    "上涨",
    "增长",
    "下调",
    "上调",
    "恢复",
    "关注",
    "反对",
    "支持",
    "审查",
    "计划",
    "通知",
    "要求",
    "表示",
    "重估",
    "提及",
    "生效",
    "确认",
    "试图",
    "存在",
    "不确定",
)

_MEANINGFUL_NUMERIC_HINT_TOKENS = (
    "%",
    "％",
    "亿美元",
    "亿元",
    "万美元",
    "欧元",
    "人民币",
    "万亿",
    "MW",
    "GW",
    "bp",
    "个基点",
    "营收",
    "利润",
    "融资",
    "估值",
    "订单",
    "关税",
    "税率",
    "采购",
    "清单",
    "驻军",
    "人数",
    "援助",
)

_CATEGORY_TOKEN_WEIGHTS: dict[str, tuple[tuple[str, int], ...]] = {
    "人工智能与科技": (
        ("人工智能", 5),
        ("AI", 4),
        ("芯片", 4),
        ("算力", 4),
        ("半导体", 4),
        ("模型", 4),
        ("OpenAI", 4),
        ("英伟达", 4),
        ("NVIDIA", 4),
        ("Copilot", 3),
        ("Agent", 3),
        ("GPU", 4),
        ("数据中心", 4),
        ("机器人", 3),
        ("H100", 4),
        ("H200", 4),
    ),
    "金融市场与宏观": (
        ("加密", 4),
        ("比特币", 4),
        ("以太坊", 4),
        ("稳定币", 4),
        ("ETF", 4),
        ("通胀", 4),
        ("利率", 4),
        ("美联储", 4),
        ("货币政策", 4),
        ("外汇", 3),
        ("期权", 3),
        ("CPI", 4),
        ("PPI", 4),
        ("债券", 3),
    ),
    "国际形式": (
        ("习近平", 5),
        ("习特", 5),
        ("访华", 6),
        ("中美", 5),
        ("外交", 4),
        ("台湾", 4),
        ("伊朗", 4),
        ("战争", 4),
        ("冲突", 4),
        ("俄乌", 4),
        ("白宫", 3),
        ("移民", 3),
        ("非法越境", 3),
        ("法院", 3),
        ("北约", 4),
        ("欧盟", 4),
        ("欧洲", 4),
        ("德国", 3),
        ("法国", 3),
        ("英国", 3),
        ("泽连斯基", 4),
        ("特朗普", 3),
        ("会晤", 3),
        ("峰会", 3),
        ("贝森特", 4),
        ("何立峰", 4),
    ),
    "财经信息": (
        ("财报", 4),
        ("营收", 4),
        ("利润", 4),
        ("收入指引", 4),
        ("融资", 4),
        ("IPO", 4),
        ("公司", 3),
        ("企业", 3),
        ("医药", 4),
        ("医疗", 4),
        ("支付", 3),
        ("并购", 4),
    ),
}

_TOPIC_TOKEN_WEIGHTS: dict[str, dict[str, tuple[tuple[str, int], ...]]] = {
    "人工智能与科技": {
        "AI基础设施": (
            ("芯片", 6),
            ("算力", 6),
            ("硬件", 5),
            ("GPU", 5),
            ("半导体", 5),
            ("能源", 4),
            ("电力", 4),
            ("数据中心", 4),
            ("英伟达", 5),
            ("NVIDIA", 5),
            ("H100", 4),
            ("H200", 4),
        ),
        "模型与产品应用": (
            ("模型", 5),
            ("产品", 4),
            ("应用", 4),
            ("Siri", 4),
            ("Copilot", 4),
            ("Agent", 4),
            ("Token", 3),
            ("Java", 3),
            ("内容", 2),
            ("发布", 3),
            ("搜索", 3),
        ),
        "AI治理与版权": (
            ("治理", 5),
            ("版权", 5),
            ("安全", 4),
            ("监管", 4),
            ("合规", 3),
            ("审查", 3),
        ),
        "机器人与具身智能": (
            ("机器人", 5),
            ("具身", 5),
            ("车载", 4),
            ("自动驾驶", 4),
        ),
        "AI资本与前沿研究": (
            ("融资", 4),
            ("投资", 4),
            ("资本", 4),
            ("前沿", 3),
            ("研究", 3),
            ("创业", 3),
            ("竞争", 2),
        ),
    },
    "金融市场与宏观": {
        "数字资产监管": (
            ("加密", 5),
            ("数字", 3),
            ("稳定币", 5),
            ("ETF", 4),
            ("交易", 4),
            ("比特币", 5),
            ("以太坊", 5),
        ),
        "宏观与货币市场": (
            ("通胀", 5),
            ("利率", 5),
            ("货币", 4),
            ("宏观", 4),
            ("贵金属", 4),
            ("外汇", 4),
            ("期权", 3),
            ("美联储", 5),
        ),
        "金融科技合规": (
            ("支付", 4),
            ("合规", 4),
            ("金融科技", 5),
            ("监管", 4),
        ),
    },
    "国际形式": {
        "美欧与欧洲安全": (
            ("美欧", 6),
            ("欧洲", 5),
            ("欧盟", 5),
            ("北约", 5),
            ("德国", 4),
            ("法国", 4),
            ("英国", 4),
            ("泽连斯基", 4),
            ("乌克兰", 4),
        ),
        "中美与国际冲突": (
            ("习近平", 6),
            ("习特", 6),
            ("访华", 6),
            ("中美", 5),
            ("台湾", 4),
            ("军售", 4),
            ("伊朗", 4),
            ("美伊", 4),
            ("对峙", 4),
            ("冲突", 4),
            ("战争", 4),
            ("外交", 4),
            ("会晤", 4),
            ("峰会", 3),
            ("贝森特", 4),
            ("何立峰", 4),
            ("贸易委员会", 4),
            ("贸易谈判", 4),
        ),
        "美国治理动态": (
            ("白宫", 5),
            ("法院", 4),
            ("移民", 4),
            ("非法越境", 4),
            ("保释", 4),
            ("国会", 4),
            ("共和党", 4),
            ("民主党", 4),
            ("执法", 3),
            ("翻修", 3),
            ("翻墙", 2),
        ),
    },
    "财经信息": {
        "医疗健康产业": (
            ("医药", 5),
            ("医疗", 5),
            ("健康", 4),
        ),
        "企业经营与财报": (
            ("财报", 5),
            ("营收", 5),
            ("利润", 5),
            ("收入指引", 4),
            ("融资", 4),
            ("IPO", 4),
            ("公司", 3),
            ("企业", 3),
            ("并购", 4),
        ),
    },
}


@dataclass(frozen=True)
class ReadingEventRecord:
    name: str
    type_records: list[TypeFourDimRecord]
    summary: str = ""
    source_type_names: list[str] = field(default_factory=list)
    synthesized_facts: list[str] = field(default_factory=list)
    synthesized_background: list[str] = field(default_factory=list)
    synthesized_impact: list[str] = field(default_factory=list)
    synthesized_contradictions: list[str] = field(default_factory=list)
    synthesis_attempted: bool = False
    synthesis_error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.name,
            "summary": self.summary,
            "source_type_names": list(self.source_type_names),
            "synthesized_facts": list(self.synthesized_facts),
            "synthesized_background": list(self.synthesized_background),
            "synthesized_impact": list(self.synthesized_impact),
            "synthesized_contradictions": list(self.synthesized_contradictions),
            "synthesis_attempted": self.synthesis_attempted,
            "synthesis_error": self.synthesis_error,
            "type_ids": [item.type_id for item in self.type_records],
        }


@dataclass(frozen=True)
class ReadingTopicRecord:
    name: str
    type_records: list[TypeFourDimRecord]
    category_group: str = ""
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    watchout: str = ""
    source_type_names: list[str] = field(default_factory=list)
    events: list[ReadingEventRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "reading_topic": self.name,
            "summary": self.summary,
            "key_points": list(self.key_points),
            "watchout": self.watchout,
            "source_type_names": list(self.source_type_names),
            "events": [event.to_dict() for event in self.events],
            "type_ids": [item.type_id for item in self.type_records],
        }


@dataclass(frozen=True)
class ReadingCategoryRecord:
    category_group: str
    intro: str
    topics: list[ReadingTopicRecord]

    def to_dict(self) -> dict[str, object]:
        return {
            "category_group": self.category_group,
            "intro": self.intro,
            "topics": [topic.to_dict() for topic in self.topics],
        }


def build_reading_topic_groups(
    type_dims: list[TypeFourDimRecord],
    *,
    max_topics_per_category: int = 5,
) -> list[ReadingCategoryRecord]:
    grouped: dict[str, list[TypeFourDimRecord]] = {name: [] for name in FIXED_CATEGORY_GROUPS}
    for item in type_dims:
        group = _reading_category_for_item(item)
        grouped[group].append(item)

    out: list[ReadingCategoryRecord] = []
    for category in FIXED_CATEGORY_GROUPS:
        items = grouped[category]
        if not items:
            continue
        topics = _build_topics_for_category(category, items, max_topics=max_topics_per_category)
        if not topics:
            continue
        placeholder = ReadingCategoryRecord(category_group=category, intro="", topics=topics)
        out.append(
            ReadingCategoryRecord(
                category_group=category,
                intro=fallback_category_intro(placeholder),
                topics=topics,
            )
        )
    return out


def collapse_type_dims_for_type_brief(
    type_dims: list[TypeFourDimRecord],
    *,
    max_topics_per_category: int = 5,
) -> list[TypeFourDimRecord]:
    collapsed: list[TypeFourDimRecord] = []
    for category in build_reading_topic_groups(type_dims, max_topics_per_category=max_topics_per_category):
        for topic in category.topics:
            record = _topic_to_type_brief_record(category.category_group, topic)
            if record is not None:
                collapsed.append(record)
    return collapsed


def fallback_category_intro(category: ReadingCategoryRecord) -> str:
    if len(category.topics) == 1 and _topic_narrative(category.topics[0]):
        return ""
    topic_summaries = [_sentence_core(_topic_summary(topic)) for topic in category.topics if _topic_summary(topic)]
    if topic_summaries:
        intro = "；".join(topic_summaries[:2])
    else:
        facts: list[str] = []
        for topic in category.topics:
            for item in topic.type_records:
                facts.extend(item.facts[:2])
        clean_facts = [_sentence_core(value) for value in facts if _clean_sentence(value)]
        if not clean_facts:
            return "该分类下的文章尚未形成足够清晰的事实线索，建议结合下方子类逐条查看。"
        intro = "；".join(clean_facts[:3])
    if len(intro) > 160:
        intro = intro[:157].rstrip("，；。 ") + "..."
    if intro and intro[-1] not in "。！？.!?":
        intro += "。"
    return intro


def render_reading_topic_brief_markdown(
    categories: list[ReadingCategoryRecord],
    *,
    report_date: str | None = None,
    url_scores: dict[str, int] | None = None,
) -> str:
    lines = [f"# {_brief_title(report_date)}", ""]
    for category_index, category in enumerate(categories, start=1):
        category_label = _numbered_category_title(category_index, category.category_group)
        lines.extend([f"## {category_label}", "", category.intro, ""])
        for topic_index, topic in enumerate(category.topics, start=1):
            topic_label = _numbered_topic_title(topic_index, topic.name)
            lines.extend([f"### {topic_label}", ""])
            narrative = _topic_narrative(topic)
            summary = _topic_summary(topic)
            if summary and not narrative:
                lines.extend([summary, ""])
            if narrative:
                lines.extend([narrative, ""])
            key_points = _topic_key_points(topic)
            if key_points and not narrative:
                lines.extend([*[f"- {value}" for value in key_points], ""])
            event_lines = _event_section_lines(topic)
            if event_lines:
                lines.extend(event_lines)
            source_lines = _topic_source_lines(topic, url_scores=url_scores)
            if source_lines:
                lines.extend(["#### 来源", *source_lines, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_reading_topic_brief_html(
    categories: list[ReadingCategoryRecord],
    *,
    report_date: str | None = None,
    url_scores: dict[str, int] | None = None,
) -> str:
    body: list[str] = [f"<h1>{html.escape(_brief_title(report_date))}</h1>"]
    body.append(
        _render_nav(
            "category-nav",
            [
                (_numbered_category_title(index, item.category_group), _category_anchor(item.category_group))
                for index, item in enumerate(categories, start=1)
            ],
        )
    )
    for category_index, category in enumerate(categories, start=1):
        category_id = _category_anchor(category.category_group)
        category_label = _numbered_category_title(category_index, category.category_group)
        body.append(f'<h2 class="category-title" id="{category_id}">{html.escape(category_label)}</h2>')
        if category.intro:
            body.append(f'<p class="category-intro">{html.escape(category.intro)}</p>')
        body.append(
            _render_nav(
                "topic-nav",
                [
                    (_numbered_topic_title(index, topic.name), _topic_anchor(category.category_group, topic.name))
                    for index, topic in enumerate(category.topics, start=1)
                ],
            )
        )
        for topic_index, topic in enumerate(category.topics, start=1):
            topic_label = _numbered_topic_title(topic_index, topic.name)
            body.append('<article class="brief-entry">')
            body.append(
                f'<h3 class="entry-title" id="{_topic_anchor(category.category_group, topic.name)}">{html.escape(topic_label)}</h3>'
            )
            narrative = _topic_narrative(topic)
            summary = _topic_summary(topic)
            if summary and not narrative:
                body.append(f'<p class="topic-summary">{html.escape(summary)}</p>')
            if narrative:
                body.append(f'<p class="topic-narrative">{html.escape(narrative)}</p>')
            key_points = _topic_key_points(topic)
            if key_points and not narrative:
                body.append('<div class="topic-box"><ul class="topic-points">')
                for value in key_points:
                    body.append(f"<li>{html.escape(value)}</li>")
                body.append("</ul></div>")
            body.extend(_render_event_section(topic))
            source_lines = _topic_source_lines(topic, url_scores=url_scores)
            if source_lines:
                body.append('<section class="factor-section source-section">')
                body.append('<h3 class="source-title">来源</h3>')
                # 来源 >5 条时只默认展示前 5，剩余包在 <details> 里，用户点击展开
                # (纯 HTML，浏览器原生 <details>/<summary>，无 JS 依赖)
                if len(source_lines) <= 5:
                    body.append('<div class="topic-box source-box"><ul>')
                    for line in source_lines:
                        body.append(f"<li>{_render_inline_markdown(line[2:])}</li>")
                    body.append("</ul></div>")
                else:
                    visible = source_lines[:5]
                    hidden = source_lines[5:]
                    body.append('<div class="topic-box source-box">')
                    body.append('<ul>')
                    for line in visible:
                        body.append(f"<li>{_render_inline_markdown(line[2:])}</li>")
                    body.append('</ul>')
                    # CSS column-reverse 让 summary 视觉在底部;[open] 状态切换文字"展开剩余 N 条" ↔ "收起"
                    body.append(f'<details class="source-more"><summary data-count="{len(hidden)}"></summary><ul>')
                    for line in hidden:
                        body.append(f"<li>{_render_inline_markdown(line[2:])}</li>")
                    body.append('</ul></details>')
                    body.append('</div>')
                body.append('</section>')
            body.append("</article>")
    return _html_page(body)


def _build_topics_for_category(
    category: str,
    items: list[TypeFourDimRecord],
    *,
    max_topics: int,
    min_sources_per_topic: int = 10,
) -> list[ReadingTopicRecord]:
    buckets: dict[str, list[TypeFourDimRecord]] = {}
    order: list[str] = []
    for item in items:
        topic = _reading_topic_name(category, item)
        if topic not in buckets:
            order.append(topic)
            buckets[topic] = []
        buckets[topic].append(item)

    while len(order) > max_topics:
        smallest = min(order, key=lambda name: len(buckets[name]))
        fallback = _fallback_topic_name(category)
        if smallest == fallback:
            break
        buckets.setdefault(fallback, []).extend(buckets.pop(smallest))
        order = [name for name in order if name != smallest]
        if fallback not in order:
            order.append(fallback)

    # v12: 每个 topic 必须 ≥ min_sources_per_topic 个 source_refs,
    # 不够的合并到同 category 内 source 最多的 topic,避免出现"机器人 1 篇"这种边缘子主题。
    def _topic_source_count(name: str) -> int:
        return sum(len(rec.source_refs) for rec in buckets.get(name, []))

    while len(order) > 1:
        # 找 source 最少的 topic; 如果它 sources >= min,所有 topic 都达标,退出
        smallest = min(order, key=_topic_source_count)
        if _topic_source_count(smallest) >= min_sources_per_topic:
            break
        largest = max(order, key=_topic_source_count)
        if smallest == largest:
            break
        buckets[largest].extend(buckets.pop(smallest))
        order = [name for name in order if name != smallest]

    out: list[ReadingTopicRecord] = []
    fallback_candidates: list[tuple[str, list[TypeFourDimRecord], list]] = []
    for name in order[:max_topics]:
        records = _topic_effective_records(category, name, buckets[name])
        events = _build_event_records(category, name, records)
        if not records or not events or not _topic_meets_quality_bar(category, name, records):
            # 不通过质量门禁但仍记录为候选,确保 4 大类不会因质量门禁全军覆没
            if records:
                fallback_candidates.append((name, records, events or []))
            continue
        out.append(
            ReadingTopicRecord(
                name=name,
                type_records=records,
                category_group=category,
                summary=_build_topic_summary(records),
                key_points=_build_topic_key_points(records),
                watchout=_build_topic_watchout(records),
                source_type_names=[item.name for item in records],
                events=events,
            )
        )
    # v12: 即使没 topic 通过质量门禁,4 大类至少保留一个 fallback topic
    # 选 records 最多的候选作为 fallback,避免整个大类消失
    if not out and fallback_candidates:
        fallback_candidates.sort(key=lambda t: -len(t[1]))
        name, records, events = fallback_candidates[0]
        out.append(
            ReadingTopicRecord(
                name=name,
                type_records=records,
                category_group=category,
                summary=_build_topic_summary(records),
                key_points=_build_topic_key_points(records),
                watchout=_build_topic_watchout(records),
                source_type_names=[item.name for item in records],
                events=events,
            )
        )
    return out


def _topic_to_type_brief_record(category: str, topic: ReadingTopicRecord) -> TypeFourDimRecord | None:
    if not topic.type_records:
        return None
    primary = _primary_record_for_topic(category, topic)
    return TypeFourDimRecord(
        type_id=primary.type_id,
        name=_clean_sentence(primary.name) or topic.name,
        category_group=category,
        facts=_topic_attr_values(topic, "facts"),
        background=_topic_attr_values(topic, "background"),
        impact=_topic_attr_values(topic, "impact"),
        contradictions=_topic_attr_values(topic, "contradictions"),
        source_links=_merged_source_links(topic.type_records),
        source_refs=_merged_source_refs(topic.type_records),
        error=primary.error,
    )


def _primary_record_for_topic(category: str, topic: ReadingTopicRecord) -> TypeFourDimRecord:
    return max(topic.type_records, key=lambda item: _primary_record_score(category, topic.name, item))


def _primary_record_score(category: str, topic_name: str, record: TypeFourDimRecord) -> int:
    topic_rules = _TOPIC_TOKEN_WEIGHTS.get(category, {}).get(topic_name, ())
    category_rules = _CATEGORY_TOKEN_WEIGHTS.get(category, ())
    factor_count = sum(1 for attr in ("facts", "background", "impact", "contradictions") if getattr(record, attr))
    return (
        _topic_record_score(record) * 2
        + _sentence_rule_score(_clean_sentence(record.name), topic_rules) * 3
        + _sentence_rule_score(_clean_sentence(record.name), category_rules)
        + _primary_record_name_bonus(category, record.name)
        + _primary_record_name_penalty(record.name)
        + factor_count * 2
        + min(len(record.source_refs) + len(record.source_links), 4) * 2
    )


def _primary_record_name_bonus(category: str, name: str) -> int:
    clean = _clean_sentence(name)
    if not clean:
        return 0
    if category == "国际形式" and any(token in clean for token in ("访华", "会晤", "峰会", "特习", "习特", "习近平", "特朗普")):
        return 12
    if category == "人工智能与科技" and any(token in clean for token in ("AI", "芯片", "算力", "半导体", "大模型")):
        return 10
    if category == "金融市场与宏观" and any(token in clean for token in ("通胀", "利率", "汇率", "美联储", "比特币", "加密")):
        return 8
    if category == "财经信息" and any(token in clean for token in ("财报", "营收", "利润", "融资", "并购", "订单")):
        return 8
    return 0


def _primary_record_name_penalty(name: str) -> int:
    clean = _clean_sentence(name)
    if not clean:
        return 0
    generic_tokens = ("随行", "诉求", "行情", "情绪", "反应", "着装", "花絮", "人权")
    return -6 if any(token in clean for token in generic_tokens) else 0


def _merged_source_links(records: list[TypeFourDimRecord]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for record in records:
        for link in record.source_links:
            clean = str(link or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def _merged_source_refs(records: list[TypeFourDimRecord]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        for ref in record.source_refs:
            url = str(ref.get("url", "")).strip()
            key = url or str(ref.get("title", "")).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "title": str(ref.get("title", "")).strip(),
                    "url": url,
                    "source": str(ref.get("source", "")).strip(),
                }
            )
    return out


def _reading_topic_name(category: str, item: TypeFourDimRecord | str) -> str:
    if isinstance(item, TypeFourDimRecord):
        topic_weights = _TOPIC_TOKEN_WEIGHTS.get(category, {})
        if topic_weights:
            best = _best_scored_label(item, topic_weights, fallback=_default_topic_name_for_category(category))
            if best is not None:
                return best
        name = str(item.name or "")
    else:
        name = str(item or "")
    return name[:12] or "其它主题"


def _reading_category_for_item(item: TypeFourDimRecord) -> str:
    scored = _best_scored_label(item, _CATEGORY_TOKEN_WEIGHTS, fallback=item.category_group if item.category_group in FIXED_CATEGORY_GROUPS else "财经信息")
    return scored or (item.category_group if item.category_group in FIXED_CATEGORY_GROUPS else "财经信息")


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(token.casefold() in lowered for token in tokens)


def _best_scored_label(
    item: TypeFourDimRecord,
    options: dict[str, tuple[tuple[str, int], ...]],
    *,
    fallback: str | None = None,
) -> str | None:
    best_name = fallback
    best_score = -10**9
    for name, rules in options.items():
        score = _keyword_weight_score(item, rules)
        if item.category_group == name:
            score += 1
        if score > best_score:
            best_name = name
            best_score = score
    if best_score <= 0:
        return fallback
    return best_name


def _keyword_weight_score(item: TypeFourDimRecord, rules: tuple[tuple[str, int], ...]) -> int:
    name = _clean_sentence(item.name)
    blob = " ".join(
        [
            name,
            *_merge_values([item], "facts", limit=6),
            *_merge_values([item], "background", limit=4),
            *_merge_values([item], "impact", limit=4),
            *_merge_values([item], "contradictions", limit=2),
        ]
    )
    lowered_name = name.casefold()
    lowered_blob = blob.casefold()
    score = 0
    for token, weight in rules:
        lowered_token = token.casefold()
        if lowered_token in lowered_name:
            score += weight + 1
        elif lowered_token in lowered_blob:
            score += weight
    return score


def _sentence_rule_score(text: str, rules: tuple[tuple[str, int], ...]) -> int:
    lowered = _clean_sentence(text).casefold()
    return sum(weight for token, weight in rules if token.casefold() in lowered)


def _is_context_frame_sentence(text: str) -> bool:
    core = _sentence_core(text)
    if not core:
        return False
    return core.startswith(_CONTEXT_FRAME_PREFIXES)


def _looks_like_fragment_sentence(text: str) -> bool:
    core = _sentence_core(text)
    if not core:
        return True
    if core.startswith(_FRAGMENTARY_PREFIXES):
        return True
    if any(core.startswith(prefix) for prefix in _GENERIC_SHORT_PREFIXES) and len(core) <= 12:
        return True
    if (
        len(core) <= 8
        and not re.search(r"\d", core)
        and not any(token in core for token in _PREDICATE_HINT_TOKENS)
        and not _contains_any(core, _TOPIC_UNCERTAINTY_TOKENS)
    ):
        return True
    if "与" in core and len(core) <= 24 and not any(token in core for token in _PREDICATE_HINT_TOKENS):
        return True
    return False


def _facts_can_stand_alone(values: list[str]) -> bool:
    return any(not _looks_like_fragment_sentence(value) for value in values)


def _fallback_topic_name(category: str) -> str:
    return {
        "国际形式": "国际综合事件",
        "人工智能与科技": "科技产业综合",
        "金融市场与宏观": "市场综合变化",
        "财经信息": "企业经营综合",
    }.get(category, "综合事件")


def _default_topic_name_for_category(category: str) -> str:
    return {
        "国际形式": "美国治理动态",
        "人工智能与科技": "AI资本与前沿研究",
        "金融市场与宏观": "金融科技合规",
        "财经信息": "企业经营与财报",
    }.get(category, "其它主题")


def _factor_section_lines(title: str, values: list[str]) -> list[str]:
    text = _factor_section_text(values)
    if not text:
        return []
    return [f"#### {title}", text, ""]


def _merge_values(records: list[TypeFourDimRecord], attr: str, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for record in records:
        for value in getattr(record, attr):
            clean = _clean_sentence(value)
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
            if len(out) >= limit:
                return out
    return out


def _topic_summary(topic: ReadingTopicRecord) -> str:
    return getattr(topic, "summary", "") or _build_topic_summary(getattr(topic, "type_records", []))


def _topic_key_points(topic: ReadingTopicRecord) -> list[str]:
    return list(getattr(topic, "key_points", []) or _build_topic_key_points(getattr(topic, "type_records", [])))


def _topic_watchout(topic: ReadingTopicRecord) -> str:
    return getattr(topic, "watchout", "") or _build_topic_watchout(getattr(topic, "type_records", []))


def _topic_narrative(topic: ReadingTopicRecord) -> str:
    key_points = _topic_key_points(topic)
    if len(key_points) <= 1:
        return ""
    return _build_topic_narrative(key_points, summary=_topic_summary(topic))


def _topic_events(topic: ReadingTopicRecord) -> list[ReadingEventRecord]:
    events = list(getattr(topic, "events", []) or [])
    if events:
        return events
    return _build_event_records(
        getattr(topic, "category_group", ""),
        getattr(topic, "name", ""),
        list(getattr(topic, "type_records", []) or []),
    )


def _build_event_records(
    category: str,
    topic_name: str,
    records: list[TypeFourDimRecord],
) -> list[ReadingEventRecord]:
    if not records:
        return []
    ordered = _sort_topic_records(_event_candidate_records(category, topic_name, records))
    terms_by_record = _distinctive_event_terms_by_record(ordered)
    anchors_by_record = _distinctive_anchor_terms_by_record(ordered)
    clusters: list[list[TypeFourDimRecord]] = []
    for record in ordered:
        best_index = -1
        best_score = 0
        for index, cluster in enumerate(clusters):
            score = _same_event_cluster_score(record, cluster, terms_by_record, anchors_by_record)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index >= 0 and best_score >= 5:
            clusters[best_index].append(record)
        else:
            clusters.append([record])

    out: list[ReadingEventRecord] = []
    for cluster in clusters:
        cluster_records = _sort_topic_records(cluster)
        if not _event_meets_quality_bar(category, topic_name, cluster_records):
            continue
        name = _event_name(category, topic_name, cluster_records)
        out.append(
            ReadingEventRecord(
                name=name,
                type_records=cluster_records,
                summary=_build_topic_summary(cluster_records),
                source_type_names=[item.name for item in cluster_records],
            )
        )
    return out


def _event_candidate_records(
    category: str,
    topic_name: str,
    records: list[TypeFourDimRecord],
) -> list[TypeFourDimRecord]:
    out: list[TypeFourDimRecord] = []
    for record in records:
        out.extend(_split_record_into_event_candidates(category, topic_name, record))
    return out


def _split_record_into_event_candidates(
    category: str,
    topic_name: str,
    record: TypeFourDimRecord,
) -> list[TypeFourDimRecord]:
    facts = _event_attr_values([record], "facts")
    background = _event_attr_values([record], "background")
    impact = _event_attr_values([record], "impact")
    contradictions = _event_attr_values([record], "contradictions")
    if len(facts) <= 1 or sum(1 for values in (background, impact, contradictions) if len(values) >= 2) == 0:
        return [record]

    candidates: list[TypeFourDimRecord] = []
    for index, fact in enumerate(facts):
        candidate = TypeFourDimRecord(
            type_id=f"{record.type_id}#event{index + 1}",
            name=_event_name_from_fact(record.name, fact),
            category_group=record.category_group,
            facts=[fact],
            background=_best_matching_event_values(fact, background, limit=1, preferred_index=index),
            impact=_best_matching_event_values(fact, impact, limit=1, preferred_index=index),
            contradictions=_best_matching_event_values(fact, contradictions, limit=1, preferred_index=index),
            source_links=list(record.source_links),
            source_refs=list(record.source_refs),
            error=record.error,
        )
        if _event_meets_quality_bar(category, topic_name, [candidate]):
            candidates.append(candidate)
    return candidates if len(candidates) >= 2 else [record]


def _best_matching_event_values(anchor: str, values: list[str], *, limit: int, preferred_index: int | None = None) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for index, value in enumerate(values):
        clean = _clean_sentence(value)
        if not clean:
            continue
        score = _sentence_event_similarity(anchor, clean)
        if preferred_index is not None and index == preferred_index and score > 0:
            score += 3
        if score > 0:
            scored.append((score, -index, clean))
    scored.sort(reverse=True)
    return [value for score, _index, value in scored[:limit] if score >= 1]


def _sentence_event_similarity(left: str, right: str) -> int:
    if _sentence_near_duplicate(left, right):
        return 8
    left_terms = {term for term in _raw_text_event_terms(left) if _event_term_is_meaningful(term)}
    right_terms = {term for term in _raw_text_event_terms(right) if _event_term_is_meaningful(term)}
    overlap = left_terms & right_terms
    if not overlap:
        return 0
    strong_overlap = [term for term in overlap if len(term) >= 3 or re.search(r"[A-Za-z0-9]", term)]
    return len(overlap) + len(strong_overlap) * 2


def _event_name_from_fact(record_name: str, fact: str) -> str:
    subject = _leading_event_subject(fact)
    if subject:
        return subject
    return _clean_sentence(record_name)


def _leading_event_subject(text: str) -> str:
    clean = re.sub(r"^\d{4}年\d{1,2}月\d{1,2}日[^，。；]*[，；]\s*", "", _sentence_core(text))
    match = re.match(
        r"([A-Za-z0-9.+\-\s\u4e00-\u9fff]{2,24}?)(?:已|在|于|宣布|同意|参与|获得|录得|预测|指出|表示|达成|完成|发布|推出|将|正|正在|通过|出现|下跌|上涨|增长|同比|环比|达)",
        clean,
    )
    if not match:
        return ""
    subject = re.sub(r"[^\w\u4e00-\u9fff.+-]+", "", match.group(1)).strip()
    if not subject or not _event_term_is_meaningful(subject):
        return ""
    return subject[:18]


def _event_meets_quality_bar(category: str, topic_name: str, records: list[TypeFourDimRecord]) -> bool:
    return all(_event_attr_values(records, attr) for attr in ("facts", "background", "impact", "contradictions"))


def _event_name(category: str, topic_name: str, records: list[TypeFourDimRecord]) -> str:
    if not records:
        return topic_name or "事件"
    primary = max(records, key=lambda item: _primary_record_score(category, topic_name, item))
    return _clean_sentence(primary.name) or topic_name or "事件"


def _same_event_cluster_score(
    record: TypeFourDimRecord,
    cluster: list[TypeFourDimRecord],
    terms_by_record: dict[int, set[str]],
    anchors_by_record: dict[int, set[str]],
) -> int:
    score = 0
    record_blob = _event_blob(record)
    record_name = _clean_sentence(record.name)
    record_terms = terms_by_record.get(id(record), set())
    record_anchors = anchors_by_record.get(id(record), set())
    cluster_terms: set[str] = set()
    cluster_anchors: set[str] = set()
    for existing in cluster:
        cluster_terms.update(terms_by_record.get(id(existing), set()))
        cluster_anchors.update(anchors_by_record.get(id(existing), set()))
        score = max(score, _event_name_relation_score(record_name, _clean_sentence(existing.name), record_blob, _event_blob(existing)))
    anchor_overlap = record_anchors & cluster_anchors
    if not anchor_overlap and score < 4:
        return 0
    if anchor_overlap:
        score += 5
    overlap = record_terms & cluster_terms
    strong_overlap = [term for term in overlap if len(term) >= 3 or re.search(r"[A-Za-z0-9]", term)]
    if strong_overlap:
        score += min(len(strong_overlap) * 2, 6)
    if len(overlap) >= 5:
        score += 2
    return score


def _event_name_relation_score(record_name: str, existing_name: str, record_blob: str, existing_blob: str) -> int:
    if not record_name or not existing_name:
        return 0
    if _sentence_near_duplicate(record_name, existing_name):
        return 6
    lcs = _longest_common_cjk_substring(record_name, existing_name)
    if len(lcs) >= 2 and _event_term_is_meaningful(lcs):
        return 5
    record_phrase = _event_name_phrase(record_name)
    existing_phrase = _event_name_phrase(existing_name)
    if existing_phrase and existing_phrase in record_blob:
        return 6 if len(existing_phrase) >= 4 else 4
    if record_phrase and record_phrase in existing_blob:
        return 6 if len(record_phrase) >= 4 else 4
    return 0


def _distinctive_event_terms_by_record(records: list[TypeFourDimRecord]) -> dict[int, set[str]]:
    raw_by_id = {id(record): _raw_event_terms(record) for record in records}
    doc_freq: dict[str, int] = {}
    for terms in raw_by_id.values():
        for term in terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1
    max_freq = max(1, len(records) - 1) if len(records) <= 3 else max(2, len(records) // 2)
    out: dict[int, set[str]] = {}
    for record in records:
        out[id(record)] = {
            term
            for term in raw_by_id[id(record)]
            if doc_freq.get(term, 0) <= max_freq and _event_term_is_meaningful(term)
        }
    return out


def _distinctive_anchor_terms_by_record(records: list[TypeFourDimRecord]) -> dict[int, set[str]]:
    raw_by_id = {id(record): _record_anchor_terms(record) for record in records}
    doc_freq: dict[str, int] = {}
    for terms in raw_by_id.values():
        for term in terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1
    out: dict[int, set[str]] = {}
    for record in records:
        out[id(record)] = {
            term
            for term in raw_by_id[id(record)]
            if not (re.search(r"[a-z0-9]", term) and doc_freq.get(term, 0) > 1)
        }
    return out


def _record_anchor_terms(record: TypeFourDimRecord) -> set[str]:
    anchors: set[str] = set()
    name_phrase = _event_name_phrase(record.name)
    if name_phrase:
        anchors.add(name_phrase.casefold())
        anchors.update(_compact_cjk_ngrams(name_phrase, min_size=2, max_size=4))
    for fact in record.facts:
        subject = _leading_event_subject(fact)
        if subject:
            anchors.add(subject.casefold())
            anchors.update(_compact_cjk_ngrams(subject, min_size=2, max_size=4))

    factor_terms: list[set[str]] = []
    for attr in ("facts", "background", "impact", "contradictions"):
        values = getattr(record, attr, [])
        factor_terms.append({term for value in values for term in _raw_text_event_terms(value) if _event_term_is_meaningful(term)})
    for index, terms in enumerate(factor_terms):
        other_terms = set().union(*(factor_terms[:index] + factor_terms[index + 1 :]))
        anchors.update(term for term in terms & other_terms if len(term) >= 2)
    return {term for term in anchors if _event_term_is_meaningful(term)}


def _compact_cjk_ngrams(text: str, *, min_size: int, max_size: int) -> set[str]:
    compact = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    out: set[str] = set()
    for size in range(min_size, min(max_size, len(compact)) + 1):
        for index in range(len(compact) - size + 1):
            out.add(compact[index : index + size].casefold())
    return out


def _raw_event_terms(record: TypeFourDimRecord) -> set[str]:
    return _raw_text_event_terms(_event_blob(record))


def _raw_text_event_terms(text: str) -> set[str]:
    terms = {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", text) if len(token) >= 2}
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(segment) <= 10:
            terms.add(segment)
        max_n = min(4, len(segment))
        for size in range(2, max_n + 1):
            for index in range(0, len(segment) - size + 1):
                terms.add(segment[index : index + size])
    return terms


def _event_blob(record: TypeFourDimRecord) -> str:
    values = [
        _clean_sentence(record.name),
        *_merge_values([record], "facts", limit=3),
        *_merge_values([record], "background", limit=2),
        *_merge_values([record], "impact", limit=2),
        *_merge_values([record], "contradictions", limit=2),
    ]
    return " ".join(value for value in values if value)


def _event_name_phrase(name: str) -> str:
    clean = re.sub(r"[^\w\u4e00-\u9fff]+", "", _clean_sentence(name))
    if 2 <= len(clean) <= 12 and _event_term_is_meaningful(clean):
        return clean
    return ""


def _longest_common_cjk_substring(left: str, right: str) -> str:
    left_clean = "".join(re.findall(r"[\u4e00-\u9fff]", left))
    right_clean = "".join(re.findall(r"[\u4e00-\u9fff]", right))
    best = ""
    for start in range(len(left_clean)):
        for end in range(start + 2, len(left_clean) + 1):
            candidate = left_clean[start:end]
            if len(candidate) > len(best) and candidate in right_clean:
                best = candidate
    return best


def _event_term_is_meaningful(term: str) -> bool:
    clean = _clean_sentence(term).casefold()
    if len(clean) < 2:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", clean):
        return False
    generic_terms = {
        "ai",
        "人工",
        "智能",
        "人工智能",
        "科技",
        "市场",
        "投资",
        "融资",
        "公司",
        "企业",
        "行业",
        "业务",
        "产品",
        "模型",
        "应用",
        "发布",
        "动态",
        "战略",
        "议题",
        "问题",
        "事件",
        "影响",
        "背景",
        "相关",
        "报道",
        "可能",
        "仍存",
        "不确定",
        "美国",
        "中国",
        "中美",
        "贸易",
        "会晤",
        "会谈",
        "安排",
        "机制",
        "美元",
        "亿美",
        "亿美元",
        "亿元",
        "收益",
        "回报",
        "协议",
        "新协议",
    }
    return clean not in generic_terms


def _event_section_lines(topic: ReadingTopicRecord) -> list[str]:
    events = _topic_events(topic)
    if not events:
        return []
    sections = _event_dimension_sections(events)
    if not sections:
        return []
    lines: list[str] = []
    for title, _slug, items in sections:
        lines.extend([f"#### {title}"])
        for _label, value in items:
            lines.append(f"- {_ensure_sentence(value)}")
        lines.append("")
    return lines


def _render_event_section(topic: ReadingTopicRecord) -> list[str]:
    events = _topic_events(topic)
    if not events:
        return []
    body: list[str] = []
    for title, slug, items in _event_dimension_sections(events):
        body.extend(
            [
                f'<section class="factor-section event-dimension-section event-dimension-{slug}">',
                f'<h3 class="factor-title">{html.escape(title)}</h3>',
                '<div class="factor-box"><ul class="event-dimension-list">',
            ]
        )
        for _label, value in items:
            body.append(f"<li>{html.escape(_ensure_sentence(value))}</li>")
        body.extend(["</ul></div>", "</section>"])
    return body


def _event_markdown_lines(event: ReadingEventRecord) -> list[str]:
    parts = _event_paragraph_parts(event)
    if not parts:
        return []
    label = _clean_sentence(getattr(event, "name", "")) or "事件"
    lines = [f"- {label}"]
    for name, value in parts:
        lines.append(f"  - {_ensure_sentence(f'{name}：{value}')}")
    return lines


def _event_block_html(event: ReadingEventRecord) -> str:
    parts = _event_paragraph_parts(event)
    if not parts:
        return ""
    label = _clean_sentence(getattr(event, "name", "")) or "事件"
    chunks: list[str] = []
    chunks.append(f'<p class="event-name">{html.escape(label)}</p>')
    chunks.append('<div class="event-factors">')
    for name, value in parts:
        slug = _event_factor_slug(name)
        chunks.append(
            f'<p class="event-factor event-factor-{slug}">'
            f'<span class="event-factor-label">{html.escape(name)}：</span>{html.escape(value)}</p>'
        )
    chunks.append("</div>")
    return "".join(chunks)


def _event_factor_slug(name: str) -> str:
    return {
        "事实": "facts",
        "背景": "background",
        "影响": "impact",
        "分歧": "contradictions",
    }.get(name, "other")


_EVENT_DIMENSION_ORDER = (
    ("事实", "事实", "facts"),
    ("背景", "背景", "background"),
    ("影响", "产生的影响", "impact"),
    ("分歧", "反面观点 / 数据矛盾点", "contradictions"),
)


def _event_dimension_sections(events: list[ReadingEventRecord]) -> list[tuple[str, str, list[tuple[str, str]]]]:
    sections: list[tuple[str, str, list[tuple[str, str]]]] = []
    attr_by_part = {
        "事实": "facts",
        "背景": "background",
        "影响": "impact",
        "分歧": "contradictions",
    }
    for part_name, title, slug in _EVENT_DIMENSION_ORDER:
        items: list[tuple[str, str]] = []
        attr = attr_by_part[part_name]
        for event in events:
            label = _clean_sentence(getattr(event, "name", "")) or "事件"
            values = _event_dimension_values(event, attr)
            value = _event_dimension_paragraph(values)
            if value:
                items.append((label, value))
        if items:
            sections.append((title, slug, items))
    return sections


def _event_dimension_paragraph(values: list[str]) -> str:
    parts = [_sentence_core(item) for item in values if _sentence_core(item)]
    if not parts:
        return ""
    return _ensure_sentence("；".join(parts))


def _event_dimension_values(event: ReadingEventRecord, attr: str) -> list[str]:
    synthesized = {
        "facts": event.synthesized_facts,
        "background": event.synthesized_background,
        "impact": event.synthesized_impact,
        "contradictions": event.synthesized_contradictions,
    }.get(attr, [])
    if synthesized:
        return _event_paragraph_values(synthesized, limit=None)
    if event.synthesis_attempted:
        return []
    records = list(getattr(event, "type_records", []) or [])
    values = _event_attr_values(records, attr)
    return _event_paragraph_values(values, limit=None)


def _event_paragraph_parts(event: ReadingEventRecord) -> list[tuple[str, str]]:
    records = list(getattr(event, "type_records", []) or [])
    facts = _event_paragraph_values(_event_attr_values(records, "facts"), limit=2)
    background = _event_paragraph_values(_event_attr_values(records, "background"), limit=2)
    impact = _event_paragraph_values(_event_attr_values(records, "impact"), limit=2)
    contradictions = _event_paragraph_values(_event_attr_values(records, "contradictions"), limit=2)
    parts: list[tuple[str, str]] = []
    if facts:
        parts.append(("事实", "；".join(_sentence_core(value) for value in facts if _sentence_core(value))))
    if background:
        parts.append(("背景", "；".join(_sentence_core(value) for value in background if _sentence_core(value))))
    if impact:
        parts.append(("影响", "；".join(_sentence_core(value) for value in impact if _sentence_core(value))))
    if contradictions:
        parts.append(("分歧", "；".join(_sentence_core(value) for value in contradictions if _sentence_core(value))))
    return [(name, value) for name, value in parts if value]


def _event_attr_values(records: list[TypeFourDimRecord], attr: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for record in records:
        for value in getattr(record, attr, []):
            clean = _clean_sentence(value)
            if not clean or _is_invalid_topic_sentence(clean, attr) or _looks_like_fragment_sentence(clean):
                continue
            if clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def _event_paragraph_values(values: list[str], *, limit: int | None) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = _strip_topic_label(_sentence_core(value))
        if not clean:
            continue
        if any(_sentence_near_duplicate(clean, existing) for existing in out):
            continue
        out.append(clean)
        if limit is not None and len(out) >= limit:
            break
    return out


def _topic_attr_values(topic: ReadingTopicRecord, attr: str) -> list[str]:
    return _topic_filtered_values(
        getattr(topic, "category_group", ""),
        getattr(topic, "name", ""),
        getattr(topic, "type_records", []),
        attr,
    )


def _build_topic_summary(records: list[TypeFourDimRecord]) -> str:
    if not records:
        return ""
    ordered = _sort_topic_records(records)
    lead_impact = _best_record_sentence(ordered, "impact")
    lead_fact = _best_record_sentence(ordered, "facts") or _best_record_sentence(ordered, "background")
    if lead_impact and lead_fact and _should_append_tail(lead_fact, lead_impact, prefer_business=True):
        return _compose_brief_line(lead_fact, lead_impact)
    if lead_impact and _sentence_value_score(lead_impact) >= _sentence_value_score(lead_fact):
        return _ensure_sentence(lead_impact)
    if lead_fact:
        tail = _best_business_tail(
            *[record.impact for record in ordered],
            *[record.background for record in ordered],
            avoid_text=lead_fact,
        )
        if tail and _should_append_tail(lead_fact, tail, prefer_business=True):
            return _compose_brief_line(lead_fact, tail)
        return _ensure_sentence(lead_fact)
    if lead_impact:
        return _ensure_sentence(lead_impact)
    return ""


def _build_topic_key_points(records: list[TypeFourDimRecord]) -> list[str]:
    points: list[str] = []
    for record in _sort_topic_records(records):
        point = _build_record_key_point(record)
        if not point:
            continue
        if any(_sentence_near_duplicate(point, existing) for existing in points):
            continue
        points.append(point)
        if len(points) >= 3:
            break
    return points


def _build_topic_watchout(records: list[TypeFourDimRecord]) -> str:
    watchout = _best_record_sentence(records, "contradictions")
    if watchout:
        return _ensure_sentence(watchout)
    return ""


def _build_topic_narrative(points: list[str], *, summary: str = "") -> str:
    clauses: list[str] = []
    summary_core = _sentence_core(summary)
    if summary_core:
        clauses.append(summary_core)
    for point in points:
        clean = _sentence_core(point)
        if not clean:
            continue
        unlabeled = _strip_topic_label(clean)
        candidate = unlabeled or clean
        if summary and (_sentence_near_duplicate(clean, summary) or _sentence_near_duplicate(candidate, summary)):
            continue
        if any(_sentence_near_duplicate(candidate, existing) for existing in clauses):
            continue
        clauses.append(candidate)
        if len(clauses) >= (2 if summary_core else 2):
            break
    if not clauses:
        return ""
    return _ensure_sentence("；".join(clauses[:2]))


def _topic_meets_quality_bar(category: str, topic_name: str, records: list[TypeFourDimRecord]) -> bool:
    facts = _topic_filtered_values(category, topic_name, records, "facts")
    impact = _topic_filtered_values(category, topic_name, records, "impact")
    background = _topic_filtered_values(category, topic_name, records, "background")
    contradictions = _topic_filtered_values(category, topic_name, records, "contradictions")

    return bool(facts and background and impact and contradictions)


def _topic_effective_records(
    category: str,
    topic_name: str,
    records: list[TypeFourDimRecord],
) -> list[TypeFourDimRecord]:
    out: list[TypeFourDimRecord] = []
    for record in records:
        filtered = _filtered_record_for_topic(category, topic_name, record)
        if filtered is None or not _record_has_meaningful_core(category, topic_name, filtered):
            continue
        out.append(filtered)
    return _sort_topic_records(out)


def _filtered_record_for_topic(
    category: str,
    topic_name: str,
    record: TypeFourDimRecord,
) -> TypeFourDimRecord | None:
    facts = _topic_filtered_values(category, topic_name, [record], "facts")
    background = _topic_filtered_values(category, topic_name, [record], "background")
    impact = _topic_filtered_values(category, topic_name, [record], "impact")
    contradictions = _topic_filtered_values(category, topic_name, [record], "contradictions")
    if not (facts or background or impact or contradictions):
        return None
    return TypeFourDimRecord(
        type_id=record.type_id,
        name=record.name,
        category_group=record.category_group,
        facts=facts,
        background=background,
        impact=impact,
        contradictions=contradictions,
        source_links=list(record.source_links),
        source_refs=list(record.source_refs),
        error=record.error,
    )


def _record_has_meaningful_core(category: str, topic_name: str, record: TypeFourDimRecord) -> bool:
    topic_rules = _TOPIC_TOKEN_WEIGHTS.get(category, {}).get(topic_name, ())
    category_rules = _CATEGORY_TOKEN_WEIGHTS.get(category, ())
    fact_score = _best_event_sentence_score(record.facts, topic_rules, category_rules, attr="facts")
    impact_score = _best_event_sentence_score(record.impact, topic_rules, category_rules, attr="impact")
    background_score = _best_event_sentence_score(record.background, topic_rules, category_rules, attr="background")
    contradiction_score = _best_event_sentence_score(
        record.contradictions,
        topic_rules,
        category_rules,
        attr="contradictions",
    )
    total = fact_score + impact_score + max(background_score, 0) + max(contradiction_score, 0)
    return max(fact_score, impact_score, background_score, contradiction_score) >= 2 and total >= 3


def _topic_filtered_values(
    category: str,
    topic_name: str,
    records: list[TypeFourDimRecord],
    attr: str,
) -> list[str]:
    topic_rules = _TOPIC_TOKEN_WEIGHTS.get(category, {}).get(topic_name, ())
    category_rules = _CATEGORY_TOKEN_WEIGHTS.get(category, ())
    out: list[str] = []
    seen: set[str] = set()
    for record in records:
        name_anchor_score = _sentence_rule_score(_clean_sentence(record.name), topic_rules)
        for value in getattr(record, attr):
            clean = _clean_sentence(value)
            if not clean:
                continue
            if not _should_keep_topic_sentence(clean, attr, topic_rules, category_rules, name_anchor_score):
                continue
            if clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def _best_event_sentence_score(
    values: list[str],
    topic_rules: tuple[tuple[str, int], ...],
    category_rules: tuple[tuple[str, int], ...],
    *,
    attr: str,
) -> int:
    return max(
        (_event_sentence_score(value, topic_rules, category_rules, attr=attr) for value in values if _clean_sentence(value)),
        default=0,
    )


def _event_sentence_score(
    text: str,
    topic_rules: tuple[tuple[str, int], ...],
    category_rules: tuple[tuple[str, int], ...],
    *,
    attr: str,
) -> int:
    clean = _clean_sentence(text)
    if not clean:
        return -10**6

    anchor_score = _sentence_anchor_score(clean, topic_rules, category_rules)
    has_high_signal = _contains_any(clean, _TOPIC_HIGH_SIGNAL_TOKENS)
    has_actionable_signal = _contains_actionable_signal(clean)
    has_meaningful_numeric_signal = _has_meaningful_numeric_signal(clean)
    has_low_signal = _contains_any(clean, _TOPIC_LOW_SIGNAL_TOKENS)
    has_surface_detail = _contains_any(clean, _SURFACE_DETAIL_TOKENS)
    has_soft_reaction = _contains_any(clean, _SOFT_REACTION_TOKENS)

    score = anchor_score
    if has_high_signal:
        score += 3
    if has_actionable_signal:
        score += 2
    if has_meaningful_numeric_signal:
        score += 2
    if attr == "contradictions" and _contains_any(clean, _TOPIC_UNCERTAINTY_TOKENS):
        score += 1
    if has_low_signal:
        score -= 2
    if has_surface_detail:
        score -= 4
    if has_soft_reaction:
        score -= 2
    if attr == "facts" and has_surface_detail and not (has_high_signal or has_actionable_signal or has_meaningful_numeric_signal):
        score -= 2
    if attr == "impact" and has_soft_reaction and not (has_high_signal or has_meaningful_numeric_signal):
        score -= 2
    return score


def _sentence_anchor_score(
    text: str,
    topic_rules: tuple[tuple[str, int], ...],
    category_rules: tuple[tuple[str, int], ...],
) -> int:
    lowered = _clean_sentence(text).casefold()
    matched: set[str] = set()
    for token, _weight in (*topic_rules, *category_rules):
        lowered_token = token.casefold()
        if lowered_token in lowered:
            matched.add(lowered_token)
    return min(len(matched), 3)


def _contains_actionable_signal(text: str) -> bool:
    return any(token in text for token in _PREDICATE_HINT_TOKENS)


def _has_meaningful_numeric_signal(text: str) -> bool:
    clean = _clean_sentence(text)
    if not re.search(r"\d", clean):
        return False
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|％|MW|GW|bp)", clean):
        return True
    return any(token in clean for token in _MEANINGFUL_NUMERIC_HINT_TOKENS)


def _should_keep_topic_sentence(
    text: str,
    attr: str,
    topic_rules: tuple[tuple[str, int], ...],
    category_rules: tuple[tuple[str, int], ...],
    name_anchor_score: int,
) -> bool:
    topic_score = _sentence_rule_score(text, topic_rules)
    category_score = _sentence_rule_score(text, category_rules)
    value_score = _sentence_value_score(text)
    low_signal = _contains_any(text, _TOPIC_LOW_SIGNAL_TOKENS)
    surface_detail = _contains_any(text, _SURFACE_DETAIL_TOKENS)
    fragmentary = _looks_like_fragment_sentence(text)
    context_frame = _is_context_frame_sentence(text)

    if fragmentary:
        return False
    if _is_invalid_topic_sentence(text, attr):
        return False
    if attr == "background" and context_frame and not (name_anchor_score >= 4 and value_score >= 3):
        return False
    if topic_score >= 3 and not (attr == "facts" and low_signal):
        return True
    if attr == "facts":
        if low_signal:
            return not surface_detail and (topic_score + category_score >= 4 or name_anchor_score >= 4) and value_score >= 0
        return not low_signal and topic_score + category_score >= 2 and value_score >= 1
    if attr == "impact":
        return topic_score + category_score >= 2 or (name_anchor_score >= 4 and not low_signal and value_score >= -2)
    if attr == "background":
        return topic_score + category_score >= 2
    if attr == "contradictions":
        return topic_score + category_score >= 2 or (
            name_anchor_score >= 4 and (value_score >= 0 or _contains_any(text, _TOPIC_UNCERTAINTY_TOKENS))
        )
    return False


def _is_invalid_topic_sentence(text: str, attr: str) -> bool:
    clean = _clean_sentence(text)
    if not clean:
        return True
    if attr == "facts":
        if any(pattern in clean for pattern in _INVALID_FACT_PATTERNS):
            return True
        if clean.startswith(("报道发布于", "标题提及")):
            return True
    if attr == "background":
        if any(pattern in clean for pattern in _INVALID_BACKGROUND_PATTERNS):
            return True
    if attr == "impact":
        if any(pattern in clean for pattern in _INVALID_IMPACT_PATTERNS):
            return True
    if attr == "contradictions":
        if any(pattern in clean for pattern in _INVALID_CONTRADICTION_PATTERNS):
            return True
    return False


def _build_record_key_point(record: TypeFourDimRecord) -> str:
    lead = _best_sentence(record.facts) or _best_sentence(record.background) or _best_sentence(record.impact)
    if not lead:
        return ""

    tail = _best_business_tail(record.impact, record.background, record.contradictions, avoid_text=lead)
    line = _compose_brief_line(lead, tail) if tail and _should_append_tail(lead, tail) else _ensure_sentence(lead)

    label = _clean_sentence(record.name)
    if label and label not in line and len(label) <= 18:
        line = _ensure_sentence(f"{label}：{_sentence_core(line)}")
    return line


def _strip_topic_label(text: str) -> str:
    clean = _clean_sentence(text)
    if "：" not in clean:
        return clean
    head, tail = clean.split("：", 1)
    if 1 <= len(head) <= 10 and not re.search(r"\d", head):
        return tail.strip()
    return clean


def _sort_topic_records(records: list[TypeFourDimRecord]) -> list[TypeFourDimRecord]:
    return sorted(records, key=_topic_record_score, reverse=True)


def _topic_record_score(record: TypeFourDimRecord) -> int:
    score = max(
        (
            _sentence_value_score(value)
            for value in [*record.facts, *record.background, *record.impact, *record.contradictions]
            if _clean_sentence(value)
        ),
        default=0,
    )
    score += max((_sentence_value_score(value) + 2 for value in record.impact if _clean_sentence(value)), default=0)
    score += min(len(record.source_refs) + len(record.source_links), 3)
    if record.contradictions:
        score += 1
    return score


def _best_record_sentence(records: list[TypeFourDimRecord], attr: str) -> str:
    return _best_sentence(
        [_clean_sentence(value) for record in records for value in getattr(record, attr, []) if _clean_sentence(value)]
    )


def _best_sentence(values: list[str]) -> str:
    candidates = [_clean_sentence(value) for value in values if _clean_sentence(value)]
    if not candidates:
        return ""
    return max(candidates, key=lambda value: (_sentence_value_score(value), -len(value)))


def _best_business_tail(*value_groups: list[str], avoid_text: str = "") -> str:
    best = ""
    best_score = -10**9
    for group in value_groups:
        for value in group:
            clean = _clean_sentence(value)
            if not clean or _sentence_near_duplicate(clean, avoid_text):
                continue
            score = _sentence_value_score(clean)
            if any(token in clean for token in _TOPIC_UNCERTAINTY_TOKENS):
                score += 1
            candidate = _ensure_sentence(clean)
            if score > best_score or (score == best_score and len(candidate) < len(best)):
                best = candidate
                best_score = score
    return best


def _should_append_tail(head: str, tail: str, *, prefer_business: bool = False) -> bool:
    if not tail or _sentence_near_duplicate(head, tail):
        return False
    head_core = _sentence_core(head)
    tail_core = _sentence_core(tail)
    combined_len = len(head_core) + len(tail_core)
    tail_score = _sentence_value_score(tail)
    head_score = _sentence_value_score(head)
    if combined_len <= 48:
        return True
    if combined_len <= 78 and tail_score >= 5:
        return True
    if prefer_business and combined_len <= 96 and tail_score >= head_score + 1:
        return True
    return False


def _compose_brief_line(head: str, tail: str) -> str:
    head_core = _sentence_core(head)
    tail_core = _sentence_core(tail)
    if not head_core:
        return _ensure_sentence(tail_core)
    if not tail_core or _sentence_near_duplicate(head_core, tail_core):
        return _ensure_sentence(head_core)
    return _ensure_sentence(f"{head_core}；{tail_core}")


def _sentence_value_score(text: str) -> int:
    clean = _clean_sentence(text)
    if not clean:
        return -10**6

    score = 0
    lowered = clean.casefold()
    score += sum(3 for token in _TOPIC_HIGH_SIGNAL_TOKENS if token.casefold() in lowered)
    score -= sum(2 for token in _TOPIC_LOW_SIGNAL_TOKENS if token.casefold() in lowered)
    if re.search(r"\d", clean):
        score += 2
    if len(clean) < 12:
        score -= 2
    elif len(clean) <= 68:
        score += 2
    elif len(clean) <= 110:
        score += 1
    return score


def _sentence_core(text: str) -> str:
    return _clean_sentence(text).rstrip("。！？!?；;")


def _ensure_sentence(text: str) -> str:
    clean = _clean_sentence(text)
    if not clean:
        return ""
    if clean[-1] not in "。！？!?":
        clean += "。"
    return clean


def _sentence_near_duplicate(left: str, right: str) -> bool:
    left_core = _sentence_core(left)
    right_core = _sentence_core(right)
    if not left_core or not right_core:
        return False
    if left_core == right_core:
        return True
    if left_core in right_core or right_core in left_core:
        return True

    left_terms = _sentence_terms(left_core)
    right_terms = _sentence_terms(right_core)
    if not left_terms or not right_terms:
        return False

    overlap = len(left_terms & right_terms)
    union = len(left_terms | right_terms)
    return union > 0 and overlap / union >= 0.72


def _sentence_terms(text: str) -> set[str]:
    terms = {item.casefold() for item in re.findall(r"[A-Za-z0-9]+", text)}
    compact = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text)
    if compact:
        if len(compact) == 1:
            terms.add(compact)
        else:
            for index in range(len(compact) - 1):
                terms.add(compact[index : index + 2])
    return terms


def _topic_source_lines(
    topic: ReadingTopicRecord,
    max_lines: int = 10,
    url_scores: dict[str, int] | None = None,
) -> list[str]:
    # v11 容量控制: 每个 reading topic (H3 子主题) 按 research_score 排序后取 top max_lines。
    # 这是输出层"按打分取最高的"——不增加输入,只让最终 brief 只保留高质量 source。
    # url_scores 从 step4_research_scores.json 加载,key=article url, value=score (0-100)
    candidates: list[tuple[int, str, str]] = []  # (score, url, line)
    seen: set[str] = set()
    for record in topic.type_records:
        for ref in record.source_refs:
            url = str(ref.get("url", "")).strip()
            if not url or url in seen:
                continue
            title = str(ref.get("title", "") or ref.get("source", "") or url).strip()
            if _is_polluted_source_title(title):
                seen.add(url)
                continue
            seen.add(url)
            line = f"- [{_escape_markdown_link_text(title)}]({url})"
            score = (url_scores or {}).get(url, 0)
            candidates.append((score, url, line))
        for url in record.source_links:
            clean_url = str(url or "").strip()
            if clean_url and clean_url not in seen:
                seen.add(clean_url)
                line = f"- {clean_url}"
                score = (url_scores or {}).get(clean_url, 0)
                candidates.append((score, clean_url, line))
    # 按 score 降序排;score 相同保持原顺序 (Python sort 稳定)
    candidates.sort(key=lambda x: x[0], reverse=True)
    if max_lines:
        candidates = candidates[:max_lines]
    return [c[2] for c in candidates]


def _source_markdown_lines(item: TypeFourDimRecord) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for ref in item.source_refs:
        url = str(ref.get("url", "")).strip()
        if not url or url in seen:
            continue
        title = str(ref.get("title", "") or ref.get("source", "") or url).strip()
        if _is_polluted_source_title(title):
            seen.add(url)
            continue
        seen.add(url)
        lines.append(f"- [{_escape_markdown_link_text(title)}]({url})")
    for url in item.source_links:
        clean_url = str(url or "").strip()
        if clean_url and clean_url not in seen:
            seen.add(clean_url)
            lines.append(f"- {clean_url}")
    return lines


def _is_polluted_source_title(title: str) -> bool:
    clean = " ".join(str(title or "").split())
    if not clean:
        return True
    lowered = clean.casefold()
    if lowered.startswith(("http://", "https://")):
        return True
    if "news.google.com/rss/articles" in lowered and ("[" in clean or "(" in clean or ")" in clean):
        return True
    if "excerpt" in lowered and ("[" in clean or "(" in clean):
        return True
    if clean.startswith("[\\[") or clean.startswith("[[") or clean.startswith("!["):
        return True
    if re.search(r"!?\[[^\]]+\]\([^)]*https?://[^)]*\)", clean):
        return True
    if clean.count("http://") + clean.count("https://") > 0:
        return True
    return False


def _escape_markdown_link_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _clean_sentence(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _render_factor_section(title: str, values: list[str]) -> list[str]:
    text = _factor_section_text(values)
    if not text:
        return []
    return [
        '<section class="factor-section">',
        f'<h3 class="factor-title">{html.escape(title)}</h3>',
        f'<div class="factor-box"><p class="factor-text">{html.escape(text)}</p></div>',
        "</section>",
    ]


def _factor_section_text(values: list[str]) -> str:
    clauses: list[str] = []
    for value in values:
        clean = _strip_topic_label(_sentence_core(value))
        if not clean:
            continue
        if any(_sentence_near_duplicate(clean, existing) for existing in clauses):
            continue
        clauses.append(clean)
        if len(clauses) >= 3:
            break
    if not clauses:
        return ""
    return _ensure_sentence("；".join(clauses))


def _render_nav(class_name: str, items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    links = [
        f'<a href="#{html.escape(anchor, quote=True)}">{html.escape(label)}</a>'
        for label, anchor in items
    ]
    return f'<nav class="{class_name}">' + "".join(links) + "</nav>"


def _brief_title(report_date: str | None) -> str:
    return f"国际新闻简报（{report_date}）" if report_date else "国际新闻简报"


def _numbered_category_title(index: int, name: str) -> str:
    return f"{_chinese_ordinal(index)}、{name}"


def _numbered_topic_title(index: int, name: str) -> str:
    return f"{index}. {name}"


def _chinese_ordinal(index: int) -> str:
    numerals = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    if 0 <= index < len(numerals):
        return numerals[index]
    return str(index)


def _category_anchor(name: str) -> str:
    return "category-" + _slug(name)


def _topic_anchor(category: str, topic: str) -> str:
    return "topic-" + _slug(f"{category}-{topic}")


def _slug(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value).strip("-") or "section"


def _render_inline_markdown(text: str) -> str:
    pattern = re.compile(r"\[((?:\\.|[^\]])+)\]\((https?://[^)\s]+)\)")
    result: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        result.append(html.escape(text[cursor : match.start()]))
        result.append(
            f'<a href="{html.escape(match.group(2), quote=True)}">{html.escape(_unescape_markdown_link_text(match.group(1)))}</a>'
        )
        cursor = match.end()
    result.append(html.escape(text[cursor:]))
    return "".join(result)


def _unescape_markdown_link_text(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _html_page(body_lines: list[str]) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>国际新闻简报</title>",
            "  <style>",
            "    :root{color-scheme:light;--text:#071832;--muted:#5b677a;--line:#d8dee8;--accent:#8fa0b7;--box:#fbfcfe;--box-line:#dfe5ee;--link:#235a9f;}",
            "    *{box-sizing:border-box} html{scroll-behavior:smooth;}",
            "    body{font-family:Arial,'Microsoft YaHei','PingFang SC',sans-serif;max-width:900px;margin:26px auto 40px;padding:0 18px;line-height:1.8;color:var(--text);background:#fff;font-size:16px;}",
            "    h1{font-size:28px;line-height:1.25;margin:0 0 22px;font-weight:800;}",
            "    .category-nav,.topic-nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 24px;padding:10px 0 14px;border-bottom:1px solid var(--line);}",
            "    .topic-nav{margin-top:-4px;margin-bottom:22px;border-bottom:0;padding-top:0;}",
            "    .category-nav a,.topic-nav a{display:inline-flex;align-items:center;min-height:32px;padding:4px 12px;border:1px solid var(--box-line);border-radius:6px;background:#fff;color:var(--text);font-size:14px;font-weight:700;text-decoration:none;}",
            "    .topic-nav a{font-weight:600;color:var(--link);}",
            "    .category-nav a:hover,.topic-nav a:hover{border-color:var(--accent);}",
            "    .category-title{font-size:24px;line-height:1.3;margin:34px 0 12px;padding:12px 0 10px;border-top:2px solid var(--line);border-bottom:1px solid var(--line);font-weight:800;letter-spacing:0;scroll-margin-top:18px;}",
            "    .category-intro{margin:0 0 14px;color:var(--muted);font-size:15px;}",
            "    .brief-entry{margin:0 0 30px;}",
            "    .entry-title{font-size:22px;line-height:1.35;margin:0 0 18px;padding:0 0 14px;border-bottom:2px solid var(--line);font-weight:800;letter-spacing:0;scroll-margin-top:18px;}",
            "    .topic-summary{margin:0 0 12px;font-size:16px;font-weight:600;color:var(--text);}",
            "    .topic-narrative{margin:0 0 14px;color:var(--text);}",
            "    .topic-box{border:1px solid var(--box-line);border-radius:7px;background:var(--box);padding:14px 18px;}",
            "    .topic-points{margin:0;padding-left:20px;list-style:disc;}",
            "    .topic-points li{margin:0;color:var(--text);}",
            "    .topic-points li+li{margin-top:8px;}",
            "    .topic-watchout{margin:12px 0 0;color:var(--muted);font-size:15px;}",
            "    .event-section{margin:18px 0;}",
            "    .event-title{font-size:18px;line-height:1.35;margin:0 0 10px;padding-left:12px;border-left:3px solid var(--accent);font-weight:800;letter-spacing:0;}",
            "    .event-box{border:1px solid var(--box-line);border-radius:7px;background:var(--box);padding:14px 18px;}",
            "    .event-list{margin:0;padding-left:20px;list-style:disc;}",
            "    .event-item{margin:0;color:var(--text);}",
            "    .event-item+.event-item{margin-top:14px;}",
            "    .event-name{margin:0 0 7px;font-weight:800;color:var(--text);}",
            "    .event-factors{display:grid;gap:6px;}",
            "    .event-factor{margin:0;color:var(--text);}",
            "    .event-factor-label{font-weight:800;color:var(--text);}",
            "    .event-dimension-list{margin:0;padding-left:20px;list-style:disc;}",
            "    .event-dimension-list li{margin:0;color:var(--text);}",
            "    .event-dimension-list li+li{margin-top:8px;}",
            "    .factor-section{margin:18px 0;}",
            "    .factor-title{font-size:18px;line-height:1.35;margin:0 0 10px;padding-left:12px;border-left:3px solid var(--accent);font-weight:800;letter-spacing:0;}",
            "    .factor-box{border:1px solid var(--box-line);border-radius:7px;background:var(--box);padding:14px 18px;}",
            "    .factor-text{margin:0;color:var(--text);}",
            "    .source-title{font-size:16px;line-height:1.35;margin:0 0 10px;font-weight:800;color:var(--text);letter-spacing:0;}",
            "    .source-box ul{margin:0;padding-left:20px;list-style:disc;}",
            "    .source-box li{margin:3px 0;color:var(--muted);}",
            # Grid 布局:summary 在第 2 行(底部),ul 在第 1 行(顶部)
            # 比 flex-direction:column-reverse 兼容性更好,在 <details> 元素上稳定
            "    .source-more{margin-top:6px;display:grid;grid-template-rows:auto auto;}",
            "    .source-more summary{grid-row:2;cursor:pointer;color:var(--link);font-size:14px;list-style:none;padding:6px 0 4px;user-select:none;border-top:1px dashed var(--box-line);margin-top:8px;}",
            "    .source-more ul{grid-row:1;margin-top:0;margin-bottom:0;padding-left:20px;list-style:disc;}",
            "    .source-more summary::-webkit-details-marker{display:none;}",
            # [open] 切换文字:'▸ 展开剩余 N 条' ↔ '▾ 收起' (data-count 在 summary 上,attr() 取得到)
            "    .source-more summary::before{content:'▸ 展开剩余 ' attr(data-count) ' 条';display:inline-block;}",
            "    .source-more[open] summary::before{content:'▾ 收起';}",
            "    .source-more summary:hover{color:var(--text);}",
            "    a{color:var(--link);text-decoration:none;border-bottom:1px solid rgba(35,90,159,.25);} a:hover{border-bottom-color:currentColor;}",
            "    @media (max-width:640px){body{padding:0 14px;font-size:15px}.entry-title{font-size:20px}.topic-summary{font-size:15px}.topic-box{padding:12px 14px}.event-title,.factor-title{font-size:17px}.event-box,.factor-box{padding:12px 14px}.source-title{font-size:15px}}",
            "  </style>",
            "</head>",
            "<body>",
            *body_lines,
            "</body>",
            "</html>",
            "",
        ]
    )
