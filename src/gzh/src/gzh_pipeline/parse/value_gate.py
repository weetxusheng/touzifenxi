"""单篇源文件价值预筛：广告/低信息含量等 → 留痕原因，低价值篇目不进入四维度解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from gzh_pipeline.audit.trace import TraceRecorder
from gzh_pipeline.parse.errors import StrictLlmFailure
from gzh_pipeline.parse.extract import SourceArticle
from gzh_pipeline.parse.llm_compat import OpenAICompatConfig, parse_llm_json_message, post_chat_completions
from gzh_pipeline.parse.vision_llm import (
    PrecleanNotesCollector,
    build_value_gate_user_content,
    gate_vision_system_line,
)


@dataclass(frozen=True)
class GatedSource:
    article: SourceArticle
    valuable: bool
    category: str
    reason_zh: str
    star_rating: int = 3          # 1-5星评分，默认3星
    summary: str = ""             # 40字以内简短摘要


def _env_flag(name: str, default_on: bool = True) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default_on
    return v not in ("0", "false", "no", "off")


def gate_enabled() -> bool:
    return _env_flag("GZH_VALUE_GATE_LLM", True)


def gate_on_error_policy() -> str:
    """API/解析失败时：``keep`` 保留该篇；``discard`` 剔除。"""
    return os.environ.get("GZH_VALUE_GATE_ON_ERROR", "keep").strip().lower() or "keep"


def gate_max_chars() -> int:
    return int(os.environ.get("GZH_VALUE_GATE_MAX_CHARS", "8000"))


def value_gate_system_prompt() -> str:
    """
    价值预筛 system 提示（仅指导大模型，代码不做标题/来源/关键词硬编码判定）。

    可用 ``GZH_VALUE_GATE_SYSTEM`` 整段覆盖；占位符 ``{vision_line}`` 会替换为配图识读说明。
    """
    custom = os.environ.get("GZH_VALUE_GATE_SYSTEM", "").strip()
    if custom:
        return custom.replace("{vision_line}", gate_vision_system_line())
    vision_line = gate_vision_system_line()
    parts = [
        # "你是中文财经与投研内容入库审核员。判断一篇公众号导出正文是否应纳入「投研日度简报」的后续四维度解析。\n",
        # "简报读者需要可核查的宏观与微观信息；请基于材料内容做语义判断，禁止用固定关键词、公众号名称、"
        # "是否出现股票代码、是否外交题材等硬规则代替阅读。\n",
        # "\n【通常无解析价值】硬广/促销/抽奖/招聘/互推、纯导流、无事实信息的活动通知、"
        # "标题与正文严重不符的诱导点击、重复转载且无新增信息、纯情绪口号无事实要素等。\n",
        # "\n【通常有解析价值】包括但不限于：\n",
        # "- 公司、行业、市场数据与事件；政策、监管、宏观数据；\n",
        # "- 地缘政治、国际关系、元首/高层会晤、条约或联合声明等可核查事实"
        # "（可能影响能源、贸易、汇率、风险偏好或板块情绪，应保留）；\n",
        # "- 含明确时间、地点、人物、机构与事件要素的报道或分析观点。\n",
        # "\n【特别注意】仅因题材为外交/时政/国际关系，不得直接判为无价值；"
        # "须看正文是否提供可核查事实或对市场/政策有信息增量。"
        # "纯礼节性贺电且无实质内容者可判无价值。\n",

         "你是金融投研内容审稿员。任务是判断一篇公众号正文是否应进入后续四维度简报生成。\n",
        "判断依据（只依据正文语义，禁用关键词黑名单或公众号名称一刀切）：\n\n",
        "### 值得保留（valuable=True）的特征：\n",
        "- 文章包含可核查事实：公司/行业数据、政策变动、宏观指标、地缘政治事件、高管公开言论、监管文件要点；\n",
        "- 提供有依据的分析观点，且对行业/市场/资产价格有潜在信息增量；\n",
        "- 即使是外交/时政题材，只要包含可能影响贸易、汇率、风险偏好或板块的具体事实，也应当保留。\n\n",
        "### 应当剔除（valuable=False）的特征：\n",
        "- 纯硬广、招聘、抽奖、互推、无事实信息的活动通知；\n",
        "- 标题与正文严重不符的诱导点击；\n",
        "- 只有情绪口号，没有数据、事件或逻辑分析的鸡汤文；\n",
        "- 重复转载且无新增信息的洗稿；\n",
        "- 纯礼节性贺电、祝贺信等无实质内容的信息。\n\n",
        "### 评分标准（star_rating: 1-5）：\n",
        "- ⭐⭐⭐⭐⭐ (5星)：重大宏观政策、地缘政治事件、影响市场的核心数据、重磅行业变革；\n",
        "- ⭐⭐⭐⭐ (4星)：重要公司动态、行业发展趋势、监管政策、市场数据分析；\n",
        "- ⭐⭐ (3星)：一般性新闻报道、常规公告、信息量有限但有参考价值；\n",
        "- ⭐⭐ (2星)：活动通知、纯导流内容、重复转载无新增信息；\n",
        "- ⭐ (1星)：硬广促销、抽奖招聘、情绪口号、标题党诱导点击。\n\n",
    ]
    if vision_line:
        parts.append(f"\n{vision_line}")
    parts.append(
        "\n只依据用户给出的标题与正文（及可能的配图识读摘录），不要做馆外联想或编造。\n"
        "最终回复必须且仅能是一个 JSON 对象，包含以下键：\n"
        "- valuable（布尔）：是否有解析价值\n"
        "- category（短英文标签，如 ad, spam, news, macro_geopolitics, company）\n"
        "- reason_zh（一两句中文理由，供审计）\n"
        "- star_rating（整数 1-5）：文章价值评分\n"
        "- summary（字符串，40字以内）：简短摘要，突出核心价值点\n"
    )
    return "".join(parts)


def assess_article_value(
    article: SourceArticle,
    trace: TraceRecorder | None,
    cfg: OpenAICompatConfig | None,
    *,
    strict_llm: bool = False,
    vision_session: requests.Session | None = None,
    preclean_collector: PrecleanNotesCollector | None = None,
) -> GatedSource:
    """
    价值预筛必须为已配置的大模型；``cfg`` 由聚合任务保证非空。

    关闭 ``GZH_VALUE_GATE_LLM`` 时跳过本步 API，本篇视为有价值。
    """
    if not cfg:
        raise StrictLlmFailure(
            "value_gate",
            "价值预筛需要大模型，但未提供 LLM 配置（请先配置密钥与 BASE）。",
        )

    if not gate_enabled():
        detail = {
            "stem": article.stem,
            "valuable": True,
            "category": "gate_disabled",
            "reason_zh": "已关闭价值预筛（GZH_VALUE_GATE_LLM），本篇直接进入四维度大模型归纳。",
        }
        if trace:
            trace.add_step("value_gate", f"assess:{article.stem}", True, detail)
        return GatedSource(
            article=article,
            valuable=True,
            category="gate_disabled",
            reason_zh=str(detail["reason_zh"]),
            star_rating=4,  # 默认高价值（因为跳过了预筛）
            summary="",     # 无摘要
        )

    excerpt = article.plain_text[: gate_max_chars()]
    if len(article.plain_text) > len(excerpt):
        excerpt += "\n…（正文已截断供预筛）"

    system = value_gate_system_prompt()
    user_text = (
        f"标题：{article.title}\n"
        f"源文件名：{article.stem}\n\n"
        f"===正文===\n{excerpt}"
    )
    user_content = build_value_gate_user_content(
        primary_text=user_text,
        html_for_images=article.body_html,
        session=vision_session,
        trace=trace,
        stem=article.stem,
        llm_cfg=cfg,
        article_title=article.title,
        preclean_collector=preclean_collector,
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]
    http_label = f"{cfg.dimension_http_slug}_value_gate"
    content, err = post_chat_completions(
        cfg,
        messages,
        trace,
        http_label=http_label,
    )
    policy = gate_on_error_policy()

    if content is None:
        if strict_llm:
            msg = f"价值预筛 API 失败（{article.stem}）：{err or 'unknown'}"
            if trace:
                trace.add_step("value_gate", f"assess:{article.stem}", False, {"strict_llm": True}, err=msg)
            raise StrictLlmFailure("value_gate", msg)
        valuable = policy != "discard"
        detail = {
            "stem": article.stem,
            "valuable": valuable,
            "category": "gate_error",
            "reason_zh": f"价值预筛调用失败，按 GZH_VALUE_GATE_ON_ERROR={policy} 处理："
            f"{'保留' if valuable else '剔除'}。错误：{err or 'unknown'}",
            "error": err,
        }
        if trace:
            trace.add_step(
                "value_gate",
                f"assess:{article.stem}",
                True,
                detail,
            )
        return GatedSource(
            article=article,
            valuable=valuable,
            category="gate_error",
            reason_zh=str(detail["reason_zh"]),
            star_rating=3,  # 默认3星
            summary="",     # 空摘要
        )

    try:
        data = parse_llm_json_message(content)
        valuable = bool(data.get("valuable", True))
        category = str(data.get("category", "unknown")).strip() or "unknown"
        reason_zh = str(data.get("reason_zh", "")).strip() or "（模型未给出原因）"
        
        # 解析新增字段：star_rating 和 summary
        star_rating = int(data.get("star_rating", 3))
        star_rating = max(1, min(5, star_rating))  # 限制在 1-5 范围内
        
        summary = str(data.get("summary", "")).strip()
        if len(summary) > 40:
            summary = summary[:40] + "..."  # 截断到 40 字以内
        
        detail = {
            "stem": article.stem,
            "valuable": valuable,
            "category": category,
            "reason_zh": reason_zh,
            "star_rating": star_rating,
            "summary": summary,
        }
        if trace:
            trace.add_step("value_gate", f"assess:{article.stem}", True, detail)
        return GatedSource(
            article=article,
            valuable=valuable,
            category=category,
            reason_zh=reason_zh,
            star_rating=star_rating,
            summary=summary,
        )
    except Exception as e:  # noqa: BLE001 — 审计需要吞掉并留痕
        if strict_llm:
            msg = f"价值预筛 JSON 无效（{article.stem}）：{e}"
            if trace:
                trace.add_step(
                    "value_gate",
                    f"assess:{article.stem}",
                    False,
                    {"strict_llm": True, "raw_excerpt": (content or "")[:2000]},
                    err=msg,
                )
            raise StrictLlmFailure("value_gate", msg) from e
        valuable = policy != "discard"
        detail = {
            "stem": article.stem,
            "valuable": valuable,
            "category": "gate_parse_error",
            "reason_zh": f"价值预筛 JSON 解析失败，按 GZH_VALUE_GATE_ON_ERROR={policy} 处理。"
            f"{'保留' if valuable else '剔除'}。错误：{e}",
            "raw_excerpt": content[:2000],
        }
        if trace:
            trace.add_step("value_gate", f"assess:{article.stem}", True, detail, err=str(e))
        return GatedSource(
            article=article,
            valuable=valuable,
            category="gate_parse_error",
            reason_zh=str(detail["reason_zh"]),
            star_rating=3,  # 默认3星
            summary="",     # 空摘要
        )
