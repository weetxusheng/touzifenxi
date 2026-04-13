from __future__ import annotations

import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List

from .data_loader import load_universe, load_watchlist
from .models import StockIdea
from .tagging import infer_sector, infer_style_tags


def _normalize_code(raw_code: str) -> str:
    return raw_code.split(".")[0]


def _safe_pct_change(current: float, base: float) -> float:
    if not base:
        return 0.0
    return (current / base) - 1


def _percentile_rank(values: List[float], current: float) -> float:
    if not values:
        return 0.5
    ordered = sorted(values)
    position = sum(1 for value in ordered if value <= current)
    return position / len(ordered)


def _normalized_metric(value: float | None, scale: float, fallback: float) -> float:
    if value is None:
        return fallback
    return max(0.0, min(1.0, value / scale))


def _coerce_numeric(value: object) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _latest_abstract_metric(rows: dict[str, dict[str, object]], *labels: str) -> float | None:
    for label in labels:
        row = rows.get(label)
        if not row:
            continue
        for column, value in row.items():
            if column in {"选项", "指标"}:
                continue
            numeric = _coerce_numeric(value)
            if numeric is not None:
                return numeric
    return None


def _call_with_retries(func, *args, retries: int = 3, delay: float = 1.0, **kwargs):
    last_error = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt == retries - 1:
                raise
            time.sleep(delay * (attempt + 1))
    raise last_error


def _call_with_timeout(func, *args, timeout_seconds: float = 20.0, **kwargs):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            raise TimeoutError(f"{getattr(func, '__name__', 'call')} timed out after {timeout_seconds}s") from exc


@contextmanager
def _requests_direct_connection(enabled: bool = True):
    if not enabled:
        yield
        return

    import requests

    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ]
    saved_env = {key: os.environ.get(key) for key in proxy_keys}
    original_init = requests.sessions.Session.__init__
    original_getproxies = urllib.request.getproxies
    original_getproxies_environment = urllib.request.getproxies_environment
    original_getproxies_registry = getattr(urllib.request, "getproxies_registry", None)

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.trust_env = False

    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        requests.sessions.Session.__init__ = patched_init
        urllib.request.getproxies = lambda: {}
        urllib.request.getproxies_environment = lambda: {}
        if original_getproxies_registry is not None:
            urllib.request.getproxies_registry = lambda: {}
        yield
    finally:
        requests.sessions.Session.__init__ = original_init
        urllib.request.getproxies = original_getproxies
        urllib.request.getproxies_environment = original_getproxies_environment
        if original_getproxies_registry is not None:
            urllib.request.getproxies_registry = original_getproxies_registry
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _fetch_financial_profile(ak: object, symbol: str) -> Dict[str, float | str]:
    profile: Dict[str, float | str] = {"fundamental_source": "local_profile"}
    try:
        indicator_df = ak.stock_financial_analysis_indicator(symbol=symbol)
    except Exception:
        indicator_df = None

    if indicator_df is not None and not indicator_df.empty:
        latest = indicator_df.iloc[0].to_dict()
        roe = latest.get("净资产收益率(%)") or latest.get("净资产收益率")
        gross_margin = latest.get("销售毛利率(%)") or latest.get("销售毛利率")
        net_margin = latest.get("净利率(%)") or latest.get("净利率")
        revenue_growth = latest.get("主营业务收入增长率(%)") or latest.get("营业收入增长率(%)")
        profit_growth = latest.get("净利润增长率(%)") or latest.get("扣非净利润增长率(%)")

        profile.update(
            {
                "roe": _normalized_metric(float(roe) / 100 if roe is not None else None, 0.30, 0.5),
                "free_cashflow_margin": _normalized_metric(float(net_margin) / 100 if net_margin is not None else None, 0.20, 0.4),
                "earnings_growth": _normalized_metric(float(profit_growth) / 100 if profit_growth is not None else None, 0.30, 0.4),
                "revenue_growth": _normalized_metric(float(revenue_growth) / 100 if revenue_growth is not None else None, 0.20, 0.35),
                "event_score": _normalized_metric(float(gross_margin) / 100 if gross_margin is not None else None, 0.40, 0.3),
                "fundamental_source": "akshare_financial_indicator",
            }
        )
        return profile

    try:
        abstract_df = ak.stock_financial_abstract(symbol=symbol)
    except Exception:
        return profile

    if abstract_df is None or abstract_df.empty or "指标" not in abstract_df.columns:
        return profile

    report_columns = [
        str(column)
        for column in abstract_df.columns
        if str(column).isdigit() and len(str(column)) == 8
    ]
    latest_report_period = report_columns[0] if report_columns else None
    abstract_rows = {
        str(row.get("指标", "")): row
        for row in abstract_df.to_dict("records")
    }
    roe = _latest_abstract_metric(abstract_rows, "净资产收益率(ROE)", "净资产收益率_平均")
    net_margin = _latest_abstract_metric(abstract_rows, "销售净利率")
    revenue_growth = _latest_abstract_metric(abstract_rows, "营业总收入增长率")
    profit_growth = _latest_abstract_metric(abstract_rows, "归属母公司净利润增长率")
    cashflow_margin = _latest_abstract_metric(abstract_rows, "经营活动净现金/销售收入", "经营性现金净流量/营业总收入")

    profile.update(
        {
            "roe": _normalized_metric(roe / 100 if roe is not None else None, 0.30, 0.5),
            "free_cashflow_margin": _normalized_metric(cashflow_margin / 100 if cashflow_margin is not None else net_margin / 100 if net_margin is not None else None, 0.20, 0.4),
            "earnings_growth": _normalized_metric(profit_growth / 100 if profit_growth is not None else None, 0.30, 0.4),
            "revenue_growth": _normalized_metric(revenue_growth / 100 if revenue_growth is not None else None, 0.20, 0.35),
            "event_score": _normalized_metric(net_margin / 100 if net_margin is not None else None, 0.25, 0.3),
            "fundamental_source": "akshare_financial_abstract",
            "report_period": latest_report_period or "",
        }
    )
    return profile


