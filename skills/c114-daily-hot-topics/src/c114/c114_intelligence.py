"""C114 第 1/2 步分析与检索清单生成模块。"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

from .llm import MiniMaxChatClient, StructuredLLMError, load_prompt_text, run_parallel_ordered
from .settings import AppPaths

CSV_HEADERS = {
    "统计日期": "report_date",
    "栏目键": "channel_key",
    "栏目名称": "channel_name",
    "栏目链接": "channel_url",
    "栏目文章数": "channel_article_count",
    "栏目热点词": "channel_hot_topics",
    "文章标题": "title",
    "发布时间": "publish_date",
    "关键词": "keywords",
    "摘要": "summary",
    "文章链接": "url",
}

TOPIC_RULES = [
    ("量子技术", ("量子", "量子信息", "量子计算", "量子通信")),
    ("卫星互联网与商业航天", ("卫星", "卫星互联网", "卫星通信", "商业航天", "太空算力", "空间计算")),
    ("低空经济", ("低空", "eVTOL", "低空经济", "飞行汽车")),
    ("AI与算力", ("OpenAI", "Anthropic", "AI", "大模型", "算力", "Token", "Agentic")),
    ("6G与下一代通信", ("6G", "AI-RAN", "通感算智", "RIS", "自智网络")),
    ("运营商与产业政策", ("运营商", "中国移动", "中国联通", "中国电信", "政策", "通知", "工作报告")),
]

SIGNAL_RULES = [
    ("投融资", (r"融资", r"投资", r"押注", r"股权")),
    ("政策", (r"印发", r"通知", r"政策", r"工作报告", r"两部委", r"发文")),
    ("订单/中标", (r"中标", r"订单", r"合同")),
    ("业绩", (r"营收", r"净利润", r"财年", r"业绩", r"财报")),
    ("技术趋势", (r"6G", r"算力", r"量子", r"卫星", r"太空", r"AI-RAN", r"通感算智")),
    ("监管/法律", (r"起诉", r"违宪", r"禁令", r"监管")),
]

ENTITY_STOPWORDS = {
    "AI",
    "6G",
    "运营商",
    "商业航天",
    "卫星互联网",
    "量子信息",
    "太空算力",
}
TITLE_SEGMENT_STOPWORDS = {
    "6G洞见",
    "洞见",
    "行业观察",
    "热点",
}
TITLE_SPLIT_RE = re.compile(r"[|｜：:，,。！？!?\-—（）()]")
ACTION_HINT_RE = re.compile(r"(融资|中标|订单|合同|发布|押注|争夺|赋能|商用|盘点|起诉|禁令|定调|优化|打造|借壳)")
LEADING_CONNECTOR_RE = re.compile(r"^(正迈入|迈入|正迈向|迈向|赋能|将定义|以|从|再迎|打造)")
SKILL_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = SKILL_ROOT / "prompts" / "search-keyword-agent.md"
RUN_DIR_PREFIX = "c114_search_"
RANGE_DIR_PREFIX = "c114_range_"
RUN_DIR_TIME_FORMAT = "%Y%m%d%H%M"
STEP_1_ANALYSIS_PREFIX = "c114_step_1_analysis"
STEP_2_CHECKLIST_PREFIX = "c114_step_2_search_checklist"
STEP_3_RESULTS_PREFIX = "c114_step_3_search_results"
STEP_4_CONTENT_PREFIX = "c114_step_4_content"
STEP_5_CONTENT_ANALYSIS_PREFIX = "c114_step_5_content_analysis"
STEP_6_BRIEF_PREFIX = "c114_step_6_brief"
STEP_7_BRIEF_REVIEW_PREFIX = "c114_step_7_brief_review"
LAYER_ISSUES_PREFIX = "c114_layer_issues"
PROVIDER_STATS_PREFIX = "c114_provider_stats"


@dataclass(frozen=True)
class RawArticleRecord:
    """表示从原始 CSV 中读取的一条文章记录。"""
    report_date: str
    channel_key: str
    channel_name: str
    channel_hot_topics: list[str]
    title: str
    publish_date: str
    keywords: list[str]
    summary: str
    url: str


@dataclass(frozen=True)
class ArticleAnalysis:
    """表示单篇文章在 step 1 的结构化分析结果。"""
    report_date: str
    channel_key: str
    channel_name: str
    title: str
    publish_date: str
    source_keywords: list[str]
    normalized_keywords: list[str]
    entities: list[str]
    signals: list[str]
    topic: str
    core_summary: str
    followup_queries: list[str]
    url: str


@dataclass(frozen=True)
class TopicBrief:
    """表示按主题聚合后的简要概览。"""
    topic: str
    article_count: int
    channels: list[str]
    keywords: list[str]
    entities: list[str]
    signals: list[str]
    summary: str
    followup_queries: list[str]
    titles: list[str]


@dataclass(frozen=True)
class SearchChecklistItem:
    """表示 step 2 中一篇文章对应的一条检索清单项。"""
    report_date: str
    channel_name: str
    title: str
    topic: str
    search_queries: list[str]
    publish_date: str
    url: str


@dataclass(frozen=True)
class SearchChecklistSection:
    """表示 step 2 中按主题分组后的检索清单。"""
    topic: str
    items: list[SearchChecklistItem]


@dataclass(frozen=True)
class AnalysisOutputPaths:
    """表示 step 1/2 输出文件的路径集合。"""
    input_path: Path
    analysis_output: Path
    checklist_output: Path


def build_search_run_dir_name(run_started_at: datetime) -> str:
    """生成单次运行目录名。"""
    return f"{RUN_DIR_PREFIX}{run_started_at.strftime(RUN_DIR_TIME_FORMAT)}"


def build_search_range_dir_name(start_date: date, end_date: date, run_started_at: datetime) -> str:
    """生成区间运行目录名。"""
    return (
        f"{RANGE_DIR_PREFIX}{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}_"
        f"{run_started_at.strftime(RUN_DIR_TIME_FORMAT)}"
    )


def c114_reports_root(reports_dir: Path) -> Path:
    """返回 C114 运行产物的总目录。"""
    return reports_dir / "c114_report"


def build_step_file_name(step_prefix: str, report_date: date, suffix: str) -> str:
    """按统一规则拼接某一步的文件名。"""
    return f"{step_prefix}_{report_date.strftime('%Y%m%d')}.{suffix}"


def step_1_analysis_name(report_date: date) -> str:
    """返回 step 1 分析 CSV 文件名。"""
    return build_step_file_name(STEP_1_ANALYSIS_PREFIX, report_date, "csv")


def step_2_checklist_name(report_date: date) -> str:
    """返回 step 2 检索清单 YAML 文件名。"""
    return build_step_file_name(STEP_2_CHECKLIST_PREFIX, report_date, "yaml")


def step_3_results_name(report_date: date) -> str:
    """返回 step 3 搜索结果 YAML 文件名。"""
    return build_step_file_name(STEP_3_RESULTS_PREFIX, report_date, "yaml")


def step_4_content_name(report_date: date) -> str:
    """返回 step 4 正文抓取 YAML 文件名。"""
    return build_step_file_name(STEP_4_CONTENT_PREFIX, report_date, "yaml")


def step_5_content_analysis_name(report_date: date) -> str:
    """返回 step 5 正文分析 YAML 文件名。"""
    return build_step_file_name(STEP_5_CONTENT_ANALYSIS_PREFIX, report_date, "yaml")


def step_6_brief_name(report_date: date) -> str:
    """返回 step 6 简报 Markdown 文件名。"""
    return build_step_file_name(STEP_6_BRIEF_PREFIX, report_date, "md")


def step_7_brief_review_name(report_date: date) -> str:
    """返回 step 7 审查 YAML 文件名。"""
    return build_step_file_name(STEP_7_BRIEF_REVIEW_PREFIX, report_date, "yaml")


def layer_issues_name(report_date: date) -> str:
    """返回分层问题汇总文件名。"""
    return build_step_file_name(LAYER_ISSUES_PREFIX, report_date, "yaml")


def provider_stats_name(report_date: date) -> str:
    """返回搜索 provider 用量统计文件名。"""
    return build_step_file_name(PROVIDER_STATS_PREFIX, report_date, "md")


def create_search_run_directory(reports_dir: Path, run_started_at: datetime | None = None) -> Path:
    """Create a new single-day run directory without mutating older runs."""

    timestamp = run_started_at or datetime.now()
    base_name = build_search_run_dir_name(timestamp)
    base_dir = c114_reports_root(reports_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def create_search_range_directory(
    reports_dir: Path,
    start_date: date,
    end_date: date,
    run_started_at: datetime | None = None,
) -> Path:
    """Create a top-level range run directory used to hold per-day subdirectories."""

    timestamp = run_started_at or datetime.now()
    base_name = build_search_range_dir_name(start_date, end_date, timestamp)
    base_dir = c114_reports_root(reports_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def find_latest_search_run_directory(reports_dir: Path, report_date: date) -> Path | None:
    """Locate the newest run directory that already contains artifacts for one day."""

    base_dir = c114_reports_root(reports_dir)
    if not base_dir.exists():
        return None
    checklist_name = step_2_checklist_name(report_date)
    candidates = [
        path
        for path in base_dir.iterdir()
        if path.is_dir() and path.name.startswith(RUN_DIR_PREFIX) and (path / checklist_name).exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name)[-1]


def find_latest_c114_step_file(reports_dir: Path, report_date: date, file_name: str) -> Path | None:
    """Find the newest matching step file for one report date."""

    base_dir = c114_reports_root(reports_dir)
    if not base_dir.exists():
        return None
    candidates: list[Path] = []
    for path in base_dir.iterdir():
        if not path.is_dir():
            continue
        if path.name.startswith(RUN_DIR_PREFIX):
            candidate = path / file_name
            if candidate.exists():
                candidates.append(candidate)
            continue
        if path.name.startswith(RANGE_DIR_PREFIX):
            for day_dir in sorted(child for child in path.iterdir() if child.is_dir()):
                candidate = day_dir / file_name
                if candidate.exists():
                    candidates.append(candidate)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.parent))[-1]


def load_daily_articles_from_csv(csv_text: str, report_date: str) -> list[RawArticleRecord]:
    """Load one day's raw article rows from the canonical C114 CSV export."""

    reader = csv.DictReader(StringIO(csv_text))
    rows: list[RawArticleRecord] = []
    seen_urls: set[str] = set()
    seen_c114_titles: set[str] = set()
    for row in reader:
        normalized = {CSV_HEADERS.get(key, key): (value or "").strip() for key, value in row.items()}
        if normalized.get("report_date") != report_date:
            continue
        title = normalized.get("title", "")
        url = normalized.get("url", "")
        if not title or not url:
            continue
        if url in seen_urls:
            continue
        if is_c114_article_url(url) and title in seen_c114_titles:
            continue
        seen_urls.add(url)
        if is_c114_article_url(url):
            seen_c114_titles.add(title)
        rows.append(
            RawArticleRecord(
                report_date=normalized["report_date"],
                channel_key=normalized["channel_key"],
                channel_name=normalized["channel_name"],
                channel_hot_topics=split_pipe_list(normalized.get("channel_hot_topics", "")),
                title=title,
                publish_date=normalized.get("publish_date", ""),
                keywords=split_pipe_list(normalized.get("keywords", "")),
                summary=normalized.get("summary", ""),
                url=url,
            )
        )
    return rows


