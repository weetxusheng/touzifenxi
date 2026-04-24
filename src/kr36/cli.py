from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from c114.runtime.config import load_c114_runtime_config
from utils.tools.analysis.csv_io import save_article_analysis_csv
from utils.tools.facades.intelligence import (
    LLM_TRACE_LOG_DIR_NAME,
    ArticleAnalysis,
    analyze_daily_articles,
    auto_group_analysis_topics,
)
from utils.tools.llm import StructuredChatClient
from utils.tools.output.briefing import write_step1_csv
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step

from . import names as kr36_names
from .brief_assets import save_brief_preview_assets
from .pipeline import run_stages_2_3_4
from .settings import AppPaths, ensure_directories, resolve_paths
from utils.tools.content_models import RawArticleRef

from .source_adapter import (
    Kr36SourceAdapter,
    effective_topic_item_kind_for_download,
    kr36_debug_log_file,
)
from .topic_fulltext_index import write_kr36_topic_fulltext_json

RUN_DIR_PREFIX = "kr36_search_"
HOT_TOPICS_RUN_PREFIX = "kr36_hot_topics_"
RAW_JSON_PREFIX = "kr36_hot_topics"
RAW_CSV_NAME = "kr36_hot_topics.csv"
# 与历史脚本兼容：曾用下划线区分的 step2–6 文件前缀（见各 *_name 函数）
LEGACY_PREFIX_STEP2_CHECKLIST = "kr36_step_2_search_checklist"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TOPIC_GROUPING_PROMPT_PATH = PROMPTS_DIR / "topic-grouping-agent.md"
SEARCH_KEYWORD_PROMPT_PATH = (
    PROMPTS_DIR / "search-keyword-agent.md"
)  # 保留；新流程不调用 write_analysis_outputs
SEARCH_TRACE_PREFIX = "kr36_search_trace_step_3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the 36Kr daily hot-topics skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hot_topics = subparsers.add_parser("kr36-hot-topics", help="Fetch daily hot topics from 36Kr.")
    hot_topics.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")

    topics_only = subparsers.add_parser(
        "kr36-topics-only",
        help="仅抓取专题列表 + topic_focus 配置的若干类专题详情（不跑活动/搜索/频道）。",
    )
    topics_only.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")

    run_parser = subparsers.add_parser("run", help="Run 36kr：四步（步骤1 分析 CSV → 步骤2 正文 → 步骤3 整合分析 → 步骤4 导出）。")
    run_parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    run_parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "tavily", "metaso", "baidu", "google"],
        help="c114 入口兼容保留；新四步 36kr 不执行外搜，此参数无效果。",
    )
    run_parser.add_argument("--per-query-limit", type=int, default=5, help="兼容 c114 入口，无效果。")
    run_parser.add_argument("--per-article-limit", type=int, default=None, help="兼容 c114 入口，无效果。")
    run_parser.add_argument("--extract-limit", type=int, default=5, help="兼容 c114 入口，无效果。")
    run_parser.add_argument(
        "--external-search",
        action="store_true",
        help="兼容 c114 入口；新 36kr 四步不启用外搜，将忽略。",
    )

    videos_from_json = subparsers.add_parser(
        "kr36-videos-from-json",
        help="根据 kr36_hot_topics_*.json 中条目，对视频页再抓 <video src>/initialState 并下载 CDN 到同目录下 kr36_topic_downloads。",
    )
    videos_from_json.add_argument(
        "--json",
        dest="json_path",
        required=True,
        help="热点 JSON 路径，如 .../kr36_hot_topics_20260422.json",
    )

    transcribe = subparsers.add_parser(
        "kr36-transcribe-audio",
        help="对本地 mp3 做火山豆包极速转写；用于修好 API Key 后对已有 .asr.mp3 补写 .transcript.txt。",
    )
    transcribe.add_argument(
        "--audio",
        type=Path,
        required=True,
        help="音频路径，如 …/…_36kr_topic_video_xxx.asr.mp3",
    )

    export_reading = subparsers.add_parser(
        "export-content-reading",
        help="根据步骤2正文 YAML（kr36_step_4_content_*.yaml）生成 HTML + Markdown 阅读原文汇编。",
    )
    export_reading.add_argument(
        "--content-yaml",
        type=Path,
        default=None,
        help="正文 YAML 路径；若省略则需同时指定 --run-dir 与 --date。",
    )
    export_reading.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="单次运行目录（其下含 kr36_step_4_content_<date>.yaml）。",
    )
    export_reading.add_argument(
        "--date",
        default=None,
        help="与 --run-dir 合用，YYYY-MM-DD，用于解析 kr36_step_4_content_YYYYMMDD.yaml。",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_with_args(args)


def run_with_args(args: argparse.Namespace, *, paths: AppPaths | None = None) -> None:
    resolved_paths = paths or resolve_paths()
    ensure_directories(resolved_paths)

    if args.command == "kr36-hot-topics":
        target_date = date.fromisoformat(args.date)
        run_dir = create_hot_topics_run_directory(resolved_paths.reports_dir)
        fetch_log = run_dir / "kr36_fetch.log"
        with kr36_debug_log_file(fetch_log):
            payload = fetch_and_materialize_kr36_articles(target_date, resolved_paths, run_dir=run_dir)
        print(f"36Kr 当日文章抓取完成 {target_date.isoformat()}")
        print(f"文章数: {len(payload['articles'])}")
        print(f"原始 JSON: {payload['raw_json_path']}")
        print(f"原始 CSV: {payload['raw_csv_path']}")
        tfp = payload.get("topic_fulltext_json_path")
        if tfp:
            print(f"专题全文索引: {tfp}")
        print(f"本次运行目录: {run_dir}")
        print(f"抓取日志: {fetch_log}")
        return

    if args.command == "kr36-topics-only":
        target_date = date.fromisoformat(args.date)
        run_dir = create_hot_topics_run_directory(resolved_paths.reports_dir)
        fetch_log = run_dir / "kr36_fetch.log"
        with kr36_debug_log_file(fetch_log):
            payload = fetch_and_materialize_kr36_articles(
                target_date,
                resolved_paths,
                run_dir=run_dir,
                source_config_overrides={"listing_topics_only": True},
            )
        articles = payload["articles"]
        topic_titles = Counter(
            str((a.metadata or {}).get("topic_title") or "").strip() or "(无专题名)"
            for a in articles
            if (a.metadata or {}).get("topic_item_kind")
        )
        kind_n = Counter(
            str((a.metadata or {}).get("topic_item_kind") or "").strip() or "?"
            for a in articles
            if (a.metadata or {}).get("topic_item_kind")
        )
        summary_path = run_dir / "kr36_topics_only_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "report_date": target_date.isoformat(),
                    "run_dir": str(run_dir.resolve()),
                    "total_articles": len(articles),
                    "topic_detail_items": sum(1 for a in articles if (a.metadata or {}).get("topic_item_kind")),
                    "by_topic_title": dict(topic_titles.most_common()),
                    "by_item_kind": dict(kind_n),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"36Kr 仅专题抓取完成 {target_date.isoformat()}")
        print(f"总文章条数(含专题外若存在): {len(articles)}")
        print(f"专题详情子项(视频/文章): {sum(1 for a in articles if (a.metadata or {}).get('topic_item_kind'))}")
        print(f"按专题名计数: {dict(topic_titles.most_common())}")
        print(f"按类型计数: {dict(kind_n)}")
        print(f"汇总 JSON: {summary_path}")
        print(f"原始 JSON: {payload['raw_json_path']}")
        print(f"原始 CSV: {payload['raw_csv_path']}")
        tfp = payload.get("topic_fulltext_json_path")
        if tfp:
            print(f"专题全文索引: {tfp}")
        print(f"抓取日志: {fetch_log}")
        return

    if args.command == "kr36-videos-from-json":
        exit_code = run_kr36_videos_from_hot_topics_json(Path(str(args.json_path)))
        if exit_code != 0:
            raise SystemExit(exit_code)
        return

    if args.command == "kr36-transcribe-audio":
        exit_code = run_kr36_transcribe_audio(Path(str(args.audio)))
        if exit_code != 0:
            raise SystemExit(exit_code)
        return

    if args.command == "export-content-reading":
        from .content_fetch_document import write_content_fetch_reading_docs

        cy = getattr(args, "content_yaml", None)
        run_dir_arg = getattr(args, "run_dir", None)
        date_arg = getattr(args, "date", None)
        if cy:
            content_yaml = Path(cy).resolve()
        elif run_dir_arg and date_arg:
            target_d = date.fromisoformat(str(date_arg))
            content_yaml = Path(run_dir_arg).resolve() / kr36_names.step4_content_name(target_d)
        else:
            raise SystemExit("请指定 --content-yaml，或同时指定 --run-dir 与 --date。")
        html_p, md_p = write_content_fetch_reading_docs(content_yaml)
        print(f"步骤2 阅读原文汇编 HTML: {html_p}")
        print(f"步骤2 阅读原文汇编 Markdown: {md_p}")
        return

    if args.command != "run":
        raise ValueError(f"Unsupported command: {args.command}")

    if getattr(args, "external_search", False):
        print("提示：--external-search 在 36kr 新四步流程中已忽略（不再走 C114 外搜/搜索清单）。")

    target_date = date.fromisoformat(args.date)
    _ = load_c114_runtime_config()
    llm_client = require_llm_client()
    run_dir = create_run_directory(resolved_paths.reports_dir)
    bind_llm_trace_log(llm_client, run_dir=run_dir, target_date=target_date, reset_file=True)

    fetch_log = run_dir / "kr36_fetch.log"
    with kr36_debug_log_file(fetch_log):
        raw_payload = fetch_and_materialize_kr36_articles(target_date, resolved_paths, run_dir=run_dir)
    print(f"抓取日志: {fetch_log}")
    raw_csv_path = raw_payload["raw_csv_path"]
    report_date_text = target_date.isoformat()

    analysis_output = run_dir / step_1_analysis_name(target_date)
    analyses, _briefs = analyze_daily_articles(raw_csv_path, report_date_text)
    if analyses and all(isinstance(item, ArticleAnalysis) for item in analyses):
        topic_grouping_checkpoint_store = StepCheckpointStore.load_or_create(
            checkpoint_path=checkpoint_path_for_step(
                output_path=analysis_output,
                step_name="step_1_5",
                report_date=report_date_text,
                prefix="kr36",
            ),
            step_name="step_1_5",
            report_date=report_date_text,
            input_path=raw_csv_path,
            output_path=analysis_output,
        )
        analyses, _briefs = auto_group_analysis_topics(
            analyses,
            llm_client,
            report_date=report_date_text,
            source_site="36kr",
            checkpoint_store=topic_grouping_checkpoint_store,
            prompt_path=TOPIC_GROUPING_PROMPT_PATH,
        )
    save_article_analysis_csv(analysis_output, analyses)
    count = len(analyses)
    print(f"36Kr 步骤1 {target_date.isoformat()} 完成 | 条数: {count}")
    print(f"  Step1 CSV: {analysis_output}")
    if not analyses:
        print("当日无分析条目，结束。")
        return

    run_stages_2_3_4(
        run_dir=run_dir,
        target_date=target_date,
        report_date_text=report_date_text,
        analyses=analyses,
        step1_csv_path=analysis_output,
        llm_client=llm_client,
    )


