from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import RunResult


def render_report(result: RunResult) -> str:
    all_theme = all(rec.stock.prefilter_bucket != "wildcard" for rec in result.recommendations) if result.recommendations else True
    wildcard_used = any(rec.stock.prefilter_bucket == "wildcard" for rec in result.recommendations)
    weekly_pool_week = next((rec.stock.prefilter_week for rec in result.recommendations if rec.stock.prefilter_week), "N/A")
    weekly_themes = sorted({rec.stock.prefilter_theme for rec in result.recommendations if rec.stock.prefilter_theme})
    weekly_buckets: dict[str, int] = {}
    for rec in result.recommendations:
        bucket = rec.stock.prefilter_bucket or "unassigned"
        weekly_buckets[bucket] = weekly_buckets.get(bucket, 0) + 1
    lines = [
        f"# 每日选股日报 - {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"- 数据源: `{result.data_source}`",
        f"- 样本数量: `{result.universe_size}`",
        f"- Ready Pool 数量: `{result.ready_pool_size}`",
        f"- Fallback Pool 数量: `{result.fallback_pool_size}`",
        f"- Ready Pool 覆盖率: `{result.coverage_ratio:.2%}`",
        f"- 路由模式: `{result.router_mode}`",
        f"- 激活主题数: `{len(result.active_themes)}`",
        f"- 旁路候选数: `{result.bypass_count}`",
        f"- 周度池周次: `{weekly_pool_week}`",
        f"- 本周命中主题: `{', '.join(weekly_themes) if weekly_themes else 'N/A'}`",
        f"- 推荐分层: `{', '.join(f'{name}:{count}' for name, count in weekly_buckets.items()) if weekly_buckets else 'N/A'}`",
        f"- 是否全部来自主题池: `{all_theme}`",
        f"- 是否触发 wildcard: `{wildcard_used}`",
        f"- 风格判断: `{result.style_view.dominant_style}`",
        f"- 风格置信度: `{result.style_view.confidence:.2f}`",
        f"- 风格说明: {result.style_view.reason}",
        "",
        "## 投委会约束",
        "",
    ]
    for note in result.portfolio_notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
        "## 推荐前 5",
        "",
        ]
    )

    for index, rec in enumerate(result.recommendations, start=1):
        lines.extend(
            [
                f"### {index}. {rec.stock.ticker} {rec.stock.name}",
                "",
                f"- 综合得分: `{rec.total_score:.4f}`",
                f"- 阶段: `{rec.stage}`",
                f"- 行业: `{rec.stock.sector}`",
                f"- 现价: `{rec.stock.last_price:.2f}`",
                f"- 基本面数据源: `{rec.stock.fundamental_source}`",
                f"- 行业映射: `{'formal' if rec.stock.industry_ready else 'fallback'}` / Ready Pool: `{rec.stock.ready_pool}`",
                f"- 主题路由: `{rec.stock.router_mode}` / 主题: `{rec.stock.theme_name or 'N/A'}` / 分层: `{rec.stock.theme_bucket}` / 来源: `{rec.stock.theme_source or 'N/A'}` / 强度: `{rec.stock.theme_strength:.2f}`",
                f"- 周度池: `{rec.stock.prefilter_week or 'N/A'}` / 主题: `{rec.stock.prefilter_theme or 'N/A'}` / 来源: `{rec.stock.prefilter_bucket or 'N/A'}` / 分数: `{rec.stock.prefilter_score:.2f}`",
                f"- Agent得分: 基本面 `{rec.agent_scores['fundamental'].score:.2f}` / 技术面 `{rec.agent_scores['technical'].score:.2f}` / 风险 `{rec.agent_scores['risk'].score:.2f}` / 事件 `{rec.agent_scores['event'].score:.2f}`",
                f"- 理由: {'；'.join(rec.reasons)}",
                f"- 风险: {'；'.join(rec.risks)}",
                "",
            ]
        )
    return "\n".join(lines)


def write_report(reports_dir: Path, content: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"daily_report_{datetime.now().strftime('%Y%m%d')}.md"
    report_path.write_text(content, encoding="utf-8")
    return report_path
