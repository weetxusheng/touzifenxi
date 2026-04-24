"""36Kr workflow artifact naming conventions."""

from __future__ import annotations

from datetime import date

STEP_1_ANALYSIS_PREFIX = "kr36_step_1_analysis"
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

