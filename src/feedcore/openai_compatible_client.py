from __future__ import annotations

import json
import os
from typing import Any

from .models import Article
from .workflow.research_gate import build_research_score_prompt


class OpenAICompatibleResponseError(RuntimeError):
    pass


def parse_chat_completion_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAICompatibleResponseError("Response did not include choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise OpenAICompatibleResponseError("Response content was empty")
    return content.strip()


class OpenAICompatibleClient:
    # 单文最大正文长度 (article_four_dims 用)。超长文章前面的导语 + 主体已涵盖 95%+ 关键事实；
    # in_max 88K tokens 极端值正是这里没截断导致的，截到 20K 字符 (~8K tokens) 兼顾质量与成本。
    ARTICLE_CONTENT_MAX_CHARS = 20000

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 120,
        max_tokens: int | None = None,
        flash_model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("API_KEY", "")
        self.base_url = (base_url or os.getenv("BASE_URL", "")).rstrip("/")
        self.model = model or os.getenv("MODEL", "")
        # flash 模型用于简单评分 (research_score)，省 token & 加速；缺省 fallback 到 pro
        self.flash_model = flash_model or os.getenv("MODEL_FLASH", "") or self.model
        self.timeout = timeout
        self.max_tokens = max_tokens
        if not self.api_key:
            raise ValueError("API_KEY is required")
        if not self.base_url:
            raise ValueError("BASE_URL is required")
        if not self.model:
            raise ValueError("MODEL is required")

    def chat(
        self,
        prompt: str,
        system_prompt: str = "你是专业中文新闻编辑；归纳须有正文依据，禁止空穴来风与常识脑补。",
        *,
        use_flash: bool = False,
    ) -> str:
        import requests

        model_name = self.flash_model if use_flash else self.model
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code == 401:
            raise PermissionError(
                "Model API unauthorized. Check .env values for API_KEY, BASE_URL, and MODEL."
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Model API request failed ({response.status_code}): {_response_error_text(response)}")
        response.raise_for_status()
        return parse_chat_completion_content(response.json())

    def summarize_article(self, article: Article, content: str) -> str:
        return self.chat(
            _build_article_summary_prompt(article, content),
            system_prompt=_article_summary_system_prompt() + _article_summary_compact_addendum(),
        )

    def score_research_article(self, article: Article, content: str, default_category: str = "") -> str:
        # v9 优化: 类别专属规则 + 龙头白名单 (避免统一标准误杀)
        # 1) 核心实体命中 → 跳过 LLM, 给底线分确保下游能处理
        # 2) 用类别专属 system prompt (国际形势/AI/金融/财经 各一套)
        from .category_rules import (
            is_core_entity_article,
            core_entity_score_payload,
            system_prompt_for_category,
        )
        matched = is_core_entity_article(article, default_category)
        if matched:
            return core_entity_score_payload(matched, default_category)
        return self.chat(
            build_research_score_prompt(article, content, default_category, max_chars=1500),
            system_prompt=system_prompt_for_category(default_category),
            use_flash=True,
        )

    def plan_subcategories(self, parent_category: str, prompt: str) -> str:
        return self.chat(
            _build_subcategory_plan_prompt(parent_category, prompt),
            system_prompt=_subcategory_plan_system_prompt(),
        )

    def plan_types(self, prompt: str) -> str:
        return self.chat(
            prompt,
            system_prompt=(
                "You are a news classification editor. Use only the provided article four-dimension records. "
                "Return strict JSON with short, specific type names."
            ),
        )

    def summarize_type_collection(self, type_name: str, articles: list[Any]) -> str:
        return self.chat(
            _build_type_collection_prompt(type_name, articles),
            system_prompt=(
                "You are a Chinese news brief editor. Synthesize only the articles in this type collection. "
                "Do not add facts outside the provided records."
            ),
        )

    def summarize_category_intro(self, category_name: str, topics: list[Any]) -> str:
        return self.chat(
            _build_category_intro_prompt(category_name, topics),
            system_prompt=(
                "你是中文新闻简报编辑。只根据输入的子类事实写一级分类概述，不能使用固定栏目介绍，不能添加材料外事实。"
            ),
        )

    def summarize_reading_event(
        self,
        category_name: str,
        topic_name: str,
        event_name: str,
        records: list[Any],
    ) -> str:
        return self.chat(
            _build_reading_event_synthesis_prompt(category_name, topic_name, event_name, records),
            system_prompt=(
                "你是中文新闻简报编辑。任务是去重并整合同一事件的四维要点。"
                "只能使用输入材料，不得添加材料外事实。只输出严格 JSON。"
            ),
        )


def _article_summary_system_prompt() -> str:
    return (
        "你是专业中文新闻编辑。必须严格依据正文与元数据写作，不得编造未出现的信息。"
        "输出 Markdown；按事实、背景、产生的影响、反面观点 / 数据矛盾点四个维度归纳。"
    )


def _article_summary_compact_addendum() -> str:
    return (
        " 优先提炼关键事实、主体、时间、数字和边界信息，不要解释性扩写，不要写成评论。"
        " 如果正文较短或信息稀薄，启用“稀疏提要模式”：事实最多2条，其他每节1条短句即可。"
    )


def _build_article_summary_prompt(article: Article, content: str) -> str:
    # 长文截断到 20K 字符 (~8K tokens)；关键事实通常在导语 + 主体前段，尾部多是
    # 例证/相关报道；in_max 88K tokens 主要由极少数 outlier 推高，截断不影响 95%+ 文章。
    if content and len(content) > OpenAICompatibleClient.ARTICLE_CONTENT_MAX_CHARS:
        content = content[: OpenAICompatibleClient.ARTICLE_CONTENT_MAX_CHARS].rstrip() + " ..."
    return f"""请基于下面新闻生成中文四要素简报。

标题：{article.title}
来源：{article.source}
发布时间：{article.pub_date}
链接：{article.link}

正文：
{content}

请严格按下面结构输出：

#### 事实
- 可核实事实要点。

#### 背景
- 与事件相关的背景脉络。

#### 产生的影响
- 报道明示或可由正文支撑的影响。

#### 反面观点 / 数据矛盾点
- 争议、对立口径、不确定性或数据冲突。"""


def _subcategory_plan_system_prompt() -> str:
    return (
        "你是新闻分类编辑。必须只依据用户提供的文章四要素信息动态识别小分类，"
        "禁止引入材料中没有出现的实体、趋势或结论。只输出严格 JSON，不要 Markdown。"
    )


def _response_error_text(response) -> str:
    try:
        payload = response.json()
    except Exception:
        return str(getattr(response, "text", "") or "").strip()
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                return str(message)
        message = payload.get("message")
        if message:
            return str(message)
    return str(getattr(response, "text", "") or payload).strip()


def _build_type_collection_prompt(type_name: str, articles: list[Any]) -> str:
    records = []
    for item in articles:
        article = getattr(item, "article", item)
        records.append(
            {
                "title": getattr(article, "title", ""),
                "source": getattr(article, "source", ""),
                "url": getattr(article, "link", ""),
                "facts": list(getattr(item, "facts", []) or []),
                "background": list(getattr(item, "background", []) or []),
                "impact": list(getattr(item, "impact", []) or []),
                "contradictions": list(getattr(item, "contradictions", []) or []),
            }
        )
    return f"""请整合类型「{type_name}」下的文章，输出中文四要素。
只基于输入材料，不做胡乱推测；如果只有单篇文章，只总结该文章。

文章材料：
{records}

#### 事实
- ...
#### 背景
- ...
#### 产生的影响
- ...
#### 反面观点 / 数据矛盾点
- ..."""


def _build_category_intro_prompt(category_name: str, topics: list[Any]) -> str:
    records = []
    for topic in topics:
        records.append(
            {
                "topic": getattr(topic, "name", ""),
                "facts": _topic_values(topic, "facts"),
                "impact": _topic_values(topic, "impact")[:3],
            }
        )
    return f"""请为一级分类「{category_name}」写一段中文概述。

要求：
- 只能总结下方子类中的事实和影响，不要写“本组覆盖/重点看”这类栏目介绍。
- 不要引入材料外事实，不要推测趋势。
- 1-2 句，80-140 字左右。
- 直接说明这一类新闻发生了什么，以及这些事实共同指向什么。
- 只输出概述正文，不要 Markdown。

子类材料：
{records}
"""


def _build_reading_event_synthesis_prompt(
    category_name: str,
    topic_name: str,
    event_name: str,
    records: list[Any],
) -> str:
    payload = []
    for record in records:
        payload.append(
            {
                "name": getattr(record, "name", ""),
                "facts": list(getattr(record, "facts", []) or []),
                "background": list(getattr(record, "background", []) or []),
                "impact": list(getattr(record, "impact", []) or []),
                "contradictions": list(getattr(record, "contradictions", []) or []),
            }
        )
    return f"""请把同一事件下的多个事项压缩成“一个事件摘要段”，不是写评论，也不是扩写成套话。

分类：{category_name}
主题：{topic_name}
事件：{event_name}

要求：
- 只基于输入材料，不新增事实。
- 每个维度最多输出 1 条，代表该事件在该维度下的一段概括。
- 同一主体、同一动作、同一数据口径的重复事项必须合并；不同事项也要压缩进同一段，但不要堆砌。
- 摘要段必须高信息密度，优先保留主体、动作、时间、金额/比例/数量、明确结果。
- 长度必须按信息量自适应：简单事件 20-60 字，中等事件 60-120 字，复杂事件最多 220 字。
- 不要为了让各维度长度接近而补充解释；背景、影响或矛盾信息弱时可以输出空数组。
- 不要为了凑齐四个维度而写泛泛表述；只输出该维度确有信息增量的内容。
- 信息过多时保留最关键、最可核验的 2-4 个点；信息很少时一句短句即可。
- 删除泛化废话和评论口吻，例如“具有重要意义”“值得关注”“体现趋势”“可能带来深远影响”等。
- 不要逐条照抄输入，不要机械拼接，不要使用“整合后事实/整合后背景”等模板话术。
- 输出严格 JSON，不要 Markdown。
- JSON 字段固定为 facts、background、impact、contradictions，字段值均为字符串数组；每个数组只能为空或包含 1 个字符串。

输入材料：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def _topic_values(topic: Any, attr: str) -> list[str]:
    values: list[str] = []
    for record in getattr(topic, "type_records", []) or []:
        values.extend(list(getattr(record, attr, []) or [])[:2])
    return values


def _build_subcategory_plan_prompt(parent_category: str, prompt: str) -> str:
    return f"""请为大分类「{parent_category}」下的文章制定小分类方案。

要求：
- 小分类名称必须具体、短、可读，不能使用“新闻动态”“综合观察”等空泛名称。
- 每篇文章最多进入一个小分类。
- 没有共同实体、事件、产业链、政策对象或数据线索的文章放入 ungrouped。
- 每个小分类必须给出 rationale，说明归类依据来自哪些文章信息。
- 输出必须是严格 JSON，字段为 parent_category、subcategories、ungrouped。

文章材料：
{prompt}"""