def _raw_article_ref_from_hot_topics_json_row(row: dict[str, Any]) -> RawArticleRef:
    """将 fetch 写出的 hot_topics json 中单条转回 ``RawArticleRef``。"""
    meta = row.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    return RawArticleRef(
        source_site=str(row.get("source_site") or "36kr"),
        article_id=str(row.get("article_id") or ""),
        title=str(row.get("title") or ""),
        url=str(row.get("url") or ""),
        published_at=str(row.get("published_at") or ""),
        channel=str(row.get("channel") or ""),
        source_bucket=str(row.get("source_bucket") or ""),
        summary=str(row.get("summary") or ""),
        metadata=meta,
    )


def run_kr36_videos_from_hot_topics_json(json_path: Path) -> int:
    """
    读取 ``kr36_hot_topics_*.json``，筛出视频条目（按 URL / topic_item_kind），
    复用 ``Kr36SourceAdapter._download_topic_items`` 拉播放页并下载 CDN 到
    ``<json 所在目录>/kr36_topic_downloads/...``。
    """
    p = json_path.resolve()
    if not p.is_file():
        print(f"[ERROR] file not found: {p}")
        return 2
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"[ERROR] read failed: {exc}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"[ERROR] invalid json: {exc}")
        return 2
    run_dir = p.parent
    rd = data.get("report_date")
    if not isinstance(rd, str) or not rd.strip():
        print("[ERROR] json missing string report_date")
        return 2
    try:
        report_date = date.fromisoformat(rd.strip()[:10])
    except ValueError:
        print(f"[ERROR] bad report_date: {rd!r}")
        return 2
    raw_list = data.get("articles")
    if not isinstance(raw_list, list) or not raw_list:
        print("[ERROR] json missing non-empty articles array")
        return 2
    video_refs: list[RawArticleRef] = []
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        ref = _raw_article_ref_from_hot_topics_json_row(row)
        if effective_topic_item_kind_for_download(ref) == "video":
            video_refs.append(ref)
    if not video_refs:
        print("no video items in json (paths with /video/ or topic_item_kind=video).")
        return 0
    runtime_config = load_c114_runtime_config()
    source_config = dict(runtime_config.source_configs.get("kr36", {}) or {})
    source_config["topic_download_dir"] = str(run_dir.resolve())
    adapter = Kr36SourceAdapter(source_config)
    log_path = run_dir / "kr36_fetch_videos_from_json.log"
    print(f"[kr36-videos-from-json] json={p}")
    print(f"[kr36-videos-from-json] run_dir={run_dir}")
    print(f"[kr36-videos-from-json] video count={len(video_refs)}")
    with kr36_debug_log_file(log_path):
        adapter._download_topic_items(video_refs, report_date=report_date)
    try:
        full_list = data.get("articles")
        if isinstance(full_list, list):
            tfp = write_kr36_topic_fulltext_json(
                full_list,
                run_dir=run_dir,
                report_date=report_date,
            )
            print(f"[kr36-videos-from-json] topic_fulltext={tfp}")
    except OSError as exc:
        print(f"[kr36-videos-from-json] topic_fulltext write skipped err={exc}")
    print(f"[kr36-videos-from-json] log={log_path}")
    print(f"[kr36-videos-from-json] downloads under {run_dir / 'kr36_topic_downloads'}")
    return 0


