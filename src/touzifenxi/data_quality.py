from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .models import UniverseFilter


@dataclass(frozen=True)
class QualityItem:
    category: str
    name: str
    status: str
    value_text: str
    threshold_text: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class QualityResult:
    context: str
    status: str
    checked_at: str
    summary: dict[str, Any]
    items: list[QualityItem]


def _previous_weekday(day: date) -> date:
    current = day
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def expected_factor_date(today: date | None = None) -> tuple[str, str]:
    # First version uses deterministic local trading-day fallback. This is
    # intentionally explicit so the dashboard can show that it is not an
    # exchange calendar confirmation.
    current = today or datetime.now().date()
    return _previous_weekday(current).isoformat(), "calendar_fallback"


def _worst_status(statuses: list[str]) -> str:
    order = {"green": 0, "weak": 1, "yellow": 2, "red": 3}
    if not statuses:
        return "green"
    return max(statuses, key=lambda status: order.get(status, 0))


def run_data_quality_check(store, context: str = "manual", related_run_id: int | None = None) -> tuple[int, QualityResult]:
    latest_snapshot = store.get_latest_factor_snapshot_date()
    expected_snapshot, calendar_source = expected_factor_date()
    weekly_summary = store.get_latest_weekly_pool_summary()
    weekly_rows = [dict(row) for row in store.get_latest_weekly_pool_rows(limit=50)]
    pool_tickers = [str(row["ticker"]) for row in weekly_rows]
    db_status = store.get_database_status()

    coverage = store.get_coverage_summary(
        snapshot_date=latest_snapshot or "1970-01-01",
        universe_filter=UniverseFilter(limit=1),
    )
    latest_run = store.get_performance_summary().get("latest_run")
    pool_quality = store.get_weekly_pool_quality_snapshot(pool_tickers, latest_snapshot or "1970-01-01")
    event_quality = store.get_recent_event_quality(days=7)
    universe_quality = store.get_universe_quality_snapshot()

    pool_size = len(weekly_rows)
    factor_covered = int(pool_quality["factor_covered"])
    real_financial = int(pool_quality["real_financial"])
    financial_stale = int(pool_quality["financial_stale"])
    local_profile = int(pool_quality["local_profile"])
    industry_covered = int(pool_quality["industry_covered"])
    event_total = int(event_quality["event_count"])
    event_url_count = int(event_quality["url_count"])
    event_url_ratio = (event_url_count / event_total) if event_total else 0.0
    recommendation_real_ratio = (
        float(latest_run["recommendation_real_financial_ratio"])
        if latest_run and latest_run.get("recommendation_real_financial_ratio") is not None
        else 0.0
    )

    items = [
        QualityItem(
            category="universe",
            name="股票主表",
            status="green" if int(universe_quality["count"]) > 0 else "red",
            value_text=f"{universe_quality['count']}只 / {universe_quality['latest_synced_at'] or 'N/A'}",
            threshold_text="主表存在且有同步时间",
            detail=universe_quality,
        ),
        QualityItem(
            category="factor",
            name="因子快照日期",
            status="green" if latest_snapshot == expected_snapshot else "red",
            value_text=f"{latest_snapshot or 'N/A'}",
            threshold_text=f"最近交易日 {expected_snapshot}",
            detail={"calendar_source": calendar_source, "expected_snapshot": expected_snapshot},
        ),
        QualityItem(
            category="factor",
            name="周度池因子覆盖",
            status="green" if factor_covered >= 45 else "red",
            value_text=f"{factor_covered}/{pool_size}",
            threshold_text=">=45/50",
            detail={"pool_size": pool_size, "snapshot_date": latest_snapshot},
        ),
        QualityItem(
            category="financial",
            name="周度池真实财务",
            status="green" if pool_size and real_financial / pool_size >= 0.8 else "yellow" if pool_size and real_financial / pool_size >= 0.6 else "red",
            value_text=f"{real_financial}/{pool_size}",
            threshold_text=">=80% green, >=60% yellow",
            detail={"financial_stale": financial_stale, "local_profile": local_profile},
        ),
        QualityItem(
            category="financial",
            name="最新推荐真实财务",
            status="green" if recommendation_real_ratio >= 0.8 else "yellow" if recommendation_real_ratio >= 0.6 else "red",
            value_text=f"{recommendation_real_ratio:.0%}",
            threshold_text=">=80% green, >=60% yellow",
            detail={"latest_run_id": latest_run.get("id") if latest_run else None},
        ),
        QualityItem(
            category="industry",
            name="周度池正式行业",
            status="green" if pool_size and industry_covered / pool_size >= 0.8 else "yellow" if pool_size and industry_covered / pool_size >= 0.6 else "red",
            value_text=f"{industry_covered}/{pool_size}",
            threshold_text=">=80% green, >=60% yellow",
            detail={},
        ),
        QualityItem(
            category="event",
            name="近期事件链接证据",
            status="green" if event_total and event_url_ratio >= 0.8 else "weak" if event_total else "yellow",
            value_text=f"{event_url_count}/{event_total}",
            threshold_text="无 URL 标记为 weak_evidence",
            detail=event_quality,
        ),
        QualityItem(
            category="database",
            name="数据库后端",
            status="green" if db_status["active_backend"] == "postgresql" else "yellow",
            value_text=str(db_status["active_backend"]),
            threshold_text="生产主库应为 postgresql",
            detail=db_status,
        ),
    ]
    status = _worst_status([item.status for item in items])
    summary = {
        "latest_snapshot": latest_snapshot,
        "latest_factor_snapshot": latest_snapshot,
        "expected_snapshot": expected_snapshot,
        "expected_factor_date": expected_snapshot,
        "calendar_source": calendar_source,
        "weekly_pool_id": weekly_summary["id"] if weekly_summary else None,
        "weekly_pool_size": pool_size,
        "weekly_pool_factor_covered": factor_covered,
        "weekly_pool_real_financial": real_financial,
        "weekly_pool_industry_covered": industry_covered,
        "recommendation_real_financial_ratio": recommendation_real_ratio,
        "recent_event_count": event_total,
        "event_url_count": event_url_count,
        "event_url_ratio": event_url_ratio,
        "active_backend": db_status["active_backend"],
        "coverage": coverage,
    }
    result = QualityResult(
        context=context,
        status=status,
        checked_at=datetime.now().isoformat(timespec="seconds"),
        summary=summary,
        items=items,
    )
    quality_run_id = store.save_data_quality_result(result, related_run_id=related_run_id)
    return quality_run_id, result


def quality_result_to_json(result: QualityResult) -> str:
    return json.dumps(
        {
            "context": result.context,
            "status": result.status,
            "checked_at": result.checked_at,
            "summary": result.summary,
            "items": [
                {
                    "category": item.category,
                    "name": item.name,
                    "status": item.status,
                    "value_text": item.value_text,
                    "threshold_text": item.threshold_text,
                    "detail": item.detail,
                }
                for item in result.items
            ],
        },
        ensure_ascii=False,
    )
