from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Protocol

from feedcore.brief_validation import compact_four_dimension_brief
from feedcore.brief_taxonomy import classify_topic
from feedcore.models import (
    Article,
    ArticleContentRecord,
    ArticleFourDimRecord,
    FeedItemRecord,
    RssSourceRecord,
    SubcategoryFourDimRecord,
    SubcategoryGroupRecord,
    SubcategoryPlanRecord,
)
from feedcore.workflow.subcategories import (
    build_subcategory_prompt,
    fallback_subcategory_groups,
    parse_subcategory_plan,
    validate_subcategory_plan,
)


class RssClient(Protocol):
    def fetch(self, url: str) -> str:
        pass


class ArticleClient(Protocol):
    def fetch_text(self, url: str) -> str:
        pass


class Translator(Protocol):
    def translate_to_chinese(self, text: str) -> str:
        pass


class SummaryClient(Protocol):
    def score_research_article(self, article: Article, content: str, default_category: str) -> str:
        pass

    def summarize_article(self, article: Article, content: str) -> str:
        pass


class SubcategoryPlanner(Protocol):
    def plan_subcategories(self, parent_category: str, prompt: str) -> str:
        pass


def select_rss_sources(
    rss_urls: list[str],
    *,
    default_categories: dict[str, str] | None = None,
    max_sources: int | None = None,
) -> list[RssSourceRecord]:
    categories = default_categories or {}
    records = [
        RssSourceRecord(
            url=url,
            label=_label_from_url(url),
            default_category=categories.get(url, _guess_source_category(url)),
            selected_reason="configured",
        )
        for url in rss_urls
        if str(url).strip()
    ]
    if max_sources is None or max_sources <= 0 or len(records) <= max_sources:
        return records

    buckets: dict[str, list[RssSourceRecord]] = defaultdict(list)
    category_order: list[str] = []
    for record in records:
        if record.default_category not in buckets:
            category_order.append(record.default_category)
        buckets[record.default_category].append(record)

    selected: list[RssSourceRecord] = []
    while len(selected) < max_sources:
        progressed = False
        for category in category_order:
            bucket = buckets[category]
            if bucket and len(selected) < max_sources:
                picked = bucket.pop(0)
                selected.append(
                    RssSourceRecord(
                        url=picked.url,
                        label=picked.label,
                        default_category=picked.default_category,
                        selected_reason="cross-category sample",
                    )
                )
                progressed = True
        if not progressed:
            break
    return selected


def fetch_feed_items(
    sources: list[RssSourceRecord],
    rss_client: RssClient,
    parse_feed_fn: Callable[[str, str], list[Article]],
) -> list[FeedItemRecord]:
    records: list[FeedItemRecord] = []
    seen_links: set[str] = set()
    source_by_url = {source.url: source for source in sources}
    for source in sources:
        try:
            feed_text = rss_client.fetch(source.url)
            articles = parse_feed_fn(feed_text, source.url)
        except Exception:
            continue
        for article in articles:
            if not article.link or article.link in seen_links:
                continue
            seen_links.add(article.link)
            owner = source_by_url.get(article.feed_url, source)
            records.append(
                FeedItemRecord(
                    article=article,
                    default_category=owner.default_category,
                    source_label=owner.label,
                )
            )
    return records


def fetch_article_contents(
    feed_items: list[FeedItemRecord],
    article_client: ArticleClient,
    *,
    translator: Translator | None = None,
) -> list[ArticleContentRecord]:
    records: list[ArticleContentRecord] = []
    for item in feed_items:
        fetch_error: str | None = None
        try:
            original = article_client.fetch_text(item.article.link)
        except Exception as exc:
            original = item.article.description or item.article.title
            fetch_error = str(exc)
        language = detect_language(original)
        translated = False
        analysis = original
        if language == "en" and translator is not None:
            try:
                analysis = translator.translate_to_chinese(original)
                translated = True
            except Exception as exc:
                fetch_error = _append_error(fetch_error, f"translation failed: {exc}")
        records.append(
            ArticleContentRecord(
                article=item.article,
                default_category=item.default_category,
                original_content=original,
                analysis_content=analysis,
                detected_language=language,
                translated=translated,
                fetch_error=fetch_error,
            )
        )
    return records


def generate_article_four_dims(
    contents: list[ArticleContentRecord],
    summary_client: SummaryClient,
) -> list[ArticleFourDimRecord]:
    records: list[ArticleFourDimRecord] = []
    for item in contents:
        model_failed = False
        try:
            brief = summary_client.summarize_article(item.article, item.analysis_content)
        except Exception as exc:
            brief = _model_failure_brief(exc)
            model_failed = True
        if not model_failed:
            brief = compact_four_dimension_brief(brief, item.analysis_content)
        dims = parse_four_dimensions(brief)
        classified = classify_topic(item.article, item.analysis_content)
        final_category = classified if classified != "社会与其它" else item.default_category
        records.append(
            ArticleFourDimRecord(
                article=item.article,
                default_category=item.default_category,
                final_category=final_category,
                facts=dims["facts"],
                background=dims["background"],
                impact=dims["impact"],
                contradictions=dims["contradictions"],
                brief=brief,
            )
        )
    return records


