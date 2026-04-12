"""C114 第 1/2 步分析与检索清单生成模块。"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

from .checkpoint import StepCheckpointStore, checkpoint_path_for_step
from .llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    coerce_json_object_payload,
    complete_json_with_postprocess_retry,
    load_prompt_text,
    run_parallel_ordered,
)
from .settings import AppPaths, resolve_override_path

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
TOPIC_GROUPING_PROMPT_PATH = SKILL_ROOT / "prompts" / "topic-grouping-agent.md"
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
LLM_TRACE_LOG_PREFIX = "c114_llm_trace"
LLM_TRACE_LOG_DIR_NAME = "logs"


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


TOPIC_GROUPING_CHECKPOINT_ENTRY_ID = "batch::all_articles"


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


def llm_trace_log_name(report_date: date) -> str:
    """返回单次运行的 LLM 调用日志文件名。"""
    return build_step_file_name(LLM_TRACE_LOG_PREFIX, report_date, "jsonl")


def llm_trace_log_name_for_step(step_name: str, report_date: date) -> str:
    """返回某一步骤专属的 LLM 调用日志文件名。"""
    normalized_step = str(step_name).strip() or "unknown"
    return f"{LLM_TRACE_LOG_PREFIX}_{normalized_step}_{report_date.strftime('%Y%m%d')}.jsonl"


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


HOME_TOPIC_PREFIX_MAP = {
    "video": "视频",
    "quantum": "量子信息",
    "satellite": "卫星互联网",
    "ai": "Cloud&AI",
    "la": "数智低空",
    "news": "新闻",
    "cloud": "Cloud&AI",
    "5g": "5G",
    "ftth": "光通信",
}


def infer_topic(
    normalized_keywords: list[str],
    channel_name: str,
    title: str,
    summary: str,
    *,
    url: str = "",
) -> str:
    """优先沿用 C114 原始栏目；首页文章则按 URL 一级前缀回落到对应模块。"""

    _ = (normalized_keywords, title, summary)
    normalized_channel = channel_name.strip()
    if normalized_channel and normalized_channel != "首页":
        return normalized_channel
    if normalized_channel == "首页":
        topic = infer_home_topic_from_url(url)
        if topic:
            return topic
    return normalized_channel or "未分类"


def infer_home_topic_from_url(url: str) -> str:
    """把首页文章按 C114 URL 一级前缀回落到对应模块。"""

    marker = "c114.com.cn/"
    if marker not in url:
        return ""
    path_parts = url.split(marker, 1)[1].strip("/").split("/")
    if not path_parts:
        return ""
    return HOME_TOPIC_PREFIX_MAP.get(path_parts[0].lower(), "")


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
    topic = infer_topic(
        normalized,
        article.channel_name,
        article.title,
        article.summary,
        url=article.url,
    )
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
    for topic, items in grouped.items():
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


def load_topic_grouping_prompt(prompt_path: Path = TOPIC_GROUPING_PROMPT_PATH) -> str:
    """读取 step 1.5 文章分类提示词。"""

    return prompt_path.read_text(encoding="utf-8")


def auto_group_analysis_topics(
    analyses: list[ArticleAnalysis],
    llm_client: MiniMaxChatClient | None,
    *,
    report_date: str,
    checkpoint_store: StepCheckpointStore | None = None,
    prompt_path: Path = TOPIC_GROUPING_PROMPT_PATH,
) -> tuple[list[ArticleAnalysis], list[TopicBrief]]:
    """在 step 1 和 step 2 之间批量重分组文章 topic。"""

    if not analyses:
        return [], []
    if llm_client is None:
        return analyses, build_topic_briefs(analyses)

    cached_assignments = _load_topic_grouping_checkpoint_assignments(analyses, checkpoint_store)
    if cached_assignments is None:
        begin_llm_step(llm_client, "step_1_5")
        payload_items = [_build_topic_grouping_payload_item(index, analysis) for index, analysis in enumerate(analyses, start=1)]
        system_prompt = load_prompt_text(prompt_path)
        try:
            cached_assignments = complete_json_with_postprocess_retry(
                llm_client=llm_client,
                system_prompt=system_prompt,
                user_prompt=(
                    "请基于下面同一天的全部文章，完成文章主题分类。\n"
                    "只返回一个 JSON 对象，格式为："
                    "{\"topics\":[{\"topic_id\":\"t1\",\"topic_name\":\"主题名\"}],"
                    "\"items\":[{\"article_id\":\"article-1\",\"topic_id\":\"t1\",\"reason\":\"一句中文说明\"}]}。\n"
                    "不要把“视频”“新闻”“首页”这类来源桶直接当最终主题；它们只能作为参考。\n"
                    "如果某篇文章和其它文章不属于同一议题，可以单独成组。\n\n"
                    f"{json.dumps({'report_date': report_date, 'items': payload_items}, ensure_ascii=False, indent=2)}"
                ),
                normalize_response=lambda response: _normalize_topic_grouping_response(
                    response,
                    article_ids=[item["article_id"] for item in payload_items],
                ),
                response_label="step 1.5 文章分类结果",
            )
        except Exception as error:
            if checkpoint_store is not None:
                status = "postprocess_error" if isinstance(error, StructuredLLMError) else "error"
                checkpoint_store.record_entry(
                    entry_id=TOPIC_GROUPING_CHECKPOINT_ENTRY_ID,
                    status=status,
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context={
                        "article_count": len(analyses),
                        "titles": [analysis.title for analysis in analyses],
                    },
                    error={"message": str(error)},
                )
            raise
        if checkpoint_store is not None:
            checkpoint_store.record_entry(
                entry_id=TOPIC_GROUPING_CHECKPOINT_ENTRY_ID,
                status="success",
                provider=getattr(llm_client, "current_provider_name", "") or "",
                request_context={
                    "article_count": len(analyses),
                    "titles": [analysis.title for analysis in analyses],
                },
                result={"assignments": cached_assignments},
            )

    grouped_analyses = _apply_topic_grouping_assignments(analyses, cached_assignments)
    return grouped_analyses, build_topic_briefs(grouped_analyses)


def load_search_agent_prompt(prompt_path: Path = PROMPT_PATH) -> str:
    """Load the step 2 keyword-generation prompt shipped with the skill."""

    return prompt_path.read_text(encoding="utf-8")


def _build_topic_grouping_payload_item(index: int, analysis: ArticleAnalysis) -> dict[str, object]:
    """构造整批文章分类的单篇输入。"""

    return {
        "article_id": f"article-{index}",
        "title": analysis.title,
        "source_bucket": analysis.topic,
        "channel_name": analysis.channel_name,
        "publish_date": analysis.publish_date,
        "entities": list(analysis.entities),
        "signals": list(analysis.signals),
        "normalized_keywords": list(analysis.normalized_keywords),
        "core_summary": analysis.core_summary,
    }


def _load_topic_grouping_checkpoint_assignments(
    analyses: list[ArticleAnalysis],
    checkpoint_store: StepCheckpointStore | None,
) -> dict[str, dict[str, str]] | None:
    """读取并校验 step 1.5 checkpoint 里的分类结果。"""

    if checkpoint_store is None:
        return None
    result = checkpoint_store.get_result(TOPIC_GROUPING_CHECKPOINT_ENTRY_ID)
    if not isinstance(result, dict):
        return None
    assignments = result.get("assignments")
    if not isinstance(assignments, dict):
        assignments = result if all(isinstance(value, dict) for value in result.values()) else None
    if not isinstance(assignments, dict):
        return None
    expected_ids = {f"article-{index}" for index, _ in enumerate(analyses, start=1)}
    if expected_ids - set(assignments):
        return None
    return {
        str(article_id): {
            "topic_name": str(payload.get("topic_name", "")).strip(),
            "reason": str(payload.get("reason", "")).strip(),
        }
        for article_id, payload in assignments.items()
        if isinstance(payload, dict)
    }


def _normalize_topic_grouping_response(
    payload: object,
    *,
    article_ids: list[str],
) -> dict[str, dict[str, str]]:
    """校验整批文章分类返回，并按 article_id 输出最终 topic_name。"""

    payload = coerce_json_object_payload(payload, "step 1.5 文章分类返回")
    raw_topics = payload.get("topics")
    topic_name_by_id: dict[str, str] = {}
    if isinstance(raw_topics, list):
        for raw_topic in raw_topics:
            if not isinstance(raw_topic, dict):
                continue
            topic_id = str(raw_topic.get("topic_id", "")).strip()
            topic_name = compact_phrase(str(raw_topic.get("topic_name", "")).strip())
            if topic_id and topic_name:
                topic_name_by_id[topic_id] = topic_name

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise StructuredLLMError("step 1.5 文章分类返回缺少 items 列表。")

    assignments: dict[str, dict[str, str]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        article_id = str(raw_item.get("article_id", "")).strip()
        if article_id not in article_ids or article_id in assignments:
            continue
        topic_name = compact_phrase(str(raw_item.get("topic_name", "")).strip())
        if not topic_name:
            topic_id = str(raw_item.get("topic_id", "")).strip()
            topic_name = topic_name_by_id.get(topic_id, "")
        if not topic_name:
            continue
        assignments[article_id] = {
            "topic_name": topic_name,
            "reason": compact_phrase(str(raw_item.get("reason", "")).strip()),
        }

    missing = [article_id for article_id in article_ids if article_id not in assignments]
    if missing:
        raise StructuredLLMError(
            "step 1.5 文章分类返回缺少这些文章的结果："
            + "、".join(missing[:5])
            + (f" 等 {len(missing)} 篇" if len(missing) > 5 else "")
        )
    return assignments


def _apply_topic_grouping_assignments(
    analyses: list[ArticleAnalysis],
    assignments: dict[str, dict[str, str]],
) -> list[ArticleAnalysis]:
    """把 step 1.5 返回的新 topic 回填到逐篇分析结果。"""

    grouped: list[ArticleAnalysis] = []
    for index, analysis in enumerate(analyses, start=1):
        assignment = assignments[f"article-{index}"]
        topic = assignment["topic_name"] or analysis.topic
        grouped.append(
            replace(
                analysis,
                topic=topic,
                core_summary=build_core_summary(topic, analysis.signals, analysis.entities, analysis.title),
                followup_queries=build_followup_queries(
                    topic,
                    analysis.entities,
                    analysis.normalized_keywords,
                    analysis.signals,
                ),
            )
        )
    return grouped


def autofill_search_checklist_items(
    items: list[SearchChecklistItem],
    analyses: list[ArticleAnalysis],
    llm_client: MiniMaxChatClient,
    prompt_path: Path = PROMPT_PATH,
    checkpoint_store: StepCheckpointStore | None = None,
) -> list[SearchChecklistItem]:
    """按 topic 批量生成关键词，再把结果回填到各篇文章。"""

    begin_llm_step(llm_client, "step_2")
    analysis_index = {analysis.title: analysis for analysis in analyses}
    system_prompt = load_prompt_text(prompt_path)
    materialized_items = apply_step2_checkpoint_results(items, checkpoint_store)
    grouped: dict[str, list[tuple[int, SearchChecklistItem]]] = {}
    for index, item in enumerate(materialized_items):
        if len(item.search_queries) >= 2:
            continue
        grouped.setdefault(item.topic, []).append((index, item))

    def complete_one_topic(
        topic_items: tuple[str, list[tuple[int, SearchChecklistItem]]]
    ) -> list[tuple[int, SearchChecklistItem]]:
        topic, ordered_items = topic_items
        if not ordered_items:
            return []
        payload_items = [
            _build_step2_keyword_payload_item(item, analysis_index.get(item.title))
            for _, item in ordered_items
        ]
        try:
            keyword_map, missing_titles = complete_json_with_postprocess_retry(
                llm_client=llm_client,
                system_prompt=system_prompt,
                user_prompt=(
                    "请基于下面同一 topic 下的多篇文章信息，为每篇文章各生成 2 组搜索关键词。\n"
                    "只返回 JSON 对象，格式为 "
                    "{\"items\": [{\"original_title\": \"标题\", \"keywords\": [\"词组1\", \"词组2\"]}]}。\n"
                    "每篇文章都必须返回一项，且 original_title 必须与输入完全一致。\n"
                    "两组关键词必须贴近原标题、便于中文搜索召回、且不能完全重复原标题。\n\n"
                    f"{json.dumps({'topic': topic, 'items': payload_items}, ensure_ascii=False, indent=2)}"
                ),
                normalize_response=lambda response: _extract_topic_keyword_response(
                    response,
                    titles=[item.title for _, item in ordered_items],
                ),
                response_label="step 2 topic 批量关键词结果",
            )
        except Exception as error:
            if checkpoint_store is not None:
                status = "postprocess_error" if isinstance(error, StructuredLLMError) else "error"
                for _, item in ordered_items:
                    checkpoint_store.record_entry(
                        entry_id=build_step2_checkpoint_entry_id(item),
                        status=status,
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        request_context=build_step2_checkpoint_request_context(item),
                        error={"message": str(error)},
                    )
            raise
        completed_items: list[tuple[int, SearchChecklistItem]] = []
        if checkpoint_store is not None:
            for _, item in ordered_items:
                if item.title not in keyword_map:
                    continue
                checkpoint_store.record_entry(
                    entry_id=build_step2_checkpoint_entry_id(item),
                    status="success",
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context=build_step2_checkpoint_request_context(item),
                    result={"keywords": keyword_map[item.title]},
                )
        for index, item in ordered_items:
            queries = keyword_map.get(item.title)
            if queries is None:
                continue
            completed_items.append(
                (
                    index,
                    SearchChecklistItem(
                        report_date=item.report_date,
                        channel_name=item.channel_name,
                        title=item.title,
                        topic=item.topic,
                        search_queries=queries,
                        publish_date=item.publish_date,
                        url=item.url,
                    ),
                )
            )
        missing_pairs = [(index, item) for index, item in ordered_items if item.title in missing_titles]
        if not missing_pairs:
            return completed_items

        def complete_missing_item(
            indexed_item: tuple[int, SearchChecklistItem]
        ) -> tuple[int, SearchChecklistItem]:
            index, item = indexed_item
            analysis = analysis_index.get(item.title)
            try:
                keywords = complete_json_with_postprocess_retry(
                    llm_client=llm_client,
                    system_prompt=system_prompt,
                    user_prompt=(
                        "请基于下面这篇文章信息，生成 2 组搜索关键词。\n"
                        "只返回 JSON 对象，格式为 "
                        "{\"keywords\": [\"词组1\", \"词组2\"]}。\n"
                        "关键词必须贴近原标题、便于中文搜索召回、且不能完全重复原标题。\n\n"
                        f"{json.dumps(_build_step2_keyword_payload_item(item, analysis), ensure_ascii=False, indent=2)}"
                    ),
                    normalize_response=lambda response: _normalize_keyword_response(
                        response,
                        title=item.title,
                    ),
                    response_label=f"step 2 单篇关键词结果：{item.title}",
                )
            except Exception as error:
                if checkpoint_store is not None:
                    checkpoint_store.record_entry(
                        entry_id=build_step2_checkpoint_entry_id(item),
                        status="postprocess_error" if isinstance(error, StructuredLLMError) else "error",
                        provider=getattr(llm_client, "current_provider_name", "") or "",
                        request_context=build_step2_checkpoint_request_context(item),
                        error={"message": str(error)},
                    )
                raise
            if checkpoint_store is not None:
                checkpoint_store.record_entry(
                    entry_id=build_step2_checkpoint_entry_id(item),
                    status="success",
                    provider=getattr(llm_client, "current_provider_name", "") or "",
                    request_context=build_step2_checkpoint_request_context(item),
                    result={"keywords": keywords},
                )
            return (
                index,
                SearchChecklistItem(
                    report_date=item.report_date,
                    channel_name=item.channel_name,
                    title=item.title,
                    topic=item.topic,
                    search_queries=keywords,
                    publish_date=item.publish_date,
                    url=item.url,
                ),
            )

        completed_items.extend(run_parallel_ordered(missing_pairs, complete_missing_item))
        return completed_items

    grouped_results = run_parallel_ordered(list(grouped.items()), complete_one_topic) if grouped else []
    completed_items: list[SearchChecklistItem | None] = [item for item in materialized_items]
    for topic_result in grouped_results:
        for index, item in topic_result:
            completed_items[index] = item
    return [item for item in completed_items if item is not None]


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
    return [SearchChecklistSection(topic=topic, items=grouped[topic]) for topic in grouped]


def build_step2_checkpoint_entry_id(item: SearchChecklistItem) -> str:
    """生成 step 2 单篇文章关键词 checkpoint 键。"""

    return f"article::{item.topic}::{item.title}"


def build_step2_checkpoint_request_context(item: SearchChecklistItem) -> dict[str, object]:
    """构造 step 2 单篇文章关键词 checkpoint 请求上下文。"""

    return {
        "topic": item.topic,
        "channel": item.channel_name,
        "original_title": item.title,
        "original_published_at": item.publish_date,
        "original_url": item.url,
    }


def apply_step2_checkpoint_results(
    items: list[SearchChecklistItem],
    checkpoint_store: StepCheckpointStore | None,
) -> list[SearchChecklistItem]:
    """用已成功的 step 2 checkpoint 结果回填关键词。"""

    if checkpoint_store is None:
        return list(items)
    completed_items: list[SearchChecklistItem] = []
    for item in items:
        result = checkpoint_store.get_result(build_step2_checkpoint_entry_id(item))
        keywords = item.search_queries
        if isinstance(result, dict):
            raw_keywords = result.get("keywords")
            if isinstance(raw_keywords, list):
                normalized = [compact_phrase(str(keyword)) for keyword in raw_keywords if compact_phrase(str(keyword))]
                if len(normalized) >= 2:
                    keywords = normalized[:2]
        completed_items.append(
            SearchChecklistItem(
                report_date=item.report_date,
                channel_name=item.channel_name,
                title=item.title,
                topic=item.topic,
                search_queries=keywords,
                publish_date=item.publish_date,
                url=item.url,
            )
        )
    return completed_items


def _normalize_keyword_response(payload: object, *, title: str) -> list[str]:
    """校验并规范化模型返回的两组关键词。"""
    payload = coerce_json_object_payload(payload, f"《{title}》的关键词返回")
    raw_keywords = payload.get("keywords")
    if not isinstance(raw_keywords, list):
        raise StructuredLLMError(f"《{title}》的关键词返回缺少 keywords 列表。")
    normalized = [compact_phrase(str(keyword)) for keyword in raw_keywords if compact_phrase(str(keyword))]
    deduped: list[str] = []
    for keyword in normalized:
        if keyword not in deduped:
            deduped.append(keyword)
    if len(deduped) < 2:
        raise StructuredLLMError(f"《{title}》的关键词数量至少为 2，当前为 {len(deduped)}。")
    return deduped[:2]


def _canonicalize_title_for_match(value: str) -> str:
    """对标题做轻量归一化，兼容引号与分隔符差异。"""
    replacements = str.maketrans(
        {
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "｜": "|",
            "—": "-",
            "–": "-",
            "　": " ",
        }
    )
    text = value.translate(replacements)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([|])\s*", r" \1 ", text)
    return text.strip()


def _extract_topic_keyword_response(payload: object, *, titles: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """解析 topic 批量关键词返回，并返回已匹配结果与缺失标题。"""
    payload = coerce_json_object_payload(payload, "topic 批量关键词返回")
    if "items" not in payload and len(titles) == 1:
        title = titles[0]
        return {title: _normalize_keyword_response(payload, title=title)}, []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise StructuredLLMError("topic 批量关键词返回缺少 items 列表。")
    expected_titles = list(dict.fromkeys(titles))
    expected_by_key: dict[str, list[str]] = {}
    for expected_title in expected_titles:
        expected_by_key.setdefault(_canonicalize_title_for_match(expected_title), []).append(expected_title)
    keyword_map: dict[str, list[str]] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        raw_title = raw_item.get("original_title")
        if not isinstance(raw_title, str):
            continue
        normalized_title = _canonicalize_title_for_match(raw_title.strip())
        matched_title = next(
            (
                candidate
                for candidate in expected_by_key.get(normalized_title, [])
                if candidate not in keyword_map
            ),
            None,
        )
        if matched_title is None:
            continue
        keyword_map[matched_title] = _normalize_keyword_response(raw_item, title=matched_title)
    missing_titles = [title for title in expected_titles if title not in keyword_map]
    return keyword_map, missing_titles


def _normalize_topic_keyword_response(payload: object, *, titles: list[str]) -> dict[str, list[str]]:
    """校验 topic 批量关键词返回，并按标题回填两组关键词。"""
    keyword_map, missing_titles = _extract_topic_keyword_response(payload, titles=titles)
    if missing_titles:
        details = "、".join(f"《{title}》" for title in missing_titles[:5])
        if len(missing_titles) > 5:
            details = f"{details} 等 {len(missing_titles)} 篇"
        raise StructuredLLMError(f"topic 批量关键词返回缺少这些文章的结果：{details}")
    return keyword_map


def compact_phrase(value: str) -> str:
    """压缩短语中的空白和符号，生成适合写入 YAML 的值。"""
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"[|；;]+", " ", text)
    return text[:40].strip()


def _build_step2_keyword_payload_item(
    item: SearchChecklistItem,
    analysis: ArticleAnalysis | None,
) -> dict[str, object]:
    """构造 step 2 关键词生成的单篇输入。"""
    return {
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
        resolve_override_path(paths.project_root, input_override)
        if input_override
        else (paths.raw_dir / "c114_hot_topics.csv").resolve()
    )
    run_dir: Path | None = None
    if not analysis_output_override and not checklist_output_override:
        run_dir = create_search_run_directory(paths.reports_dir, run_started_at=run_started_at)
    analysis_output = (
        resolve_override_path(paths.project_root, analysis_output_override)
        if analysis_output_override
        else ((run_dir or paths.processed_dir) / step_1_analysis_name(report_date)).resolve()
    )
    checklist_output = (
        resolve_override_path(paths.project_root, checklist_output_override)
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
    *,
    auto_fill_keywords: bool = True,
) -> list[SearchChecklistItem]:
    """Persist the analysis CSV and, when available, the step 2 checklist YAML."""

    save_article_analysis_csv(output_paths.analysis_output, analyses)
    if not analyses:
        if output_paths.checklist_output.exists():
            output_paths.checklist_output.unlink()
        return []

    checklist_items = build_search_checklist_items(analyses)
    checkpoint_store = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=output_paths.checklist_output,
            step_name="step_2",
            report_date=report_date,
        ),
        step_name="step_2",
        report_date=report_date,
        input_path=output_paths.analysis_output,
        output_path=output_paths.checklist_output,
    )
    if auto_fill_keywords:
        if llm_client is None:
            raise RuntimeError("未配置 llm.api_key，无法自动生成 step 2 搜索关键词。")
        checklist_items = autofill_search_checklist_items(
            checklist_items,
            analyses,
            llm_client,
            checkpoint_store=checkpoint_store,
        )
    else:
        for item in checklist_items:
            if checkpoint_store.get_entry(build_step2_checkpoint_entry_id(item)) is None:
                checkpoint_store.record_entry(
                    entry_id=build_step2_checkpoint_entry_id(item),
                    status="pending",
                    request_context=build_step2_checkpoint_request_context(item),
                    result={"keywords": list(item.search_queries)},
                )
        checklist_items = apply_step2_checkpoint_results(checklist_items, checkpoint_store)
    output_paths.checklist_output.parent.mkdir(parents=True, exist_ok=True)
    output_paths.checklist_output.write_text(
        render_search_checklist_yaml(report_date, checklist_items),
        encoding="utf-8",
    )
    return checklist_items
