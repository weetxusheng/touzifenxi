"""36Kr 主流程产物的文件名约定（与 C114 的 step2 搜索清单、step3 外搜等无关）。"""

from __future__ import annotations

from datetime import date

# 步骤 1：与历史一致（kr36_step_1_analysis_…）
STEP_1_ANALYSIS_PREFIX = "kr36_step_1_analysis"

# 36kr 跳过 step2（搜索清单）与 step3（外搜结果），直接从 step4 开始，与 c114 编号对齐
STEP_4_CONTENT_PREFIX = "kr36_step_4_content"
STEP_5_ANALYSIS_PREFIX = "kr36_step_5_content_analysis"
STEP_6_BRIEF_PREFIX = "kr36_step_6_brief"
LAYER_ISSUES_PREFIX = "kr36_layer_issues"


def step1_analysis_name(report_date: date) -> str:
    return f"{STEP_1_ANALYSIS_PREFIX}_{report_date.strftime('%Y%m%d')}.csv"


def step4_content_name(report_date: date) -> str:
    return f"{STEP_4_CONTENT_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step5_analysis_name(report_date: date) -> str:
    return f"{STEP_5_ANALYSIS_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step6_brief_name(report_date: date) -> str:
    return f"{STEP_6_BRIEF_PREFIX}_{report_date.strftime('%Y%m%d')}.md"


def layer_issues_name(report_date: date) -> str:
    return f"{LAYER_ISSUES_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"
