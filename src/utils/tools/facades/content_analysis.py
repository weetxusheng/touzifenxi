"""Step 5/6 兼容门面。

主体逻辑已拆入 `analysis`、`brief` 和 `steps` 子包。旧测试、CLI 和外部调用
仍可从本模块导入公开函数，但新增逻辑不得继续写在这里。
"""

from __future__ import annotations

from ..analysis.models import (
    BRIEF_PROMPT_PATH,
    CONTENT_ANALYSIS_PROMPT_PATH,
    OPTIONAL_ANALYSIS_LIST_FIELDS,
    REQUIRED_ANALYSIS_LIST_FIELDS,
    STEP5_TOPIC_BATCH_ITEM_LIMIT,
    BriefSectionDraft,
    ContentAnalysisDraft,
    ContentAnalysisInput,
    ContentAnalysisItem,
    ContentAnalysisOutputPaths,
    ContentAnalysisSection,
    ContentDocument,
    SelectedDocument,
)
from ..analysis.title_matching import normalize_analysis_title_key
from ..analysis.validation import (
    collect_missing_analysis_fields,
    normalize_content_analysis_draft,
    normalize_content_analysis_topic_response,
)
from ..analysis.yaml_io import (
    load_content_analysis_inputs,
    render_content_analysis_yaml,
    resolve_content_analysis_output_paths,
    save_content_analysis_yaml,
)
from ..brief.links import format_brief_link_line, infer_brief_title, infer_related_step3_path
from ..brief.markdown import build_brief_runtime_summary, render_brief_markdown, render_generated_brief_markdown
from ..brief.render import escape_yaml, render_analysis_list, render_layer_issues_yaml
from utils.steps.step5_content_analysis import (
    auto_complete_content_analysis,
    build_content_analysis_prompt_payload,
    build_content_analysis_topic_prompt_payload,
    build_step5_batch_entry_id,
    build_step5_item_entry_id,
    content_analysis_batch_to_dict,
    content_analysis_item_from_dict,
    content_analysis_item_to_dict,
    content_analysis_items_from_batch_dict,
    html_fallback_edge_limit,
    prepare_step5_prompt_text,
    split_content_analysis_items_for_topic,
    truncate_html_fallback_prompt_text,
)
from utils.steps.step6_brief import (
    auto_complete_brief_sections,
    brief_section_draft_from_dict,
    brief_section_draft_to_dict,
    build_brief_prompt_payload,
    build_step6_topic_entry_id,
    generate_brief_markdown,
    generate_layer_issues,
    normalize_brief_sections,
    save_brief_markdown,
    save_layer_issues_yaml,
)

__all__ = [
    "BRIEF_PROMPT_PATH",
    "CONTENT_ANALYSIS_PROMPT_PATH",
    "OPTIONAL_ANALYSIS_LIST_FIELDS",
    "REQUIRED_ANALYSIS_LIST_FIELDS",
    "STEP5_TOPIC_BATCH_ITEM_LIMIT",
    "BriefSectionDraft",
    "ContentAnalysisDraft",
    "ContentAnalysisInput",
    "ContentAnalysisItem",
    "ContentAnalysisOutputPaths",
    "ContentAnalysisSection",
    "ContentDocument",
    "SelectedDocument",
    "auto_complete_brief_sections",
    "auto_complete_content_analysis",
    "brief_section_draft_from_dict",
    "brief_section_draft_to_dict",
    "build_brief_prompt_payload",
    "build_brief_runtime_summary",
    "build_content_analysis_prompt_payload",
    "build_content_analysis_topic_prompt_payload",
    "build_step5_batch_entry_id",
    "build_step5_item_entry_id",
    "build_step6_topic_entry_id",
    "collect_missing_analysis_fields",
    "content_analysis_batch_to_dict",
    "content_analysis_item_from_dict",
    "content_analysis_item_to_dict",
    "content_analysis_items_from_batch_dict",
    "escape_yaml",
    "format_brief_link_line",
    "generate_brief_markdown",
    "generate_layer_issues",
    "html_fallback_edge_limit",
    "infer_brief_title",
    "infer_related_step3_path",
    "load_content_analysis_inputs",
    "normalize_analysis_title_key",
    "normalize_brief_sections",
    "normalize_content_analysis_draft",
    "normalize_content_analysis_topic_response",
    "prepare_step5_prompt_text",
    "render_analysis_list",
    "render_brief_markdown",
    "render_content_analysis_yaml",
    "render_generated_brief_markdown",
    "render_layer_issues_yaml",
    "resolve_content_analysis_output_paths",
    "save_brief_markdown",
    "save_content_analysis_yaml",
    "save_layer_issues_yaml",
    "split_content_analysis_items_for_topic",
    "truncate_html_fallback_prompt_text",
]