def is_c114_article_url(url: str) -> bool:
    """判断链接是否属于 C114 主站文章页。"""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host == "www.c114.com.cn" or host.endswith(".c114.com.cn")


def split_pipe_list(value: str) -> list[str]:
    """把竖线分隔文本拆成列表。"""
    return [part.strip() for part in value.split("|") if part.strip()]


def normalize_keywords(keywords: list[str], title: str, summary: str) -> list[str]:
    """Normalize article keywords while backfilling from title and summary when needed."""

    counter: Counter[str] = Counter()
    for keyword in keywords:
        cleaned = keyword.strip()
        if cleaned:
            counter[cleaned] += 3
    for pattern in (r"[A-Za-z][A-Za-z0-9&.+-]{1,30}", r"[\u4e00-\u9fff]{2,12}"):
        for match in re.findall(pattern, f"{title} {summary}"):
            token = match.strip()
            if len(token) < 2:
                continue
            counter[token] += 1
    results: list[str] = []
    for token, _ in counter.most_common(8):
        if token not in results:
            results.append(token)
    return results


def infer_topic(normalized_keywords: list[str], channel_name: str, title: str, summary: str) -> str:
    """根据关键词、标题和摘要推断文章所属主题。"""
    text = " ".join([channel_name, title, summary, *normalized_keywords])
    for topic, patterns in TOPIC_RULES:
        if any(pattern in text for pattern in patterns):
            return topic
    return normalized_keywords[0] if normalized_keywords else channel_name


