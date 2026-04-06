from __future__ import annotations

import time

from .market_data import _requests_direct_connection
from .models import UniverseFilter
from .storage import ResearchStore


def sync_candidate_stock_industries(
    store: ResearchStore,
    network_mode: str = "direct",
    limit: int = 200,
    offset: int = 0,
    only_missing: bool = True,
) -> int:
    import akshare as ak

    universe_filter = UniverseFilter(limit=limit, offset=offset)
    if only_missing:
        candidates = store.list_candidates_missing_industry(universe_filter)
    else:
        candidates = store.list_universe_candidates(universe_filter)
    rows = []
    with _requests_direct_connection(enabled=network_mode == "direct"):
        for row in candidates:
            code = str(row["code"])
            ticker = str(row["ticker"])
            try:
                df = ak.stock_industry_change_cninfo(symbol=code, start_date="20091227", end_date="20301231")
            except Exception:
                time.sleep(0.3)
                continue
            if df is None or df.empty:
                continue
            latest = df.sort_values("变更日期").iloc[-1].to_dict()
            rows.append(
                (
                    ticker,
                    str(latest.get("分类标准", "")),
                    str(latest.get("行业编码", "")),
                    str(latest.get("行业门类", "")),
                    str(latest.get("行业次类", "")),
                    str(latest.get("行业大类", "")),
                    str(latest.get("行业中类", "")),
                    "cninfo_stock_industry",
                )
            )
            time.sleep(0.2)
    return store.save_stock_industries(rows)
