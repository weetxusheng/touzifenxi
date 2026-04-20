from __future__ import annotations

import time

from .market_data import _requests_direct_connection
from ..models import UniverseFilter
from ..db.storage import ResearchStore


def fetch_industry_dictionary(network_mode: str = "direct") -> list[tuple[str, str, str]]:
    import akshare as ak

    rows: list[tuple[str, str, str]] = []
    with _requests_direct_connection(enabled=network_mode == "direct"):
        ths_df = ak.stock_board_industry_name_ths()
        cninfo_df = ak.stock_industry_category_cninfo(symbol="巨潮行业分类标准")

    for item in ths_df.to_dict("records"):
        rows.append((str(item["name"]), str(item["code"]), "ths_board"))

    level_four = cninfo_df[cninfo_df["分级"] == "4"] if "分级" in cninfo_df.columns else cninfo_df
    for item in level_four.to_dict("records"):
        rows.append((str(item["类目名称"]), str(item["类目编码"]), "cninfo"))

    dedup = {}
    for name, code, source in rows:
        dedup[(name, source)] = (name, code, source)
    return list(dedup.values())


def sync_candidate_stock_industries(
    store: ResearchStore,
    network_mode: str = "direct",
    limit: int = 200,
    offset: int = 0,
    only_missing: bool = True,
) -> int:
    """Sync formal stock-industry mappings for current candidates."""
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