def extract_entities(normalized_keywords: list[str], title: str, summary: str) -> list[str]:
    """从关键词、标题和摘要中提取主体实体。"""
    entities: list[str] = []
    for token in normalized_keywords:
        if token in ENTITY_STOPWORDS:
            continue
        if re.search(r"[A-Z]", token) or re.search(r"[\u4e00-\u9fff]{2,8}", token):
            entities.append(token)
        if len(entities) >= 5:
            break
    if not entities:
        for token in re.findall(r"[\u4e00-\u9fff]{2,8}", f"{title} {summary}"):
            if token not in ENTITY_STOPWORDS and token not in entities:
                entities.append(token)
            if len(entities) >= 5:
                break
    return entities


def classify_signals(title: str, summary: str) -> list[str]:
    """根据标题和摘要识别行业信号类型。"""
    text = f"{title} {summary}"
    signals: list[str] = []
    for label, patterns in SIGNAL_RULES:
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            signals.append(label)
    return signals or ["信息更新"]


def build_core_summary(topic: str, signals: list[str], entities: list[str], title: str) -> str:
    """生成单篇文章的核心摘要。"""
    entity_text = "、".join(entities[:2]) if entities else "相关主体"
    signal_text = "、".join(signals[:2])
    return f"{topic}方向出现{signal_text}信号，重点涉及{entity_text}；代表事件为《{title}》。"


