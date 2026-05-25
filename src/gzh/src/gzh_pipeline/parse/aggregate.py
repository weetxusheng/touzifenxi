"""按 ``(biz_date, 公众号)`` 聚合导出 HTML → 成品 HTML（默认多篇合并一篇，或按环境变量每源一篇）+ 单体 .trace.json。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from gzh_pipeline.audit.redact import maybe_truncate_body
from gzh_pipeline.audit.paths import build_audit_trace_path
from gzh_pipeline.audit.trace import TraceRecorder, write_trace_json
from gzh_pipeline.constants import AGGREGATE_PARSE_VERSION
from gzh_pipeline.parse.dimensions import build_dimensions
from gzh_pipeline.parse.errors import StrictLlmFailure
from gzh_pipeline.parse.extract import SourceArticle, extract_from_export_html, sha256_bytes
from gzh_pipeline.parse.images import IMAGE_SRC_PRIORITY, iframes_in_body, scan_and_rewrite_images
from gzh_pipeline.parse.llm_compat import parse_strict_llm_enabled, resolve_openai_compat_llm
from gzh_pipeline.parse.review import review_and_refine_dimensions
from gzh_pipeline.parse.source_dimension import (
    attach_source_dimension,
    source_dimension_coverage,
)
from gzh_pipeline.parse.value_gate import GatedSource, assess_article_value
from gzh_pipeline.parse.vision_llm import PrecleanNotesCollector, vision_llm_enabled, vision_preclean_model_name
from gzh_pipeline.util.text import biz_date_for_path, biz_date_iso_for_display, escape_html, safe_filename

AUDIT_ENV = "PARSE_AUDIT_JSON_ROOT"
INPUT_ENV = "PARSE_INPUT_ROOT"
OUTPUT_ENV = "PARSE_OUTPUT_ROOT"


def _per_source_idempotent_skip_ok(prev_one: dict[str, Any], dest_html: Path) -> bool:
    """上次已判无价值时须重跑预筛；仅在上次产出有价值成品且文件仍在时跳过。"""
    if prev_one.get("valuable") is False:
        return False
    return dest_html.is_file()


def _remove_per_source_product_files(dest_html: Path, st_path: Path) -> None:
    if dest_html.is_file():
        dest_html.unlink()
    if st_path.is_file():
        st_path.unlink()


def _write_per_source_discard_state(
    st_path: Path,
    *,
    article: SourceArticle,
    run_id: str,
    gated: GatedSource,
) -> None:
    st_path.write_text(
        json.dumps(
            {
                "input_sha256": article.sha256_hex,
                "parse_version": AGGREGATE_PARSE_VERSION,
                "run_id": run_id,
                "mode": "one_html_per_source",
                "valuable": False,
                "value_gate_category": gated.category,
                "value_gate_reason_zh": gated.reason_zh,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def aggregate_one_html_per_source_enabled() -> bool:
    """为真时：``日期/公众号`` 下每个源 ``.html`` 各生成一份成品 ``{stem}.html``，不再合并为单一 ``output_stem``。"""
    return os.environ.get("GZH_AGGREGATE_ONE_HTML_PER_SOURCE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_progress_account = threading.local()


def set_progress_account(account: str | None) -> None:
    """并行解析时在线程内设置公众号名，供 ``_stderr_progress`` 打前缀。"""
    if account:
        _progress_account.name = account
    elif hasattr(_progress_account, "name"):
        del _progress_account.name


def _stderr_progress(msg: str) -> None:
    if os.environ.get("GZH_PARSE_PROGRESS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    acct = getattr(_progress_account, "name", None)
    head = f"[{acct}] " if acct else ""
    print(f"[gzh-parse] {head}{msg}", file=sys.stderr, flush=True)


def _state_path(out_dir: Path, stem: str) -> Path:
    return out_dir / f".{stem}.aggregate.state.json"


def _load_state(p: Path) -> dict[str, Any] | None:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# 版式参考企业微信侧 brief_llm_adaptive：CSS 变量 + factor-section / factor-box（节选必要规则）
_AGGREGATE_BRIEF_CSS = """    :root{color-scheme:light;--text:#071832;--muted:#5b677a;--line:#d8dee8;--accent:#8fa0b7;--box:#fbfcfe;--box-line:#dfe5ee;--link:#235a9f;}
    *{box-sizing:border-box} html{scroll-behavior:smooth;}
    body{font-family:Arial,'Microsoft YaHei','PingFang SC',sans-serif;max-width:900px;margin:26px auto 40px;padding:0 18px;line-height:1.8;color:var(--text);background:#fff;font-size:16px;}
    h1{font-size:28px;line-height:1.25;margin:0 0 22px;font-weight:800;}
    .brief-entry{margin:0;}
    .factor-section{margin:18px 0;}
    .factor-title{font-size:18px;line-height:1.35;margin:0 0 10px;padding-left:12px;border-left:3px solid var(--accent);font-weight:800;letter-spacing:0;}
    .factor-box{border:1px solid var(--box-line);border-radius:7px;background:var(--box);padding:14px 18px;}
    .factor-box :where(p,ul,ol,li,div){color:var(--text);}
    .factor-box ul,.factor-box ol{padding-left:1.25em;margin:0;}
    .factor-box li+li{margin-top:8px;}
    .dim-note{color:var(--muted);font-size:15px;}
    .dim-empty{color:var(--muted);}
    .source-links-list{margin:8px 0 0;padding-left:1.35em;line-height:1.75;}
    .source-links-list a,.source-links a.source-original-link{color:var(--accent);word-break:break-all;}
    .source-article-title{font-weight:600;}
    a{color:var(--link);text-decoration:none;border-bottom:1px solid rgba(35,90,159,.25);} a:hover{border-bottom-color:currentColor;}
    @media (max-width:640px){body{padding:0 14px;font-size:15px}.factor-title{font-size:17px}.factor-box{padding:12px 14px}}
"""


def _vision_preclean_audit_max_bytes(trace: TraceRecorder) -> int:
    """汇入 job summary / trace 顶层 summary 的单篇配图识读正文上限。"""
    raw = os.environ.get("GZH_VISION_AUDIT_SUMMARY_READING_MAX_BYTES", "").strip()
    default = 262_144
    try:
        cap = int(raw) if raw else default
    except ValueError:
        cap = default
    cap = max(4_096, cap)
    return min(cap, trace.max_body_bytes)


def _vision_preclean_readings_audit_rows(
    collector: PrecleanNotesCollector | None,
    trace: TraceRecorder,
) -> list[dict[str, Any]] | None:
    """
    将「送入归纳前的配图识读」写入审计 summary：``stem`` / ``title`` / ``vision_preclean_model`` /
    ``reading_notes``（超长则 ``maybe_truncate_body``）。
    """
    if not collector or not collector.items():
        return None
    vm = vision_preclean_model_name()
    lim = _vision_preclean_audit_max_bytes(trace)
    rows: list[dict[str, Any]] = []
    for stem, title, notes in collector.items():
        rows.append(
            {
                "stem": stem,
                "title": title,
                "vision_preclean_model": vm,
                "reading_notes": maybe_truncate_body(notes, lim),
                "reading_char_count": len(notes),
            }
        )
    return rows


def _vision_preclean_audit_row_for_stem(
    collector: PrecleanNotesCollector | None,
    trace: TraceRecorder,
    stem: str,
) -> dict[str, Any] | None:
    """单篇配图识读留痕结构（用于逐篇模式下 ``per_source_outputs``）。"""
    if not collector:
        return None
    for s, title, notes in collector.items():
        if s != stem:
            continue
        lim = _vision_preclean_audit_max_bytes(trace)
        return {
            "stem": stem,
            "title": title,
            "vision_preclean_model": vision_preclean_model_name(),
            "reading_notes": maybe_truncate_body(notes, lim),
            "reading_char_count": len(notes),
        }
    return None


def _new_preclean_collector() -> PrecleanNotesCollector | None:
    if vision_llm_enabled() and vision_preclean_model_name():
        return PrecleanNotesCollector()
    return None


def build_aggregate_html(
    biz_date_path: str,
    account: str,
    dimensions: dict[str, str],
    parse_version: str,
    *,
    source_article_title: str | None = None,
    source_url: str | None = None,
) -> str:
    """
    成品页：最简文档壳 + 「来源」（原文地址，可点击）+ 四段大模型 HTML。
    「配图识读」VL 正文仅写入 trace/summary，不写入成品 HTML。
    ``source_article_title``：一篇一文件模式下写入标题级副标（仍不含源路径）。
    ``source_url``：原始文章 URL，用于在"来源"模块中显示为可点击按钮。
    """
    biz_label = biz_date_iso_for_display(biz_date_path)
    base = f"{escape_html(account)} · {escape_html(biz_label)}（{escape_html(biz_date_path)}）"
    if source_article_title and source_article_title.strip():
        h1_inner = base + " · " + escape_html(source_article_title.strip())
    else:
        h1_inner = base

    dim_blocks: list[tuple[str, str, str]] = [
        ("source", "event-dimension-source", "来源"),
        ("facts", "event-dimension-facts", "事实"),
        ("background", "event-dimension-background", "背景"),
        ("impact", "event-dimension-impact", "产生的影响"),
        ("counterpoints", "event-dimension-contradictions", "反面观点 / 数据矛盾点"),
    ]
    
    # 构建四维度内容
    body_parts: list[str] = [
        '<article class="brief-entry gzh-collection-brief">\n',
    ]
    for key, section_cls, title_zh in dim_blocks:
        frag = dimensions.get(key) or ""
        
        # 如果是"来源"模块且有原始 URL，添加跳转按钮
        if key == "source" and source_url and source_url.strip():
            safe_url = escape_html(source_url)
            button_html = (
                f'\n<div style="margin-top: 12px;">'
                f'  <a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
                f'     style="display: inline-flex; align-items: center; gap: 6px; '
                f'            padding: 8px 16px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); '
                f'            color: white; border-radius: 6px; text-decoration: none; '
                f'            font-weight: 500; font-size: 14px; '
                f'            box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3); '
                f'            transition: all 0.2s ease;" '
                f'     onmouseover="this.style.transform=\'translateY(-1px)\'; this.style.boxShadow=\'0 4px 8px rgba(102, 126, 234, 0.4)\';" '
                f'     onmouseout="this.style.transform=\'translateY(0)\'; this.style.boxShadow=\'0 2px 4px rgba(102, 126, 234, 0.3)\';">'
                f'     查看原始文章'
                f'  </a>'
                f'</div>\n'
            )
            frag = frag + button_html
        
        body_parts.append(
            f'<section class="factor-section event-dimension-section {section_cls}" id="dim-{key}">\n'
            f'<h3 class="factor-title">{escape_html(title_zh)}</h3>\n'
            f'<div class="factor-box">{frag}</div>\n'
            "</section>\n"
        )
    body_parts.append("</article>\n")
    
    # 单栏模式
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "  <style>\n"
        + _AGGREGATE_BRIEF_CSS
        + "  </style>\n"
        f"  <title>{escape_html(account)} {escape_html(biz_label)} 简报</title>\n"
        f'  <meta name="generator" content="gzh-pipeline {escape_html(parse_version)}">\n'
        "</head>\n"
        "<body>\n"
        f"<h1>{h1_inner}</h1>\n"
        + "".join(body_parts)
        + "</body>\n</html>\n"
    )


def _build_dual_pane_html(
    h1_inner: str,
    parsed_content: str,
    source_url: str,
    parse_version: str,
) -> str:
    """
    已废弃：双栏布局函数（因微信公众号等网站拒绝 iframe 嵌入）。
    保留此函数仅为向后兼容，实际不再使用。
    """
    # 此函数已不再使用，保留仅为兼容性
    pass


def run_aggregate_job(
    biz_date: str,
    account: str,
    input_root: Path,
    output_root: Path,
    audit_root: Path,
    output_stem: str,
    *,
    force: bool = False,  # API 脚本可 False；CLI 默认传 True，每次重写 summary。
    image_mode: str | None = None,
    max_audit_body_bytes: int | None = None,
    max_image_bytes: int = 6_000_000,
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    account_safe = safe_filename(account)
    biz_date_path = biz_date_for_path(biz_date)
    max_body = max_audit_body_bytes or int(os.environ.get("AUDIT_STEP_BODY_MAX_BYTES", "2000000"))

    src_dir = input_root / biz_date_path / account_safe
    out_dir = output_root / biz_date_path / account_safe
    out_dir.mkdir(parents=True, exist_ok=True)

    trace = TraceRecorder(max_body_bytes=max_body)
    trace.add_step("job_start", "aggregate_parse", True, {"biz_date": biz_date_path, "account": account, "src_dir": str(src_dir)})

    t0 = datetime.now(timezone.utc).isoformat()
    img_mode = (image_mode or os.environ.get("PARSE_IMAGE_MODE", "remote")).strip().lower()
    if img_mode not in ("remote", "mirror"):
        img_mode = "remote"

    assets_dir = out_dir / os.environ.get("PARSE_ASSETS_SUBDIR", "assets")
    http_session = requests.Session()

    if not src_dir.is_dir():
        summary = _failed_summary(
            run_id,
            "skipped",
            biz_date_path,
            account,
            account_safe,
            None,
            [],
            0,
            f"源目录不存在: {src_dir}",
            t0,
        )
        trace.add_step("file_read", "list_source_dir", False, {"path": str(src_dir)}, err="not_a_directory")
        _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
        return summary

    html_files = sorted(src_dir.glob("*.html"))
    articles: list[SourceArticle] = []
    partial_sources: list[dict[str, str]] = []

    for fp in html_files:
        try:
            raw = fp.read_text(encoding="utf-8")
            h = sha256_bytes(raw.encode("utf-8"))
            ar = extract_from_export_html(raw, fp)
            ar.sha256_hex = h
            articles.append(ar)
            trace.add_step(
                "file_read",
                f"read:{fp.name}",
                True,
                {"path": str(fp), "byte_length": len(raw.encode("utf-8")), "sha256_hex": h},
            )
        except OSError as e:
            partial_sources.append({"path": str(fp), "reason": str(e)})
            trace.add_step("file_read", f"read:{fp.name}", False, {"path": str(fp)}, err=str(e))

    if not articles:
        summary = _failed_summary(
            run_id,
            "skipped",
            biz_date_path,
            account,
            account_safe,
            None,
            [str(p) for p in html_files],
            0,
            "目录下无可用 .html",
            t0,
            partial=partial_sources,
        )
        trace.add_step("aggregate_write", "no_sources", False, {}, err="empty")
        _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
        return summary

    bundle_hasher = hashlib.sha256()
    for ar in sorted(articles, key=lambda x: x.path.name):
        bundle_hasher.update(ar.sha256_hex.encode("ascii"))
        bundle_hasher.update(b"\n")
    input_bundle_sha256 = bundle_hasher.hexdigest()

    # 一篇一成品：按单文件指纹跳过；合并模式仍按「目录指纹」整包跳过。
    if not aggregate_one_html_per_source_enabled():
        state_path = _state_path(out_dir, output_stem)
        prev = _load_state(state_path)
        if not force and prev and prev.get("input_bundle_sha256") == input_bundle_sha256 and prev.get("parse_version") == AGGREGATE_PARSE_VERSION:
            out_html = out_dir / f"{output_stem}.html"
            if out_html.is_file():
                summary = {
                    "run_id": run_id,
                    "status": "skipped",
                    "reason": "idempotent_same_bundle",
                    "parse_version": AGGREGATE_PARSE_VERSION,
                    "account": account,
                    "account_dir": account_safe,
                    "biz_date": biz_date_path,
                    "output_path": str(out_html),
                    "aggregate_mode": "bundle",
                    "source_paths": [str(a.path) for a in articles],
                    "source_count": len(articles),
                    "input_bundle_sha256": input_bundle_sha256,
                    "previous_run_id": prev.get("run_id"),
                }
                trace.add_step(
                    "idempotent_skip",
                    "same_bundle",
                    True,
                    {"input_bundle_sha256": input_bundle_sha256, "existing": str(out_html)},
                )
                _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
                return summary

    n_iframe = sum(iframes_in_body(a.body_html) for a in articles)
    if n_iframe and trace:
        trace.add_step(
            "parse_partial",
            "iframe_notice",
            True,
            {"iframe_tags_total": n_iframe, "note": "iframe 内图片需按原文链接在微信侧核对。"},
        )

    processed: list[SourceArticle] = []
    all_img_records: list[dict[str, Any]] = []
    all_img_failures: list[dict[str, Any]] = []
    idx = 1
    _stderr_progress(f"正文内插图处理（PARSE_IMAGE_MODE={img_mode}），共 {len(articles)} 篇导出…")
    for ar in articles:
        adir = assets_dir if img_mode == "mirror" else None
        new_body, recs, fails, idx = scan_and_rewrite_images(
            ar.body_html,
            source_stem=ar.stem,
            assets_dir=adir,
            image_mode=img_mode,
            trace=trace,
            http_session=http_session,
            max_image_bytes=max_image_bytes,
            image_index_start=idx,
        )
        all_img_records.extend(recs)
        all_img_failures.extend(fails)
        processed.append(
            SourceArticle(
                path=ar.path,
                stem=ar.stem,
                title=ar.title,
                source_url=ar.source_url,
                body_html=new_body,
                sha256_hex=ar.sha256_hex,
                plain_text=ar.plain_text,
            )
        )

    llm_cfg = resolve_openai_compat_llm()
    gate_strict_env = parse_strict_llm_enabled()

    try:
        if not llm_cfg:
            raise StrictLlmFailure(
                "job_config",
                "聚合解析仅限大模型：请配置 DEEPSEEK_API_KEY / ALIYUN_DEEPSEEK_API_KEY "
                "并设置可用的 DEEPSEEK_API_BASE、DEEPSEEK_MODEL（或改用 OPENAI_API_KEY "
                "且设 GZH_DIMENSION_LLM=1）；且勿用 GZH_USE_DEEPSEEK=0 误关 DeepSeek。\n"
                "若已在项目根目录的 .env 中填写密钥但仍失败：请执行 pip install python-dotenv，"
                "并尽量用「python -m gzh_pipeline.cli.parse」或自带批处理运行（程序会沿源码路径向上查找 .env）。",
            )

        if vision_llm_enabled():
            _stderr_progress(
                "GZH_LLM_VISION 已启用：配图先走识图模型写「配图识读」，再以纯文本与正文一并送入归纳模型；拉图可能较慢。"
            )

        if aggregate_one_html_per_source_enabled():
            _stderr_progress(
                "GZH_AGGREGATE_ONE_HTML_PER_SOURCE：每篇导出各生成一份「stem.html」，不再合并为单个 output_stem 文件。"
            )
            output_paths: list[str] = []
            per_source_rows: list[dict[str, Any]] = []
            valuable_n = 0
            skipped_unchanged = 0
            rerun_count = 0
            merged_meta_last: dict[str, Any] = {"dimension_prompt_revision": os.environ.get("GZH_PROMPT_REVISION", "1")}
            dim_cov_last: dict[str, str] = {}

            for idx, ar in enumerate(processed, start=1):
                safe_stem = safe_filename(ar.stem)
                dest_html = out_dir / f"{safe_stem}.html"
                st_path = _state_path(out_dir, safe_stem)
                prev_one = _load_state(st_path)
                if (
                    not force
                    and prev_one
                    and prev_one.get("input_sha256") == ar.sha256_hex
                    and prev_one.get("parse_version") == AGGREGATE_PARSE_VERSION
                    and _per_source_idempotent_skip_ok(prev_one, dest_html)
                ):
                    skipped_unchanged += 1
                    trace.add_step(
                        "idempotent_skip",
                        f"per_source:{safe_stem}",
                        True,
                        {"input_sha256": ar.sha256_hex, "existing": str(dest_html)},
                    )
                    output_paths.append(str(dest_html))
                    continue

                rerun_count += 1
                preclean_collector = _new_preclean_collector()
                _stderr_progress(f"[逐篇] 价值预筛 {idx}/{len(processed)}：{ar.stem}")
                g = assess_article_value(
                    ar,
                    trace,
                    llm_cfg,
                    strict_llm=gate_strict_env,
                    vision_session=http_session,
                    preclean_collector=preclean_collector,
                )
                if not g.valuable:
                    _remove_per_source_product_files(dest_html, st_path)
                    _write_per_source_discard_state(st_path, article=ar, run_id=run_id, gated=g)
                    trace.add_step(
                        "value_gate_skip_output",
                        f"no_product_html:{safe_stem}",
                        True,
                        {
                            "stem": safe_stem,
                            "valuable": False,
                            "category": g.category,
                            "reason_zh": g.reason_zh,
                        },
                    )
                    row_out: dict[str, Any] = {
                        "stem": safe_stem,
                        "valuable": False,
                        "path": None,
                        "dimension_engine": "skipped_filtered",
                        "value_gate_category": g.category,
                        "value_gate_reason_zh": g.reason_zh,
                        "star_rating": g.star_rating,
                        "summary": g.summary,
                    }
                    vread = _vision_preclean_audit_row_for_stem(preclean_collector, trace, ar.stem)
                    if vread:
                        row_out["vision_preclean_reading"] = vread
                    per_source_rows.append(row_out)
                    _stderr_progress(f"[逐篇] 无价值，跳过成品 HTML：{ar.stem}")
                    continue
                else:
                    valuable_n += 1
                    _stderr_progress(f"[逐篇] 四维度 · {ar.stem}")
                    dim_html, dim_cov, dim_meta = build_dimensions(
                        [g.article],
                        trace,
                        vision_session=http_session,
                        preclean_collector=preclean_collector,
                    )
                    _stderr_progress(f"[逐篇] 维度审稿 · {ar.stem}")
                    dim_html = attach_source_dimension(dim_html, [g.article])
                    dim_cov["source"] = source_dimension_coverage([g.article])
                    trace.add_step(
                        "source_dimension",
                        f"extract:{safe_stem}",
                        bool(g.article.source_url),
                        {
                            "stem": safe_stem,
                            "source_url": g.article.source_url or None,
                            "coverage": dim_cov["source"],
                        },
                    )
                    dim_html, review_meta = review_and_refine_dimensions(
                        dim_html,
                        [g.article],
                        trace,
                        llm_cfg,
                    )

                merged_meta_last = {**dim_meta, **review_meta}
                dim_cov_last = dim_cov
                dim_engine = str(merged_meta_last.get("dimension_engine") or "unknown")

                page = build_aggregate_html(
                    biz_date_path,
                    account,
                    dim_html,
                    AGGREGATE_PARSE_VERSION,
                    source_article_title=ar.title,
                    source_url=ar.source_url or None,
                )
                dest_html.write_text(page, encoding="utf-8")
                trace.add_step(
                    "aggregate_write",
                    f"write_product_html:{safe_stem}",
                    True,
                    {
                        "path": str(dest_html),
                        "byte_length": len(page.encode("utf-8")),
                        "stem": safe_stem,
                        "valuable": bool(g.valuable),
                    },
                )
                st_path.write_text(
                    json.dumps(
                        {
                            "input_sha256": ar.sha256_hex,
                            "parse_version": AGGREGATE_PARSE_VERSION,
                            "run_id": run_id,
                            "mode": "one_html_per_source",
                            "valuable": True,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                output_paths.append(str(dest_html))
                row_out: dict[str, Any] = {
                    "stem": safe_stem,
                    "valuable": g.valuable,
                    "path": str(dest_html),
                    "dimension_engine": dim_engine,
                    "value_gate_category": g.category,
                    "value_gate_reason_zh": g.reason_zh,
                    "star_rating": g.star_rating,
                    "summary": g.summary,
                }
                vread = _vision_preclean_audit_row_for_stem(preclean_collector, trace, ar.stem)
                if vread:
                    row_out["vision_preclean_reading"] = vread
                per_source_rows.append(row_out)

            discarded_n = rerun_count - valuable_n

            vision_reads_summary = [
                r["vision_preclean_reading"]
                for r in per_source_rows
                if isinstance(r.get("vision_preclean_reading"), dict)
            ]

            summary = {
                "run_id": run_id,
                "status": "success",
                "parse_version": AGGREGATE_PARSE_VERSION,
                "account": account,
                "account_dir": account_safe,
                "biz_date": biz_date_path,
                "aggregate_mode": "one_html_per_source",
                "output_paths": output_paths,
                "output_path": output_paths[0] if output_paths else str(out_dir),
                "source_paths": [str(a.path) for a in articles],
                "source_count": len(articles),
                "input_bundle_sha256": input_bundle_sha256,
                "partial_sources": partial_sources or None,
                "dimensions_coverage": dim_cov_last,
                "image_mode": img_mode,
                "image_records_count": len(all_img_records),
                "image_failures": all_img_failures or None,
                "image_src_priority": list(IMAGE_SRC_PRIORITY),
                "started_at": t0,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                **merged_meta_last,
                "dimension_engine": str(merged_meta_last.get("dimension_engine") or "unknown"),
                "valuable_source_count": valuable_n,
                "discarded_source_count": discarded_n,
                "per_source_rerun_count": rerun_count,
                "per_source_idempotent_skips": skipped_unchanged,
                "per_source_outputs": per_source_rows,
                "llm_openai_compat_provider": llm_cfg.provider_label,
                "strict_llm": gate_strict_env,
            }
            if vision_reads_summary:
                summary["vision_preclean_readings"] = vision_reads_summary
            trace.add_step(
                "job_end",
                "per_source_ok",
                True,
                {
                    "outputs": len(output_paths),
                    "valuable": valuable_n,
                    "discarded": discarded_n,
                    "rerun": rerun_count,
                    "idempotent_skipped": skipped_unchanged,
                },
            )
            _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
            _stderr_progress(
                f"逐篇解析完成：有价值成品 {len(output_paths)} 个 HTML，"
                f"无价值跳过 {discarded_n} 篇（不生成成品 HTML）。"
            )
            return summary

        preclean_collector = _new_preclean_collector()

        gated: list[GatedSource] = []
        for i, ar in enumerate(processed, start=1):
            _stderr_progress(f"价值预筛（大模型）{i}/{len(processed)}：{ar.stem}")
            gated.append(
                assess_article_value(
                    ar,
                    trace,
                    llm_cfg,
                    strict_llm=gate_strict_env,
                    vision_session=http_session,
                    preclean_collector=preclean_collector,
                )
            )
        valuable_gated = [g for g in gated if g.valuable]
        discarded_rows = [
            {
                "stem": g.article.stem,
                "title": g.article.title,
                "reason_zh": g.reason_zh,
                "category": g.category,
            }
            for g in gated
            if not g.valuable
        ]

        if not valuable_gated:
            _stderr_progress("价值预筛无保留篇：跳过四维度与汇总成品 HTML。")
            dim_cov = {
                "facts": "all_filtered",
                "background": "all_filtered",
                "impact": "all_filtered",
                "counterpoint": "all_filtered",
            }
            dim_meta = {
                "dimension_engine": "skipped_all_filtered",
                "dimension_prompt_revision": os.environ.get("GZH_PROMPT_REVISION", "1"),
            }
            review_meta = {"dimension_review_engine": "skipped"}
            merged_meta = {**dim_meta, **review_meta}
            trace.add_step(
                "dimension_infer",
                "skipped_all_low_value",
                True,
                {"discarded_count": len(discarded_rows), "records": discarded_rows},
            )
            trace.add_step(
                "aggregate_write",
                "skipped_no_valuable_sources",
                True,
                {"discarded_count": len(discarded_rows)},
            )
            html_out = out_dir / f"{output_stem}.html"
            if html_out.is_file():
                html_out.unlink()
            bundle_state_path = _state_path(out_dir, output_stem)
            if bundle_state_path.is_file():
                bundle_state_path.unlink()
            summary = {
                "run_id": run_id,
                "status": "success",
                "parse_version": AGGREGATE_PARSE_VERSION,
                "account": account,
                "account_dir": account_safe,
                "biz_date": biz_date_path,
                "aggregate_mode": "bundle",
                "output_path": None,
                "source_paths": [str(a.path) for a in articles],
                "source_count": len(articles),
                "input_bundle_sha256": input_bundle_sha256,
                "partial_sources": partial_sources or None,
                "dimensions_coverage": dim_cov,
                "image_mode": img_mode,
                "image_records_count": len(all_img_records),
                "image_failures": all_img_failures or None,
                "image_src_priority": list(IMAGE_SRC_PRIORITY),
                "started_at": t0,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                **merged_meta,
                "dimension_engine": str(merged_meta.get("dimension_engine") or "unknown"),
                "valuable_source_count": 0,
                "discarded_source_count": len(discarded_rows),
                "value_gate_discarded": discarded_rows or None,
                "llm_openai_compat_provider": llm_cfg.provider_label,
                "strict_llm": gate_strict_env,
            }
            vbundle = _vision_preclean_readings_audit_rows(preclean_collector, trace)
            if vbundle:
                summary["vision_preclean_readings"] = vbundle
            _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
            _stderr_progress("解析完成：无有价值篇目，未生成汇总 HTML。")
            return summary
        else:
            _stderr_progress(f"四维度简报（大模型），送入 {len(valuable_gated)} 篇（篇数多时可能分批调用与合并）…")
            dim_html, dim_cov, dim_meta = build_dimensions(
                [g.article for g in valuable_gated],
                trace,
                vision_session=http_session,
                preclean_collector=preclean_collector,
            )
            src_articles = [g.article for g in valuable_gated]
            dim_html = attach_source_dimension(dim_html, src_articles)
            dim_cov = {**dim_cov, "source": source_dimension_coverage(src_articles)}
            trace.add_step(
                "source_dimension",
                "extract_bundle",
                any((a.source_url or "").strip() for a in src_articles),
                {
                    "article_count": len(src_articles),
                    "urls": [a.source_url for a in src_articles if (a.source_url or "").strip()][:30],
                    "coverage": dim_cov.get("source"),
                },
            )

        _stderr_progress("维度审稿（大模型）…")
        dim_html, review_meta = review_and_refine_dimensions(
            dim_html,
            [g.article for g in valuable_gated],
            trace,
            llm_cfg,
        )
        merged_meta = {**dim_meta, **review_meta}
        dim_engine = str(merged_meta.get("dimension_engine") or "unknown")

        html_out = out_dir / f"{output_stem}.html"
        _stderr_progress(f"写入汇总页 {output_stem}.html …")
        page = build_aggregate_html(
            biz_date_path,
            account,
            dim_html,
            AGGREGATE_PARSE_VERSION,
        )
        html_out.write_text(page, encoding="utf-8")
        trace.add_step(
            "aggregate_write",
            "write_product_html",
            True,
            {"path": str(html_out), "byte_length": len(page.encode("utf-8"))},
        )

        bundle_state_path = _state_path(out_dir, output_stem)
        bundle_state_path.write_text(
            json.dumps(
                {
                    "input_bundle_sha256": input_bundle_sha256,
                    "parse_version": AGGREGATE_PARSE_VERSION,
                    "run_id": run_id,
                    "output_stem": output_stem,
                    "aggregate_mode": "bundle",
                    "valuable_source_count": len(valuable_gated),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        summary = {
            "run_id": run_id,
            "status": "success",
            "parse_version": AGGREGATE_PARSE_VERSION,
            "account": account,
            "account_dir": account_safe,
            "biz_date": biz_date_path,
            "aggregate_mode": "bundle",
            "output_path": str(html_out),
            "source_paths": [str(a.path) for a in articles],
            "source_count": len(articles),
            "input_bundle_sha256": input_bundle_sha256,
            "partial_sources": partial_sources or None,
            "dimensions_coverage": dim_cov,
            "image_mode": img_mode,
            "image_records_count": len(all_img_records),
            "image_failures": all_img_failures or None,
            "image_src_priority": list(IMAGE_SRC_PRIORITY),
            "started_at": t0,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **merged_meta,
            "dimension_engine": dim_engine,
            "valuable_source_count": len(valuable_gated),
            "discarded_source_count": len(discarded_rows),
            "value_gate_discarded": discarded_rows or None,
            "llm_openai_compat_provider": llm_cfg.provider_label,
            "strict_llm": gate_strict_env,
        }
        vbundle = _vision_preclean_readings_audit_rows(preclean_collector, trace)
        if vbundle:
            summary["vision_preclean_readings"] = vbundle
        _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
        _stderr_progress(f"解析完成：{html_out}")
        return summary

    except StrictLlmFailure as e:
        summary = _failed_summary(
            run_id,
            "failed",
            biz_date_path,
            account,
            account_safe,
            None,
            [str(a.path) for a in articles],
            len(articles),
            e.message,
            t0,
            partial=partial_sources or None,
        )
        summary["strict_llm"] = gate_strict_env
        summary["strict_llm_step"] = e.step
        summary["input_bundle_sha256"] = input_bundle_sha256
        summary["llm_openai_compat_provider"] = llm_cfg.provider_label if llm_cfg else None
        trace.add_step(
            "job_failed",
            e.step,
            False,
            {"strict_llm_gate": gate_strict_env, "step": e.step},
            err=e.message,
        )
        _write_trace(audit_root, run_id, biz_date_path, account_safe, summary, trace.steps)
        return summary


def _failed_summary(
    run_id: str,
    status: str,
    biz_date: str,
    account: str,
    account_safe: str,
    output_path: str | None,
    source_paths: list[str],
    source_count: int,
    err: str,
    started_at: str,
    partial: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": status,
        "parse_version": AGGREGATE_PARSE_VERSION,
        "account": account,
        "account_dir": account_safe,
        "biz_date": biz_date,
        "output_path": output_path,
        "source_paths": source_paths,
        "source_count": source_count,
        "error_message": err,
        "partial_sources": partial,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def _annotate_vision_preclean_model_output_in_summary(
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    """
    在 ``.trace.json`` 顶层 ``summary`` 中**显式**标出「VL 模型返回的配图识读正文」的位置与空因，
    避免与 ``reading_topic_groups``（仅插图投递方式）混淆。
    """
    vm = vision_preclean_model_name()
    enabled = vision_llm_enabled()
    readings = summary.get("vision_preclean_readings")
    if readings is None:
        readings = []
    out: list[dict[str, Any]] = []
    for r in readings:
        if not isinstance(r, dict):
            continue
        notes = r.get("reading_notes")
        out.append(
            {
                "stem": r.get("stem"),
                "article_title": r.get("title"),
                "vision_preclean_model": r.get("vision_preclean_model"),
                "model_output_text": notes,
                "model_output_char_count": r.get("reading_char_count"),
            }
        )
    summary["vision_preclean_model_outputs"] = out

    summary["vision_preclean_output_find_in_trace_json"] = {
        "summary_keys": [
            "vision_preclean_model_outputs[].model_output_text",
            "vision_preclean_readings[].reading_notes（与上一项同文，兼容旧字段名）",
        ],
        "steps": "查 steps 中 kind=vision_preclean 且 ok=true 的项：detail.reading_text 或 detail.model_output_text（超长时与上述一致经截断+sha256）。",
        "reading_topic_groups_is_not_model_text_zh": (
            "detail.reading_topic_groups 仅按企微 JSON 形状记录「每张图 URL + 投递策略」，"
            "**不是** VL 模型返回的配图识读正文。"
        ),
    }

    if out:
        summary["vision_preclean_empty_reason_zh"] = None
        return

    if not enabled:
        reason = "未启用 GZH_LLM_VISION，未走识图模型，故无 model_output_text。"
    elif not vm:
        reason = "已开 GZH_LLM_VISION 但未配置 GZH_VISION_PRECLEAN_MODEL，未调用 VL，故无输出。"
    else:
        vp = [s for s in steps if s.get("kind") == "vision_preclean"]
        if not vp:
            reason = (
                "本轮未写入任何 kind=vision_preclean 步骤：常见为导出 HTML 内无可用 http(s) 插图，"
                "或在到达识图前已中止。"
            )
        elif not any(s.get("ok") for s in vp):
            errs = [str(s.get("error") or "") for s in vp if not s.get("ok")]
            joined = "; ".join(e for e in errs if e)[:1200]
            reason = "VL 步骤均已失败或无有效应答：" + (joined or "见各 vision_preclean 步骤 error/detail。")
        else:
            reason = (
                "存在成功的 vision_preclean 步骤，但任务 summary 未汇总到 vision_preclean_readings（少见）；"
                "请直接在 steps[].detail.reading_text 查看。"
            )
    summary["vision_preclean_empty_reason_zh"] = reason


def _write_trace(
    audit_root: Path,
    run_id: str,
    biz_date_path_segment: str,
    account_safe: str,
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    trace_path = build_audit_trace_path(
        audit_root,
        biz_date_path_segment,
        phase="parse",
        run_id=run_id,
        account=account_safe,
    )
    _annotate_vision_preclean_model_output_in_summary(summary, steps)
    write_trace_json(trace_path, summary, steps)
