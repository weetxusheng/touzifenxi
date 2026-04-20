"""Step 1.5 文章主题聚类编排。

本模块负责一次性把当天文章集合交给 LLM 做主题归并，并把结果写入 checkpoint。
规则化单篇分析仍在 step1_analysis，搜索关键词生成仍在 step2_keywords。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..analysis.models import ArticleAnalysis, TopicBrief
from ..analysis.normalization import build_core_summary, build_followup_queries, compact_phrase
from utils.tools.llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    coerce_json_object_payload,
    complete_json_with_postprocess_retry,
    load_prompt_text,
)
from utils.tools.runtime.checkpoint import StepCheckpointStore
from .step1_analysis import build_topic_briefs

PROJECT_ROOT = Path(__file__).resolve().parents[4]
C114_ROOT = PROJECT_ROOT / "src" / "c114"
TOPIC_GROUPING_PROMPT_PATH = C114_ROOT / "prompts" / "topic-grouping-agent.md"
TOPIC_GROUPING_CHECKPOINT_ENTRY_ID = "batch::all_articles"

def load_topic_grouping_prompt(prompt_path: Path = TOPIC_GROUPING_PROMPT_PATH) -> str:
    """读取 step 1.5 文章分类提示词。"""

    return prompt_path.read_text(encoding="utf-8")


def auto_group_analysis_topics(
    analyses: list[ArticleAnalysis],
    llm_client: MiniMaxChatClient | None,
    *,
    report_date: str,
    source_site: str = "c114",
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
                    "先按较粗研究主题归并，优先把同一技术方向、同一产业链、同一监管议题或同一公司动态归到一起。"
                    "目标是让一天的文章形成少数几个可用于简报的主题簇，而不是每篇文章一个主题。"
                    "一天 10-25 篇文章时，最终主题不得超过 5 个，优先合并到 3-5 个主题；主题名必须是上位研究主题，"
                    "不要写成具体访谈对象、单家公司单次发布或单条融资标题。"
                    "只有确实找不到共同研究线索的文章，才可以单独成组。\n\n"
                    f"{json.dumps({'source_site': source_site, 'report_date': report_date, 'items': payload_items}, ensure_ascii=False, indent=2)}"
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
                        "source_site": source_site,
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
                    "source_site": source_site,
                    "article_count": len(analyses),
                    "titles": [analysis.title for analysis in analyses],
                },
                result={"assignments": cached_assignments},
            )

    grouped_analyses = _apply_topic_grouping_assignments(analyses, cached_assignments)
    return grouped_analyses, build_topic_briefs(grouped_analyses)


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
