"""简报与 YAML 渲染共用的小型文本工具。

这里放无状态、无 I/O 的渲染辅助，避免各 step 复制 YAML 转义逻辑。
复杂 Markdown 章节渲染仍放在 `brief.markdown`。
"""

from __future__ import annotations

from typing import Any


def escape_yaml(value: str) -> str:
    """转义 YAML 单引号标量中的单引号。"""
    return value.replace("'", "''")


def render_layer_issues_yaml(report_date: str, issues: dict[str, list[dict[str, Any]]]) -> str:
    """把各层问题统计渲染成 YAML 文本。"""
    lines = [f"report_date: '{report_date}'", "layers:"]
    for layer, layer_issues in issues.items():
        lines.append(f"  {layer}:")
        if not layer_issues:
            lines.append("    issues: []")
            continue
        lines.append("    issues:")
        for issue in layer_issues:
            lines.append(f"      - code: '{escape_yaml(str(issue['code']))}'")
            lines.append(f"        count: {issue['count']}")
            lines.append("        examples:")
            for example in issue.get("examples", []):
                lines.append(f"          - '{escape_yaml(str(example))}'")
    return "\n".join(lines)


def render_analysis_list(name: str, values: list[str], indent: str) -> list[str]:
    """把分析字段列表渲染成指定缩进的 YAML 片段。"""
    lines = [f"{indent}{name}:"]
    if values:
        lines.extend(f"{indent}  - '{escape_yaml(value)}'" for value in values)
    return lines
