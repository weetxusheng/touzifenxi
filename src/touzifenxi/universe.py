from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import random
import time
from typing import List

import pandas as pd

from .market_data import _requests_direct_connection
from .models import UniverseStock


def _base_code(raw_code: str) -> str:
    code = str(raw_code).lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def _ticker_from_code(code: str) -> str:
    normalized = _base_code(code)
    if normalized.startswith(("600", "601", "603", "605", "688")):
        return f"{normalized}.SH"
    if normalized.startswith(("000", "001", "002", "003", "300")):
        return f"{normalized}.SZ"
    if normalized.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "874", "875", "876", "877", "878", "879", "920")):
        return f"{normalized}.BJ"
    return normalized


def _exchange_from_code(code: str) -> str:
    normalized = _base_code(code)
    if normalized.startswith("6"):
        return "SH"
    if normalized.startswith(("0", "2", "3")):
        return "SZ"
    return "BJ"


def _board_from_code(code: str) -> str:
    normalized = _base_code(code)
    if normalized.startswith("688"):
        return "STAR"
    if normalized.startswith("300"):
        return "GEM"
    if normalized.startswith("8") or normalized.startswith("4") or normalized.startswith("920"):
        return "BSE"
    if normalized.startswith(("600", "601", "603", "605")):
        return "MAIN_SH"
    return "MAIN_SZ"


@dataclass
class UniverseSnapshot:
    captured_at: datetime
    stocks: List[UniverseStock]
    source: str


def _fetch_full_universe_eastmoney(network_mode: str) -> tuple[pd.DataFrame, str]:
    import requests

    url = "https://82.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
    }

    with _requests_direct_connection(enabled=network_mode == "direct"):
        response = requests.get(url, params=params, timeout=15)
        payload = response.json()
        first_page = payload.get("data", {}).get("diff", []) or []
        if not first_page:
            raise RuntimeError("eastmoney universe first page is empty")
        total = int(payload["data"]["total"])
        per_page = len(first_page)
        total_page = math.ceil(total / per_page)
        pages = [pd.DataFrame(first_page)]
        success_pages = 1
        for page in range(2, total_page + 1):
            page_params = dict(params)
            page_params["pn"] = str(page)
            page_success = False
            for attempt in range(3):
                try:
                    page_response = requests.get(url, params=page_params, timeout=15)
                    page_payload = page_response.json()
                    diff = page_payload.get("data", {}).get("diff", []) or []
                    if diff:
                        pages.append(pd.DataFrame(diff))
                        success_pages += 1
                    page_success = True
                    break
                except Exception:
                    time.sleep((attempt + 1) * random.uniform(0.8, 1.5))
            if not page_success:
                continue
        if not pages:
            raise RuntimeError("eastmoney universe pagination returned no pages")
        return pd.concat(pages, ignore_index=True), f"eastmoney_spot_pages_{success_pages}"


def _fetch_full_universe_sina(network_mode: str) -> tuple[pd.DataFrame, str]:
    import akshare as ak

    with _requests_direct_connection(enabled=network_mode == "direct"):
        return ak.stock_zh_a_spot(), "akshare_sina_spot"


def fetch_full_universe(network_mode: str = "direct") -> UniverseSnapshot:
    source = "unknown"
    try:
        df, source = _fetch_full_universe_eastmoney(network_mode=network_mode)
        df = df.rename(
            columns={
                "f12": "代码",
                "f14": "名称",
                "f2": "最新价",
                "f3": "涨跌幅",
                "f8": "换手率",
                "f6": "成交额",
            }
        )
    except Exception:
        df, source = _fetch_full_universe_sina(network_mode=network_mode)

    stocks: List[UniverseStock] = []
    for row in df.to_dict("records"):
        code = _base_code(str(row["代码"]))
        name = str(row["名称"])
        latest_price = float(row["最新价"] or 0.0)
        amount = float(row["成交额"] or 0.0)
        turnover_ratio = float(row["换手率"] or 0.0) if "换手率" in row else 0.0
        change_percent = float(row["涨跌幅"] or 0.0)
        stocks.append(
            UniverseStock(
                ticker=_ticker_from_code(code),
                code=code,
                name=name,
                exchange=_exchange_from_code(code),
                board=_board_from_code(code),
                latest_price=latest_price,
                change_percent=change_percent,
                turnover_ratio=turnover_ratio,
                amount=amount,
                is_st="ST" in name.upper(),
                is_suspended=latest_price == 0.0,
            )
        )
    return UniverseSnapshot(
        captured_at=datetime.now(),
        stocks=stocks,
        source=source,
    )
