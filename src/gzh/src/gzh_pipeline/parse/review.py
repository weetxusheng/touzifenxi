"""研究员 + 投资经理双视角：对四维度成品做通顺性与投研价值校对，输出修订后的维度 HTML。"""

from __future__ import annotations

import os
from typing import Any

from gzh_pipeline.audit.trace import TraceRecorder
from gzh_pipeline.parse.errors import StrictLlmFailure
from gzh_pipeline.parse.extract import SourceArticle
from gzh_pipeline.parse.llm_compat import OpenAICompatConfig, parse_llm_json_message, post_chat_completions
from gzh_pipeline.util.text import escape_html


def review_enabled() -> bool:
    v = os.environ.get("GZH_DIMENSION_REVIEW_LLM", "").strip().lower()
    if not v:
        return True
    return v not in ("0", "false", "no", "off")


def review_and_refine_dimensions(
    dimensions: dict[str, str],
    articles: list[SourceArticle],
    trace: TraceRecorder | None,
    cfg: OpenAICompatConfig | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """
    ``articles`` 为已通过价值预筛、进入维度解析的篇目。

    未开启复审或无比对篇目时原样返回。

    已开启复审且 ``cfg`` 非空时必须成功调用大模型；失败抛 ``StrictLlmFailure``。
    """
    meta: dict[str, Any] = {"dimension_review_engine": "skipped"}
    source_html = dimensions.get("source", "")

    if not cfg or not articles or not review_enabled():
        reason = "no_llm" if not cfg else ("no_articles" if not articles else "review_disabled")
        meta["dimension_review_skip_reason"] = reason
        if trace:
            trace.add_step(
                "dimension_review",
                "skipped",
                True,
                meta,
            )
        return dimensions, meta

    titles = "\n".join(f"- {a.title}（{a.stem}）" for a in articles)
    system = (
        "你同时扮演卖方研究员与二级市场投资经理审稿人，校对「当日合集四维度简报」是否通顺、"
        "上下文是否自洽、是否对投研读者有信息价值；是否在无依据处做了过度推断。\n"
        "只允许基于用户给出的维度 HTML 与篇目列表做小幅度编辑：删冗余、理顺逻辑、修补明显语病、"
        "标注尚不确定之处；禁止引入新的事实或馆外信息。\n"
        "若认为已足够好，仍须原样返回四段 HTML（可仅改标点或换行）。\n"
        "最终回复必须且仅能是一个合法 JSON 对象，键："
        "facts_html, background_html, impact_html, counterpoints_html, review_notes_zh（简短中文审稿说明）。"
    )
    user = (
        "参与合集的篇目（已通过低价值过滤）：\n"
        f"{titles}\n\n"
        "当前四维度 HTML：\n"
        f"<facts>\n{dimensions.get('facts', '')}\n</facts>\n"
        f"<background>\n{dimensions.get('background', '')}\n</background>\n"
        f"<impact>\n{dimensions.get('impact', '')}\n</impact>\n"
        f"<counterpoints>\n{dimensions.get('counterpoints', '')}\n</counterpoints>\n"
        "请输出审稿后的 JSON。"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    http_label = f"{cfg.dimension_http_slug}_dimension_review"
    content, err = post_chat_completions(cfg, messages, trace, http_label=http_label)

    if content is None:
        msg = err or "empty response"
        meta_fail = {
            "dimension_review_engine": cfg.provider_label,
            "dimension_review_ok": False,
            "dimension_review_error": msg,
        }
        if trace:
            trace.add_step("dimension_review", http_label, False, meta_fail, err=msg)
        raise StrictLlmFailure("dimension_review", f"审稿 API 失败：{msg}")

    try:
        data = parse_llm_json_message(content)
        out = {
            "facts": str(data.get("facts_html", dimensions.get("facts", ""))),
            "background": str(data.get("background_html", dimensions.get("background", ""))),
            "impact": str(data.get("impact_html", dimensions.get("impact", ""))),
            "counterpoints": str(data.get("counterpoints_html", dimensions.get("counterpoints", ""))),
            "source": source_html,
        }
        notes = str(data.get("review_notes_zh", "")).strip()
        meta = {
            "dimension_review_engine": cfg.provider_label,
            "dimension_review_model": cfg.model,
            "dimension_review_ok": True,
            "dimension_review_notes_zh": notes,
            "dimension_prompt_revision": os.environ.get("GZH_PROMPT_REVISION", "1"),
        }
        if trace:
            trace.add_step(
                "dimension_review",
                "refined",
                True,
                {k: v for k, v in meta.items() if k != "dimension_review_notes_zh" or notes},
            )
        return out, meta
    except Exception as e:  # noqa: BLE001
        meta_fail = {
            "dimension_review_engine": cfg.provider_label,
            "dimension_review_ok": False,
            "dimension_review_error": str(e),
        }
        if trace:
            trace.add_step("dimension_review", "parse_error", False, meta_fail, err=str(e))
        raise StrictLlmFailure("dimension_review", f"审稿 JSON 解析失败：{e}") from e


def dimensions_placeholder_single_discarded(
    *,
    title: str,
    stem: str,
    reason_zh: str,
    category: str,
) -> dict[str, str]:
    """单篇被价值预筛剔除时，用于「一篇一成品」模式下的四维度占位。"""
    note = (
        '<p class="dim-note"><strong>价值预筛：</strong>本篇 <code>'
        + escape_html(stem)
        + "</code>（"
        + escape_html(title)
        + "）经大模型判定为无解析价值（<code>"
        + escape_html(category)
        + "</code>），未进入四维度归纳。原因："
        + escape_html(reason_zh)
        + "</p>"
    )
    empty = '<p class="dim-empty">（未生成实质性维度归纳）</p>'
    return {
        "facts": note + empty,
        "background": empty,
        "impact": empty,
        "counterpoints": empty,
    }


def dimensions_placeholder_all_filtered(discarded: list[dict[str, Any]]) -> dict[str, str]:
    """全部篇目被预筛剔除时，四维度区仅展示审计说明。"""
    items = []
    for d in discarded:
        items.append(
            "<li><strong>"
            + escape_html(str(d.get("title", "")))
            + "</strong> (<code>"
            + escape_html(str(d.get("stem", "")))
            + "</code>) — "
            + escape_html(str(d.get("reason_zh", "")))
            + "</li>"
        )
    note = (
        '<p class="dim-note"><strong>价值预筛：</strong>下列篇目经大模型判定为无解析价值，'
        "未进入四维度归纳。</p><ul>"
        + "".join(items)
        + "</ul>"
    )
    empty = '<p class="dim-empty">（未生成实质性维度归纳）</p>'
    return {
        "facts": note + empty,
        "background": empty,
        "impact": empty,
        "counterpoints": empty,
    }
