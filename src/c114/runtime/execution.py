"""C114 skill 的执行模式、检查点与控制 agent 契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

ExecutionMode = Literal["builtin", "controller-agent"]
CheckpointStatus = Literal["pending", "ready_for_agent", "agent_completed", "validated", "failed"]
ALL_STEP_NAMES = ("step_1", "step_2", "step_3", "step_4", "step_5", "step_6", "step_7")


@dataclass(frozen=True)
class StepInstruction:
    """描述某个步骤交给控制 agent 时需要读取和产出的契约。"""

    step_name: str
    prompt_path: Path | None
    input_paths: tuple[Path, ...]
    output_path: Path
    required_fields: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepCheckpoint:
    """表示某个步骤当前的状态与对应契约。"""

    step_name: str
    status: CheckpointStatus
    output_path: Path
    prompt_path: Path | None = None
    input_paths: tuple[Path, ...] = ()
    required_fields: tuple[str, ...] = ()
    error: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControllerAgentBridge:
    """表示当前一次停点时暴露给控制 agent 的完整上下文。"""

    report_date: str
    execution_mode: ExecutionMode
    current_step: str
    manifest_path: Path
    checkpoints: tuple[StepCheckpoint, ...]
    instruction: StepInstruction
    next_action: str
    updated_at: str


def normalize_execution_mode(value: str | None) -> ExecutionMode:
    """把配置或命令行里的执行模式归一化成受支持的值。"""

    normalized = str(value or "").strip().lower()
    if normalized == "controller-agent":
        return "controller-agent"
    return "builtin"


def execution_manifest_name(report_date: date) -> str:
    """返回单日执行 manifest 的文件名。"""

    return f"c114_execution_manifest_{report_date.strftime('%Y%m%d')}.yaml"


def create_controller_agent_bridge(
    *,
    report_date: str,
    manifest_path: Path,
    current_step: str,
    checkpoints: list[StepCheckpoint],
    instruction: StepInstruction,
    next_action: str,
) -> ControllerAgentBridge:
    """构造一次 controller-agent 停点所需的 manifest 数据。"""

    return ControllerAgentBridge(
        report_date=report_date,
        execution_mode="controller-agent",
        current_step=current_step,
        manifest_path=manifest_path,
        checkpoints=tuple(checkpoints),
        instruction=instruction,
        next_action=next_action,
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )


def save_controller_agent_bridge(bridge: ControllerAgentBridge) -> None:
    """把控制 agent bridge 渲染并写入 manifest 文件。"""

    bridge.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    bridge.manifest_path.write_text(render_controller_agent_bridge(bridge), encoding="utf-8")


def render_controller_agent_bridge(bridge: ControllerAgentBridge) -> str:
    """把控制 agent bridge 渲染成 YAML 文本。"""

    lines = [
        f"report_date: '{escape_yaml(str(bridge.report_date))}'",
        f"execution_mode: '{bridge.execution_mode}'",
        f"current_step: '{escape_yaml(bridge.current_step)}'",
        f"updated_at: '{escape_yaml(bridge.updated_at)}'",
        f"manifest_path: '{escape_yaml(str(bridge.manifest_path))}'",
        f"next_action: '{escape_yaml(bridge.next_action)}'",
        "steps:",
    ]
    for checkpoint in bridge.checkpoints:
        lines.extend(render_checkpoint_lines(checkpoint))
    lines.extend(
        [
            "instruction:",
            f"  step_name: '{escape_yaml(bridge.instruction.step_name)}'",
            f"  prompt_path: '{escape_yaml(str(bridge.instruction.prompt_path or ''))}'",
            "  input_paths:",
        ]
    )
    if bridge.instruction.input_paths:
        for input_path in bridge.instruction.input_paths:
            lines.append(f"    - '{escape_yaml(str(input_path))}'")
    else:
        lines.append("    []")
    lines.extend(
        [
            f"  output_path: '{escape_yaml(str(bridge.instruction.output_path))}'",
            "  required_fields:",
        ]
    )
    if bridge.instruction.required_fields:
        for field_name in bridge.instruction.required_fields:
            lines.append(f"    - '{escape_yaml(field_name)}'")
    else:
        lines.append("    []")
    lines.append("  notes:")
    if bridge.instruction.notes:
        for note in bridge.instruction.notes:
            lines.append(f"    - '{escape_yaml(note)}'")
    else:
        lines.append("    []")
    return "\n".join(lines)


def render_checkpoint_lines(checkpoint: StepCheckpoint) -> list[str]:
    """渲染单个步骤状态块。"""

    lines = [
        f"  - step_name: '{escape_yaml(checkpoint.step_name)}'",
        f"    status: '{escape_yaml(checkpoint.status)}'",
        f"    output_path: '{escape_yaml(str(checkpoint.output_path))}'",
        f"    prompt_path: '{escape_yaml(str(checkpoint.prompt_path or ''))}'",
        "    input_paths:",
    ]
    if checkpoint.input_paths:
        for input_path in checkpoint.input_paths:
            lines.append(f"      - '{escape_yaml(str(input_path))}'")
    else:
        lines.append("      []")
    lines.append("    required_fields:")
    if checkpoint.required_fields:
        for field_name in checkpoint.required_fields:
            lines.append(f"      - '{escape_yaml(field_name)}'")
    else:
        lines.append("      []")
    lines.append(f"    error: '{escape_yaml(checkpoint.error)}'")
    lines.append("    notes:")
    if checkpoint.notes:
        for note in checkpoint.notes:
            lines.append(f"      - '{escape_yaml(note)}'")
    else:
        lines.append("      []")
    return lines


def build_checkpoint_sequence(
    *,
    ready_step: str,
    output_paths: dict[str, Path],
    prompt_paths: dict[str, Path | None] | None = None,
    input_paths: dict[str, tuple[Path, ...]] | None = None,
    required_fields: dict[str, tuple[str, ...]] | None = None,
    errors: dict[str, str] | None = None,
    notes: dict[str, tuple[str, ...]] | None = None,
) -> list[StepCheckpoint]:
    """按固定顺序生成一整条步骤状态序列。"""

    prompt_map = prompt_paths or {}
    input_map = input_paths or {}
    fields_map = required_fields or {}
    error_map = errors or {}
    note_map = notes or {}
    checkpoints: list[StepCheckpoint] = []
    seen_ready = False
    for step_name in ALL_STEP_NAMES:
        if step_name not in output_paths:
            continue
        if step_name == ready_step:
            status: CheckpointStatus = "ready_for_agent"
            seen_ready = True
        elif seen_ready:
            status = "pending"
        else:
            status = "validated"
        if error_map.get(step_name):
            status = "failed"
        checkpoints.append(
            StepCheckpoint(
                step_name=step_name,
                status=status,
                output_path=output_paths[step_name],
                prompt_path=prompt_map.get(step_name),
                input_paths=input_map.get(step_name, ()),
                required_fields=fields_map.get(step_name, ()),
                error=error_map.get(step_name, ""),
                notes=note_map.get(step_name, ()),
            )
        )
    return checkpoints


def escape_yaml(value: str) -> str:
    """转义 YAML 单引号标量。"""

    return value.replace("'", "''")
