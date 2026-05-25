from __future__ import annotations

import re
from typing import Final

from .models import Article

# Display order for markdown sections (一级分类).
TOPIC_ORDER: Final[list[str]] = [
    "国际形势与地缘政治",
    "美国政治与政策",
    "人工智能与科技",
    "金融市场与宏观",
    "产业与公司",
    "社会与其它",
]

# Keywords / regex fragments scored per topic (Chinese + common English tokens).
_TOPIC_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "国际形势与地缘政治": (
        "乌克兰",
        "俄罗斯",
        "中东",
        "伊朗",
        "以色列",
        "北约",
        "联合国",
        "外交",
        "地缘",
        "冲突",
        "战争",
        "停火",
        "制裁",
        "欧盟",
        "亚太",
        "印太",
        "朝鲜",
        "台湾",
        "南海",
        "格陵兰",
        "NATO",
        "Russia",
        "Ukraine",
        "Iran",
        "Israel",
        "Middle East",
        "UN ",
        "summit",
        "NATO",
    ),
    "美国政治与政策": (
        "特朗普",
        "拜登",
        "白宫",
        "国会",
        "参议院",
        "众议院",
        "共和党",
        "民主党",
        "最高法院",
        "大选",
        "联邦",
        "Trump",
        "Biden",
        "White House",
        "Congress",
        "Senate",
        "House ",
        "Republican",
        "Democrat",
        "DOGE",
        "Fed ",
        "Federal Reserve",
    ),
    "人工智能与科技": (
        "人工智能",
        "生成式",
        "大模型",
        "算法",
        "芯片",
        "半导体",
        "英伟达",
        "OpenAI",
        "Anthropic",
        "谷歌",
        "微软",
        "Meta ",
        "ChatGPT",
        "GPU",
        "算力",
        "数据中心",
        "云计算",
        "自动驾驶",
        "机器人",
        "AI ",
        "LLM",
        "NVIDIA",
        "Google ",
        "Microsoft",
        "Silicon Valley",
        "startup",
        "IPO",
    ),
    "金融市场与宏观": (
        "股市",
        "纳斯达克",
        "标普",
        "道琼斯",
        "金价",
        "油价",
        "通胀",
        "降息",
        "加息",
        "美联储",
        "财报",
        "比特币",
        "以太坊",
        "加密货币",
        "熊市",
        "牛市",
        "GDP",
        "Nasdaq",
        "S&P",
        "stock",
        "market",
        "inflation",
        "Fed ",
        "Treasury",
        "Bitcoin",
        "Ethereum",
    ),
    "产业与公司": (
        "特斯拉",
        "苹果",
        "亚马逊",
        "波音",
        "裁员",
        "并购",
        "财报",
        "CEO",
        "供应链",
        "工厂",
        "能源",
        "汽车",
        "新能源",
        "Tesla",
        "Apple",
        "Amazon",
        "SpaceX",
        "TSMC",
        "台积电",
    ),
}


def classify_topic(article: Article, content: str) -> str:
    """Assign one primary topic bucket from title + body text + source."""
    blob = f"{article.title}\n{article.source}\n{content[:8000]}"
    scores: dict[str, int] = {name: 0 for name in TOPIC_ORDER}
    blob_lower = blob.casefold()
    for topic, keys in _TOPIC_KEYWORDS.items():
        for kw in keys:
            k = kw.strip()
            if len(k) <= 2 and k.isascii():
                # Short English tokens: word boundary-ish
                if re.search(rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])", blob, re.IGNORECASE):
                    scores[topic] += 2
            elif k.lower() in blob_lower:
                scores[topic] += k.count(" ") + len(k) // 4 + 1

    best = max(scores, key=lambda t: scores[t])
    if scores[best] == 0:
        return "社会与其它"
    return best


def sort_topics_present(topics: set[str]) -> list[str]:
    ordered = [t for t in TOPIC_ORDER if t in topics]
    for t in topics:
        if t not in ordered:
            ordered.append(t)
    return ordered