def plan_dynamic_subcategories(
    records: list[ArticleFourDimRecord],
    planner: SubcategoryPlanner,
) -> list[SubcategoryPlanRecord]:
    plans: list[SubcategoryPlanRecord] = []
    by_category: dict[str, list[ArticleFourDimRecord]] = {}
    for record in records:
        by_category.setdefault(record.final_category, []).append(record)
    for category, items in by_category.items():
        prompt = build_subcategory_prompt(category, items)
        try:
            raw = planner.plan_subcategories(category, prompt)
            plans.append(parse_subcategory_plan(category, raw))
        except Exception as exc:
            plans.append(
                SubcategoryPlanRecord(
                    parent_category=category,
                    subcategories=[],
                    validation_errors=[f"planner failed: {exc}"],
                )
            )
    return plans


def group_validated_subcategories(
    records: list[ArticleFourDimRecord],
    plans: list[SubcategoryPlanRecord],
) -> tuple[list[SubcategoryGroupRecord], list[SubcategoryPlanRecord]]:
    by_category: dict[str, list[ArticleFourDimRecord]] = {}
    for record in records:
        by_category.setdefault(record.final_category, []).append(record)

    plan_by_category = {plan.parent_category: plan for plan in plans}
    groups: list[SubcategoryGroupRecord] = []
    validated_plans: list[SubcategoryPlanRecord] = []
    for category, items in by_category.items():
        plan = plan_by_category.get(category)
        if plan is None:
            groups.extend(fallback_subcategory_groups(category, items, reason="missing subcategory plan"))
            validated_plans.append(
                SubcategoryPlanRecord(
                    parent_category=category,
                    subcategories=[],
                    validation_errors=["missing subcategory plan"],
                )
            )
            continue
        category_groups, validated = validate_subcategory_plan(category, items, plan)
        groups.extend(category_groups)
        validated_plans.append(validated)
    return groups, validated_plans


def generate_subcategory_four_dims(groups: list[SubcategoryGroupRecord]) -> list[SubcategoryFourDimRecord]:
    dims: list[SubcategoryFourDimRecord] = []
    for group in groups:
        dims.append(
            SubcategoryFourDimRecord(
                parent_category=group.parent_category,
                subcategory=group.subcategory,
                facts=_merge_unique([v for item in group.articles for v in item.facts]),
                background=_merge_unique([v for item in group.articles for v in item.background]),
                impact=_merge_unique([v for item in group.articles for v in item.impact]),
                contradictions=_merge_unique([v for item in group.articles for v in item.contradictions]),
                source_links=_merge_unique([item.article.link for item in group.articles if item.article.link]),
            )
        )
    return dims


def parse_four_dimensions(brief: str) -> dict[str, list[str]]:
    mapping = {
        "事实": "facts",
        "背景": "background",
        "产生的影响": "impact",
        "反面观点": "contradictions",
    }
    result = {"facts": [], "background": [], "impact": [], "contradictions": []}
    current: str | None = None
    for raw in brief.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("####"):
            current = None
            header = line.lstrip("#").strip()
            for text, key in mapping.items():
                if header.startswith(text):
                    current = key
                    break
            continue
        if current:
            result[current].append(_strip_bullet(line))
    return result


def _model_failure_brief(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return "\n".join(
        [
            "#### 事实",
            f"- 模型生成失败：{message}",
            "",
            "#### 背景",
            "- 该条文章已抓取，但模型未能完成四要素归纳。",
            "",
            "#### 产生的影响",
            "- 当前自动简报无法使用该条文章形成可靠结论。",
            "",
            "#### 反面观点 / 数据矛盾点",
            "- 该条文章未完成模型归纳，需人工复核。",
        ]
    )


def detect_language(text: str) -> str:
    sample = (text or "")[:1000]
    if not sample.strip():
        return "unknown"
    ascii_letters = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return "en" if ascii_letters > cjk * 2 else "zh"


def _label_from_url(url: str) -> str:
    match = re.search(r"[?&]q=([^&]+)", url)
    if match:
        return match.group(1).replace("+", " ")
    return url.rstrip("/").rsplit("/", 1)[-1] or url


def _guess_source_category(url: str) -> str:
    lowered = url.casefold()
    if any(token in lowered for token in ("openai", "ai", "nvidia", "chip", "tech")):
        return "人工智能与科技"
    if any(token in lowered for token in ("stock", "market", "inflation", "fed", "bitcoin", "finance")):
        return "金融市场与宏观"
    if any(token in lowered for token in ("war", "nato", "russia", "ukraine", "israel", "iran")):
        return "国际形势与地缘政治"
    if any(token in lowered for token in ("trump", "biden", "white+house", "congress", "senate")):
        return "美国政治与政策"
    return "社会与其它"


def _strip_bullet(line: str) -> str:
    line = line.strip()
    if line.startswith(("- ", "* ")):
        return line[2:].strip()
    return re.sub(r"^\d{1,2}[.)]\s*", "", line).strip()


def _append_error(existing: str | None, message: str) -> str:
    return f"{existing}; {message}" if existing else message


def _merge_unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value).split())
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out
