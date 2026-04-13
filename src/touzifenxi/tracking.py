from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .market_data import _call_with_timeout, _requests_direct_connection


def _sina_symbol_from_ticker(ticker: str) -> str:
    code, exchange = ticker.split(".")
    if exchange == "SH":
        return f"sh{code}"
    if exchange == "SZ":
        return f"sz{code}"
    raise ValueError(f"Unsupported ticker for return tracking: {ticker}")


@dataclass
class ReturnUpdate:
    recommendation_id: int
    horizon_1d: float | None
    horizon_5d: float | None
    horizon_20d: float | None
    horizon_60d: float | None


def _compute_return(base_price: float, future_price: float | None) -> float | None:
    if future_price is None or base_price == 0:
        return None
    return (future_price / base_price) - 1


def fetch_forward_returns(
    ticker: str,
    run_date: str,
    base_price: float,
    network_mode: str = "direct",
) -> dict[str, float | None]:
    import akshare as ak

    symbol = _sina_symbol_from_ticker(ticker)
    start_date = (datetime.fromisoformat(run_date).date() - timedelta(days=5)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    with _requests_direct_connection(enabled=network_mode == "direct"):
        try:
            df = _call_with_timeout(
                ak.stock_zh_a_daily,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
                timeout_seconds=15.0,
            )
        except Exception:
            df = None

    if df is None or df.empty:
        return {"horizon_1d": None, "horizon_5d": None, "horizon_20d": None, "horizon_60d": None}

    df = df.sort_values("date").reset_index(drop=True)
    run_dt = datetime.fromisoformat(run_date).date()
    forward_df = df[df["date"] > run_dt].reset_index(drop=True)

    def horizon_close(offset: int) -> float | None:
        if len(forward_df) < offset:
            return None
        return float(forward_df.iloc[offset - 1]["close"])

    return {
        "horizon_1d": _compute_return(base_price, horizon_close(1)),
        "horizon_5d": _compute_return(base_price, horizon_close(5)),
        "horizon_20d": _compute_return(base_price, horizon_close(20)),
        "horizon_60d": _compute_return(base_price, horizon_close(60)),
    }
