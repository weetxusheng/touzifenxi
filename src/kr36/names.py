"""36Kr 主流程产物的文件名约定（与 C114 的 step2 搜索清单、step3 外搜等无关）。"""

from __future__ import annotations

from datetime import date

# 步骤 1：与历史一致（kr36_step_1_analysis_…）
STEP_1_ANALYSIS_PREFIX = "kr36_step_1_analysis"

# 新四步中：2=抓正文，3=LLM 整合分析，4=导出简报
STEP_2_CONTENT_PREFIX = "kr36_step2_content"
STEP_3_ANALYSIS_PREFIX = "kr36_step3_analysis"
STEP_4_BRIEF_PREFIX = "kr36_step4_brief"
LAYER_ISSUES_PREFIX = "kr36_layer_issues"


def step1_analysis_name(report_date: date) -> str:
    return f"{STEP_1_ANALYSIS_PREFIX}_{report_date.strftime('%Y%m%d')}.csv"


def step2_content_name(report_date: date) -> str:
    return f"{STEP_2_CONTENT_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step3_analysis_name(report_date: date) -> str:
    return f"{STEP_3_ANALYSIS_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"


def step4_brief_name(report_date: date) -> str:
    return f"{STEP_4_BRIEF_PREFIX}_{report_date.strftime('%Y%m%d')}.md"


def layer_issues_name(report_date: date) -> str:
    return f"{LAYER_ISSUES_PREFIX}_{report_date.strftime('%Y%m%d')}.yaml"
