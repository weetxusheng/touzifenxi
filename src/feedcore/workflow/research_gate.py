from __future__ import annotations

import json
import re
from dataclasses import replace

from feedcore.models import Article, ArticleTextRecord, ResearchScoreRecord

_ALLOWED_DECISIONS = {"keep", "drop", "pending"}
_DECISION_ALIASES = {
    "keep": "keep",
    "include": "keep",
    "useful": "keep",
    "yes": "keep",
    "retain": "keep",
    "drop": "drop",
    "exclude": "drop",
    "not useful": "drop",
    "not_useful": "drop",
    "no": "drop",
    "reject": "drop",
    "pending": "pending",
    "uncertain": "pending",
    "unsure": "pending",
}
_PASS_THROUGH_PENDING_ERRORS = (
    "invalid decision:",
    "invalid investment_relevance:",
    "invalid information_increment:",
    "invalid decision_value:",
    "invalid verifiability:",
    "invalid noise_penalty:",
    "score exceeds component total",
    "missing evidence for keep-worthy score",
)
_VAGUE_REASON_PATTERNS = (
    "值得关注",
    "值得留意",
    "可关注",
    "有一定影响",
    "信息较重要",
    "需要关注",
    "important",
    "worth watching",
)
_LOW_VALUE_RESEARCH_TITLE_TOKENS = (
    "抵达",
    "专机",
    "迎接",
    "机场",
    "arrival",
    "landed",
    "welcomed",
)
_LOW_VALUE_RESEARCH_TEXT_TOKENS = (
    "挥舞",
    "高喊",
    "欢迎欢迎",
    "国旗",
    "暂停开放",
    "安保",
    "禁止拍照",
    "机场",
    "迎接",
    "青年",
    "礼宾",
    "crowd",
    "waving",
    "welcome",
    "security",
    "closed",
    "photo",
)
_HIGH_VALUE_RESEARCH_TOKENS = (
    "关税",
    "贸易",
    "委员会",
    "市场准入",
    "审批",
    "芯片",
    "出口",
    "协议",
    "制裁",
    "会谈",
    "谈判",
    "监管",
    "政策",
    "法案",
    "投资",
    "财报",
    "业绩",
    "tariff",
    "trade",
    "committee",
    "market access",
    "approval",
    "chip",
    "export",
    "sanction",
    "meeting",
    "talk",
    "policy",
    "regulation",
    "earnings",
)


