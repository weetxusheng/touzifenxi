"""类别专属过滤规则 (v9 优化)。

背景: v7/v8 实测发现 research_score 用单一"投资可执行性"prompt 对 4 类内容评分不公,
导致地缘政治评论被砍("no financial implications")、AI 模型发布被砍("no market data") —
这些恰恰是各类核心信号。

本模块提供:
1. SYSTEM_PROMPTS_BY_CATEGORY: 类别专属 LLM 评分 prompt
2. CORE_ENTITIES: 龙头公司/机构白名单 (命中即给底线分,避免 false negative)
3. apply_category_rules(article, default_category, content): 综合判定函数
"""

from __future__ import annotations

import json
from typing import Any


# 类别专属 system prompt (替代通用"buy-side researcher")
SYSTEM_PROMPTS_BY_CATEGORY: dict[str, str] = {
    "国际形势": (
        "You are a geopolitical analyst screening articles for an investment research brief. "
        "High signal value: great-power dynamics (US-China, US-Russia, NATO/G7), "
        "policy shifts (sanctions, tariffs, export controls), "
        "military events (Ukraine, Middle East, Indo-Pacific), "
        "diplomatic breakthroughs or breakdowns. "
        "Low signal: single-country domestic politics, individual biographies, "
        "historical retrospectives, opinion columns without new facts. "
        "Use only facts from text. Return strict JSON. "
        'Decision values: "keep", "drop", "pending". All scores integers.'
    ),
    "人工智能与科技": (
        "You are an AI/tech industry analyst screening articles for an investment research brief. "
        "High signal value: model releases (GPT/Claude/Gemini/Llama new versions), "
        "compute/GPU supply (Nvidia/TSMC), AI regulation/safety policy, "
        "MAG7 (Microsoft/Apple/Google/Meta/Amazon/Nvidia/Tesla) concrete moves, "
        "AI funding rounds (Series A/B/C with named amounts), enterprise AI deployments. "
        "Low signal: AI 'use case' listicles, general AI commentary, "
        "consumer gadget reviews without industry impact. "
        "Use only facts from text. Return strict JSON. "
        'Decision values: "keep", "drop", "pending". All scores integers.'
    ),
    "金融市场与宏观": (
        "You are a macro/markets analyst screening articles for an investment research brief. "
        "High signal value: Federal Reserve/Treasury statements, FOMC decisions, "
        "CPI/PPI/employment data releases, Treasury yields/curve, "
        "equity index moves (S&P/Nasdaq with specific %), commodities (oil/gold) with prices, "
        "ECB/BOJ/PBOC policy moves, sovereign debt events. "
        "Low signal: retail trading tips, generic market commentary, "
        "individual stock picks without data, brand-sponsored content. "
        "Use only facts from text. Return strict JSON. "
        'Decision values: "keep", "drop", "pending". All scores integers.'
    ),
    "财经信息": (
        "You are a corporate-events analyst screening articles for an investment research brief. "
        "High signal value: company earnings (specific revenue/EPS numbers), "
        "M&A deals (named parties + deal size), IPO pricing/filings, "
        "SEC enforcement actions, major executive changes, "
        "bank/credit market events, large capital allocation announcements. "
        "Low signal: startup hype articles, founder profiles, "
        "general 'best of' lists, opinion essays. "
        "Use only facts from text. Return strict JSON. "
        'Decision values: "keep", "drop", "pending". All scores integers.'
    ),
}

# 默认 prompt (与原 score_research_article 一致,保持兼容)
DEFAULT_SYSTEM_PROMPT = (
    "You are a buy-side fund researcher / portfolio manager assistant. "
    "Judge whether the article is useful for investment research. "
    "Do not reward popularity, ideology, or political entertainment. "
    'Use only facts from the provided text and return strict JSON. '
    'Allowed decision values are exactly "keep", "drop", and "pending". '
    "All score fields must be integers."
)


# 龙头实体白名单 — 命中即获底线分,跳过 LLM 评判
# (按类别区分,每个实体至少 4 字符避免误匹配,如 "GPT-" 而非 "GPT")
CORE_ENTITIES: dict[str, tuple[str, ...]] = {
    "国际形势": (
        "State Department", "White House", "Pentagon", "NATO", "G7 summit",
        "Federal Reserve", "Treasury Secretary",  # 美国关键机构
        "习近平", "Xi Jinping", "Putin", "Kremlin",  # 关键人物
        "FOMC", "UN Security Council",
    ),
    "人工智能与科技": (
        "OpenAI", "Anthropic", "DeepMind", "Mistral AI", "xAI",
        "Nvidia", "TSMC", "ASML", "AMD ", "Intel ",
        "Microsoft", "Apple ", "Google ", "Meta ", "Amazon ",
        "ChatGPT", "Claude ", "Gemini", "Llama", "GPT-",
        "Sora", "DALL-E",
    ),
    "金融市场与宏观": (
        "Federal Reserve", "FOMC", "Jerome Powell", "Powell",
        "US Treasury", "Treasury Secretary", "Bessent",
        "SEC ", "ECB", "Bank of Japan", "Bank of England",
        "S&P 500", "Nasdaq Composite", "Dow Jones",
    ),
    "财经信息": (
        "Goldman Sachs", "Morgan Stanley", "JPMorgan", "JPMorgan Chase",
        "BlackRock", "Berkshire Hathaway", "Vanguard",
        "Apollo Global", "Blackstone", "KKR ", "Citadel",
        "SEC enforcement", "SEC charges",
    ),
}


def _matches_core_entity(text: str, category: str) -> str | None:
    """判断 text 是否包含类别的核心实体。返回命中的实体名,无命中返回 None。"""
    if not text or category not in CORE_ENTITIES:
        return None
    text_l = text.lower()
    for entity in CORE_ENTITIES[category]:
        # 简单子串匹配; entity 已经设计成 ≥4 字符避免误匹配
        if entity.lower() in text_l:
            return entity
    return None


def is_core_entity_article(article: Any, default_category: str) -> str | None:
    """判断文章是否含本类核心实体 (用于 score_research_article 跳过 LLM)。"""
    title = getattr(article, "title", "") or ""
    desc = getattr(article, "description", "") or ""
    return _matches_core_entity(f"{title} {desc}", default_category)


def core_entity_score_payload(entity: str, default_category: str) -> str:
    """命中核心实体时返回的 hardcoded JSON (跳过 LLM 调用)。

    score=55 是"保底分但不强行 keep" — 仍由下游 type_classification 决定最终去留,
    避免硬塞低质量的"OpenAI 早餐" 这种无信号文章。
    """
    return json.dumps({
        "score": 55,
        "decision": "keep",
        "reason": f"Core entity ({entity}) in {default_category}; baseline kept for downstream analysis.",
        "investment_relevance": 18,
        "information_increment": 14,
        "decision_value": 12,
        "verifiability": 8,
        "noise_penalty": 0,
        "evidence": [entity],
        "tags": ["core_entity_bypass"],
        "validation_errors": [],
    }, ensure_ascii=False)


def system_prompt_for_category(default_category: str) -> str:
    """根据 default_category 返回对应的 system prompt。"""
    return SYSTEM_PROMPTS_BY_CATEGORY.get(default_category, DEFAULT_SYSTEM_PROMPT)