def _kr36_transcribe_output_paths(audio: Path) -> tuple[Path, Path]:
    """与专题落盘规则一致：foo.asr.mp3 → foo.transcript.txt、foo.asr.json。"""
    if audio.name.lower().endswith(".asr.mp3"):
        base = audio.name[: -len(".asr.mp3")]
    else:
        base = audio.stem
    d = audio.parent
    return d / f"{base}.transcript.txt", d / f"{base}.asr.json"


def run_kr36_transcribe_audio(audio_path: Path) -> int:
    from . import topic_focus_step15 as t15

    p = audio_path.resolve()
    if not p.is_file():
        print(f"[ERROR] 文件不存在: {p}")
        return 2
    runtime_config = load_c114_runtime_config()
    source_config = dict(runtime_config.source_configs.get("kr36", {}) or {})
    transcript_path, json_path = _kr36_transcribe_output_paths(p)
    try:
        code, nchars = t15.step6b_transcribe_mp3_to_transcript_files(
            p,
            transcript_path=transcript_path,
            asr_json_path=json_path,
            video_path_for_json=p,
            config=source_config,
            timeout_seconds=float(source_config.get("topic_asr_timeout_seconds") or 300),
        )
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] 识别失败: {exc}")
        return 1
    print(f"OK 字数={nchars} code={code} transcript={transcript_path} json={json_path}")
    return 0


