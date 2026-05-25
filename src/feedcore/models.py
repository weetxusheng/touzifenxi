from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Article:
    title: str
    link: str
    pub_date: str
    description: str
    source: str
    feed_url: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ArticleBrief:
    article: Article
    content: str
    brief: str
    fetch_error: str | None = None
    #: 基于四维要点由大模型生成的一句话概要（可选）
    one_liner: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "article": self.article.to_dict(),
            "content": self.content,
            "brief": self.brief,
            "fetch_error": self.fetch_error,
            "one_liner": self.one_liner,
        }


@dataclass(frozen=True)
class RssSourceRecord:
    url: str
    label: str = ""
    default_category: str = "社会与其它"
    selected_reason: str = ""
    status: str = "selected"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FeedItemRecord:
    article: Article
    default_category: str
    source_label: str = ""
    status: str = "queued"
    skip_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "source_label": self.source_label,
            "status": self.status,
            "skip_reason": self.skip_reason,
        }


@dataclass(frozen=True)
class ArticleContentRecord:
    article: Article
    default_category: str
    original_content: str
    analysis_content: str
    detected_language: str = "unknown"
    translated: bool = False
    fetch_error: str | None = None
    document_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "original_content": self.original_content,
            "analysis_content": self.analysis_content,
            "detected_language": self.detected_language,
            "translated": self.translated,
            "fetch_error": self.fetch_error,
            "document_paths": list(self.document_paths),
        }


@dataclass(frozen=True)
class ArticleFourDimRecord:
    article: Article
    default_category: str
    final_category: str
    facts: list[str]
    background: list[str]
    impact: list[str]
    contradictions: list[str]
    brief: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "final_category": self.final_category,
            "facts": list(self.facts),
            "background": list(self.background),
            "impact": list(self.impact),
            "contradictions": list(self.contradictions),
            "brief": self.brief,
        }


@dataclass(frozen=True)
class ArticleTextRecord:
    rss_id: str
    article_id: str
    article: Article
    default_category: str
    text: str
    detected_language: str = "unknown"
    translated: bool = False
    fetch_error: str | None = None
    extract_error: str | None = None
    translation_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rss_id": self.rss_id,
            "article_id": self.article_id,
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "text": self.text,
            "detected_language": self.detected_language,
            "translated": self.translated,
            "fetch_error": self.fetch_error,
            "extract_error": self.extract_error,
            "translation_error": self.translation_error,
        }


@dataclass(frozen=True)
class ResearchScoreRecord:
    rss_id: str
    article_id: str
    article: Article
    default_category: str
    score: int
    decision: str
    reason: str
    investment_relevance: int
    information_increment: int
    decision_value: int
    verifiability: int
    noise_penalty: int
    evidence: list[str]
    tags: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "rss_id": self.rss_id,
            "article_id": self.article_id,
            "article": self.article.to_dict(),
            "default_category": self.default_category,
            "score": self.score,
            "decision": self.decision,
            "reason": self.reason,
            "investment_relevance": self.investment_relevance,
            "information_increment": self.information_increment,
            "decision_value": self.decision_value,
            "verifiability": self.verifiability,
            "noise_penalty": self.noise_penalty,
            "evidence": list(self.evidence),
            "tags": list(self.tags),
            "validation_errors": list(self.validation_errors),
        }


@dataclass(frozen=True)
class TypeProposalRecord:
    type_id: str
    name: str
    rationale: str
    article_ids: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "type_id": self.type_id,
            "name": self.name,
            "rationale": self.rationale,
            "article_ids": list(self.article_ids),
        }


@dataclass(frozen=True)
class TypePlanRecord:
    types: list[TypeProposalRecord]
    ungrouped: list[dict[str, str]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "types": [item.to_dict() for item in self.types],
            "ungrouped": [dict(item) for item in self.ungrouped],
            "validation_errors": list(self.validation_errors),
        }


@dataclass(frozen=True)
class TypeCollectionRecord:
    type_id: str
    name: str
    rationale: str
    articles: list[ArticleFourDimRecord]
    source_rss_ids: list[str]
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "type_id": self.type_id,
            "name": self.name,
            "rationale": self.rationale,
            "articles": [item.to_dict() for item in self.articles],
            "source_rss_ids": list(self.source_rss_ids),
            "validation_errors": list(self.validation_errors),
        }


@dataclass(frozen=True)
class TypeFourDimRecord:
    type_id: str
    name: str
    facts: list[str]
    background: list[str]
    impact: list[str]
    contradictions: list[str]
    category_group: str = "其它"
    source_links: list[str] = field(default_factory=list)
    source_refs: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "type_id": self.type_id,
            "name": self.name,
            "category_group": self.category_group,
            "facts": list(self.facts),
            "background": list(self.background),
            "impact": list(self.impact),
            "contradictions": list(self.contradictions),
            "source_links": list(self.source_links),
            "source_refs": [dict(item) for item in self.source_refs],
            "error": self.error,
        }


@dataclass(frozen=True)
class SubcategoryProposalRecord:
    parent_category: str
    name: str
    rationale: str
    article_ids: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_category": self.parent_category,
            "name": self.name,
            "rationale": self.rationale,
            "article_ids": list(self.article_ids),
        }


@dataclass(frozen=True)
class SubcategoryPlanRecord:
    parent_category: str
    subcategories: list[SubcategoryProposalRecord]
    ungrouped: list[dict[str, str]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_category": self.parent_category,
            "subcategories": [item.to_dict() for item in self.subcategories],
            "ungrouped": [dict(item) for item in self.ungrouped],
            "validation_errors": list(self.validation_errors),
        }


@dataclass(frozen=True)
class SubcategoryGroupRecord:
    parent_category: str
    subcategory: str
    rationale: str
    articles: list[ArticleFourDimRecord]

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_category": self.parent_category,
            "subcategory": self.subcategory,
            "rationale": self.rationale,
            "articles": [item.to_dict() for item in self.articles],
        }


@dataclass(frozen=True)
class SubcategoryFourDimRecord:
    parent_category: str
    subcategory: str
    facts: list[str]
    background: list[str]
    impact: list[str]
    contradictions: list[str]
    source_links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_category": self.parent_category,
            "subcategory": self.subcategory,
            "facts": list(self.facts),
            "background": list(self.background),
            "impact": list(self.impact),
            "contradictions": list(self.contradictions),
            "source_links": list(self.source_links),
        }