def build_followup_queries(
    topic: str, entities: list[str], normalized_keywords: list[str], signals: list[str]
) -> list[str]:
    """Build deterministic helper queries used inside step 1 analysis summaries."""

    seeds = entities[:2] + normalized_keywords[:3]
    queries: list[str] = []
    for seed in seeds:
        query = f"{seed} {topic}"
        if query not in queries:
            queries.append(query)
    for signal in signals[:2]:
        query = f"{topic} {signal}"
        if query not in queries:
            queries.append(query)
    return queries[:5]


def analyze_article(article: RawArticleRecord) -> ArticleAnalysis:
    """Build a structured article analysis from one raw record."""

    normalized = normalize_keywords(article.keywords, article.title, article.summary)
    topic = infer_topic(normalized, article.channel_name, article.title, article.summary)
    entities = extract_entities(normalized, article.title, article.summary)
    signals = classify_signals(article.title, article.summary)
    return ArticleAnalysis(
        report_date=article.report_date,
        channel_key=article.channel_key,
        channel_name=article.channel_name,
        title=article.title,
        publish_date=article.publish_date,
        source_keywords=article.keywords,
        normalized_keywords=normalized,
        entities=entities,
        signals=signals,
        topic=topic,
        core_summary=build_core_summary(topic, signals, entities, article.title),
        followup_queries=build_followup_queries(topic, entities, normalized, signals),
        url=article.url,
    )


def build_topic_briefs(analyses: list[ArticleAnalysis]) -> list[TopicBrief]:
    """Group article analyses into topic-level rollups for downstream reporting."""

    grouped: dict[str, list[ArticleAnalysis]] = {}
    for analysis in analyses:
        grouped.setdefault(analysis.topic, []).append(analysis)
    briefs: list[TopicBrief] = []
    for topic, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        keyword_counter: Counter[str] = Counter()
        entity_counter: Counter[str] = Counter()
        signal_counter: Counter[str] = Counter()
        query_order: list[str] = []
        channels: list[str] = []
        titles: list[str] = []
        for item in items:
            keyword_counter.update(item.normalized_keywords)
            entity_counter.update(item.entities)
            signal_counter.update(item.signals)
            titles.append(item.title)
            if item.channel_name not in channels:
                channels.append(item.channel_name)
            for query in item.followup_queries:
                if query not in query_order:
                    query_order.append(query)
        summary = f"{topic}共涉及{len(items)}篇文章，重点信号为{'、'.join(signal for signal, _ in signal_counter.most_common(3))}。"
        briefs.append(
            TopicBrief(
                topic=topic,
                article_count=len(items),
                channels=channels,
                keywords=[keyword for keyword, _ in keyword_counter.most_common(6)],
                entities=[entity for entity, _ in entity_counter.most_common(5)],
                signals=[signal for signal, _ in signal_counter.most_common(4)],
                summary=summary,
                followup_queries=query_order[:6],
                titles=titles[:5],
            )
        )
    return briefs


