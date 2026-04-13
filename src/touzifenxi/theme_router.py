from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from .market_data import _call_with_retries, _call_with_timeout, _requests_direct_connection
from .models import ThemeEvent, ThemeRouteResult


@dataclass(frozen=True)
class ThemeDefinition:
    name: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]
    policy_keywords: tuple[str, ...]
    sector_keywords: tuple[str, ...]
    core_tickers: tuple[str, ...]
    negative_keywords: tuple[str, ...]
    low_priority_keywords: tuple[str, ...]


def _normalize_ticker(raw: str) -> str:
    code = str(raw).split(".")[0]
    if not code:
        return ""
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def _unique_texts(values: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def load_theme_definitions(config_path: Path) -> list[ThemeDefinition]:
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    themes = payload.get("themes")
    if not isinstance(themes, list) or not themes:
        raise ValueError("theme config must contain a non-empty 'themes' list")

    normalized: list[ThemeDefinition] = []
    theme_names: set[str] = set()
    core_tickers: set[str] = set()
    for item in themes:
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("theme config contains a theme without name")
        if name in theme_names:
            raise ValueError(f"duplicate theme name: {name}")
        theme_names.add(name)
        tickers = tuple(_normalize_ticker(raw) for raw in item.get("core_tickers", []))
        cleaned_tickers = tuple(ticker for ticker in tickers if ticker)
        for ticker in cleaned_tickers:
            if ticker in core_tickers:
                raise ValueError(f"duplicate core ticker across themes: {ticker}")
            core_tickers.add(ticker)
        normalized.append(
            ThemeDefinition(
                name=name,
                aliases=_unique_texts(item.get("aliases", [])),
                keywords=_unique_texts(item.get("keywords", [])),
                policy_keywords=_unique_texts(item.get("policy_keywords", [])),
                sector_keywords=_unique_texts(item.get("sector_keywords", [])),
                core_tickers=cleaned_tickers,
                negative_keywords=_unique_texts(item.get("negative_keywords", [])),
                low_priority_keywords=_unique_texts(item.get("low_priority_keywords", [])),
            )
        )
    return normalized


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _match_notice_theme(theme: ThemeDefinition, text: str) -> float:
    if not text:
        return 0.0
    if _contains_any(text, theme.negative_keywords):
        return 0.0
    strong_keywords = theme.keywords + theme.aliases
    if not _contains_any(text, strong_keywords):
        return 0.0
    penalty = 0.2 if _contains_any(text, theme.low_priority_keywords) else 0.0
    return max(0.2, 1.0 - penalty)


def _match_policy_theme(theme: ThemeDefinition, text: str) -> float:
    if not text:
        return 0.0
    if _contains_any(text, theme.negative_keywords):
        return 0.0
    keywords = theme.policy_keywords or theme.keywords or theme.aliases
    if not _contains_any(text, keywords):
        return 0.0
    return 0.7


def fetch_theme_events(trade_date: date, direct_connection: bool = True) -> tuple[list[dict], list[dict]]:
    import akshare as ak

    notice_rows: list[dict] = []
    policy_rows: list[dict] = []
    date_text = trade_date.strftime("%Y%m%d")
    with _requests_direct_connection(enabled=direct_connection):
        try:
            notice_df = _call_with_timeout(
                _call_with_retries,
                ak.stock_notice_report,
                symbol="全部",
                date=date_text,
                retries=2,
                delay=0.5,
                timeout_seconds=12.0,
            )
        except Exception:
            notice_df = None
        if notice_df is not None and not notice_df.empty:
            notice_rows = notice_df.to_dict("records")
        try:
            policy_df = _call_with_timeout(
                _call_with_retries,
                ak.news_cctv,
                date=date_text,
                retries=2,
                delay=0.5,
                timeout_seconds=12.0,
            )
        except Exception:
            policy_df = None
        if policy_df is not None and not policy_df.empty:
            policy_rows = policy_df.to_dict("records")
    return notice_rows, policy_rows


def collect_theme_signals(
    trade_date: date,
    config_path: Path,
    direct_connection: bool = True,
) -> tuple[list[ThemeDefinition], dict[str, float], list[ThemeEvent], dict[str, dict[str, float]]]:
    theme_defs = load_theme_definitions(config_path)
    notice_rows, policy_rows = fetch_theme_events(trade_date, direct_connection=direct_connection)

    theme_strength: dict[str, float] = {theme.name: 0.0 for theme in theme_defs}
    matched_events: list[ThemeEvent] = []
    matched_notice_tickers: dict[str, dict[str, float]] = {}

    for row in notice_rows:
        title = str(row.get("公告标题", ""))
        notice_type = str(row.get("公告类型", ""))
        stock_name = str(row.get("名称", ""))
        ticker = _normalize_ticker(str(row.get("代码", "")))
        text = " ".join(part for part in [title, notice_type, stock_name] if part)
        for theme in theme_defs:
            strength = _match_notice_theme(theme, text)
            if strength <= 0:
                continue
            theme_strength[theme.name] += strength
            matched_events.append(
                ThemeEvent(
                    theme_name=theme.name,
                    source_type="notice",
                    title=title or notice_type or stock_name,
                    event_date=trade_date.isoformat(),
                    ticker=ticker,
                    source_name=notice_type or "公告",
                    source_url=str(row.get("网址", "") or row.get("url", "") or ""),
                    strength=strength,
                )
            )
            if ticker:
                matched_notice_tickers.setdefault(ticker, {})
                matched_notice_tickers[ticker][theme.name] = max(matched_notice_tickers[ticker].get(theme.name, 0.0), strength)

    for row in policy_rows:
        title = str(row.get("title", ""))
        content = str(row.get("content", ""))
        text = f"{title} {content}"
        for theme in theme_defs:
            strength = _match_policy_theme(theme, text)
            if strength <= 0:
                continue
            theme_strength[theme.name] += strength
            matched_events.append(
                ThemeEvent(
                    theme_name=theme.name,
                    source_type="policy",
                    title=title,
                    event_date=trade_date.isoformat(),
                    source_name="news_cctv",
                    source_url="",
                    strength=strength,
                )
            )
    return theme_defs, theme_strength, matched_events, matched_notice_tickers


def _build_base_candidate_rows(candidate_rows: list[dict], extra_rows: list[dict] | None = None) -> dict[str, dict]:
    """合并候选池和补充池，供主题分配与旁路补齐共用同一视图。"""

    base_rows: dict[str, dict] = {str(row["ticker"]): dict(row) for row in candidate_rows}
    for row in extra_rows or []:
        base_rows.setdefault(str(row["ticker"]), dict(row))
    return base_rows


def assign_theme_candidates(
    candidate_rows: list[dict],
    theme_defs: list[ThemeDefinition],
    active_themes: list[str],
    theme_strength: dict[str, float],
    matched_notice_tickers: dict[str, dict[str, float]],
    extra_rows: list[dict] | None = None,
) -> list[dict]:
    base_rows = _build_base_candidate_rows(candidate_rows, extra_rows)

    assignments: dict[str, dict[str, object]] = {}
    theme_lookup = {theme.name: theme for theme in theme_defs}

    def assign(ticker: str, theme_name: str, bucket: str, source: str, strength: float) -> None:
        row = base_rows.get(ticker)
        if row is None:
            return
        priority = {"core": 3, "expanded": 2, "bypass": 1}.get(bucket, 0)
        current = assignments.get(ticker)
        if current is None or priority > int(current["priority"]) or (
            priority == int(current["priority"]) and strength > float(current["theme_strength"])
        ):
            assignments[ticker] = {
                "theme_name": theme_name,
                "theme_bucket": bucket,
                "theme_source": source,
                "theme_strength": round(strength, 4),
                "priority": priority,
            }

    for theme_name in active_themes:
        theme = theme_lookup[theme_name]
        strength = theme_strength[theme_name]
        for ticker in theme.core_tickers:
            assign(ticker, theme_name, "core", "manual_core", strength + 0.3)

        for ticker, ticker_theme_map in matched_notice_tickers.items():
            if theme_name in ticker_theme_map:
                assign(ticker, theme_name, "expanded", "notice_auto", strength + ticker_theme_map[theme_name])

        for ticker, row in base_rows.items():
            sector = str(row.get("sector", ""))
            name = str(row.get("name", ""))
            if theme.sector_keywords and _contains_any(sector, theme.sector_keywords):
                assign(ticker, theme_name, "expanded", "sector_auto", strength + 0.4)
                continue
            if _contains_any(name, theme.keywords + theme.aliases):
                assign(ticker, theme_name, "expanded", "name_auto", strength + 0.3)

    themed_rows: list[dict] = []
    for ticker, assignment in assignments.items():
        row = dict(base_rows[ticker])
        row["theme_name"] = str(assignment["theme_name"])
        row["theme_bucket"] = str(assignment["theme_bucket"])
        row["theme_source"] = str(assignment["theme_source"])
        row["theme_strength"] = float(assignment["theme_strength"])
        row["router_mode"] = "theme"
        themed_rows.append(row)
    return themed_rows


def build_theme_route(
    trade_date: date,
    candidate_rows: list[dict],
    config_path: Path,
    top_n: int,
    direct_connection: bool = True,
    candidate_limit: int = 50,
    extra_rows: list[dict] | None = None,
) -> ThemeRouteResult:
    if not config_path.exists():
        return ThemeRouteResult(router_mode="fallback", candidate_rows=candidate_rows[:candidate_limit])

    theme_defs, theme_strength, matched_events, matched_notice_tickers = collect_theme_signals(
        trade_date=trade_date,
        config_path=config_path,
        direct_connection=direct_connection,
    )

    active_themes = [theme.name for theme in theme_defs if theme_strength.get(theme.name, 0.0) >= 1.0]
    if not active_themes:
        return ThemeRouteResult(router_mode="fallback", candidate_rows=candidate_rows[:candidate_limit], events=matched_events)

    themed_rows = assign_theme_candidates(
        candidate_rows=candidate_rows,
        theme_defs=theme_defs,
        active_themes=active_themes,
        theme_strength=theme_strength,
        matched_notice_tickers=matched_notice_tickers,
        extra_rows=extra_rows,
    )

    themed_rows.sort(
        key=lambda row: (
            {"core": 0, "expanded": 1}.get(str(row.get("theme_bucket", "")), 2),
            -float(row.get("theme_strength", 0.0)),
            -int(row.get("ready_pool", 0)),
            -int(row.get("financial_ready", 0)),
            -float(row.get("amount", 0.0)),
        )
    )
    target_pool_size = max(top_n * 2, top_n + 2)
    bypass_count = 0
    final_rows = list(themed_rows)
    used_tickers = {str(row["ticker"]) for row in themed_rows}
    if len(final_rows) < target_pool_size:
        # 主题池不足时用原始候选池补齐，避免日报因为热点过窄而没有足够候选。
        base_rows = _build_base_candidate_rows(candidate_rows, extra_rows)
        bypass_candidates = [
            dict(row)
            for _, row in base_rows.items()
            if str(row["ticker"]) not in used_tickers
        ]
        bypass_candidates.sort(
            key=lambda row: (
                -int(row.get("ready_pool", 0)),
                -int(row.get("financial_ready", 0)),
                -int(row.get("factor_ready", 0)),
                -float(row.get("amount", 0.0)),
            )
        )
        for row in bypass_candidates:
            row["theme_name"] = ""
            row["theme_bucket"] = "bypass"
            row["theme_source"] = "router_bypass"
            row["theme_strength"] = 0.0
            row["router_mode"] = "theme"
            final_rows.append(row)
            bypass_count += 1
            if len(final_rows) >= target_pool_size:
                break

    if not themed_rows:
        return ThemeRouteResult(router_mode="fallback", candidate_rows=candidate_rows[:candidate_limit], events=matched_events)

    return ThemeRouteResult(
        router_mode="theme",
        active_themes=active_themes,
        candidate_rows=final_rows[:candidate_limit],
        events=matched_events,
        bypass_count=bypass_count,
    )
