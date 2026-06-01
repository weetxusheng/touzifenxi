from __future__ import annotations

import json
import re

from feedcore.models import ArticleFourDimRecord, TypeCollectionRecord, TypePlanRecord, TypeProposalRecord


def build_type_plan_prompt(records: list[ArticleFourDimRecord], article_ids: dict[int, str]) -> str:
    articles = []
    for index, item in enumerate(records):
        articles.append(
            {
                "article_id": article_ids[id(item)],
                "title": item.article.title,
                "source": item.article.source,
                "url": item.article.link,
                "category": item.final_category,
                "facts": item.facts,
                "background": item.background,
                "impact": item.impact,
                "contradictions": item.contradictions,
            }
        )
    return "\n".join(
        [
            "请根据文章四要素把文章分成短类型。",
            "类型名必须简短、具体、一眼可懂，优先 2-8 个汉字，例如 AI芯片、算力基建、美联储。",
            "不要使用 科技新闻、财经动态、综合观察、其他热点 这类泛名。",
            "输出严格 JSON：{\"types\":[{\"type_id\":\"type_001\",\"name\":\"AI芯片\",\"rationale\":\"...\",\"article_ids\":[\"...\"]}],\"ungrouped\":[]}",
            "",
            json.dumps({"articles": articles}, ensure_ascii=False, indent=2),
        ]
    )


def parse_type_plan(raw: str) -> TypePlanRecord:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return TypePlanRecord(types=[], validation_errors=[f"invalid JSON: {exc.msg}"])

    proposals: list[TypeProposalRecord] = []
    for index, item in enumerate(data.get("types", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        type_id = str(item.get("type_id") or f"type_{index:03d}").strip()
        proposals.append(
            TypeProposalRecord(
                type_id=type_id,
                name=str(item.get("name", "")).strip(),
                rationale=str(item.get("rationale", "")).strip(),
                article_ids=[str(v).strip() for v in item.get("article_ids", []) if str(v).strip()],
            )
        )

    ungrouped = []
    for item in data.get("ungrouped", []) or []:
        if isinstance(item, dict):
            ungrouped.append(
                {
                    "article_id": str(item.get("article_id", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                }
            )
    return TypePlanRecord(types=proposals, ungrouped=ungrouped)


def validate_type_plan(
    records: list[ArticleFourDimRecord],
    plan: TypePlanRecord,
    article_ids: dict[int, str],
    rss_ids: dict[int, str],
) -> tuple[list[TypeCollectionRecord], TypePlanRecord]:
    if plan.validation_errors and not plan.types:
        return _fallback_type_collections(records, article_ids, rss_ids, reason="invalid model output"), plan

    article_by_id = {article_ids[id(item)]: item for item in records}
    assigned: set[str] = set()
    errors = list(plan.validation_errors)
    collections: list[TypeCollectionRecord] = []

    for index, proposal in enumerate(plan.types, start=1):
        name = _clean_type_name(proposal.name)
        if _is_invalid_type_name(name):
            errors.append(f"invalid type name: {proposal.name}")
            continue
        if not proposal.rationale:
            errors.append(f"missing rationale: {proposal.name}")
            continue

        articles = []
        for article_id in proposal.article_ids:
            article = article_by_id.get(article_id)
            if article is None:
                errors.append(f"unknown article id {article_id}: {proposal.name}")
                continue
            if article_id in assigned:
                errors.append(f"duplicate article id {article_id}: {proposal.name}")
                continue
            assigned.add(article_id)
            articles.append(article)
        if articles:
            type_id = proposal.type_id or f"type_{index:03d}"
            collections.append(
                TypeCollectionRecord(
                    type_id=type_id,
                    name=name,
                    rationale=proposal.rationale,
                    articles=articles,
                    source_rss_ids=_unique([rss_ids.get(id(item), "") for item in articles]),
                )
            )

    for article_id, item in article_by_id.items():
        if article_id not in assigned:
            errors.append(f"ungrouped article id {article_id}: omitted by model")
            collections.extend(_fallback_type_collections([item], article_ids, rss_ids, reason="omitted by model"))

    validated = TypePlanRecord(types=plan.types, ungrouped=plan.ungrouped, validation_errors=errors)
    return collections, validated


def _fallback_type_collections(
    records: list[ArticleFourDimRecord],
    article_ids: dict[int, str],
    rss_ids: dict[int, str],
    *,
    reason: str,
) -> list[TypeCollectionRecord]:
    out = []
    for index, item in enumerate(records, start=1):
        name = _fallback_type_name(item)
        out.append(
            TypeCollectionRecord(
                type_id=f"type_fallback_{index:03d}",
                name=name,
                rationale=reason,
                articles=[item],
                source_rss_ids=_unique([rss_ids.get(id(item), "")]),
                validation_errors=[reason],
            )
        )
    return out


def _fallback_type_name(item: ArticleFourDimRecord) -> str:
    category = _clean_type_name(item.final_category)
    if not _is_invalid_type_name(category):
        return category
    words = re.findall(r"[\w\u4e00-\u9fff]+", item.article.title)
    return _clean_type_name("".join(words)[:8]) or "待复核"


def _clean_type_name(name: str) -> str:
    return "".join(str(name or "").split())


def _is_invalid_type_name(name: str) -> bool:
    vague = {"科技新闻", "财经动态", "综合观察", "其他热点", "其它热点", "新闻动态", "行业动态"}
    if not name or name in vague:
        return True
    cjk = sum(1 for ch in name if "\u4e00" <= ch <= "\u9fff")
    return cjk > 8 or len(name) > 16


def _unique(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
