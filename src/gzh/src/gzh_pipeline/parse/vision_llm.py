"""配图：下载导出 HTML 中的图片为 data URI，经独立 VL 模型写出「配图识读」纯文本，再与主模型文本归纳拼接。"""

from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING, Any

import requests
from bs4 import BeautifulSoup

from gzh_pipeline.audit.redact import maybe_truncate_body
from gzh_pipeline.audit.trace import TraceRecorder
from gzh_pipeline.parse.images import IMAGE_SRC_PRIORITY, IMG_ATTR_REMOTE_FOR_VISION

if TYPE_CHECKING:
    from gzh_pipeline.parse.extract import SourceArticle
    from gzh_pipeline.parse.llm_compat import OpenAICompatConfig


class PrecleanNotesCollector:
    """单次聚合任务内按篇收集「配图识读」正文（写入审计 trace），不再写入成品 HTML。"""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._stem_to_notes: dict[str, str] = {}
        self._stem_to_title: dict[str, str] = {}

    def record(self, stem: str, article_title: str, notes: str) -> None:
        t = (notes or "").strip()
        if not t:
            return
        if stem not in self._stem_to_notes:
            self._order.append(stem)
        self._stem_to_notes[stem] = t
        self._stem_to_title[stem] = (article_title or "").strip() or stem

    def items(self) -> list[tuple[str, str, str]]:
        """``(stem, title, notes)``，顺序为首次出现篇目。"""
        return [(s, self._stem_to_title.get(s, s), self._stem_to_notes[s]) for s in self._order if s in self._stem_to_notes]

    def __bool__(self) -> bool:
        return bool(self._stem_to_notes)


def vision_llm_enabled() -> bool:
    """设为 1/true/on 时，尝试识图并参与价值预筛与四维度（须配置 ``GZH_VISION_PRECLEAN_MODEL`` 才实际调用 VL）。"""
    return os.environ.get("GZH_LLM_VISION", "").strip().lower() in ("1", "true", "yes", "on")


def vision_preclean_model_name() -> str:
    return os.environ.get("GZH_VISION_PRECLEAN_MODEL", "").strip()


def vision_preclean_temperature() -> float:
    return float(os.environ.get("GZH_VISION_PRECLEAN_TEMPERATURE", "0.1"))


def vision_preclean_llm_timeout_seconds() -> float:
    return float(os.environ.get("GZH_VISION_PRECLEAN_TIMEOUT_SECONDS", "300"))


def vision_preclean_output_max_chars() -> int:
    return max(500, int(os.environ.get("GZH_VISION_PRECLEAN_MAX_CHARS", "12000")))


def vision_trace_reading_max_bytes(trace: TraceRecorder | None) -> int:
    """写入 .trace.json 的「配图识读」正文上限（字节）；超限由 ``maybe_truncate_body`` 缩略并带哈希。"""
    raw = os.environ.get("GZH_VISION_TRACE_READING_MAX_BYTES", "").strip()
    default = 262_144
    try:
        cap = int(raw) if raw else default
    except ValueError:
        cap = default
    cap = max(4_096, cap)
    if trace is not None:
        cap = min(cap, trace.max_body_bytes)
    return cap