def _fetch_valuation_profile(ak: object, symbol: str) -> Dict[str, float]:
    try:
        spot_df = ak.stock_zh_a_spot_em()
    except Exception:
        return {}

    if spot_df is None or spot_df.empty or "代码" not in spot_df.columns:
        return {}

    matched = spot_df[spot_df["代码"].astype(str) == symbol]
    if matched.empty:
        return {}

    row = matched.iloc[0].to_dict()
    pe = row.get("市盈率-动态")
    pb = row.get("市净率")
    valuation_score = 0.5
    signals = 0

    if pe not in (None, "-", ""):
        pe_value = float(pe)
        valuation_score += 0.15 if 0 < pe_value <= 18 else 0.05 if pe_value <= 30 else -0.10
        signals += 1
    if pb not in (None, "-", ""):
        pb_value = float(pb)
        valuation_score += 0.10 if 0 < pb_value <= 2.5 else 0.02 if pb_value <= 4 else -0.08
        signals += 1

    if not signals:
        return {}

    return {"valuation_percentile": max(0.0, min(1.0, 1 - valuation_score))}


def _normalize_symbol_for_sina(raw_code: str) -> str:
    code = _normalize_code(raw_code)
    return f"sh{code}" if code.startswith("6") else f"sz{code}"


@dataclass
class SampleDataProvider:
    universe_path: object

    def load(self) -> tuple[str, List[StockIdea]]:
        return "sample", load_universe(self.universe_path)


@dataclass
class AkshareDataProvider:
    watchlist_path: object
    lookback_days: int = 200
    direct_connection: bool = True
    use_eastmoney_valuation: bool = False

    def load(self) -> tuple[str, List[StockIdea]]:
        watchlist = load_watchlist(self.watchlist_path)
        return load_akshare_watchlist(
            watchlist=watchlist,
            lookback_days=self.lookback_days,
            direct_connection=self.direct_connection,
            use_eastmoney_valuation=self.use_eastmoney_valuation,
        )


