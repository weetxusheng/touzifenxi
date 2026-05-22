"""四维度：仅 DeepSeek/OpenAI Chat Completions，无规则兜底。"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from gzh_pipeline.audit.trace import TraceRecorder
from gzh_pipeline.parse.errors import StrictLlmFailure
from gzh_pipeline.parse.extract import SourceArticle
from gzh_pipeline.parse.llm_compat import (
    OpenAICompatConfig,
    parse_llm_json_message,
    post_chat_completions,
    resolve_openai_compat_llm,
)
from gzh_pipeline.parse.vision_llm import (
    PrecleanNotesCollector,
    build_dimensions_user_content,
    dimensions_vision_system_line,
    vision_llm_enabled,
    vision_preclean_model_name,
)

# 测试兼容：围栏 JSON 解析别名
_parse_json_from_llm_message = parse_llm_json_message


def _dimension_chars_per_article() -> int:
    return max(500, int(os.environ.get("GZH_DIMENSION_CHARS_PER_ARTICLE", "6000")))


def _dimension_articles_per_llm_call() -> int | None:
    """
    单次四维度请求最多包含的篇数。

    ``None``：不拆批。默认 ``3``，多篇文章时单次请求更小，可降低网关「未返回即断连」概率。

    环境变量设为 ``0`` / ``all`` 则关闭拆批（全部一篇请求里送）。
    """
    raw = os.environ.get("GZH_DIMENSION_ARTICLES_PER_CALL", "3").strip().lower()
    if raw in ("", "0", "all", "none"):
        return None
    return max(1, int(raw))


def _system_prompt_dimensions() -> str:
    base = (
        # "你是资深中文财经编辑，负责撰写「当日多篇文章合集简报」。"
        # "仅依据用户粘贴的合集正文归纳，严禁引入合集外信息或使用想像补全。\n"
        "你是一个严谨的财经简报生成器。输入：一篇公众号文章全文。输出：简报体四维度。"
    )
    vision = dimensions_vision_system_line()
    return (
        base
        + vision
        + 
        # "输出要求是「简报体」：各维度用小标题可用的 HTML 段落/有序或无序列表，句子简洁；"
        # "可标注观点来自哪一篇文章标题；禁止全文逐段照抄或大段复制粘贴原文。\n"
        # "四类维度均需给出：事实、背景、产生的影响、反面观点。若某一维在原文中无明显材料，须在对应 HTML "
        # "中写明暂无并简述理由。\n"

        "格式要求：\n"
        "- 每个字段值为一个字符串，内容是可直接嵌入 <div> 的 HTML（推荐 <ul><li> 结构）。\n"
        "- 严禁大段复制原文，必须用自己的话精炼概括。\n"
        "- 禁止引入文章外的任何事实或数据。\n\n"
        "五个维度的具体规范：\n\n"
        "### 1. facts_html（核心事实）\n"
        "- 提取：时间、主体、事件、数值、结论等可验证信息。\n"
        "- 数量：3～8 条。\n"
        "- 每条长度：30～60 字。\n"
        "- 示例：<li>2025年3月1日，某市二手房指导价覆盖80%小区，指导价比挂牌价低15%。</li>\n\n"
        "### 2. background_html（背景信息）\n"
        "- 与主题相关的历史、政策沿革、行业环境、同类案例等。\n"
        "- 数量：2～5 条。\n"
        "- 每条长度：40～80 字。\n"
        "- 若原文完全没有背景信息，输出：<p>暂无（原文未提供相关背景）</p>\n\n"
        "### 3. impact_html（产生的影响）\n"
        "- 分正面/负面/中性，每条前用 [正面]/[负面]/[中性] 标注。\n"
        "- 数量：2～6 条。\n"
        "- 每条长度：30～70 字。\n"
        "- 示例：<li>[负面] 改善型买家换房成本增加。</li>\n\n"
        "### 4. counterpoints_html（反面观点）\n"
        "- 优先提取原文中明确提到的反对意见、质疑。\n"
        "- 若原文无任何反面观点，则根据逻辑补充一个合理反方视角，末尾标注（推测）。\n"
        "- 数量：1～3 条。\n"
        "- 每条长度：40～80 字。\n\n"
        "### 5. contradictions_html（数据矛盾点）\n"
        "- 原文内部数据前后不一致 / 与公开权威数据冲突 / 结论与支撑数据逻辑跳跃。\n"
        "- 没有则输出：<p>无</p>\n"
        "- 有则每条 50～100 字，用 <li> 列出。\n\n"

        "最终回复必须且仅能是一个合法 JSON 对象（不要 Markdown 废话），包含四个字符串键："
        "facts_html, background_html, impact_html, counterpoints_html，"
        "值为已转义的、可直接嵌入 div 内部的 HTML 片段。"
    )


def _parse_dimension_json_content(content: str) -> dict[str, str]:
    data = parse_llm_json_message(content)
    return {
        "facts": data.get("facts_html", ""),
        "background": data.get("background_html", ""),
        "impact": data.get("impact_html", ""),
        "counterpoints": data.get("counterpoints_html", ""),
    }


def _ensure_usable_dims(dims: dict[str, str], *, where: str) -> None:
    usable = dims and any((dims.get(k) or "").strip() for k in ("facts", "background", "impact", "counterpoints"))
    if not usable:
        raise StrictLlmFailure("dimension_infer", f"{where}：大模型返回的四维度字段均为空，无法写入成品。")


def _call_dimensions_llm(
    articles: list[SourceArticle],
    trace: TraceRecorder | None,
    cfg: OpenAICompatConfig,
    *,
    http_label: str,
    max_chars: int,
    vision_session: requests.Session | None = None,
    preclean_collector: PrecleanNotesCollector | None = None,
) -> dict[str, str]:
    user_content = build_dimensions_user_content(
        articles,
        max_chars,
        vision_session,
        trace,
        llm_cfg=cfg,
        preclean_collector=preclean_collector,
    )
    messages = [
        {"role": "system", "content": _system_prompt_dimensions()},
        {"role": "user", "content": user_content},
    ]
    content, err = post_chat_completions(cfg, messages, trace, http_label=http_label)
    if content is None:
        if trace:
            trace.add_step("dimension_infer", http_label, False, {}, err=err)
        raise StrictLlmFailure("dimension_infer", f"四维度 API 调用失败：{err or 'unknown'}")
    try:
        dims = _parse_dimension_json_content(content)
        _ensure_usable_dims(dims, where="四维度")
        return dims
    except StrictLlmFailure:
        raise
    except Exception as e:  # noqa: BLE001
        if trace:
            trace.add_step("dimension_infer", http_label, False, {}, err=str(e))
        raise StrictLlmFailure("dimension_infer", f"四维度 JSON 解析失败：{e}") from e


def _merge_dimension_batches_llm(
    cfg: OpenAICompatConfig,
    trace: TraceRecorder | None,
    batch_dims: list[tuple[list[SourceArticle], dict[str, str]]],
) -> dict[str, str]:
    """多批「分段四维度」合成一份去重、连贯的合集四维度。"""
    payload: list[dict[str, Any]] = []
    for i, (part_arts, dm) in enumerate(batch_dims, start=1):
        payload.append(
            {
                "segment_index": i,
                "article_titles": [a.title for a in part_arts],
                "facts_html": dm.get("facts", ""),
                "background_html": dm.get("background", ""),
                "impact_html": dm.get("impact", ""),
                "counterpoints_html": dm.get("counterpoints", ""),
            }
        )
    system = (
        "你是资深中文财经编辑。用户给出「同一公众号、同一业务日」下多段文章**分别**归纳的四维度 HTML 草稿（分段 JSON 数组），"
        "请合并为**一份**面向投研读者的当日合集四维度简报：去重合并同类信息、理顺叙述、避免重复小节标题堆叠；"
        "禁止引入各段之外的新事实。\n"
        "最终回复必须且仅能是一个合法 JSON 对象（不要 Markdown），包含四个字符串键："
        "facts_html, background_html, impact_html, counterpoints_html。"
    )
    user = "请合并下列分段结果：\n" + json.dumps(payload, ensure_ascii=False)
    http_label = f"{cfg.dimension_http_slug}_dimension_merge"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    content, err = post_chat_completions(cfg, messages, trace, http_label=http_label)
    if content is None:
        if trace:
            trace.add_step("dimension_infer", http_label, False, {}, err=err)
        raise StrictLlmFailure("dimension_infer", f"四维度合并 API 调用失败：{err or 'unknown'}")
    try:
        dims = _parse_dimension_json_content(content)
        _ensure_usable_dims(dims, where="四维度合并")
        if trace:
            trace.add_step(
                "dimension_infer",
                "llm_merge_batches",
                True,
                {"segments_merged": len(batch_dims), "model": cfg.model},
            )
        return dims
    except StrictLlmFailure:
        raise
    except Exception as e:  # noqa: BLE001
        if trace:
            trace.add_step("dimension_infer", http_label, False, {}, err=str(e))
        raise StrictLlmFailure("dimension_infer", f"四维度合并 JSON 解析失败：{e}") from e


def _infer_dimensions_chat_completions(
    articles: list[SourceArticle],
    trace: TraceRecorder | None,
    vision_session: requests.Session | None = None,
    preclean_collector: PrecleanNotesCollector | None = None,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    cfg = resolve_openai_compat_llm()
    if not cfg:
        raise StrictLlmFailure(
            "dimension_infer",
            "聚合解析仅限大模型，但未解析到可用的 OpenAI 兼容配置（KEY / BASE / 开关）。",
        )

    mn = cfg.model.lower()
    if trace and ("ocr" in mn or mn.endswith("-ocr")) and not vision_llm_enabled():
        trace.add_step(
            "dimension_infer",
            "model_notice",
            True,
            {
                "model": cfg.model,
                "notice": "当前模型名含 OCR，常用于识图；OpenAI 兼容 /chat/completions 文本归纳请使用对话模型（如 deepseek-chat）。"
                " 若频繁出现 Remote end closed / without response，请更换模型。",
            },
        )

    max_chars = _dimension_chars_per_article()
    cap = _dimension_articles_per_llm_call()
    rev = os.environ.get("GZH_PROMPT_REVISION", "1")
    base_meta: dict[str, Any] = {
        "dimension_engine": cfg.provider_label,
        "dimension_model": cfg.model,
        "dimension_prompt_revision": rev,
        "provider": cfg.provider_label.split("/")[0] if "/" in cfg.provider_label else cfg.provider_label,
        "vision_llm": vision_llm_enabled(),
        "vision_preclean_model_configured": bool(vision_preclean_model_name()),
    }
    cov = {
        "facts": "llm",
        "background": "llm",
        "impact": "llm",
        "counterpoint": "llm",
    }

    if cap is None or len(articles) <= cap:
        slug = cfg.dimension_http_slug
        dims = _call_dimensions_llm(
            articles,
            trace,
            cfg,
            http_label=slug,
            max_chars=max_chars,
            vision_session=vision_session,
            preclean_collector=preclean_collector,
        )
        if trace:
            trace.add_step("dimension_infer", "llm_aggregate", True, {**base_meta, "dimension_batches": 1})
        return dims, cov, base_meta

    batch_dims: list[tuple[list[SourceArticle], dict[str, str]]] = []
    for bi in range(0, len(articles), cap):
        chunk = articles[bi : bi + cap]
        label = f"{cfg.dimension_http_slug}_batch{(bi // cap) + 1}"
        d = _call_dimensions_llm(
            chunk,
            trace,
            cfg,
            http_label=label,
            max_chars=max_chars,
            vision_session=vision_session,
            preclean_collector=preclean_collector,
        )
        batch_dims.append((chunk, d))
    if trace:
        trace.add_step(
            "dimension_infer",
            "llm_aggregate_batches_done",
            True,
            {"batches": len(batch_dims), "articles": len(articles), "cap": cap},
        )
    dims = _merge_dimension_batches_llm(cfg, trace, batch_dims)
    meta = {**base_meta, "dimension_batches": len(batch_dims), "dimension_merge": True}
    if trace:
        trace.add_step("dimension_infer", "llm_aggregate", True, meta)
    return dims, cov, meta


def build_dimensions(
    articles: list[SourceArticle],
    trace: TraceRecorder | None,
    *,
    vision_session: requests.Session | None = None,
    preclean_collector: PrecleanNotesCollector | None = None,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    """
    必须使用已配置的大模型生成四维度；失败抛 ``StrictLlmFailure``。

    ``articles`` 必须非空（由上层保证只对「有价值的」合集调用）。
    """
    if not articles:
        raise StrictLlmFailure("dimension_infer", "内部错误：四维度假集不能为空。")
    return _infer_dimensions_chat_completions(articles, trace, vision_session, preclean_collector)
