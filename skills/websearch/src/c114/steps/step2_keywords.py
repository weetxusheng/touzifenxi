"""Step 2 搜索关键词生成编排。

本模块负责把 step 1/1.5 的文章分析转成搜索清单，按 topic 调用 LLM 生成关键词，
并用 checkpoint 支持跳过成功项和补跑失败项。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..analysis.models import ArticleAnalysis, SearchChecklistItem, SearchChecklistSection
from ..analysis.normalization import (
    ACTION_HINT_RE,
    LEADING_CONNECTOR_RE,
    TITLE_SEGMENT_STOPWORDS,
    TITLE_SPLIT_RE,
)
from ..llm import (
    MiniMaxChatClient,
    StructuredLLMError,
    begin_llm_step,
    coerce_json_object_payload,
    complete_json_with_postprocess_retry,
    load_prompt_text,
    run_parallel_ordered,
)
from ..runtime.checkpoint import StepCheckpointStore

SKILL_ROOT = Path(__file__).resolve().parents[3]
PROMPT_PATH = SKILL_ROOT / "prompts" / "search-keyword-agent.md"

def load_search_agent_prompt(prompt_path: Path = PROMPT_PATH) -> str:
    """Load the step 2 keyword-generation prompt shipped with the skill."""

    return prompt_path.read_text(encoding="utf-8")

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