def save_article_analysis_csv(output_path: Path, analyses: list[ArticleAnalysis]) -> None:
    """把 step 1 分析结果写成 CSV 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "统计日期",
                "栏目",
                "文章标题",
                "发布时间",
                "原始关键词",
                "归一关键词",
                "实体",
                "信号",
                "主题",
                "核心摘要",
                "延伸检索词",
                "文章链接",
            ],
        )
        writer.writeheader()
        for item in analyses:
            writer.writerow(
                {
                    "统计日期": item.report_date,
                    "栏目": item.channel_name,
                    "文章标题": item.title,
                    "发布时间": item.publish_date,
                    "原始关键词": "|".join(item.source_keywords),
                    "归一关键词": "|".join(item.normalized_keywords),
                    "实体": "|".join(item.entities),
                    "信号": "|".join(item.signals),
                    "主题": item.topic,
                    "核心摘要": item.core_summary,
                    "延伸检索词": "|".join(item.followup_queries),
                    "文章链接": item.url,
                }
            )


def analyze_daily_articles(input_path: Path, report_date: str) -> tuple[list[ArticleAnalysis], list[TopicBrief]]:
    """Analyze one day's C114 CSV into per-article insights and grouped topic briefs."""

    csv_text = input_path.read_text(encoding="utf-8")
    articles = load_daily_articles_from_csv(csv_text, report_date)
    analyses = [analyze_article(article) for article in articles]
    briefs = build_topic_briefs(analyses)
    return analyses, briefs


def load_search_agent_prompt(prompt_path: Path = PROMPT_PATH) -> str:
    """Load the step 2 keyword-generation prompt shipped with the skill."""

    return prompt_path.read_text(encoding="utf-8")


def autofill_search_checklist_items(
    items: list[SearchChecklistItem],
    analyses: list[ArticleAnalysis],
    llm_client: MiniMaxChatClient,
    prompt_path: Path = PROMPT_PATH,
) -> list[SearchChecklistItem]:
    """Use the fixed MiniMax model to generate exactly two keywords per article."""

    analysis_index = {analysis.title: analysis for analysis in analyses}
    system_prompt = load_prompt_text(prompt_path)
    def complete_one(item: SearchChecklistItem) -> SearchChecklistItem:
        analysis = analysis_index.get(item.title)
        payload = {
            "report_date": item.report_date,
            "channel": item.channel_name,
            "topic": item.topic,
            "original_title": item.title,
            "original_published_at": item.publish_date,
            "original_url": item.url,
            "core_summary": analysis.core_summary if analysis else "",
            "signals": list(analysis.signals) if analysis else [],
            "entities": list(analysis.entities) if analysis else [],
            "existing_followup_queries": list(analysis.followup_queries) if analysis else [],
        }
        response = llm_client.complete_json(
            system_prompt=system_prompt,
            user_prompt=(
                "请基于下面这篇文章信息生成 2 组搜索关键词。\n"
                "只返回 JSON 对象，格式为 {\"keywords\": [\"词组1\", \"词组2\"]}。\n"
                "两组关键词必须贴近原标题、便于中文搜索召回、且不能完全重复原标题。\n\n"
                f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
            ),
        )
        keywords = _normalize_keyword_response(response, title=item.title)
        return SearchChecklistItem(
            report_date=item.report_date,
            channel_name=item.channel_name,
            title=item.title,
            topic=item.topic,
            search_queries=keywords,
            publish_date=item.publish_date,
            url=item.url,
        )

    return run_parallel_ordered(items, complete_one)


def build_search_checklist_items(analyses: list[ArticleAnalysis]) -> list[SearchChecklistItem]:
    """Convert per-article analyses into the step 2 checklist template."""

    items: list[SearchChecklistItem] = []
    for analysis in analyses:
        items.append(
            SearchChecklistItem(
                report_date=analysis.report_date,
                channel_name=analysis.channel_name,
                title=analysis.title,
                topic=analysis.topic,
                search_queries=[],
                publish_date=analysis.publish_date,
                url=analysis.url,
            )
        )
    return items


