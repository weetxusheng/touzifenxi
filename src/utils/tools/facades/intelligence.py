"""Step 1/1.5/2 兼容门面。

运行目录、文件命名等历史约定暂时保留在这里，便于 search/content/CLI 继续复用。
主体分析、topic 聚类和关键词生成逻辑已拆入 `analysis` 与 `steps` 子包。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from utils.tools.llm import StructuredLLMError

PROJECT_ROOT = Path(__file__).resolve().parents[4]
C114_ROOT = PROJECT_ROOT / "src" / "c114"
PROMPT_PATH = C114_ROOT / "prompts" / "search-keyword-agent.md"
TOPIC_GROUPING_PROMPT_PATH = C114_ROOT / "prompts" / "topic-grouping-agent.md"
RUN_DIR_PREFIX = "c114_search_"
RANGE_DIR_PREFIX = "c114_range_"
RUN_DIR_TIME_FORMAT = "%Y%m%d%H%M"
STEP_1_ANALYSIS_PREFIX = "c114_step_1_analysis"
STEP_2_CHECKLIST_PREFIX = "c114_step_2_search_checklist"
STEP_3_RESULTS_PREFIX = "c114_step_3_search_results"
STEP_4_CONTENT_PREFIX = "c114_step_4_content"
STEP_5_CONTENT_ANALYSIS_PREFIX = "c114_step_5_content_analysis"
STEP_6_BRIEF_PREFIX = "c114_step_6_brief"
STEP_7_BRIEF_REVIEW_PREFIX = "c114_step_7_brief_review"
LAYER_ISSUES_PREFIX = "c114_layer_issues"
PROVIDER_STATS_PREFIX = "c114_provider_stats"
LLM_TRACE_LOG_PREFIX = "c114_llm_trace"
LLM_TRACE_LOG_DIR_NAME = "logs"

def build_search_run_dir_name(run_started_at: datetime) -> str:
    """生成单次运行目录名。"""
    return f"{RUN_DIR_PREFIX}{run_started_at.strftime(RUN_DIR_TIME_FORMAT)}"


def build_search_range_dir_name(start_date: date, end_date: date, run_started_at: datetime) -> str:
    """生成区间运行目录名。"""
    return (
        f"{RANGE_DIR_PREFIX}{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}_"
        f"{run_started_at.strftime(RUN_DIR_TIME_FORMAT)}"
    )


def c114_reports_root(reports_dir: Path) -> Path:
    """返回 C114 运行产物的总目录。"""
    return reports_dir / "c114_report"


def build_step_file_name(step_prefix: str, report_date: date, suffix: str) -> str:
    """按统一规则拼接某一步的文件名。"""
    return f"{step_prefix}_{report_date.strftime('%Y%m%d')}.{suffix}"


def step_1_analysis_name(report_date: date) -> str:
    """返回 step 1 分析 CSV 文件名。"""
    return build_step_file_name(STEP_1_ANALYSIS_PREFIX, report_date, "csv")


def step_2_checklist_name(report_date: date) -> str:
    """返回 step 2 检索清单 YAML 文件名。"""
    return build_step_file_name(STEP_2_CHECKLIST_PREFIX, report_date, "yaml")


def step_3_results_name(report_date: date) -> str:
    """返回 step 3 搜索结果 YAML 文件名。"""
    return build_step_file_name(STEP_3_RESULTS_PREFIX, report_date, "yaml")


def step_4_content_name(report_date: date) -> str:
    """返回 step 4 正文抓取 YAML 文件名。"""
    return build_step_file_name(STEP_4_CONTENT_PREFIX, report_date, "yaml")


def step_5_content_analysis_name(report_date: date) -> str:
    """返回 step 5 正文分析 YAML 文件名。"""
    return build_step_file_name(STEP_5_CONTENT_ANALYSIS_PREFIX, report_date, "yaml")


def step_6_brief_name(report_date: date) -> str:
    """返回 step 6 简报 Markdown 文件名。"""
    return build_step_file_name(STEP_6_BRIEF_PREFIX, report_date, "md")


def step_7_brief_review_name(report_date: date) -> str:
    """返回 step 7 审查 YAML 文件名。"""
    return build_step_file_name(STEP_7_BRIEF_REVIEW_PREFIX, report_date, "yaml")


def layer_issues_name(report_date: date) -> str:
    """返回分层问题汇总文件名。"""
    return build_step_file_name(LAYER_ISSUES_PREFIX, report_date, "yaml")


def provider_stats_name(report_date: date, *, source_prefix: str = "c114") -> str:
    """返回搜索 provider 用量统计文件名。"""
    normalized_prefix = str(source_prefix or "c114").strip().lower().replace("-", "_") or "c114"
    return build_step_file_name(f"{normalized_prefix}_provider_stats", report_date, "md")


def llm_trace_log_name(report_date: date) -> str:
    """返回单次运行的 LLM 调用日志文件名。"""
    return build_step_file_name(LLM_TRACE_LOG_PREFIX, report_date, "jsonl")


def llm_trace_log_name_for_step(step_name: str, report_date: date) -> str:
    """返回某一步骤专属的 LLM 调用日志文件名。"""
    normalized_step = str(step_name).strip() or "unknown"
    return f"{LLM_TRACE_LOG_PREFIX}_{normalized_step}_{report_date.strftime('%Y%m%d')}.jsonl"


def create_search_run_directory(reports_dir: Path, run_started_at: datetime | None = None) -> Path:
    """Create a new single-day run directory without mutating older runs."""

    timestamp = run_started_at or datetime.now()
    base_name = build_search_run_dir_name(timestamp)
    base_dir = c114_reports_root(reports_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def create_search_range_directory(
    reports_dir: Path,
    start_date: date,
    end_date: date,
    run_started_at: datetime | None = None,
) -> Path:
    """Create a top-level range run directory used to hold per-day subdirectories."""

    timestamp = run_started_at or datetime.now()
    base_name = build_search_range_dir_name(start_date, end_date, timestamp)
    base_dir = c114_reports_root(reports_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / base_name
    suffix = 2
    while run_dir.exists():
        run_dir = base_dir / f"{base_name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def find_latest_search_run_directory(reports_dir: Path, report_date: date) -> Path | None:
    """Locate the newest run directory that already contains artifacts for one day."""

    base_dir = c114_reports_root(reports_dir)
    if not base_dir.exists():
        return None
    checklist_name = step_2_checklist_name(report_date)
    candidates = [
        path
        for path in base_dir.iterdir()
        if path.is_dir() and path.name.startswith(RUN_DIR_PREFIX) and (path / checklist_name).exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name)[-1]


def find_latest_c114_step_file(reports_dir: Path, report_date: date, file_name: str) -> Path | None:
    """Find the newest matching step file for one report date."""

    base_dir = c114_reports_root(reports_dir)
    if not base_dir.exists():
        return None
    candidates: list[Path] = []
    for path in base_dir.iterdir():
        if not path.is_dir():
            continue
        if path.name.startswith(RUN_DIR_PREFIX):
            candidate = path / file_name
            if candidate.exists():
                candidates.append(candidate)
            continue
        if path.name.startswith(RANGE_DIR_PREFIX):
            for day_dir in sorted(child for child in path.iterdir() if child.is_dir()):
                candidate = day_dir / file_name
                if candidate.exists():
                    candidates.append(candidate)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.parent))[-1]


from ..analysis.csv_io import load_daily_articles_from_csv, save_article_analysis_csv  # noqa: E402
from ..analysis.models import (  # noqa: E402
    AnalysisOutputPaths,
    ArticleAnalysis,
    RawArticleRecord,
    SearchChecklistItem,
    SearchChecklistSection,
    TopicBrief,
)
from ..analysis.normalization import (  # noqa: E402
    build_core_summary,
    build_followup_queries,
    classify_signals,
    compact_phrase,
    extract_entities,
    infer_home_topic_from_url,
    infer_topic,
    is_c114_article_url,
    normalize_keywords,
    split_pipe_list,
)
from ..analysis.output_paths import resolve_analysis_output_paths, write_analysis_outputs  # noqa: E402
from ..steps.step1_5_topic_grouping import (  # noqa: E402
    TOPIC_GROUPING_CHECKPOINT_ENTRY_ID,
    auto_group_analysis_topics,
    load_topic_grouping_prompt,
)
from ..steps.step1_analysis import analyze_article, analyze_daily_articles, build_topic_briefs  # noqa: E402
from ..steps.step2_keywords import (  # noqa: E402
    _normalize_keyword_response,
    apply_step2_checkpoint_results,
    autofill_search_checklist_items,
    build_search_checklist_items,
    build_search_checklist_sections,
    build_step2_checkpoint_entry_id,
    build_step2_checkpoint_request_context,
    build_title_aligned_queries,
    compact_search_phrase,
    derive_fallback_query,
    derive_primary_query,
    derive_secondary_query,
    escape_yaml_scalar,
    extract_action_hint,
    extract_title_segments,
    load_search_agent_prompt,
    normalize_title_detail,
    render_search_checklist_yaml,
)

__all__ = [
    "AnalysisOutputPaths",
    "ArticleAnalysis",
    "LLM_TRACE_LOG_DIR_NAME",
    "PROMPT_PATH",
    "RawArticleRecord",
    "SearchChecklistItem",
    "SearchChecklistSection",
    "StructuredLLMError",
    "TOPIC_GROUPING_CHECKPOINT_ENTRY_ID",
    "TOPIC_GROUPING_PROMPT_PATH",
    "TopicBrief",
    "analyze_article",
    "analyze_daily_articles",
    "apply_step2_checkpoint_results",
    "auto_group_analysis_topics",
    "autofill_search_checklist_items",
    "build_core_summary",
    "build_followup_queries",
    "build_search_checklist_items",
    "build_search_checklist_sections",
    "build_search_range_dir_name",
    "build_search_run_dir_name",
    "build_step2_checkpoint_entry_id",
    "build_step2_checkpoint_request_context",
    "build_step_file_name",
    "build_title_aligned_queries",
    "build_topic_briefs",
    "_normalize_keyword_response",
    "c114_reports_root",
    "classify_signals",
    "compact_phrase",
    "compact_search_phrase",
    "create_search_range_directory",
    "create_search_run_directory",
    "derive_fallback_query",
    "derive_primary_query",
    "derive_secondary_query",
    "escape_yaml_scalar",
    "extract_action_hint",
    "extract_entities",
    "extract_title_segments",
    "find_latest_c114_step_file",
    "find_latest_search_run_directory",
    "infer_home_topic_from_url",
    "infer_topic",
    "is_c114_article_url",
    "layer_issues_name",
    "llm_trace_log_name",
    "llm_trace_log_name_for_step",
    "load_daily_articles_from_csv",
    "load_search_agent_prompt",
    "load_topic_grouping_prompt",
    "normalize_keywords",
    "normalize_title_detail",
    "provider_stats_name",
    "render_search_checklist_yaml",
    "resolve_analysis_output_paths",
    "save_article_analysis_csv",
    "split_pipe_list",
    "step_1_analysis_name",
    "step_2_checklist_name",
    "step_3_results_name",
    "step_4_content_name",
    "step_5_content_analysis_name",
    "step_6_brief_name",
    "step_7_brief_review_name",
    "write_analysis_outputs",
]