def load_akshare_watchlist(
    watchlist: list[dict],
    lookback_days: int = 200,
    direct_connection: bool = True,
    use_eastmoney_valuation: bool = False,
) -> tuple[str, List[StockIdea]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("akshare not installed") from exc

    with _requests_direct_connection(enabled=direct_connection):
        valuation_cache = _fetch_valuation_profile if use_eastmoney_valuation else (lambda _ak, _symbol: {})
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
        end_date = datetime.now().strftime("%Y%m%d")
        stock_ideas: List[StockIdea] = []
        volatilities: List[float] = []

        raw_metrics = []
        for item in watchlist:
            symbol = _normalize_code(item["ticker"])
            hist_source = "eastmoney"
            try:
                hist = _call_with_timeout(
                    _call_with_retries,
                    ak.stock_zh_a_hist,
                    symbol=symbol,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq",
                    timeout_seconds=20.0,
                )
            except Exception:
                try:
                    hist = _call_with_timeout(
                        _call_with_retries,
                        ak.stock_zh_a_daily,
                        symbol=_normalize_symbol_for_sina(item["ticker"]),
                        start_date=start_date,
                        end_date=end_date,
                        adjust="qfq",
                        timeout_seconds=20.0,
                    )
                    hist_source = "sina"
                except Exception:
                    continue

            if hist is None or hist.empty or len(hist) < 80:
                continue

            fallback_profile = {
                "earnings_growth": float(item.get("earnings_growth", 0.10)),
                "revenue_growth": float(item.get("revenue_growth", 0.10)),
                "roe": float(item.get("roe", 0.12)),
                "free_cashflow_margin": float(item.get("free_cashflow_margin", 0.08)),
                "event_score": float(item.get("event_score", 0.30)),
                "fundamental_source": str(item.get("fundamental_source", "local_profile")),
            }
            try:
                financial_profile = _call_with_timeout(
                    _call_with_retries,
                    _fetch_financial_profile,
                    ak,
                    symbol,
                    retries=2,
                    delay=0.5,
                    timeout_seconds=12.0,
                )
            except Exception:
                financial_profile = fallback_profile
            if str(financial_profile.get("fundamental_source", "local_profile")) == "local_profile":
                financial_profile = fallback_profile
            try:
                valuation_profile = _call_with_timeout(
                    _call_with_retries,
                    valuation_cache,
                    ak,
                    symbol,
                    retries=2,
                    delay=0.5,
                    timeout_seconds=12.0,
                )
            except Exception:
                valuation_profile = {}

            if hist_source == "eastmoney":
                hist = hist.sort_values("日期").reset_index(drop=True)
                close = hist["收盘"].astype(float)
                volume = hist["成交量"].astype(float)
                amount = hist["成交额"].astype(float) if "成交额" in hist.columns else volume * close
            else:
                hist = hist.sort_values("date").reset_index(drop=True)
                close = hist["close"].astype(float)
                volume = hist["volume"].astype(float)
                amount = hist["amount"].astype(float) if "amount" in hist.columns else volume * close
            ma20 = close.rolling(20).mean()
            ma60 = close.rolling(60).mean()
            volume_ma20 = volume.rolling(20).mean()
            amount_ma20 = amount.rolling(20).mean()
            returns = close.pct_change().dropna()

            last_close = float(close.iloc[-1])
            raw_metrics.append(
                {
                    "item": item,
                    "financial_profile": financial_profile,
                    "valuation_profile": valuation_profile,
                    "hist_source": hist_source,
                    "last_close": last_close,
                    "momentum_20d": _safe_pct_change(last_close, float(close.iloc[-21])),
                    "momentum_60d": _safe_pct_change(last_close, float(close.iloc[-61])),
                    "relative_strength_raw": _safe_pct_change(last_close, float(close.iloc[-41])),
                    "volume_trend": _safe_pct_change(float(volume_ma20.iloc[-1]), float(volume_ma20.iloc[-21])),
                    "turnover_trend": _safe_pct_change(float(amount_ma20.iloc[-1]), float(amount_ma20.iloc[-21])),
                    "drawdown_from_high": _safe_pct_change(last_close, float(close.tail(120).max())),
                    "volatility": float(returns.tail(60).std() * (252 ** 0.5)),
                    "ma20_gap": _safe_pct_change(last_close, float(ma20.iloc[-1])),
                    "ma60_gap": _safe_pct_change(last_close, float(ma60.iloc[-1])),
                    "price_above_ma20": last_close >= float(ma20.iloc[-1]),
                    "price_above_ma60": last_close >= float(ma60.iloc[-1]),
                    "ma20_slope": _safe_pct_change(float(ma20.iloc[-1]), float(ma20.iloc[-6])),
                    "crowding_proxy": _safe_pct_change(float(amount.iloc[-1]), float(amount_ma20.iloc[-1])),
                }
            )
            volatilities.append(raw_metrics[-1]["volatility"])

        if not raw_metrics:
            raise RuntimeError("akshare returned no usable market data")

        rs_values = [item["relative_strength_raw"] for item in raw_metrics]
        crowding_values = [item["crowding_proxy"] for item in raw_metrics]
        volatility_values = volatilities[:]

        for metrics in raw_metrics:
            item = metrics["item"]
            financial_profile = metrics["financial_profile"]
            valuation_profile = metrics["valuation_profile"]
            hist_source = metrics["hist_source"]
            stock_ideas.append(
                StockIdea(
                    ticker=f"{_normalize_code(item['ticker'])}{'.SH' if _normalize_code(item['ticker']).startswith('6') else '.SZ'}",
                    name=item["name"],
                    sector=item["sector"],
                    style_tags=item["style_tags"],
                    valuation_percentile=float(valuation_profile.get("valuation_percentile", item["valuation_percentile"])),
                    earnings_growth=float(financial_profile.get("earnings_growth", item["earnings_growth"])),
                    revenue_growth=float(financial_profile.get("revenue_growth", item["revenue_growth"])),
                    roe=float(financial_profile.get("roe", item["roe"])),
                    free_cashflow_margin=float(financial_profile.get("free_cashflow_margin", item["free_cashflow_margin"])),
                    momentum_20d=float(metrics["momentum_20d"]),
                    momentum_60d=float(metrics["momentum_60d"]),
                    relative_strength=_percentile_rank(rs_values, metrics["relative_strength_raw"]),
                    volume_trend=max(0.0, min(1.0, 0.5 + metrics["volume_trend"] * 2)),
                    drawdown_from_high=float(metrics["drawdown_from_high"]),
                    volatility=max(0.0, min(1.0, _percentile_rank(volatility_values, metrics["volatility"]))),
                    crowding=max(0.0, min(1.0, _percentile_rank(crowding_values, metrics["crowding_proxy"]))),
                    event_score=float(financial_profile.get("event_score", item["event_score"])),
                    last_price=float(metrics["last_close"]),
                    ma20_gap=float(metrics["ma20_gap"]),
                    ma60_gap=float(metrics["ma60_gap"]),
                    price_above_ma20=bool(metrics["price_above_ma20"]),
                    price_above_ma60=bool(metrics["price_above_ma60"]),
                    ma20_slope=float(metrics["ma20_slope"]),
                    turnover_trend=max(0.0, min(1.0, 0.5 + metrics["turnover_trend"] * 2)),
                    data_source=f"akshare_{hist_source}",
                    fundamental_source=str(financial_profile.get("fundamental_source", "local_profile")),
                    industry_ready=bool(item.get("industry_ready", False)),
                    financial_ready=bool(item.get("financial_ready", False)),
                    factor_ready=bool(item.get("factor_ready", False)),
                    ready_pool=bool(item.get("ready_pool", False)),
                    theme_name=str(item.get("theme_name", "")),
                    theme_bucket=str(item.get("theme_bucket", "fallback")),
                    theme_source=str(item.get("theme_source", "")),
                    theme_strength=float(item.get("theme_strength", 0.0)),
                    router_mode=str(item.get("router_mode", "fallback")),
                    prefilter_week=str(item.get("prefilter_week", "")),
                    prefilter_theme=str(item.get("prefilter_theme", "")),
                    prefilter_bucket=str(item.get("prefilter_bucket", "")),
                    prefilter_score=float(item.get("prefilter_score", 0.0)),
                    policy_score=float(item.get("policy_score", 0.0)),
                    valuation_score=float(item.get("valuation_score", 0.0)),
                    performance_score=float(item.get("performance_score", 0.0)),
                    prefilter_source=str(item.get("prefilter_source", "")),
                )
            )

    data_source_label = "akshare_mixed"
    if stock_ideas:
        sources = {idea.data_source for idea in stock_ideas}
        if len(sources) == 1:
            data_source_label = sources.pop()
    return data_source_label, stock_ideas


def load_market_universe(
    data_source: str,
    sample_universe_path: object,
    watchlist_path: object,
    direct_connection: bool = True,
) -> tuple[str, List[StockIdea]]:
    if data_source == "sample":
        return SampleDataProvider(sample_universe_path).load()
    if data_source == "akshare":
        return AkshareDataProvider(watchlist_path, direct_connection=direct_connection).load()
    if data_source == "auto":
        try:
            return AkshareDataProvider(watchlist_path, direct_connection=direct_connection).load()
        except Exception:
            return SampleDataProvider(sample_universe_path).load()
    raise ValueError(f"Unsupported data source: {data_source}")


def build_research_universe_from_rows(
    rows: list[dict],
    industry_map: dict[str, dict] | None = None,
    financial_map: dict[str, dict] | None = None,
) -> list[dict]:
    research_rows = []
    industry_map = industry_map or {}
    financial_map = financial_map or {}
    for row in rows:
        industry_row = industry_map.get(f"{row['code']}.{row['exchange']}")
        financial_row = financial_map.get(f"{row['code']}.{row['exchange']}")
        sector = (
            str(industry_row["sector_lv3"])
            if industry_row and industry_row["sector_lv3"]
            else str(industry_row["sector_lv2"])
            if industry_row and industry_row["sector_lv2"]
            else infer_sector(row["name"], row["board"])
        )
        style_tags = infer_style_tags(
            board=row["board"],
            latest_price=float(row["latest_price"]),
            turnover_ratio=float(row["turnover_ratio"]),
            sector=sector,
        )
        research_rows.append(
            {
                "ticker": row["code"],
                "name": row["name"],
                "sector": sector,
                "style_tags": style_tags,
                "valuation_percentile": float(financial_row["valuation_percentile"]) if financial_row else 0.50,
                "earnings_growth": float(financial_row["earnings_growth"]) if financial_row else 0.10,
                "revenue_growth": float(financial_row["revenue_growth"]) if financial_row else 0.10,
                "roe": float(financial_row["roe"]) if financial_row else 0.12,
                "free_cashflow_margin": float(financial_row["free_cashflow_margin"]) if financial_row else 0.08,
                "event_score": float(financial_row["event_score"]) if financial_row else 0.30,
                "fundamental_source": str(financial_row["fundamental_source"]) if financial_row else "local_profile",
                "industry_ready": bool(row.get("industry_ready", False)),
                "financial_ready": bool(row.get("financial_ready", False)),
                "factor_ready": bool(row.get("factor_ready", False)),
                "ready_pool": bool(row.get("ready_pool", False)),
                "theme_name": str(row.get("theme_name", "")),
                "theme_bucket": str(row.get("theme_bucket", "fallback")),
                "theme_source": str(row.get("theme_source", "")),
                "theme_strength": float(row.get("theme_strength", 0.0)),
                "router_mode": str(row.get("router_mode", "fallback")),
                "prefilter_week": str(row.get("prefilter_week", "")),
                "prefilter_theme": str(row.get("prefilter_theme", "")),
                "prefilter_bucket": str(row.get("prefilter_bucket", "")),
                "prefilter_score": float(row.get("prefilter_score", 0.0)),
                "policy_score": float(row.get("policy_score", 0.0)),
                "valuation_score": float(row.get("valuation_score", 0.0)),
                "performance_score": float(row.get("performance_score", 0.0)),
                "prefilter_source": str(row.get("prefilter_source", "")),
            }
        )
    return research_rows


def load_snapshot_universe_from_rows(
    rows: list[dict],
    factor_map: dict[str, dict],
) -> tuple[str, List[StockIdea]]:
    stock_ideas: list[StockIdea] = []
    for row in rows:
        factor_row = factor_map.get(str(row["ticker"]))
        if not factor_row:
            continue
        factor_data = dict(factor_row)
        style_tags = str(factor_data.get("style_tags", "")).split(",") if factor_data.get("style_tags") else []
        stock_ideas.append(
            StockIdea(
                ticker=str(row["ticker"]),
                name=str(row["name"]),
                sector=str(factor_data.get("sector", row.get("sector", "综合"))),
                style_tags=[tag for tag in style_tags if tag],
                valuation_percentile=float(factor_data.get("valuation_percentile", row.get("valuation_percentile", 0.5))),
                earnings_growth=float(factor_data.get("earnings_growth", 0.1)),
                revenue_growth=float(factor_data.get("revenue_growth", 0.1)),
                roe=float(factor_data.get("roe", 0.12)),
                free_cashflow_margin=float(factor_data.get("free_cashflow_margin", 0.08)),
                momentum_20d=float(factor_data.get("momentum_20d", 0.0)),
                momentum_60d=float(factor_data.get("momentum_60d", 0.0)),
                relative_strength=float(factor_data.get("relative_strength", 0.5)),
                volume_trend=float(factor_data.get("volume_trend", 0.5)),
                drawdown_from_high=float(factor_data.get("drawdown_from_high", 0.0)),
                volatility=float(factor_data.get("volatility", 0.5)),
                crowding=float(factor_data.get("crowding", 0.5)),
                event_score=float(factor_data.get("event_score", 0.3)),
                last_price=float(factor_data.get("last_price", row.get("latest_price", 0.0))),
                ma20_gap=float(factor_data.get("ma20_gap", 0.0)),
                ma60_gap=float(factor_data.get("ma60_gap", 0.0)),
                price_above_ma20=bool(factor_data.get("price_above_ma20", 0)),
                price_above_ma60=bool(factor_data.get("price_above_ma60", 0)),
                ma20_slope=float(factor_data.get("ma20_slope", 0.0)),
                turnover_trend=float(factor_data.get("turnover_trend", 0.5)),
                data_source=str(factor_data.get("data_source", "snapshot_cache")),
                fundamental_source=str(factor_data.get("fundamental_source", row.get("fundamental_source", "local_profile"))),
                industry_ready=bool(row.get("industry_ready", False)),
                financial_ready=bool(row.get("financial_ready", False)),
                factor_ready=bool(row.get("factor_ready", False)),
                ready_pool=bool(row.get("ready_pool", False)),
                theme_name=str(row.get("theme_name") or row.get("prefilter_theme", "")),
                theme_bucket=str(row.get("theme_bucket") or row.get("prefilter_bucket", "fallback")),
                theme_source=str(row.get("theme_source") or row.get("prefilter_source", "")),
                theme_strength=float(row.get("theme_strength", row.get("prefilter_score", 0.0))),
                router_mode=str(row.get("router_mode") or ("weekly_pool" if row.get("prefilter_week") else "fallback")),
                prefilter_week=str(row.get("prefilter_week", "")),
                prefilter_theme=str(row.get("prefilter_theme", "")),
                prefilter_bucket=str(row.get("prefilter_bucket", "")),
                prefilter_score=float(row.get("prefilter_score", 0.0)),
                policy_score=float(row.get("policy_score", 0.0)),
                valuation_score=float(row.get("valuation_score", 0.0)),
                performance_score=float(row.get("performance_score", 0.0)),
                prefilter_source=str(row.get("prefilter_source", "")),
            )
        )
    return "snapshot_cache", stock_ideas