def build_title_aligned_queries(analysis: ArticleAnalysis) -> list[str]:
    """根据标题语义生成两条贴近原标题的检索词。"""
    segments = extract_title_segments(analysis.title)
    queries: list[str] = []

    primary = derive_primary_query(analysis.title, segments)
    if primary:
        queries.append(primary)

    secondary = derive_secondary_query(analysis, segments, primary)
    if secondary and secondary not in queries:
        queries.append(secondary)

    if len(queries) < 2:
        fallback = derive_fallback_query(analysis.title, primary)
        if fallback and fallback not in queries:
            queries.append(fallback)

    if len(queries) < 2 and primary:
        queries.append(primary)

    return queries[:2]


def extract_title_segments(title: str) -> list[str]:
    """把标题拆成便于组合检索词的短语片段。"""
    segments: list[str] = []
    for raw_part in TITLE_SPLIT_RE.split(title):
        segment = compact_phrase(raw_part).strip("'\" ")
        if len(segment) < 2 or segment in TITLE_SEGMENT_STOPWORDS:
            continue
        if segment not in segments:
            segments.append(segment)
    return segments


def derive_primary_query(title: str, segments: list[str]) -> str:
    """生成第一条主检索词。"""
    if len(segments) >= 2:
        return compact_phrase(f"{segments[0]} {segments[1]}")
    if segments:
        return compact_phrase(segments[0])
    return compact_phrase(title)


def derive_secondary_query(analysis: ArticleAnalysis, segments: list[str], primary: str) -> str:
    """生成第二条补充检索词。"""
    if len(segments) >= 3:
        leading = normalize_title_detail(segments[1])
        trailing = normalize_title_detail(segments[2])
        if leading and trailing:
            return compact_phrase(f"{leading} {trailing}")
        if leading and leading != primary:
            return leading
    if len(segments) == 2:
        head = compact_phrase(segments[0])
        tail = normalize_title_detail(segments[1])
        if tail and len(tail) >= 4 and tail != primary:
            if len(head) <= 6 or re.search(r"[A-Za-z]", head):
                return tail
            return compact_phrase(f"{head} {tail}")
    return derive_fallback_query(analysis.title, primary)


def derive_fallback_query(title: str, primary: str) -> str:
    """在主副检索词不足时生成兜底检索词。"""
    subject = compact_phrase(title[: ACTION_HINT_RE.search(title).start()]) if ACTION_HINT_RE.search(title) else ""
    action = extract_action_hint(title)
    if subject and action:
        candidate = compact_phrase(f"{subject} {action}")
        if candidate != primary:
            return candidate
    title_compact = compact_phrase(title)
    if title_compact and title_compact != primary:
        return title_compact
    return ""


def extract_action_hint(title: str) -> str:
    """从标题中抽取动作提示词。"""
    match = ACTION_HINT_RE.search(title)
    if not match:
        return ""
    action = match.group(1)
    suffix = title[match.start() : match.start() + 14]
    return compact_phrase(suffix) or action


def normalize_title_detail(segment: str) -> str:
    """清洗标题片段中的连接词与冗余前缀。"""
    cleaned = compact_phrase(segment)
    cleaned = LEADING_CONNECTOR_RE.sub("", cleaned).strip()
    return compact_phrase(cleaned)


def build_search_checklist_sections(items: list[SearchChecklistItem]) -> list[SearchChecklistSection]:
    """按主题对检索清单项分组。"""
    grouped: dict[str, list[SearchChecklistItem]] = {}
    for item in items:
        grouped.setdefault(item.topic, []).append(item)
    return [SearchChecklistSection(topic=topic, items=grouped[topic]) for topic in sorted(grouped)]


def _normalize_keyword_response(payload: object, *, title: str) -> list[str]:
    """校验并规范化模型返回的两组关键词。"""
    if not isinstance(payload, dict):
        raise StructuredLLMError(f"《{title}》的关键词返回不是 JSON 对象。")
    raw_keywords = payload.get("keywords")
    if not isinstance(raw_keywords, list):
        raise StructuredLLMError(f"《{title}》的关键词返回缺少 keywords 列表。")
    normalized = [compact_phrase(str(keyword)) for keyword in raw_keywords if compact_phrase(str(keyword))]
    deduped: list[str] = []
    for keyword in normalized:
        if keyword not in deduped:
            deduped.append(keyword)
    if len(deduped) != 2:
        raise StructuredLLMError(f"《{title}》的关键词数量必须为 2，当前为 {len(deduped)}。")
    return deduped


