from __future__ import annotations

from .market_data import _requests_direct_connection


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
