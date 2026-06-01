"""Step 1/2 文本归一化与规则推断。

这里放不依赖 I/O 和 LLM 的文章关键词、实体、信号、主题和检索短语规则。
模型调用模块只消费这些稳定工具，不直接维护规则表。
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

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

def compact_phrase(value: str) -> str:
    """压缩短语中的空白和符号，生成适合写入 YAML 的值。"""
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"[|；;]+", " ", text)
    return text[:40].strip()