def build_research_score_prompt(
    article: Article,
    content: str,
    default_category: str,
    *,
    article_id: str = "",
    max_chars: int = 6000,
) -> str:
    payload = {
        "article_id": article_id,
        "title": article.title,
        "source": article.source,
        "published_at": article.pub_date,
        "rss_default_category": default_category,
        "text_excerpt": _excerpt_text(content, max_chars=max_chars),
    }
    return "\n".join(
        [
            "You are judging whether this article belongs in a buy-side investment research brief.",
            "Use only the supplied article metadata and text excerpt.",
            "Return strict JSON only. No markdown. No explanation outside JSON.",
            'Allowed decision values: keep, drop, pending. Do not use "include", "exclude", "useful", or "not useful".',
            "All numeric fields must be integers.",
            "Field ranges: investment_relevance 0-30, information_increment 0-25, decision_value 0-20, verifiability 0-15, noise_penalty 0-10, score 0-100.",
            "If score >= 60, evidence must contain 1-3 short strings copied or paraphrased from the article.",
            "Use this exact schema:",
            '{'
            '"score": 72,'
            '"decision": "keep",'
            '"reason": "Concrete one-sentence judgment.",'
            '"investment_relevance": 24,'
            '"information_increment": 18,'
            '"decision_value": 15,'
            '"verifiability": 12,'
            '"noise_penalty": 3,'
            '"evidence": ["fact 1", "fact 2"],'
            '"tags": ["macro", "policy"]'
            '}',
            "",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def parse_research_score(
    raw: str,
    *,
    rss_id: str,
    article_id: str,
    article: Article,
    default_category: str,
) -> ResearchScoreRecord:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _pending_record(
            rss_id=rss_id,
            article_id=article_id,
            article=article,
            default_category=default_category,
            reason="Research score output was not valid JSON.",
            validation_errors=[f"invalid JSON: {exc.msg}"],
        )

    if not isinstance(data, dict):
        return _pending_record(
            rss_id=rss_id,
            article_id=article_id,
            article=article,
            default_category=default_category,
            reason="Research score output was not a JSON object.",
            validation_errors=["invalid JSON shape: expected object"],
        )

    errors: list[str] = []
    raw_decision = str(data.get("decision", "pending")).strip().lower()
    decision = _DECISION_ALIASES.get(raw_decision, raw_decision)
    if decision not in _ALLOWED_DECISIONS:
        errors.append(f"invalid decision: {raw_decision or '<empty>'}")
        decision = "pending"

    record = ResearchScoreRecord(
        rss_id=rss_id,
        article_id=article_id,
        article=article,
        default_category=default_category,
        score=_coerce_int(data.get("score"), "score", 0, 100, errors),
        decision=decision,
        reason=_clean_text(data.get("reason")),
        investment_relevance=_coerce_int(
            data.get("investment_relevance"), "investment_relevance", 0, 30, errors
        ),
        information_increment=_coerce_int(
            data.get("information_increment"), "information_increment", 0, 25, errors
        ),
        decision_value=_coerce_int(data.get("decision_value"), "decision_value", 0, 20, errors),
        verifiability=_coerce_int(data.get("verifiability"), "verifiability", 0, 15, errors),
        noise_penalty=_coerce_int(data.get("noise_penalty"), "noise_penalty", 0, 10, errors),
        evidence=_clean_string_list(data.get("evidence"), max_items=3),
        tags=_clean_string_list(data.get("tags"), max_items=8),
        validation_errors=errors,
    )
    return validate_research_score(record)


def validate_research_score(record: ResearchScoreRecord) -> ResearchScoreRecord:
    errors = list(record.validation_errors)
    decision = record.decision
    reason = record.reason or "Research score output could not be validated."
    component_errors = _has_component_errors(errors)

    component_total = (
        record.investment_relevance
        + record.information_increment
        + record.decision_value
        + record.verifiability
        - record.noise_penalty
    )
    if record.score > component_total and not component_errors:
        errors.append("score exceeds component total")
        decision = "pending"

    if decision == "keep" and record.score < 60:
        errors.append("keep decision downgraded because score < 60")
        decision = "drop"

    if decision == "drop" and record.score >= 60 and record.evidence:
        errors.append("drop decision conflicts with keep-worthy score")
        decision = "pending"

    if record.score >= 60 and not record.evidence:
        errors.append("missing evidence for keep-worthy score")
        decision = "pending"

    if not reason:
        errors.append("missing reason")
        decision = "pending"
        reason = "Research score output did not include a concrete reason."
    elif _is_vague_reason(reason):
        errors.append("vague reason")
        decision = "pending"

    if decision == "keep" and _has_blocking_keep_errors(errors):
        decision = "pending"

    return replace(record, decision=decision, reason=reason, validation_errors=errors)


def build_default_keep_score(item: ArticleTextRecord) -> ResearchScoreRecord:
    evidence = _clean_text(item.article.title) or _excerpt_text(item.text, max_chars=120)
    return ResearchScoreRecord(
        rss_id=item.rss_id,
        article_id=item.article_id,
        article=item.article,
        default_category=item.default_category,
        score=60,
        decision="keep",
        reason="Research scorer unavailable; defaulted to keep for compatibility.",
        investment_relevance=15,
        information_increment=15,
        decision_value=15,
        verifiability=15,
        noise_penalty=0,
        evidence=[evidence] if evidence else [],
        tags=["fallback"],
    )


def is_research_keep(record: ResearchScoreRecord) -> bool:
    return record.decision == "keep"


def should_pass_through_research_pending(record: ResearchScoreRecord) -> bool:
    if record.decision != "pending":
        return False
    if not record.validation_errors:
        return False
    return all(
        any(error.startswith(prefix) for prefix in _PASS_THROUGH_PENDING_ERRORS)
        for error in record.validation_errors
    )


def enforce_research_value_heuristics(record: ResearchScoreRecord, article_text: str) -> ResearchScoreRecord:
    if record.decision == "drop":
        return record
    if not _looks_like_low_value_research_article(record.article, article_text):
        return record
    errors = list(record.validation_errors)
    errors.append("heuristic drop: ceremonial/logistical article")
    return replace(
        record,
        score=min(record.score, 45),
        decision="drop",
        reason="Article is mainly ceremonial/logistical reporting with limited policy, market, or technology value.",
        validation_errors=errors,
    )


def _pending_record(
    *,
    rss_id: str,
    article_id: str,
    article: Article,
    default_category: str,
    reason: str,
    validation_errors: list[str],
) -> ResearchScoreRecord:
    return ResearchScoreRecord(
        rss_id=rss_id,
        article_id=article_id,
        article=article,
        default_category=default_category,
        score=0,
        decision="pending",
        reason=reason,
        investment_relevance=0,
        information_increment=0,
        decision_value=0,
        verifiability=0,
        noise_penalty=0,
        evidence=[],
        tags=[],
        validation_errors=validation_errors,
    )


def _looks_like_low_value_research_article(article: Article, article_text: str) -> bool:
    title = (article.title or "").casefold()
    text = (article_text or "").casefold()
    title_hits = sum(token in title for token in _LOW_VALUE_RESEARCH_TITLE_TOKENS)
    low_hits = sum(token.casefold() in text for token in _LOW_VALUE_RESEARCH_TEXT_TOKENS)
    high_hits = sum(token.casefold() in text or token.casefold() in title for token in _HIGH_VALUE_RESEARCH_TOKENS)
    if title_hits >= 1 and low_hits >= 2 and high_hits <= 1:
        return True
    if low_hits >= 4 and high_hits == 0:
        return True
    return False


def _coerce_int(value: object, field_name: str, minimum: int, maximum: int, errors: list[str]) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors.append(f"invalid {field_name}: expected integer")
        return minimum
    if number < minimum or number > maximum:
        errors.append(f"invalid {field_name}: out of range {minimum}-{maximum}")
        return min(max(number, minimum), maximum)
    return number


def _clean_string_list(value: object, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        clean = _clean_text(item)
        if clean and clean not in seen:
            out.append(clean)
            seen.add(clean)
        if len(out) >= max_items:
            break
    return out


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _excerpt_text(text: str, *, max_chars: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip(" ,;:") + " ..."


def _is_vague_reason(reason: str) -> bool:
    lowered = reason.casefold()
    return any(token.casefold() in lowered for token in _VAGUE_REASON_PATTERNS)


def _has_component_errors(errors: list[str]) -> bool:
    prefixes = (
        "invalid investment_relevance:",
        "invalid information_increment:",
        "invalid decision_value:",
        "invalid verifiability:",
        "invalid noise_penalty:",
    )
    return any(any(error.startswith(prefix) for prefix in prefixes) for error in errors)


def _has_blocking_keep_errors(errors: list[str]) -> bool:
    non_blocking_prefixes = (
        "invalid investment_relevance:",
        "invalid information_increment:",
        "invalid decision_value:",
        "invalid verifiability:",
        "invalid noise_penalty:",
    )
    for error in errors:
        if any(error.startswith(prefix) for prefix in non_blocking_prefixes):
            continue
        return True
    return False