def fetch_and_materialize_kr36_articles(
    target_date: date,
    paths: AppPaths,
    *,
    run_dir: Path | None = None,
    source_config_overrides: dict[str, Any] | None = None,
) -> dict[str, object]:
    runtime_config = load_c114_runtime_config()
    source_config = dict(runtime_config.source_configs.get("kr36", {}) or {})
    if source_config_overrides:
        source_config.update(source_config_overrides)
    topic_download_dir = str(source_config.get("topic_download_dir") or "").strip()
    if run_dir is not None and topic_download_dir in (".", ""):
        source_config["topic_download_dir"] = str(run_dir.resolve())
    elif not topic_download_dir:
        download_base_dir = (run_dir or (paths.reports_dir / "kr36_report")).resolve()
        source_config["topic_download_dir"] = str(download_base_dir)
    adapter = Kr36SourceAdapter(source_config)
    articles = adapter.fetch_standard_articles(target_date)
    raw_json_path = (run_dir or paths.raw_dir) / raw_json_name(target_date)
    raw_csv_path = (run_dir or paths.raw_dir) / RAW_CSV_NAME
    raw_json_path.parent.mkdir(parents=True, exist_ok=True)
    raw_json_path.write_text(
        json.dumps(
            {
                "report_date": target_date.isoformat(),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "articles": [asdict(article) for article in articles],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_step1_csv(raw_csv_path, target_date.isoformat(), articles)
    topic_fulltext_path: Path | None = None
    if run_dir is not None:
        topic_fulltext_path = write_kr36_topic_fulltext_json(
            articles,  # type: ignore[arg-type]
            run_dir=run_dir,
            report_date=target_date,
        )
    out: dict[str, object] = {
        "articles": articles,
        "raw_json_path": raw_json_path,
        "raw_csv_path": raw_csv_path,
    }
    if topic_fulltext_path is not None:
        out["topic_fulltext_json_path"] = topic_fulltext_path
    return out


def require_llm_client() -> StructuredChatClient:
    return StructuredChatClient.from_runtime_config()


def bind_llm_trace_log(
    llm_client: StructuredChatClient,
    *,
    run_dir: Path,
    target_date: date,
    reset_file: bool = False,
) -> None:
    llm_client.set_trace_log_directory(
        (run_dir / LLM_TRACE_LOG_DIR_NAME).resolve(),
        report_date=target_date.isoformat(),
        source_prefix="kr36",
        reset_files=reset_file,
    )


def create_run_directory(reports_dir: Path, run_started_at: datetime | None = None) -> Path:
    timestamp = run_started_at or datetime.now()
    base_name = f"{RUN_DIR_PREFIX}{timestamp.strftime('%Y%m%d%H%M')}"
    base_dir = reports_dir / "kr36_report"
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def create_hot_topics_run_directory(reports_dir: Path, run_started_at: datetime | None = None) -> Path:
    """仅抓取热点（kr36-hot-topics）时使用的输出子目录，与 kr36_search_* 区分。"""
    timestamp = run_started_at or datetime.now()
    base_name = f"{HOT_TOPICS_RUN_PREFIX}{timestamp.strftime('%Y%m%d%H%M')}"
    base_dir = reports_dir / "kr36_report"
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def raw_json_name(report_date: date) -> str:
    return f"{RAW_JSON_PREFIX}_{report_date.strftime('%Y%m%d')}.json"


# --- 文件名以 kr36_names 为准（与 c114 步骤编号对齐：4=正文, 5=分析, 6=简报） ---


def step_1_analysis_name(report_date: date) -> str:
    return kr36_names.step1_analysis_name(report_date)


def step_4_content_name(report_date: date) -> str:
    return kr36_names.step4_content_name(report_date)


def step_5_content_analysis_name(report_date: date) -> str:
    return kr36_names.step5_analysis_name(report_date)


def step_6_brief_name(report_date: date) -> str:
    return kr36_names.step6_brief_name(report_date)


def layer_issues_name(report_date: date) -> str:
    return kr36_names.layer_issues_name(report_date)


# --- 废弃别名（保留以免旧 import 在导入期崩溃） ---


def step2_content_name(report_date: date) -> str:
    return kr36_names.step4_content_name(report_date)


def step3_analysis_name(report_date: date) -> str:
    return kr36_names.step5_analysis_name(report_date)


def step4_brief_name(report_date: date) -> str:
    return kr36_names.step6_brief_name(report_date)


def step_2_checklist_name(report_date: date) -> str:
    """已废弃；新流程不生成。"""
    return f"{LEGACY_PREFIX_STEP2_CHECKLIST}_{report_date.strftime('%Y%m%d')}.yaml"


def step_3_results_name(report_date: date) -> str:
    """已废弃。"""
    return f"kr36_step_3_search_results_{report_date.strftime('%Y%m%d')}.yaml"


def search_trace_log_name(report_date: date) -> str:
    return f"{SEARCH_TRACE_PREFIX}_{report_date.strftime('%Y%m%d')}.jsonl"


if __name__ == "__main__":
    main()
