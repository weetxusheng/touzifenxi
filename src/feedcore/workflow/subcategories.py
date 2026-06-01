from __future__ import annotations

import json

from feedcore.models import (
    ArticleFourDimRecord,
    SubcategoryGroupRecord,
    SubcategoryPlanRecord,
    SubcategoryProposalRecord,
)


def build_subcategory_prompt(parent_category: str, articles: list[ArticleFourDimRecord]) -> str:
    records: list[dict[str, object]] = []
    for index, item in enumerate(articles, start=1):
        records.append(
            {
                "id": f"A{index}",
                "title": item.article.title,
                "source": item.article.source,
                "url": item.article.link,
                "facts": item.facts,
                "background": item.background,
                "impact": item.impact,
                "contradictions": item.contradictions,
            }
        )
    payload = {"parent_category": parent_category, "articles": records}
    return "\n".join(
        [
            "请根据下面同一大分类下的文章四要素，动态识别精准小分类。",
            "只允许基于输入材料归类，不要创造新事实。",
            "输出严格 JSON，字段为 parent_category、subcategories、ungrouped。",
            "subcategories[].name 必须具体，subcategories[].rationale 必须说明归类依据。",
            "",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def parse_subcategory_plan(parent_category: str, raw: str) -> SubcategoryPlanRecord:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return SubcategoryPlanRecord(
            parent_category=parent_category,
            subcategories=[],
            validation_errors=[f"invalid JSON: {exc.msg}"],
        )

    subcategories: list[SubcategoryProposalRecord] = []
    for item in data.get("subcategories", []) or []:
        if not isinstance(item, dict):
            continue
        subcategories.append(
            SubcategoryProposalRecord(
                parent_category=str(data.get("parent_category") or parent_category),
                name=str(item.get("name", "")).strip(),
                rationale=str(item.get("rationale", "")).strip(),
                article_ids=[str(v).strip() for v in item.get("article_ids", []) if str(v).strip()],
            )
        )

    ungrouped: list[dict[str, str]] = []
    for item in data.get("ungrouped", []) or []:
        if isinstance(item, dict):
            ungrouped.append(
                {
                    "article_id": str(item.get("article_id", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                }
            )

    return SubcategoryPlanRecord(
        parent_category=str(data.get("parent_category") or parent_category),
        subcategories=subcategories,
        ungrouped=ungrouped,
    )


def validate_subcategory_plan(
    parent_category: str,
    articles: list[ArticleFourDimRecord],
    plan: SubcategoryPlanRecord,
) -> tuple[list[SubcategoryGroupRecord], SubcategoryPlanRecord]:
    if plan.validation_errors and not plan.subcategories:
        return fallback_subcategory_groups(parent_category, articles, reason="invalid model output"), plan

    article_by_id = {f"A{index}": item for index, item in enumerate(articles, start=1)}
    assigned: set[str] = set()
    errors = list(plan.validation_errors)
    groups: list[SubcategoryGroupRecord] = []

    for proposal in plan.subcategories:
        group_articles: list[ArticleFourDimRecord] = []
        for article_id in proposal.article_ids:
            if article_id not in article_by_id:
                errors.append(f"unknown article id {article_id}: {proposal.name}")
                continue
            if article_id in assigned:
                errors.append(f"duplicate article id {article_id}: {proposal.name}")
                continue
            if proposal.rationale.strip() and not _is_vague_name(proposal.name):
                assigned.add(article_id)
                group_articles.append(article_by_id[article_id])

        if not proposal.rationale.strip():
            errors.append(f"missing rationale: {proposal.name}")
            continue
        if _is_vague_name(proposal.name):
            errors.append(f"vague subcategory name: {proposal.name}")
            continue

        if group_articles:
            groups.append(
                SubcategoryGroupRecord(
                    parent_category=parent_category,
                    subcategory=proposal.name,
                    rationale=proposal.rationale,
                    articles=group_articles,
                )
            )

    for article_id, article in article_by_id.items():
        if article_id not in assigned:
            errors.append(f"ungrouped article id {article_id}: omitted by model")
            groups.append(
                SubcategoryGroupRecord(
                    parent_category=parent_category,
                    subcategory=article.article.title,
                    rationale="模型未能可靠归类，按单篇文章保留。",
                    articles=[article],
                )
            )

    validated = SubcategoryPlanRecord(
        parent_category=parent_category,
        subcategories=plan.subcategories,
        ungrouped=plan.ungrouped,
        validation_errors=errors,
    )
    return groups, validated


def fallback_subcategory_groups(
    parent_category: str,
    articles: list[ArticleFourDimRecord],
    *,
    reason: str,
) -> list[SubcategoryGroupRecord]:
    return [
        SubcategoryGroupRecord(
            parent_category=parent_category,
            subcategory=item.article.title,
            rationale=reason,
            articles=[item],
        )
        for item in articles
    ]


def _is_vague_name(name: str) -> bool:
    clean = "".join(name.split())
    vague_names = {
        "科技新闻",
        "财经动态",
        "国际观察",
        "其它热点",
        "其他热点",
        "新闻动态",
        "综合新闻",
        "行业动态",
    }
    return clean in vague_names or len(clean) < 4