def vision_preclean_merge_payload() -> dict[str, Any]:
    raw = os.environ.get("GZH_VISION_PRECLEAN_EXTRA_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def vision_preclean_system_prompt(*, stem: str, n_images: int) -> str:
    custom = os.environ.get("GZH_VISION_PRECLEAN_SYSTEM", "").strip()
    if custom:
        return custom.replace("{stem}", stem).replace("{n_images}", str(n_images))
    return (
        "你是财经类公众号插图识读助手。用户附上同一篇文章中的多张配图（按文档顺序排列），"
        f"stem={stem}，共{n_images}张。\n请用「### 图1」「### 图2」…编号；每张下列出："
        "可见标题或栏目、图中的关键中文与阿拉伯数字（表格/柱状图刻度等可作概括）；"
        "与上市公司、市场行情、宏观经济相关处可略作一句关联，不得编造图上不存在的信息。"
        "若该图无明显可读正文，对应小节写一行「暂无清晰可读文字」。输出避免冗长，总字数适中。"
    )


def vision_max_per_article() -> int:
    return max(0, int(os.environ.get("GZH_VISION_MAX_IMAGES_PER_ARTICLE", "6")))


def vision_max_image_bytes() -> int:
    return max(32_000, int(os.environ.get("GZH_VISION_MAX_IMAGE_BYTES", str(3 * 1024 * 1024))))


def vision_image_delivery_https_first() -> bool:
    """多模态识图如何把图塞进请求。\n\n- 默认 ``https_first``：``https://`` 外链先试 ``image_url.url = 外链``（不占本机带宽）；VL 报错或空答复时同一篇再打一轮「本机拉取→data-uri」。\n- ``data_uri_only``：与原行为一致，一律本机抓取后以内联编码送出。"""
    raw = os.environ.get("GZH_VISION_IMAGE_DELIVERY", "").strip().lower()
    if raw in ("data_uri_only", "client_fetch_only", "local", "always_fetch"):
        return False
    return True


def vision_fetch_timeout_seconds() -> float:
    return float(os.environ.get("GZH_VISION_FETCH_TIMEOUT_SECONDS", "45"))


def vision_referer() -> str:
    return (os.environ.get("GZH_VISION_REFERER", "https://mp.weixin.qq.com/") or "https://mp.weixin.qq.com/").strip()


def gate_vision_system_line() -> str:
    if not vision_llm_enabled():
        return ""
    return (
        "若正文后附有「配图识读」小节，其内容为独立多模态模型对插图的可读信息摘录；"
        "可与正文一起做价值判断依据，但仍不得编造或做材料外推论。\n"
    )


def dimensions_vision_system_line() -> str:
    if not vision_llm_enabled():
        return ""
    return (
        "若某一篇文章的正文节选后附有「配图识读」，其为多模态模型对插图的可读摘录，"
        "可与该篇正文节选一并视作材料的一部分；严禁臆造，须输出合法四键 JSON。\n"
    )


def _vision_user_agent() -> str:
    return os.environ.get(
        "GZH_VISION_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ).strip()


_IMG_URL_PROBE_ATTRS: tuple[str, ...] = (IMG_ATTR_REMOTE_FOR_VISION,) + IMAGE_SRC_PRIORITY


def is_gif_image_tag(img) -> bool:
    """微信导出常见 ``data-type=\"gif\"`` 动图，多模态识读阶段跳过。"""
    dtype = (img.get("data-type") or "").strip().lower()
    return dtype == "gif"


def list_http_image_urls(html_fragment: str, *, limit: int) -> list[str]:
    """从正文 HTML 中取出用于下载/识图的远程图 URL（跳过 ``data-type=gif`` 动图）。"""
    if limit <= 0 or not (html_fragment or "").strip():
        return []
    soup = BeautifulSoup(html_fragment, "html.parser")
    out: list[str] = []
    for img in soup.find_all("img"):
        if is_gif_image_tag(img):
            continue
        picked = ""
        for attr in _IMG_URL_PROBE_ATTRS:
            v = img.get(attr)
            if not v:
                continue
            s = str(v).strip()
            if not s or s.lower().startswith("data:"):
                continue
            if s.startswith("//"):
                s = "https:" + s
            if s.startswith("http"):
                picked = s
                break
        if not picked:
            continue
        out.append(picked)
        if len(out) >= limit:
            break
    return out


def _normalize_image_mime(ctype: str | None) -> str:
    if not ctype:
        return "image/jpeg"
    main = ctype.split(";")[0].strip().lower()
    if main in ("image/jpg",):
        return "image/jpeg"
    if main.startswith("image/"):
        return main
    return "image/jpeg"


def fetch_image_data_uri(
    url: str,
    session: requests.Session,
    *,
    max_bytes: int,
    referer: str | None,
    timeout: float,
) -> tuple[str | None, str | None]:
    headers = {
        "User-Agent": _vision_user_agent(),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    try:
        with session.get(url, headers=headers, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            ctype = _normalize_image_mime(r.headers.get("Content-Type"))
            chunks: list[bytes] = []
            total = 0
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    return None, f"image too large (>{max_bytes} bytes)"
                chunks.append(chunk)
        raw = b"".join(chunks)
        if not raw:
            return None, "empty body"
        b64 = base64.standard_b64encode(raw).decode("ascii")
        return f"data:{ctype};base64,{b64}", None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def _reading_topic_groups_preclean_audit(
    *,
    stem_label: str,
    urls: list[str],
    per_image_delivery: list[str],
    intro: str,
) -> list[dict[str, Any]]:
    """
    与企业微信 ``reading_topic_groups.json`` **同形的**留痕摘要（顶层数组、元素含
    ``category_group`` / ``intro`` / ``topics``），便于与同目录已有档案对照。
    ``type_ids`` 在此复用为「插图投递策略」语义标签而非业务 type id。
    """
    topics: list[dict[str, Any]] = []
    for i, u in enumerate(urls):
        tag = (
            per_image_delivery[i]
            if i < len(per_image_delivery)
            else "delivery_unknown"
        )
        preview = u[:400] + ("…(截断)" if len(u) > 400 else "")
        topics.append(
            {
                "reading_topic": f"插图{i + 1}",
                "source_type_names": [preview],
                "type_ids": [tag],
            }
        )
    return [
        {
            "category_group": stem_label,
            "intro": intro,
            "topics": topics,
        }
    ]


def _assemble_preclean_image_parts(
    urls: list[str],
    *,
    session: requests.Session,
    opener: str,
    stem_label: str,
    trace: TraceRecorder | None,
    fetch_remote_https: bool,
    max_b: int,
    ref: str,
    tmo_dl: float,
) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
    """
    拼装多模态 user content（含开头的 text opener）。\n
    返回 ``(parts, attachment_tags 与 urls 对齐, 成功附带的原始 url 列表, attached_count)``
    """
    parts: list[dict[str, Any]] = [{"type": "text", "text": opener}]
    tags: list[str] = []
    attached_urls: list[str] = []
    attached = 0
    for i, u in enumerate(urls):
        if fetch_remote_https and u.lower().startswith("https://"):
            parts.append({"type": "image_url", "image_url": {"url": u}})
            tags.append("delivery_https_url")
            attached_urls.append(u)
            attached += 1
            continue
        uri, ferr = fetch_image_data_uri(u, session, max_bytes=max_b, referer=ref, timeout=tmo_dl)
        if uri:
            parts.append({"type": "image_url", "image_url": {"url": uri}})
            tags.append("delivery_client_data_uri")
            attached_urls.append(u)
            attached += 1
        else:
            tags.append("delivery_skipped_fetch_failed")
            if trace:
                trace.add_step(
                    "vision_fetch_preclean_dl",
                    f"{stem_label}:{i}",
                    False,
                    {"url": u[:400]},
                    err=ferr,
                )
    return parts, tags, attached_urls, attached


def _run_preclean_llm_once(
    *,
    urls: list[str],
    session: requests.Session,
    trace: TraceRecorder | None,
    stem_label: str,
    http_slug: str,
    llm_cfg: "OpenAICompatConfig",
    vision_model: str,
) -> tuple[str, int]:
    """每篇文章：一轮含 HTTPS ``image_url`` 直传；失败或空答复时可能对同一篇再打一轮「全员本机抓取→data-uri」。"""
    from gzh_pipeline.parse.llm_compat import (
        post_chat_completions,
        resolve_vision_preclean_openai_compat_cfg,
    )

    vp_cfg = resolve_vision_preclean_openai_compat_cfg(llm_cfg)
    if vp_cfg is None:
        return "", 0
    shared_auth = vp_cfg is llm_cfg

    ref = vision_referer()
    tmo_dl = vision_fetch_timeout_seconds()
    max_b = vision_max_image_bytes()
    headline = vision_preclean_system_prompt(stem=stem_label, n_images=len(urls))
    opener = headline + (
        "\n以下为按导出顺序给出的插图。请按要求输出纯文本识读；不要使用 Markdown 代码块。"
    )
    prefers_https_first = vision_image_delivery_https_first()
    merge_pl = vision_preclean_merge_payload() or None

    passes: list[dict[str, Any]] = []
    extracted = ""
    err: str | None = None

    parts_a, tags_a, attached_urls_a, attached_a = _assemble_preclean_image_parts(
        urls,
        session=session,
        opener=opener,
        stem_label=stem_label,
        trace=trace,
        fetch_remote_https=prefers_https_first,
        max_b=max_b,
        ref=ref,
        tmo_dl=tmo_dl,
    )
    messages_a = [{"role": "user", "content": parts_a}]
    http_label = f"{vp_cfg.dimension_http_slug}_vision_preclean_{http_slug}:{stem_label}"[:280]

    def _inject_pass(
        *,
        label: str,
        attached_cnt: int,
        image_tags: list[str],
        err_v: str | None,
        ok_text: bool,
    ) -> None:
        passes.append(
            {
                "pass": label,
                "attached": attached_cnt,
                "ok_text": ok_text,
                "error": err_v,
                "per_image_delivery": list(image_tags),
            }
        )

    if attached_a == 0:
        return "", 0

    ext_a, err_a = post_chat_completions(
        vp_cfg,
        messages_a,
        trace,
        http_label=http_label,
        temperature=vision_preclean_temperature(),
        model_override=vision_model,
        merge_payload=merge_pl,
        timeout_seconds=vision_preclean_llm_timeout_seconds(),
    )
    text_a = (ext_a or "").strip()
    _inject_pass(
        label="attempt_first",
        attached_cnt=attached_a,
        image_tags=list(tags_a),
        err_v=err_a if err_a else (None if text_a else "empty_vl_reply"),
        ok_text=bool(text_a),
    )

    extracted, err = ext_a or "", err_a
    used_tags = list(tags_a)
    used_attached_urls = list(attached_urls_a)
    used_attached = attached_a

    used_any_remote_https = "delivery_https_url" in tags_a
    should_retry_with_client_fetch_all = prefers_https_first and used_any_remote_https and (
        bool(err_a) or not text_a
    )

    ran_client_data_uri_retry = False
    if should_retry_with_client_fetch_all:
        parts_b, tags_b, attached_urls_b, attached_b = _assemble_preclean_image_parts(
            urls,
            session=session,
            opener=opener,
            stem_label=stem_label,
            trace=trace,
            fetch_remote_https=False,
            max_b=max_b,
            ref=ref,
            tmo_dl=tmo_dl,
        )
        if attached_b > 0:
            messages_b = [{"role": "user", "content": parts_b}]
            ext_b, err_b = post_chat_completions(
                vp_cfg,
                messages_b,
                trace,
                http_label=http_label,
                temperature=vision_preclean_temperature(),
                model_override=vision_model,
                merge_payload=merge_pl,
                timeout_seconds=vision_preclean_llm_timeout_seconds(),
            )
            text_b = (ext_b or "").strip()
            _inject_pass(
                label="attempt_retry_client_data_uri",
                attached_cnt=attached_b,
                image_tags=list(tags_b),
                err_v=err_b if err_b else (None if text_b else "empty_vl_reply"),
                ok_text=bool(text_b),
            )
            extracted, err = ext_b or "", err_b
            used_tags = list(tags_b)
            used_attached_urls = list(attached_urls_b)
            used_attached = attached_b
            ran_client_data_uri_retry = True
            passes[0]["note"] = "首轮以 image_url=https 直传插图；网关或 VL 不可用时自动进入下一 pass。"
            passes[-1]["note"] = "二轮全部为「本机拉取后以 data-uri 送入」。"

    ok = not err and bool((extracted or "").strip())

    intro_bits = [
        (
            "配图识读多模态投递：首轮 "
            + (
                "https 外链优先 + 非 https 仍本机抓取"
                if prefers_https_first
                else "一律本机抓取后以 data-uri 送入"
            )
            + "。"
        )
    ]
    if ran_client_data_uri_retry:
        intro_bits.append("已对同一篇插图执行第二轮「全员 client_data_uri」重试，详见 detail.delivery_passes。")

    audit_groups = _reading_topic_groups_preclean_audit(
        stem_label=stem_label,
        urls=urls,
        per_image_delivery=used_tags,
        intro=" ".join(intro_bits),
    )
    _rtg_semantics = (
        "reading_topic_groups：按企微 JSON 形状留痕「每张插图的 URL 预览 + 投递策略」，"
        "**不是** VL 返回的配图识读正文；正文见 model_output_text / reading_text。"
    )
    _vl_http_audit = {
        "auth": "shared_with_main_llm" if shared_auth else "dedicated_GZH_VISION_PRECLEAN_API_KEY",
        "chat_completions_base": vp_cfg.api_base,
    }

    if err or extracted is None or not ok:
        if trace:
            trace.add_step(
                "vision_preclean",
                stem_label,
                False,
                {
                    "model": vision_model,
                    "http_slug": http_slug,
                    "images_attached": used_attached,
                    "preclean_image_urls": [u[:400] for u in used_attached_urls],
                    "vision_image_delivery_https_first": prefers_https_first,
                    "delivery_passes": passes,
                    "reading_topic_groups": audit_groups,
                    "reading_topic_groups_semantics_zh": _rtg_semantics,
                    "model_output_text": "",
                    "reading_text": "",
                    "vision_llm_http_config": _vl_http_audit,
                },
                err=err or "empty_vl_reply",
            )
        return "", 0
    text = (extracted or "").strip()
    cap_out = vision_preclean_output_max_chars()
    if len(text) > cap_out:
        text = text[:cap_out] + "\n…（配图识读已截断）"
    if trace:
        tb = vision_trace_reading_max_bytes(trace)
        reading_blob = maybe_truncate_body(text, tb)
        trace.add_step(
            "vision_preclean",
            stem_label,
            True,
            {
                "model": vision_model,
                "http_slug": http_slug,
                "images_attached": used_attached,
                "reply_chars": len(text),
                "preclean_image_urls": [u[:400] for u in used_attached_urls],
                "reading_text": reading_blob,
                "model_output_text": reading_blob,
                "reading_trace_max_bytes": tb,
                "vision_image_delivery_https_first": prefers_https_first,
                "delivery_passes": passes,
                "reading_topic_groups": audit_groups,
                "reading_topic_groups_semantics_zh": _rtg_semantics,
                "vision_llm_http_config": _vl_http_audit,
            },
        )
    return text, used_attached


def run_vision_preclean_for_html(
    *,
    html_fragment: str,
    stem: str,
    http_slug: str,
    session: requests.Session | None,
    trace: TraceRecorder | None,
    llm_cfg: "OpenAICompatConfig",
    article_title: str | None = None,
    collector: PrecleanNotesCollector | None = None,
) -> tuple[str, int]:
    vm = vision_preclean_model_name()
    if not vision_llm_enabled() or not vm or session is None:
        return "", 0
    cap = vision_max_per_article()
    urls = list_http_image_urls(html_fragment, limit=cap)
    if not urls:
        return "", 0
    text, n = _run_preclean_llm_once(
        urls=urls,
        session=session,
        trace=trace,
        stem_label=stem,
        http_slug=http_slug,
        llm_cfg=llm_cfg,
        vision_model=vm,
    )
    if text.strip() and collector is not None:
        collector.record(stem, article_title or stem, text)
    return text, n


def build_value_gate_user_content(
    *,
    primary_text: str,
    html_for_images: str,
    session: requests.Session | None,
    trace: TraceRecorder | None,
    stem: str,
    llm_cfg: "OpenAICompatConfig | None" = None,
    article_title: str | None = None,
    preclean_collector: PrecleanNotesCollector | None = None,
) -> str:
    if not vision_llm_enabled() or session is None:
        return primary_text

    vm = vision_preclean_model_name()
    if vm and llm_cfg is not None:
        notes, n_att = run_vision_preclean_for_html(
            html_fragment=html_for_images,
            stem=stem,
            http_slug="gate",
            session=session,
            trace=trace,
            llm_cfg=llm_cfg,
            article_title=article_title,
            collector=preclean_collector,
        )
        if trace and notes and n_att:
            trace.add_step(
                "vision_llm",
                f"value_gate_preclean:{stem}",
                True,
                {"model": vm, "images_attached": n_att},
            )
        if notes.strip():
            return primary_text + "\n\n===配图识读===\n" + notes.strip()

    # 已开「配图」但未设 VL 型号或识图失败：仅送正文，不附 base64 到主模型。
    return primary_text


def build_dimensions_user_content(
    articles: list["SourceArticle"],
    max_chars: int,
    session: requests.Session | None,
    trace: TraceRecorder | None,
    llm_cfg: "OpenAICompatConfig | None" = None,
    preclean_collector: PrecleanNotesCollector | None = None,
) -> str:
    joined_core = "\n\n".join(f"【{a.title}】\n{a.plain_text[:max_chars]}" for a in articles)
    text_only = "请生成上述 JSON。\n\n===合集正文===\n" + joined_core
    if not vision_llm_enabled() or session is None:
        return text_only

    vm = vision_preclean_model_name()
    if not vm or llm_cfg is None:
        return text_only

    chunks: list[str] = []
    preclean_batches = 0
    preclean_imgs = 0
    for a in articles:
        piece_head = f"【{a.title}】\n{a.plain_text[:max_chars]}"
        notes, n_att = run_vision_preclean_for_html(
            html_fragment=getattr(a, "body_html", "") or "",
            stem=getattr(a, "stem", "?"),
            http_slug="dim",
            session=session,
            trace=trace,
            llm_cfg=llm_cfg,
            article_title=getattr(a, "title", None),
            collector=preclean_collector,
        )
        if notes.strip():
            piece = piece_head + "\n\n===配图识读===\n" + notes.strip()
            preclean_batches += 1
            preclean_imgs += n_att
        else:
            piece = piece_head
        chunks.append(piece)
    merged = (
        "请生成上述 JSON。多篇合集按下列块分段；若有「配图识读」小节，"
        "为独立多模态模型对插图的可读摘录，可与同块正文节选一并视作材料。\n\n"
        "===合集正文（按篇）===\n" + "\n\n".join(chunks)
    )
    if trace and preclean_imgs:
        trace.add_step(
            "vision_llm",
            "dimensions_preclean_aggregate",
            True,
            {
                "model": vm,
                "articles": len(articles),
                "preclean_calls": preclean_batches,
                "images_via_preclean": preclean_imgs,
            },
        )
    return merged