def compact_phrase(value: str) -> str:
    """压缩短语中的空白和符号，生成适合写入 YAML 的值。"""
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"[|；;]+", " ", text)
    return text[:40].strip()


def compact_search_phrase(seed: str, topic: str) -> str:
    """把检索种子和主题压缩成最终可搜索短语。"""
    cleaned_seed = compact_phrase(seed)
    cleaned_topic = compact_phrase(topic)
    if not cleaned_seed:
        return ""
    if cleaned_topic and cleaned_topic not in cleaned_seed:
        return f"{cleaned_seed} {cleaned_topic}"
    return cleaned_seed


def render_search_checklist_yaml(report_date: str, items: list[SearchChecklistItem]) -> str:
    """Render the step 2 YAML template consumed by the keyword-generation agent."""

    sections = build_search_checklist_sections(items)
    lines = [
        f"report_date: '{report_date}'",
        f"prompt_path: '{PROMPT_PATH}'",
        "instructions: 'keywords 由 skill 内置模型读取 prompt_path 后自动生成；每条固定 2 组，且必须贴近原标题。'",
        "categories:",
    ]
    for section in sections:
        lines.extend(
            [
                f"  - topic: '{escape_yaml_scalar(section.topic)}'",
                "    items:",
            ]
        )
        for item in section.items:
            lines.extend(
                [
                    f"      - original_title: '{escape_yaml_scalar(item.title)}'",
                    f"        channel: '{escape_yaml_scalar(item.channel_name)}'",
                    f"        url: '{escape_yaml_scalar(item.url)}'",
                    f"        original_published_at: '{escape_yaml_scalar(item.publish_date)}'",
                    "        keywords:",
                    "          # 由 skill 内置模型根据标题自动生成两组搜索关键词",
                ]
            )
            for query in item.search_queries:
                lines.append(f"          - '{escape_yaml_scalar(query)}'")
    return "\n".join(lines)


def escape_yaml_scalar(value: str) -> str:
    """转义 YAML 单引号标量中的单引号。"""
    return value.replace("'", "''")


def resolve_analysis_output_paths(
    paths: AppPaths,
    report_date: date,
    input_override: str | None = None,
    analysis_output_override: str | None = None,
    checklist_output_override: str | None = None,
    run_started_at: datetime | None = None,
) -> AnalysisOutputPaths:
    """Resolve canonical input and output paths for the C114 analysis workflow."""

    input_path = (
        (paths.project_root / input_override).resolve()
        if input_override
        else (paths.raw_dir / "c114_hot_topics.csv").resolve()
    )
    run_dir: Path | None = None
    if not analysis_output_override and not checklist_output_override:
        run_dir = create_search_run_directory(paths.reports_dir, run_started_at=run_started_at)
    analysis_output = (
        (paths.project_root / analysis_output_override).resolve()
        if analysis_output_override
        else ((run_dir or paths.processed_dir) / step_1_analysis_name(report_date)).resolve()
    )
    checklist_output = (
        (paths.project_root / checklist_output_override).resolve()
        if checklist_output_override
        else ((run_dir or paths.reports_dir) / step_2_checklist_name(report_date)).resolve()
    )
    return AnalysisOutputPaths(
        input_path=input_path,
        analysis_output=analysis_output,
        checklist_output=checklist_output,
    )


def write_analysis_outputs(
    output_paths: AnalysisOutputPaths,
    report_date: str,
    analyses: list[ArticleAnalysis],
    llm_client: MiniMaxChatClient | None = None,
) -> list[SearchChecklistItem]:
    """Persist the analysis CSV and, when available, the step 2 checklist YAML."""

    save_article_analysis_csv(output_paths.analysis_output, analyses)
    if not analyses:
        if output_paths.checklist_output.exists():
            output_paths.checklist_output.unlink()
        return []

    if llm_client is None:
        raise RuntimeError("未配置 llm.api_key，无法自动生成 step 2 搜索关键词。")
    checklist_items = autofill_search_checklist_items(build_search_checklist_items(analyses), analyses, llm_client)
    output_paths.checklist_output.parent.mkdir(parents=True, exist_ok=True)
    output_paths.checklist_output.write_text(
        render_search_checklist_yaml(report_date, checklist_items),
        encoding="utf-8",
    )
    return checklist_items
