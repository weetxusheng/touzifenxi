"""controller-agent 相关的状态判断与 manifest 辅助。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .c114_brief_review import validate_brief_markdown_for_agent, validate_brief_review_yaml_for_agent
from .facades.content import load_search_results_yaml, validate_content_fetch_inputs
from .facades.content_analysis import collect_missing_analysis_fields, load_content_analysis_inputs
from .facades.intelligence import (
    create_search_run_directory,
    find_latest_c114_step_file,
    step_1_analysis_name,
    step_2_checklist_name,
    step_3_results_name,
    step_4_content_name,
    step_5_content_analysis_name,
    step_6_brief_name,
    step_7_brief_review_name,
)
from .runtime.config import load_c114_runtime_config
from .runtime.execution import (
    StepInstruction,
    build_checkpoint_sequence,
    create_controller_agent_bridge,
    execution_manifest_name,
    save_controller_agent_bridge,
)
from .runtime.settings import AppPaths
from .search.workflow import load_search_checklist_yaml, validate_search_checklist_items


def step_manifest_path(day_dir: Path, target_date: date) -> Path:
    """返回某一天运行目录下的执行 manifest 路径。"""

    return (day_dir / execution_manifest_name(target_date)).resolve()


def write_controller_agent_manifest(
    *,
    day_dir: Path,
    target_date: date,
    current_step: str,
    output_paths: dict[str, Path],
    prompt_paths: dict[str, Path | None],
    input_paths: dict[str, tuple[Path, ...]],
    required_fields: dict[str, tuple[str, ...]],
    instruction: StepInstruction,
    next_action: str,
    errors: dict[str, str] | None = None,
    notes: dict[str, tuple[str, ...]] | None = None,
) -> Path:
    """写出 controller-agent 模式的 manifest。"""

    manifest_path = step_manifest_path(day_dir, target_date)
    bridge = create_controller_agent_bridge(
        report_date=target_date.isoformat(),
        manifest_path=manifest_path,
        current_step=current_step,
        checkpoints=build_checkpoint_sequence(
            ready_step=current_step,
            output_paths=output_paths,
            prompt_paths=prompt_paths,
            input_paths=input_paths,
            required_fields=required_fields,
            errors=errors,
            notes=notes,
        ),
        instruction=instruction,
        next_action=next_action,
    )
    save_controller_agent_bridge(bridge)
    return manifest_path


def build_controller_output_paths(day_dir: Path, target_date: date) -> dict[str, Path]:
    """为 manifest 统一整理各步骤的目标输出路径。"""

    return {
        "step_1": (day_dir / step_1_analysis_name(target_date)).resolve(),
        "step_2": (day_dir / step_2_checklist_name(target_date)).resolve(),
        "step_3": (day_dir / step_3_results_name(target_date)).resolve(),
        "step_4": (day_dir / step_4_content_name(target_date)).resolve(),
        "step_5": (day_dir / step_5_content_analysis_name(target_date)).resolve(),
        "step_6": (day_dir / step_6_brief_name(target_date)).resolve(),
        "step_7": (day_dir / step_7_brief_review_name(target_date)).resolve(),
    }


def controller_run_directory(paths: AppPaths, target_date: date) -> Path:
    """为 controller-agent 模式复用最近的未完成目录，或创建新目录。"""

    latest = find_latest_c114_step_file(paths.reports_dir, target_date, execution_manifest_name(target_date))
    if latest is not None:
        return latest.parent.resolve()
    return create_search_run_directory(paths.reports_dir)


def step2_keywords_completed(checklist_path: Path) -> bool:
    """判断 step 2 是否已补齐当前配置要求的关键词组数。"""

    if not checklist_path.exists():
        return False
    try:
        _report_date, items = load_search_checklist_yaml(checklist_path)
        validate_search_checklist_items(items, keyword_count=load_c114_runtime_config().search_keyword_count)
    except Exception:
        return False
    return True


def step3_review_completed(search_results_path: Path) -> bool:
    """判断 step 3 是否已补齐 ai_review。"""

    if not search_results_path.exists():
        return False
    try:
        validate_content_fetch_inputs(load_search_results_yaml(search_results_path))
    except Exception:
        return False
    return True


def step5_analysis_completed(analysis_path: Path) -> bool:
    """判断 step 5 八个分析字段是否都已补齐。"""

    if not analysis_path.exists():
        return False
    try:
        payload = load_content_analysis_inputs(analysis_path)
    except Exception:
        return False
    return not collect_missing_analysis_fields(payload)


def step6_brief_completed(brief_path: Path, analysis_path: Path) -> bool:
    """判断 step 6 简报是否已由控制 agent 正式完成。"""

    if not brief_path.exists():
        return False
    try:
        validate_brief_markdown_for_agent(brief_path, analysis_path)
    except Exception:
        return False
    return True


def step7_review_completed(review_path: Path) -> bool:
    """判断 step 7 审查 YAML 是否已由控制 agent 正式完成。"""

    if not review_path.exists():
        return False
    try:
        validate_brief_review_yaml_for_agent(review_path)
    except Exception:
        return False
    return True
