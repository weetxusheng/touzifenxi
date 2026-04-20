from __future__ import annotations

from .market_data import (
    _call_with_retries,
    _call_with_timeout,
    _fetch_financial_profile,
    _requests_direct_connection,
)
from ..models import UniverseFilter
from ..db.storage import ResearchStore


def sync_candidate_financial_profiles(
    store: ResearchStore,
    network_mode: str = "direct",
    limit: int = 100,
    offset: int = 0,
    only_missing: bool = True,
) -> int:
    import akshare as ak

    universe_filter = UniverseFilter(limit=limit, offset=offset)
    if only_missing:
        candidates = store.list_candidates_missing_financials(universe_filter)
    else:
        candidates = store.list_universe_candidates(universe_filter)

    rows = []
    with _requests_direct_connection(enabled=network_mode == "direct"):
        for row in candidates:
            code = str(row["code"])
            ticker = str(row["ticker"])
            try:
                profile = _call_with_timeout(
                    _call_with_retries,
                    _fetch_financial_profile,
                    ak,
                    code,
                    retries=2,
                    delay=0.5,
                    timeout_seconds=12.0,
                )
            except Exception:
                continue

            source = str(profile.get("fundamental_source", "local_profile"))
            if source == "local_profile":
                continue

            rows.append(
                (
                    ticker,
                    float(profile.get("earnings_growth", 0.10)),
                    float(profile.get("revenue_growth", 0.10)),
                    float(profile.get("roe", 0.12)),
                    float(profile.get("free_cashflow_margin", 0.08)),
                    float(profile.get("event_score", 0.30)),
                    0.50,
                    source,
                    str(profile.get("report_period", "")) or None,
                )
            )

    return store.save_financial_profiles(rows)
